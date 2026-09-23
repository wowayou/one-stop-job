"""LLM 用量记录（借鉴 geekgeekrun 的 LlmModelUsageRecord）。"""

from __future__ import annotations

from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine

from backend.app.models import LlmUsageRecord
from backend.app.services import ai
from backend.app.services.llm_usage import usage_summary


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_record_usage_forwards_token_counts_to_injected_recorder():
    """ai._record_usage 把一次成功调用的 token 用量交给注入的回调；不注入时静默。"""
    captured: list[dict] = []
    ai.set_usage_recorder(captured.append)
    try:
        resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=120, completion_tokens=40, total_tokens=160))
        ai._record_usage(resp, "主力卡", "qwen-max", "decision")
    finally:
        ai.set_usage_recorder(None)

    assert len(captured) == 1
    assert captured[0] == {
        "provider_label": "主力卡",
        "model": "qwen-max",
        "purpose": "decision",
        "prompt_tokens": 120,
        "completion_tokens": 40,
        "total_tokens": 160,
    }

    # 未注入回调时不抛、不记（行为与加它之前一致）。
    ai._record_usage(resp, "主力卡", "qwen-max", "decision")


def test_record_usage_never_raises_when_recorder_fails():
    """记账是旁路：回调抛异常也不能冒泡影响一次真正的模型调用。"""
    def _boom(_payload):
        raise RuntimeError("db locked")

    ai.set_usage_recorder(_boom)
    try:
        resp = SimpleNamespace(usage=None)
        ai._record_usage(resp, None, "m", "probe")  # 不应抛
    finally:
        ai.set_usage_recorder(None)


def test_usage_summary_aggregates_by_purpose():
    session = _session()
    session.add(LlmUsageRecord(model="m1", purpose="extract", prompt_tokens=100, completion_tokens=20, total_tokens=120))
    session.add(LlmUsageRecord(model="m1", purpose="extract", prompt_tokens=50, completion_tokens=10, total_tokens=60))
    session.add(LlmUsageRecord(model="m2", purpose="decision", prompt_tokens=200, completion_tokens=80, total_tokens=280))
    session.commit()

    summary = usage_summary(session)
    assert summary["calls"] == 3
    assert summary["prompt_tokens"] == 350
    assert summary["completion_tokens"] == 110
    assert summary["total_tokens"] == 460
    by_purpose = {row["purpose"]: row for row in summary["by_purpose"]}
    assert by_purpose["extract"]["calls"] == 2 and by_purpose["extract"]["total_tokens"] == 180
    assert by_purpose["decision"]["calls"] == 1 and by_purpose["decision"]["total_tokens"] == 280
