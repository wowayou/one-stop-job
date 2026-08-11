"""全局测试夹具。

唯一职责：兜住决策分析的模型调用（候选建议 `services/advice.py` 与问答落盘
`services/decision_reply.py`）。这两处都挂在主流程后面，一旦某个用例设了 `OPENAI_API_KEY`
又没桩掉，就会真的去连模型服务商——违反 CLAUDE.md §4「测试不得联网」，表现是用例慢到离谱
甚至挂住（provider 不通还要走重试+退避）。

默认返回 None = 「模型不可用，按规则引擎的确定性结论走」，这是产品里本来就存在的降级路径，
不改变任何被测行为。要断言「模型确实被调用/被跳过」的用例，用 `decision_llm_calls` 夹具看
调用记录，或自己 `monkeypatch.setattr` 覆盖掉对应模块里的这个桩（在夹具之后生效）。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def decision_llm_calls(monkeypatch):
    """桩掉建议/问答里的模型调用，返回记录调用参数的列表（默认零联网、走规则降级）。"""
    from backend.app.services import advice, decision_reply

    calls: list[dict] = []

    def fake_analyze(**kwargs):
        calls.append(kwargs)
        return None

    for module in (advice, decision_reply):
        monkeypatch.setattr(module, "analyze_decision_chat_llm", fake_analyze)
    return calls
