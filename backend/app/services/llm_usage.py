"""LLM 用量记录与汇总（借鉴 geekgeekrun 的 LlmModelUsageRecord）。

`record_llm_usage` 是注入给 `ai.set_usage_recorder` 的写库回调：`ai._chat` 每成功一次就调它写一行。
它自开一个 Session（`_chat` 在请求上下文之外也会被调用），并把所有异常吞在这里——记账是旁路，
绝不能因为写库失败而拖垮一次真正的模型调用（调用方 `ai._record_usage` 也再包了一层）。

只记 token 计数与 model/provider/purpose，不存 API key、不存请求/响应内容（本地优先 + 不泄密）。
"""

from __future__ import annotations

import logging

from sqlmodel import Session, func, select

from ..models import LlmUsageRecord

logger = logging.getLogger(__name__)


def record_llm_usage(payload: dict) -> None:
    """写一行 LLM 用量。失败只记 debug 日志，不抛。"""
    try:
        from ..db import engine

        with Session(engine) as session:
            session.add(
                LlmUsageRecord(
                    provider_label=payload.get("provider_label"),
                    model=payload.get("model"),
                    purpose=payload.get("purpose") or "other",
                    prompt_tokens=payload.get("prompt_tokens"),
                    completion_tokens=payload.get("completion_tokens"),
                    total_tokens=payload.get("total_tokens"),
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001 - 记账旁路，不能影响主流程
        logger.debug("写入 LLM 用量失败（已忽略）", exc_info=True)


def usage_summary(session: Session) -> dict:
    """AI 用量汇总：总调用次数 / 各 token 合计 + 按 purpose 分组。只读。"""
    total_calls = session.exec(select(func.count()).select_from(LlmUsageRecord)).one()
    totals = session.exec(
        select(
            func.coalesce(func.sum(LlmUsageRecord.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.completion_tokens), 0),
            func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0),
        )
    ).one()
    by_purpose_rows = session.exec(
        select(
            LlmUsageRecord.purpose,
            func.count(),
            func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0),
        ).group_by(LlmUsageRecord.purpose)
    ).all()
    return {
        "calls": int(total_calls or 0),
        "prompt_tokens": int(totals[0] or 0),
        "completion_tokens": int(totals[1] or 0),
        "total_tokens": int(totals[2] or 0),
        "by_purpose": [
            {"purpose": row[0], "calls": int(row[1] or 0), "total_tokens": int(row[2] or 0)}
            for row in by_purpose_rows
        ],
    }
