"""候选岗位的决策建议：让手机端在「识别到候选」之外，先拿到一句「值不值得推进」。

为什么单独一个模块：
- 判断逻辑本身已经存在——Web 决策聊天的 `build_rule_analysis`（确定性规则）+
  `analyze_decision_chat_llm`（模型润色）+ `merge_model_analysis`（模型不得抹掉规则证据）。
  这里**只做适配**：把一条 ingest 候选包装成规则引擎认识的「岗位事实」，跑同一条链路，
  再压成手机上一眼能读完的三行。不新写第二套判断标准（CLAUDE.md §11 KISS）。
- 放在 `chat_ingest` 之外，是因为那个模块有绊线测试锁定「只写聊天、绝不入库」；建议生成
  会构造一个**临时 Job 对象**当规则引擎的输入载体，放进去会和绊线的 `Job(` 断言打架。

红线：本模块**只读**（画像、只读上下文仓库、候选字段）。构造的 `Job(...)` 是纯内存对象，
从不 `session.add`、从不进任何 upsert；`session` 参数只用来读画像。
`tests/test_ingest.py::test_advice_and_decision_reply_never_import_importer` 锁定这一点
（AST 级断言：本模块不得对 `session` 调 add/commit 之类的写操作）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session

from ..candidates import Candidate
from ..models import Job, UserProfile
from .ai import analyze_decision_chat_llm
from .chat_support import decision_context, job_context
from .decision_chat import build_rule_analysis, merge_model_analysis
from .queries import get_profile

logger = logging.getLogger(__name__)

# 默认最多为前几个候选生成建议：每条建议是一次独立的模型调用，一次发来 8 个岗位的截图时
# 全量生成既慢又贵，而手机上也读不完。超出的候选仍原样保留在聊天里，去 Web 逐个看。
DEFAULT_MAX_ADVICE_ITEMS = 3

# 临时 Job 对象要带哪些候选字段（规则引擎 `build_rule_analysis` / `job_context` 会读到的那些）。
_JOB_FACT_KEYS = (
    "title",
    "company_name",
    "salary_text",
    "salary_min_k",
    "salary_max_k",
    "city",
    "area",
    "experience",
    "degree",
    "skills",
    "description",
    "recruiter",
    "url",
    "source",
)


def candidate_job(candidate: Candidate) -> Job:
    """把候选 dict 包成一个**纯内存**的 Job 对象，仅作为规则引擎的输入载体。

    绝不入库：没有 id，不 add 进 session，函数返回后即被丢弃。这样做而不是给
    `build_rule_analysis` 另开一条「按 dict 判断」的分支，是为了让手机建议和 Web 决策
    聊天走**完全相同**的判断代码，避免两套结论对不上。

    `decision_reply` 也复用它：追问时把「这条线索在聊的候选」变成同样的岗位事实载体。
    """
    fields: dict[str, Any] = {key: candidate.get(key) for key in _JOB_FACT_KEYS if candidate.get(key) is not None}
    fields.setdefault("title", "未命名岗位")
    fields.setdefault("company_name", "未知公司")
    fields.setdefault("source", "ingest")
    # external_id 是 Job 的必填列；这里给个显式占位，强调该对象不参与任何去重/入库。
    return Job(external_id="", **fields)


def _candidate_text(candidate: Candidate) -> str:
    """候选的可读材料：给规则引擎当「本次输入的材料」，也是模型看到的对话内容。"""
    parts = [
        candidate.get("title"),
        candidate.get("company_name"),
        candidate.get("salary_text"),
        " · ".join(filter(None, [candidate.get("city"), candidate.get("area")])),
        candidate.get("experience"),
        candidate.get("degree"),
        candidate.get("skills"),
        candidate.get("description"),
    ]
    return "\n".join(str(part) for part in parts if part)


def _advice_from_analysis(analysis: dict[str, Any], *, ai_used: bool) -> dict[str, Any]:
    """压成手机上够用的最小字段集；完整分析不落盘（候选卡不是决策记录，Web 里可以再问）。"""
    reasons = [str(item) for item in analysis.get("reasons") or [] if str(item).strip()]
    uncertainties = [str(item) for item in analysis.get("uncertainties") or [] if str(item).strip()]
    hard_conditions = [str(item) for item in analysis.get("hard_conditions") or [] if str(item).strip()]
    return {
        "priority": analysis.get("priority") or "待确认",
        "direction": analysis.get("direction") or "待确认",
        "next_action": analysis.get("next_action") or "补充信息",
        "summary": str(analysis.get("summary") or "")[:160],
        "action_text": str(analysis.get("action_text") or "")[:160],
        "reasons": reasons[:2],
        "ask_first": uncertainties[:2],
        "hard_conditions": hard_conditions[:2],
        "ai_used": ai_used,
    }


def build_candidate_advice(
    session: Session,
    candidates: list[Candidate],
    *,
    ai_enabled: bool,
    max_items: int = DEFAULT_MAX_ADVICE_ITEMS,
    profile: UserProfile | None = None,
) -> int:
    """给前 `max_items` 个候选原地挂上 `candidate["advice"]`，返回实际生成的条数。

    `ai_enabled=False` 时不做任何模型调用，只落规则引擎的确定性结论——手机上照样有建议，
    只是措辞是模板化的（和 Web 聊天「仅规则」模式一致）。

    任何一条建议失败都只跳过该条：建议是锦上添花，绝不能让 ingest 落盘整体失败。
    注意：调用方（`chat_ingest`）已在 threadpool 里，这里的模型调用是同步阻塞的，
    条数上限也是为了控制这个等待时间。
    """
    if not candidates or max_items <= 0:
        return 0

    profile = profile or get_profile(session)
    try:
        context_text, _rules_version, context_available = decision_context()
    except Exception:  # noqa: BLE001 - 只读上下文仓库不可用时降级为「只用应用内画像」
        logger.warning("读取决策上下文失败，建议改用应用内画像", exc_info=True)
        context_text, context_available = "", False

    generated = 0
    for candidate in candidates[:max_items]:
        try:
            job = candidate_job(candidate)
            message = _candidate_text(candidate)
            rule_analysis = build_rule_analysis(
                message=message,
                profile=profile,
                job=job,
                thread_kind="ingest",
                context_available=context_available,
                image_attached=False,
                policy_context=context_text,
            )
            model_analysis = None
            if ai_enabled:
                model_analysis = analyze_decision_chat_llm(
                    context=context_text,
                    conversation=[{"role": "user", "content": message[:4000]}],
                    job_context=job_context(job),
                    rule_analysis=rule_analysis,
                )
            analysis = merge_model_analysis(rule_analysis, model_analysis)
            candidate["advice"] = _advice_from_analysis(analysis, ai_used=model_analysis is not None)
            generated += 1
        except Exception:  # noqa: BLE001 - 单条建议失败不影响其它候选，更不影响原料落盘
            logger.warning("候选建议生成失败，跳过该条", exc_info=True)
    return generated


def format_advice_block(candidates: list[Candidate]) -> str:
    """把已挂在候选上的建议排成手机/聊天都能直接读的文本块；没有任何建议时返回空串。

    手机上一屏能读完是硬约束：每个候选最多四行（标题行 + 建议 + 理由 + 先问），
    命中硬性条件时再加一行「硬条件」。发送前调用方无需再截断，`send_message` 另有 4000 字兜底。
    """
    numbers = "①②③④⑤⑥⑦⑧⑨⑩"
    blocks: list[str] = []
    advised = 0
    for index, candidate in enumerate(candidates):
        advice = candidate.get("advice")
        if not isinstance(advice, dict):
            continue
        advised += 1
        marker = numbers[index] if index < len(numbers) else f"{index + 1}."
        head = " · ".join(
            str(part)
            for part in [candidate.get("title") or "未命名岗位", candidate.get("company_name"), candidate.get("salary_text")]
            if part
        )
        lines = [
            f"{marker} {head}",
            f"建议：{advice.get('priority')} / {advice.get('direction')} → {advice.get('next_action')}",
        ]
        if advice.get("hard_conditions"):
            lines.append(f"硬条件：{'；'.join(advice['hard_conditions'])}")
        reason = "；".join(advice.get("reasons") or []) or advice.get("summary") or ""
        if reason:
            lines.append(f"理由：{reason}")
        if advice.get("ask_first"):
            lines.append(f"先问：{'；'.join(advice['ask_first'])}")
        elif advice.get("action_text"):
            lines.append(f"下一步：{advice['action_text']}")
        blocks.append("\n".join(lines))

    if not blocks:
        return ""
    remaining = len(candidates) - advised
    text = "\n\n".join(blocks)
    if remaining > 0:
        text += f"\n\n其余 {remaining} 个候选未生成建议，可在 Web 逐个查看。"
    return text
