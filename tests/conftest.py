"""全局测试夹具。

两个职责：

1. **禁止写入真实个人上下文仓库**（`block_context_repo_writes`）。见该夹具的 docstring：
   曾有用例真的把测试候选写进了本人看板。注意刻意**没有**采用"隔离整个 .env"的做法——
   那会连带掐掉测试所依赖的 `ai.providers` 密钥，实测引发 25 个用例误伤。

2. **兜住决策分析的模型调用**（`decision_llm_calls`，候选建议 `services/advice.py` 与问答落盘
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
def block_context_repo_writes(monkeypatch):
    """兜底：禁止测试把任何字节写进**真实**个人上下文仓库。

    `get_settings()` 每次都 `load_dotenv(PROJECT_DIR/".env")`，而开发机 `.env` 里的
    `JOB_ONE_STOP_CONTEXT_REPO_PATH` 指向真实个人仓库。用例即使 `delenv` 也拦不住——
    删掉的变量马上被 .env 重新填上。后果不是"断言挂了"这么轻：`ContextWriter` 拿到真
    路径后，真的把测试候选写进了本人看板的收集箱列（实测污染 9 行）。

    这里在最外层拦住写入本身：写入目标只要落在 `.env` 配置的真实仓库内就直接抛错，
    用例自己 `setenv` 到 tmp_path 的合法写入不受影响。比"隔离整个 .env"安全得多——
    后者会连带掐掉测试所依赖的 `ai.providers` 密钥，引发大面积误伤。
    """
    import os

    from backend.app.services import context_repository

    real_root = (os.getenv("JOB_ONE_STOP_CONTEXT_REPO_PATH") or "").strip()
    if not real_root:
        return
    from pathlib import Path

    try:
        guarded = Path(context_repository._to_wsl_path(real_root) if hasattr(context_repository, "_to_wsl_path") else real_root).resolve()
    except (OSError, ValueError):
        return

    original = context_repository.ContextWriter.insert_line_in_section

    def guarded_insert(self, key: str, section_heading: str, line: str):
        if self.root is not None and (self.root == guarded or guarded in self.root.parents or self.root in guarded.parents):
            raise AssertionError(
                f"测试试图写入真实个人上下文仓库（{key} @ {self.root}）。"
                "用例必须把 JOB_ONE_STOP_CONTEXT_REPO_PATH 指向 tmp_path，"
                "或（表达『未配置』时）置为空字符串——delenv 会被 .env 重新填上。"
            )
        return original(self, key, section_heading, line)

    monkeypatch.setattr(context_repository.ContextWriter, "insert_line_in_section", guarded_insert)


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
