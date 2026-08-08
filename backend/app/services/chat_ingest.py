"""ingest → chat 落盘、线程查找与删除的中立助手模块（Phase R · R1 从 main.py 下沉）。

同时被两处调用方依赖：
- HTTP `POST /api/ingest` 端点（`main.py` 的 `ingest_text`）与聊天相关端点（创建消息/
  删除线程/批量删除）。
- Telegram 长轮询循环（`main.py` 的 `_telegram_poll_loop`，仍留在 main.py，但需要
  `_persist_ingest_to_chat` / `_find_ingest_thread_by_receipt` / `_find_ingest_message_by_tg_id`）。

在此之前这些函数堆在 main.py 里，两个调用方都要 import main 才能用，为 R2（按域拆
`APIRouter`）设置了隐性障碍。本模块就是那个两边都能安全 import 的中立位置。

红线（CLAUDE.md §2/§6）：本模块只把候选写入 `kind="ingest"` 聊天线程，**绝不 import
`importer` / 调用任何 `upsert_*`**——真正入库只能发生在用户在 Web 聊天里点「入库选中」
触发的 commit 端点（仍在 main.py，走 `services.importer`）。
`tests/test_ingest.py` 的 `test_persist_ingest_and_poll_loop_write_chat_only` 对本模块的
`_persist_ingest_to_chat` 做源码级断言，`test_chat_ingest_module_never_imports_importer`
对本模块的 import 做 AST 级断言，任何人违反都会立刻测试翻红。

依赖方向：本模块只依赖 `db` / `config` / `models` / `services.*`，**不得 import
`main`**（会形成循环 import）——main.py 反过来从本模块 import 这些函数使用。
"""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func
from sqlmodel import Session, select

from ..candidates import Candidate
from ..config import Settings, get_settings
from ..models import ChatMessage, ChatThread, Job, utc_now
from .ai import is_ai_available
from .ingest import candidate_match_key, find_duplicate_thread, run_ingest
from .jobs import job_ids_by_canonical_key

# 复用线程时最多带多少条已识别候选给模型当上下文（与 ai.extract_jobs_freeform 内部再截一次一致，
# 双保险控制 prompt 体积；单用户、同一线索里的岗位数量本就很小）。
_MAX_PRIOR_CONTEXT_CANDIDATES = 5


def _chat_thread_payload(session: Session, thread: ChatThread) -> dict:
    message_count = session.exec(select(func.count(ChatMessage.id)).where(ChatMessage.thread_id == thread.id)).one()
    last_message = session.exec(
        select(ChatMessage.content).where(ChatMessage.thread_id == thread.id).order_by(ChatMessage.created_at.desc())
    ).first()
    job = session.get(Job, thread.job_id) if thread.job_id else None
    return {
        **jsonable_encoder(thread),
        "message_count": message_count,
        "last_message": last_message[:160] if last_message else None,
        "job": (
            {
                "id": job.id,
                "title": job.title,
                "company_name": job.company_name,
                "salary_text": job.salary_text,
                "city": job.city,
                "area": job.area,
            }
            if job
            else None
        ),
    }


def _save_chat_image(data_url: str, original_name: str | None, settings: Settings | None = None) -> dict:
    # settings 可选：main.py 的调用方都会显式传入自己当前的 `settings`（可被测试
    # monkeypatch，如 `main.settings = replace(main.settings, data_dir=...)`）；
    # 缺省时才现取一次，兼容测试里直接调用本函数、不关心 data_dir 的场景。
    settings = settings or get_settings()
    header, encoded = data_url.split(",", 1)
    mime_type = header.removeprefix("data:").removesuffix(";base64")
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type)
    if extension is None:
        raise HTTPException(status_code=400, detail="截图格式不支持，仅支持 PNG、JPEG、WebP")
    attachment_id = f"{uuid.uuid4().hex}.{extension}"
    attachment_dir = settings.data_dir / "chat_attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)
    path = attachment_dir / attachment_id
    path.write_bytes(base64.b64decode(encoded, validate=True))
    return {
        "kind": "image",
        "id": attachment_id,
        "name": (original_name or "截图")[:180],
        "mime_type": mime_type,
        "size_bytes": path.stat().st_size,
    }


def _chat_attachment_path(attachment_id: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    if not re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg|webp)", attachment_id):
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = (settings.data_dir / "chat_attachments" / attachment_id).resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    return path


def _remove_chat_attachment(attachment_id: str | None, settings: Settings | None = None) -> None:
    """删除一个聊天截图文件；ID 非法或文件已缺失都容忍,不抛异常。"""
    if not attachment_id:
        return
    settings = settings or get_settings()
    try:
        path = _chat_attachment_path(attachment_id, settings)
    except HTTPException:
        return
    path.unlink(missing_ok=True)


def _delete_chat_thread(session: Session, thread: ChatThread, settings: Settings | None = None) -> None:
    """删除单个线程连同全部消息与消息里引用的截图附件；调用方负责 `session.commit()`。

    抽成内部函数供单删端点与批量删除端点共用，行为完全一致(消息+线程+附件文件都清)。
    """
    settings = settings or get_settings()
    messages = session.exec(select(ChatMessage).where(ChatMessage.thread_id == thread.id)).all()
    attachment_ids = [
        message.metadata_json.get("attachment", {}).get("id")
        for message in messages
        if isinstance(message.metadata_json, dict) and isinstance(message.metadata_json.get("attachment"), dict)
    ]

    for message in messages:
        session.delete(message)
    session.delete(thread)

    for attachment_id in attachment_ids:
        _remove_chat_attachment(attachment_id, settings)


def _ingest_thread_title(text: str, has_image: bool) -> str:
    """压缩空白、剔除 URL 后取前 24 字生成线程标题；全是链接/空文本时回退到可辨识占位。

    原文可能是几百字的招聘文案或裸链接；不截断会把整段文字怼进线程标题，在聊天头部
    （`.chat-head` / 侧栏 `.chat-thread`）撑出超宽内容，真机上表现为标题被撑破、
    聊天区出现异常留白（前端另配 CSS 截断兜底，见 styles.css）。
    """
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    without_urls = re.sub(r"https?://\S+", "", collapsed)
    without_urls = re.sub(r"\s+", " ", without_urls).strip()
    if without_urls:
        snippet = without_urls[:24]
        if len(without_urls) > 24:
            snippet += "…"
        return f"入库候选 · {snippet}"
    if collapsed:
        # 原文非空但全是链接：没有可读文字可截取，用时间戳兜底，避免标题变成裸链接或空字符串。
        return f"入库候选 · {utc_now().strftime('%m%d %H:%M')}"
    if has_image:
        return "入库候选 · 截图入库"
    return "入库候选"


def _find_ingest_thread_by_receipt(session: Session, reply_to_message_id: int) -> int | None:
    """按 Telegram「回复了哪条回执」找回对应的 ingest 线程 id；找不到返回 None。

    单用户、量小：只看最近 ~50 个 ingest 线程里任意一条 assistant 消息的
    `metadata_json.receipt_tg_message_id` 是否匹配即可，不加表不加列不做 JSON 索引。
    """
    threads = session.exec(
        select(ChatThread).where(ChatThread.kind == "ingest").order_by(ChatThread.updated_at.desc()).limit(50)
    ).all()
    thread_ids = [t.id for t in threads if t.id is not None]
    if not thread_ids:
        return None
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.thread_id.in_(thread_ids), ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc())
    ).all()
    for message in messages:
        if (message.metadata_json or {}).get("receipt_tg_message_id") == reply_to_message_id:
            return message.thread_id
    return None


def _find_ingest_message_by_tg_id(session: Session, tg_message_id: int) -> ChatMessage | None:
    """按 Telegram 消息自身的 message_id 找回当初落盘的那条 user 消息（`source_tg_message_id`）。

    用于处理「编辑消息」：Telegram 编辑事件的 message_id 和被编辑的原消息完全一致，据此判断
    「这是不是在编辑本 bot 处理过的某条消息」，而不是一条全新消息。只看最近 ~50 个 ingest
    线程，和 `_find_ingest_thread_by_receipt` 同样的取舍：单用户、量小，足够覆盖实际场景。
    """
    threads = session.exec(
        select(ChatThread).where(ChatThread.kind == "ingest").order_by(ChatThread.updated_at.desc()).limit(50)
    ).all()
    thread_ids = [t.id for t in threads if t.id is not None]
    if not thread_ids:
        return None
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.thread_id.in_(thread_ids), ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
    ).all()
    for message in messages:
        if (message.metadata_json or {}).get("source_tg_message_id") == tg_message_id:
            return message
    return None


def _persist_ingest_to_chat(
    session: Session,
    text: str | None,
    image_data_url: str | None = None,
    target_thread_id: int | None = None,
    source_tg_message_id: int | None = None,
    edited_from_tg_message_id: int | None = None,
) -> dict:
    """抽取候选 → 写入 kind=ingest 聊天线程（**不写 Job 表**）。HTTP 与 Telegram 共用。

    即便 unmatched 也建线程，保留原文/截图原料，满足「资料别删」。

    `target_thread_id`：可选。命中一个已有的 `kind="ingest"` 线程时，这次的 user/assistant
    消息追加进该线程而不是新建（用于 Telegram「回复回执」把补充材料关联回同一条线索，或
    同一相册的多张图片归到一起）；线程不存在或不是 ingest 类型时静默回退到新建线程——
    对应「回复的不是回执/太久远」的现状行为不变。

    `source_tg_message_id`：仅 Telegram 路径传入，记到这条 user 消息的 metadata 里，供以后
    「这条消息在 Telegram 上被编辑了」时反查回同一条线索（见 `_find_ingest_message_by_tg_id`）。
    `edited_from_tg_message_id`：这条 user 消息本身就是某次编辑事件追加的内容时传入，标注它
    源自哪条 Telegram 消息的编辑——和 `source_tg_message_id` 分开存，避免同一个 tg message_id
    在同一线程里对应两条 user 消息时查找结果有歧义。

    注：不接受 `settings` 参数——HTTP 端点与 Telegram 轮询都用固定的位置/关键字参数调用
    （轮询里还有测试直接 monkeypatch 整个函数,签名必须保持不变),因此这里现取一次
    `get_settings()`,而不是像 `_save_chat_image` 那样接受调用方传入。
    """
    settings = get_settings()
    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    ai_enabled = bool(ai_cfg.get("enabled")) and is_ai_available()
    text = (text or "").strip()

    # 先解析 target_thread_id（相册/回复回执场景）：命中已有 ingest 线程时，把它已识别的候选
    # 作为上下文喂给本次抽取——同一岗位拆多张图/多条消息时，单独一张碎片图（如只有「任职要求」）
    # 独立抽取会一个岗位都认不出；带上已知候选，模型才能判断"这是补充"而不是"没内容"。
    #
    # 健壮性：跨该线程**所有** assistant 消息收集候选、按 match_key 去重，而不是只看最后一条——
    # 这样才不依赖到达顺序（相册里「碎片图在前、主图在后」时也拿得到主图那条），也能把多轮补充
    # 累积起来。从近到远遍历，同一岗位保留最近一条（最完整/已合并的版本胜出），最多带 5 条。
    explicit_reuse_thread = None
    prior_candidates: list[dict] = []
    if target_thread_id is not None:
        maybe_thread = session.get(ChatThread, target_thread_id)
        if maybe_thread is not None and maybe_thread.kind == "ingest":
            explicit_reuse_thread = maybe_thread
            try:
                assistants = session.exec(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == maybe_thread.id, ChatMessage.role == "assistant")
                    .order_by(ChatMessage.created_at.desc())
                ).all()
                by_key: dict[str, dict] = {}
                for msg in assistants:
                    if not isinstance(msg.metadata_json, dict):
                        continue
                    stored = msg.metadata_json.get("candidates")
                    if not isinstance(stored, list):
                        continue
                    for cand in stored:
                        if not isinstance(cand, dict):
                            continue
                        # 去重 key：优先 canonical_key/标题+公司（candidate_match_key）；它对「公司未知」
                        # 的候选会返回 None（去重安全考虑），但一个有真实**标题**、只是公司未知的岗位
                        # （如「独立站运营·未知公司」——正是真机里的常见形态）当上下文完全有用，不能丢。
                        # 所以 match_key 为空时退到「标题」做 key；连标题都没有的纯占位候选才跳过。
                        key = candidate_match_key(cand)
                        if not key:
                            title = re.sub(r"\s+", "", str(cand.get("title") or "").strip().lower())
                            if not title:
                                continue
                            key = f"title:{title}"
                        if key not in by_key:
                            by_key[key] = cand
                    if len(by_key) >= _MAX_PRIOR_CONTEXT_CANDIDATES:
                        break
                prior_candidates = list(by_key.values())[:_MAX_PRIOR_CONTEXT_CANDIDATES]
            except Exception:  # noqa: BLE001 - 上下文纯属锦上添花，读取异常绝不能打断 ingest 主流程
                prior_candidates = []

    extract = run_ingest(
        text,
        wechat_cfg=settings.wechat_config,
        bebee_cfg=settings.bebee_config,
        ai_enabled=ai_enabled,
        image_data_url=image_data_url,
        manual_source=str(settings.ingest_config.get("manual_source") or "manual"),
        prior_candidates=prior_candidates or None,
    )

    candidates: list[Candidate] = extract["candidates"]
    # 只读标注：命中已入库岗位的 canonical_key 就打上 existing_job_id，供前端标「已在岗位池」
    # 并默认不勾选；仍允许勾选提交（commit 端点走 importer 合并已有记录，不重复建 Job，这里只读不写）。
    existing_by_key = job_ids_by_canonical_key(session, [c.get("canonical_key") for c in candidates])
    for candidate in candidates:
        key = candidate.get("canonical_key")
        if key and key in existing_by_key:
            candidate["existing_job_id"] = existing_by_key[key]

    reused_thread = explicit_reuse_thread

    # 重复检测：只在没有更明确的关联目标（回复回执/同相册）时才跑，避免和那两种「有意归并」互相打架。
    # 全部命中同一个既有线程 → 直接复用那个线程（不新建，等同 target_thread_id 机制）；
    # 部分命中 → 仍新建线程，只在候选上打 duplicate_in_thread_id 标注，原料一条都不丢。
    duplicate_merge = False
    duplicate_count = 0
    if reused_thread is None and candidates:
        merge_thread_id, duplicate_count = find_duplicate_thread(session, candidates)
        if merge_thread_id is not None:
            maybe_thread = session.get(ChatThread, merge_thread_id)
            if maybe_thread is not None and maybe_thread.kind == "ingest":
                reused_thread = maybe_thread
                duplicate_merge = True

    if reused_thread is not None:
        thread = reused_thread
    else:
        title = _ingest_thread_title(text, bool(image_data_url))
        thread = ChatThread(kind="ingest", job_id=None, title=title)
        session.add(thread)
        session.commit()
        session.refresh(thread)

    attachment = _save_chat_image(image_data_url, None, settings) if image_data_url else None
    user_content = text or ("[截图]" if attachment else "")
    user_metadata: dict = {}
    if attachment:
        user_metadata["attachment"] = attachment
    if source_tg_message_id is not None:
        user_metadata["source_tg_message_id"] = source_tg_message_id
    if edited_from_tg_message_id is not None:
        user_metadata["edited_from_tg_message_id"] = edited_from_tg_message_id
    user_message = ChatMessage(
        thread_id=thread.id or 0,
        role="user",
        content=user_content,
        metadata_json=user_metadata,
    )
    session.add(user_message)

    n = extract["candidate_count"]
    ai_error = extract.get("ai_error")
    if duplicate_merge:
        # 全部候选都和这条既有线索重复：不重复入库提示，直接说明已经归入哪条线索，原料仍然保留在这条消息里。
        assistant_text = f"与已有线索重复，已归入『{thread.title}』（未入库）。"
    elif n:
        assistant_text = f"识别到 {n} 个候选岗位。请在下方勾选要入库的项；原文和截图已保留在本对话。"
        if duplicate_count:
            assistant_text += f" 其中 {duplicate_count} 个与近期候选重复。"
    elif ai_error:
        # AI 已启用且理应能连通，但本次调用真的失败了：必须跟「AI 未启用」「AI 正常但没识别出岗位」区分开，
        # 否则用户只会看到「未认出」，误以为是内容问题而反复重试（真实场景常是模型不支持图片输入）。
        assistant_text = f"AI 抽取失败：{ai_error}。若发送的是截图，请确认所配模型支持图片输入（OPENAI_MODEL）。原料已保留。"
    elif extract.get("needs_ai"):
        assistant_text = "未认出可抓取链接，且 AI 未启用，无法从文本/截图抽取岗位。原料已保留；启用 AI 后可重发。"
    else:
        assistant_text = "未从链接、文本或截图中认出岗位。原料已保留，可补充更完整的 JD 或更清晰的截图后重发。"

    if extract.get("known_uncrawlable_hint"):
        assistant_text += (
            "\n检测到 BOSS/智联链接：该平台受风控无法直接抓取公开页，"
            "请复制 JD 文本或随手发一张截图（可与链接同一条消息）。链接已随原料保留。"
        )

    assistant_message = ChatMessage(
        thread_id=thread.id or 0,
        role="assistant",
        content=assistant_text,
        metadata_json={
            "candidates": candidates,
            "sources_report": extract["sources_report"],
            "unmatched": extract["unmatched"],
            "needs_ai": extract.get("needs_ai", False),
            "ai_error": ai_error,
            "known_uncrawlable_hint": extract.get("known_uncrawlable_hint", False),
            "run_status": (
                "rules_only" if not ai_enabled else "ai_failed" if ai_error else "completed" if n else "fallback"
            ),
        },
    )
    session.add(assistant_message)
    thread.updated_at = utc_now()
    session.add(thread)
    session.commit()
    session.refresh(thread)
    session.refresh(user_message)
    session.refresh(assistant_message)

    return {
        "thread": _chat_thread_payload(session, thread),
        "user_message": user_message,
        "assistant_message": assistant_message,
        "candidate_count": n,
        "unmatched": extract["unmatched"],
        "needs_ai": extract.get("needs_ai", False),
        "ai_error": ai_error,
        "known_uncrawlable_hint": extract.get("known_uncrawlable_hint", False),
        "sources_report": extract["sources_report"],
        "candidates": candidates,
        "appended": reused_thread is not None,
        # duplicate_merge=True 时 appended 也是 True（同样是「并入既有线程」），但措辞要和
        # target_thread_id 那种「有意补充」区分开——这批候选是查重命中的，不是用户主动关联的。
        "duplicate_merge": duplicate_merge,
        "duplicate_count": duplicate_count,
    }
