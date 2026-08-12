"""决策问答的落盘核心：一条用户消息 → 规则+模型分析 → assistant 回复 + AnalysisRun。

从 `routers/chat.py::create_chat_message` 原样下沉（行为逐字不变），目的是让 Telegram
「在手机上追问」和 Web 聊天走**同一条**判断与落盘代码：同样的规则引擎初判、同样的模型
合并策略、同样的 `AnalysisRun` 审计记录。两处各写一遍必然漂移。

红线（CLAUDE.md §2/§6）：本模块只写聊天与分析记录，**不 import importer / 不调用任何
upsert**——追问不会入库任何岗位；入库只发生在用户点「入库选中」的 commit 端点。
依赖方向：只依赖 `config` / `models` / `services.*`，不得 import `main` 或 `routers`。
"""

from __future__ import annotations

from sqlmodel import Session, select

from ..candidates import Candidate
from ..config import Settings, get_settings
from ..models import AnalysisRun, ChatMessage, ChatThread, Job, utc_now
from .advice import candidate_job
from .ai import analyze_decision_chat_llm, configured_model, is_ai_available
from .chat_ingest import _chat_thread_payload, _save_chat_image, thread_candidates
from .chat_support import decision_context, job_context, recent_conversation
from .decision_chat import assistant_content, build_rule_analysis, mark_image_processed, merge_model_analysis
from .queries import get_profile

# 手机端追问在没有明确线索可挂时落到这条通用线程；标题固定，便于复用同一条而不是每问一次开一条。
MOBILE_THREAD_TITLE = "手机提问"

# 一条 ingest 线索里最多认这么多个候选可供指名（`?2` 之类）；和建议条数一样，手机上本就读不完更多。
_MAX_ANCHOR_CANDIDATES = 5

_MARKERS = "①②③④⑤⑥⑦⑧⑨⑩"

# 取回多少条历史消息交给 `recent_conversation`（它只用最后 12 条，留一倍余量即可）。
_HISTORY_WINDOW = 24


def candidate_label(candidate: Candidate, index: int) -> str:
    """「① 独立站运营 · 未知公司」——回答开头要回显它，否则你根本不知道模型在答哪个岗位。"""
    marker = _MARKERS[index] if index < len(_MARKERS) else f"{index + 1}."
    head = " · ".join(
        str(part) for part in [candidate.get("title") or "未命名岗位", candidate.get("company_name")] if part
    )
    return f"{marker} {head}"


def resolve_thread_anchor(session: Session, thread: ChatThread, candidate_index: int | None = None) -> dict:
    """确定「这次提问在问哪个岗位」，返回一个可读的锚点描述。

    过去只看 `thread.job_id`：岗位聊天有值，**ingest 线索恒为 None**（候选没入库前没有 Job
    记录），于是模型只能从对话正文里猜——发的是截图时正文只有字面量「[截图]」，等于什么
    都没有。这里补上真正的事实来源：该线索已识别的候选。

    优先级：
    1. `thread.job_id` → 岗位聊天，用真实 Job（最完整，还带公司/评分等既有上下文）。
    2. ingest 线索的候选：`candidate_index` 指名哪个就用哪个（手机上 `?2`、Web 上点「问这个」）；
       没指名时单候选直接用它，多候选默认第一个并在回答里提示怎么换。
    3. 已入库（committed）的候选 → 回到真实 Job；未入库的用纯内存 Job 承载候选字段。
    4. 都没有 → `kind="none"`，行为与改动前一致（只靠对话正文）。
    """
    none_anchor: dict = {"kind": "none", "job": None, "label": None, "index": None, "total": 0}
    if thread.job_id:
        job = session.get(Job, thread.job_id)
        if job is not None:
            return {
                "kind": "job",
                "job": job,
                "label": f"{job.company_name} · {job.title}",
                "index": None,
                "total": 0,
            }
        return none_anchor
    if thread.kind != "ingest":
        return none_anchor

    candidates = thread_candidates(session, thread.id or 0, limit=_MAX_ANCHOR_CANDIDATES)
    if not candidates:
        return none_anchor

    index = candidate_index if candidate_index is not None and 0 <= candidate_index < len(candidates) else 0
    candidate = candidates[index]
    # 已入库的候选回到真实 Job：字段更全，且和 Web 上打开这个岗位看到的事实完全一致。
    job = session.get(Job, candidate["job_id"]) if candidate.get("job_id") else None
    return {
        "kind": "candidate",
        "job": job or candidate_job(candidate),
        "label": candidate_label(candidate, index),
        "index": index,
        "total": len(candidates),
    }


def reply_in_thread(
    session: Session,
    thread: ChatThread,
    content: str,
    *,
    image_data_url: str | None = None,
    image_name: str | None = None,
    use_ai: bool = True,
    candidate_index: int | None = None,
    settings: Settings | None = None,
) -> dict:
    """在给定线程里追加一轮问答（user 消息 + 分析 + assistant 回复 + AnalysisRun）。

    `use_ai=False`（Web 的「本条不用 AI」开关）与「AI 未启用/不可用」共用同一条降级路径：
    `model_analysis` 保持 None、`ai_used=False`、`run_status="rules_only"`、provider="rules"。

    `candidate_index`：在 ingest 线索里指名问第几个候选（手机 `?2`、Web 点「问这个」）。
    解析结果见 `resolve_thread_anchor`；回答开头会回显锚点，返回值里也带 `anchor`。
    """
    settings = settings or get_settings()
    thread_id = thread.id or 0
    anchor = resolve_thread_anchor(session, thread, candidate_index)
    job = anchor["job"]
    profile = get_profile(session)

    attachment = _save_chat_image(image_data_url, image_name, settings) if image_data_url else None
    user_message = ChatMessage(
        thread_id=thread_id,
        role="user",
        content=content,
        metadata_json={"attachment": attachment} if attachment else {},
    )
    session.add(user_message)
    thread.updated_at = utc_now()
    if thread.title == "新对话":
        thread.title = content.replace("\n", " ")[:32]
    session.add(thread)
    session.commit()
    session.refresh(user_message)

    context_text, rules_version, context_available = decision_context()
    rule_analysis = build_rule_analysis(
        message=content,
        profile=profile,
        job=job,
        thread_kind=thread.kind,
        context_available=context_available,
        image_attached=bool(image_data_url),
        policy_context=context_text,
    )
    # 只取最近 _HISTORY_WINDOW 条：`recent_conversation` 反正只用最后 12 条，而「手机提问」
    # 线程是**永久复用**的（`find_or_create_mobile_thread`），一路问下去会积累上千条消息——
    # 原来每次追问都把整条线程读出来（还要逐条解 metadata_json 里的 analysis），耗时随消息数
    # 线性上涨（实测 ~1000 条时单次追问 8ms → 30ms，且没有上界）。倒序取窗口再翻回正序，
    # 结果与原来逐字一致。created_at 相同时用 id 兜底定序，避免同秒消息顺序不稳。
    history = list(
        session.exec(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(_HISTORY_WINDOW)
        ).all()
    )[::-1]
    conversation = recent_conversation(history)
    job_ctx = job_context(job)

    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    ai_enabled = bool(ai_cfg.get("enabled")) and is_ai_available() and use_ai
    model_analysis = None
    if ai_enabled:
        model_analysis = analyze_decision_chat_llm(
            context=context_text,
            conversation=conversation,
            job_context=job_ctx,
            rule_analysis=rule_analysis,
            image_data_url=image_data_url,
        )
    ai_used = model_analysis is not None
    analysis = merge_model_analysis(rule_analysis, model_analysis)
    if ai_used and image_data_url:
        mark_image_processed(analysis)
    run_status = "completed" if ai_used else ("fallback" if ai_enabled else "rules_only")
    provider = str(ai_cfg.get("provider") or "openai_compatible") if ai_enabled else "rules"

    # 回答开头回显「在答哪个岗位」：ingest 线索里可能有好几个候选，不回显的话你看到一句
    # 「B / 邻近可接受」根本不知道说的是哪一个——这正是「怎么确定问的是哪个岗位」的答案要落地的地方。
    body = assistant_content(analysis, ai_used=ai_used)
    reply_text = f"针对 {anchor['label']}\n\n{body}" if anchor["kind"] == "candidate" else body
    assistant_message = ChatMessage(
        thread_id=thread_id,
        role="assistant",
        content=reply_text,
        metadata_json={
            "analysis": analysis,
            "ai_used": ai_used,
            "run_status": run_status,
            # 前端据此显示「针对哪个候选」，也让「换一个候选再问」有据可依。
            "anchor": {"kind": anchor["kind"], "label": anchor["label"], "index": anchor["index"], "total": anchor["total"]},
        },
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)
    analysis_run = AnalysisRun(
        thread_id=thread_id,
        user_message_id=user_message.id or 0,
        assistant_message_id=assistant_message.id,
        rules_version=rules_version,
        provider=provider,
        model=configured_model() if ai_enabled else None,
        status=run_status,
        result_json=analysis,
    )
    session.add(analysis_run)
    session.commit()
    session.refresh(analysis_run)
    session.refresh(thread)
    session.refresh(user_message)
    session.refresh(assistant_message)

    return {
        "thread": _chat_thread_payload(session, thread),
        "user_message": user_message,
        "assistant_message": assistant_message,
        "analysis_run": analysis_run,
        "analysis": analysis,
        "ai_used": ai_used,
        # 调用方（Telegram 回答排版、前端提示）据此告诉用户「答的是哪个岗位、怎么换一个」。
        "anchor": {"kind": anchor["kind"], "label": anchor["label"], "index": anchor["index"], "total": anchor["total"]},
    }


def find_or_create_mobile_thread(session: Session) -> ChatThread:
    """手机端「没挂在任何线索上的提问」落到哪条线程：复用最近一条同名通用线程，没有才新建。

    刻意不给每个问题开一条线程——手机上的追问通常是连续的同一件事，堆一串一次性线程只会
    把侧栏刷满（这正是回复回执归并材料要解决的同类问题）。想换话题时在 Web 里改标题即可。
    """
    thread = session.exec(
        select(ChatThread)
        .where(ChatThread.kind == "general", ChatThread.title == MOBILE_THREAD_TITLE)
        .order_by(ChatThread.updated_at.desc())
    ).first()
    if thread is not None:
        return thread
    thread = ChatThread(kind="general", job_id=None, title=MOBILE_THREAD_TITLE)
    session.add(thread)
    session.commit()
    session.refresh(thread)
    return thread
