"""候选卡「写入看板」（CLAUDE.md 红线 §3.10）：

已入库（committed）的候选岗位可以在聊天里再点一次「写入看板」，把一行卡片插入
个人操作仓库看板的「收集箱」列；点之前不写一个字节，不建立独立的建议/审批实体，
直接复用候选卡自身的 `status` / `job_id` / `board_written` 字段。全程不联网。
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime
from pathlib import Path

import httpx


# ==================== 仿真实 Obsidian Kanban 看板 fixture ====================

COLLECT_TEMPLATE_LINE = (
    "- [ ] 新岗位线索 - 薪资未知 - 渠道/日期 - 未判断 - 下一步：补齐主行并新建详情卡 - "
    "[模板](job-pipeline/_template.md)"
)

BOARD_SETTINGS_TAIL = (
    "%% kanban:settings\n"
    "```\n"
    '{"kanban-plugin":"board"}\n'
    "```\n"
    "%%\n"
)

BOARD_BEFORE = (
    "---\nkanban-plugin: board\n---\n\n"
    "## 收集箱\n\n"
    f"{COLLECT_TEMPLATE_LINE}\n\n"
    "## 待沟通\n\n"
    "- [ ] 示例卡片 - 占位\n\n"
    "## 使用规则\n\n"
    "状态变更只拖动看板卡片。\n"
    f"{BOARD_SETTINGS_TAIL}"
)

# 缺「收集箱」列的看板（用于 d 场景：段落缺失应导致单条写入失败）。
BOARD_WITHOUT_COLLECT_SECTION = (
    "---\nkanban-plugin: board\n---\n\n"
    "## 待沟通\n\n"
    "- [ ] 示例卡片 - 占位\n\n"
    "## 使用规则\n\n"
    "状态变更只拖动看板卡片。\n"
    f"{BOARD_SETTINGS_TAIL}"
)


def _write_context_fixture(root: Path, board_content: str = BOARD_BEFORE) -> None:
    (root / "toolkit/job-pipeline/cards").mkdir(parents=True)
    (root / "README.md").write_text("# Entry\n", encoding="utf-8")
    (root / "toolkit/24-job-search-decision-rules.md").write_text(
        "# Rules\n\n> Updated: 2026-07-19\n", encoding="utf-8"
    )
    (root / "toolkit/job-pipeline/PROFILE.md").write_text("# Profile\n", encoding="utf-8")
    (root / "toolkit/23-job-pipeline.md").write_text(board_content, encoding="utf-8")


def _board_path(root: Path) -> Path:
    return root / "toolkit" / "23-job-pipeline.md"


def _expected_inbox_line(company: str, title: str, salary: str, source: str = "manual") -> str:
    date_tag = datetime.now().strftime("%m%d")
    return f"- [ ] {company} - {title} - {salary} - {source}/{date_tag} - 未判断 - 下一步：补齐主行并新建详情卡"


def _fresh_app(monkeypatch, tmp_path, name: str):
    monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", f"sqlite:///{tmp_path / name}")
    from backend.app import config

    config.get_settings.cache_clear()
    import backend.app.db as db
    import backend.app.main as main

    db = importlib.reload(db)
    main = importlib.reload(main)
    db.init_db()
    return main


async def _client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


def _patch_freeform(monkeypatch, title: str, company: str, salary: str = "8-12K"):
    from backend.app.services import ai

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None: [{"title": title, "company_name": company, "salary_text": salary}],
    )


async def _ingest(client, title: str, company: str, salary: str) -> tuple[int, int]:
    ingested = (await client.post("/api/ingest", json={"text": f"招聘 {title} {company} {salary}"})).json()
    return ingested["thread"]["id"], ingested["assistant_message"]["id"]


def _setup(monkeypatch, tmp_path, name: str, *, board_content: str | None = BOARD_BEFORE):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    context_root = tmp_path / "personal-context"
    if board_content is not None:
        _write_context_fixture(context_root, board_content)
        monkeypatch.setenv("JOB_ONE_STOP_CONTEXT_REPO_PATH", str(context_root))
    main = _fresh_app(monkeypatch, tmp_path, name)
    return main, context_root


# ==================== a. commit -> board-write -> 收集箱多出一行，其余逐字节不变 ====================


def test_board_write_inserts_line_after_template_and_marks_board_written(monkeypatch, tmp_path):
    main, context_root = _setup(monkeypatch, tmp_path, "wb-write.sqlite3")
    _patch_freeform(monkeypatch, "后端工程师", "示例科技二部", "20-30K")
    board_path = _board_path(context_root)

    async def scenario():
        async for client in _client(main.app):
            thread_id, assistant_id = await _ingest(client, "后端工程师", "示例科技二部", "20-30K")
            commit = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert commit.status_code == 200, commit.text
            assert commit.json()["created"] == 1

            # 点「写入看板」前，看板必须原样未动。
            assert board_path.read_text(encoding="utf-8") == BOARD_BEFORE

            written = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/board-write",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert written.status_code == 200, written.text
            body = written.json()
            assert body["results"] == [{"index": 0, "ok": True, "reason": "已写入看板"}]
            candidate = body["assistant_message"]["metadata_json"]["candidates"][0]
            assert candidate["board_written"] is True
            assert str(context_root) not in written.text

            expected_line = _expected_inbox_line("示例科技二部", "后端工程师", "20-30K")
            expected_after = BOARD_BEFORE.replace(
                f"{COLLECT_TEMPLATE_LINE}\n\n",
                f"{COLLECT_TEMPLATE_LINE}\n{expected_line}\n\n",
            )
            assert board_path.read_text(encoding="utf-8") == expected_after

    asyncio.run(scenario())


# ==================== b. 重复 board-write 同一候选 -> 跳过不重复写 ====================


def test_board_write_is_idempotent_and_does_not_duplicate_line(monkeypatch, tmp_path):
    main, context_root = _setup(monkeypatch, tmp_path, "wb-idempotent.sqlite3")
    _patch_freeform(monkeypatch, "前端工程师", "示例科技三部", "15-25K")
    board_path = _board_path(context_root)

    async def scenario():
        async for client in _client(main.app):
            thread_id, assistant_id = await _ingest(client, "前端工程师", "示例科技三部", "15-25K")
            await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            first = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/board-write",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert first.status_code == 200, first.text
            after_first = board_path.read_text(encoding="utf-8")

            second = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/board-write",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert second.status_code == 200, second.text
            result = second.json()["results"][0]
            assert result["ok"] is True
            assert result.get("skipped") is True

            # 没有二次写入：文件与第一次写入后完全一致。
            assert board_path.read_text(encoding="utf-8") == after_first

    asyncio.run(scenario())


# ==================== c. 未 commit 的候选 -> 该条拒绝，不写入 ====================


def test_board_write_rejects_uncommitted_candidate(monkeypatch, tmp_path):
    main, context_root = _setup(monkeypatch, tmp_path, "wb-uncommitted.sqlite3")
    _patch_freeform(monkeypatch, "运营专员", "示例科技五部", "9-13K")
    board_path = _board_path(context_root)

    async def scenario():
        async for client in _client(main.app):
            thread_id, assistant_id = await _ingest(client, "运营专员", "示例科技五部", "9-13K")
            # 全部跳过，candidate 保持 pending，从未 commit。
            await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": []},
            )

            written = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/board-write",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert written.status_code == 200, written.text
            result = written.json()["results"][0]
            assert result["ok"] is False
            assert "入库" in result["reason"]

            assert board_path.read_text(encoding="utf-8") == BOARD_BEFORE

    asyncio.run(scenario())


# ==================== d1. 未配置上下文仓库 -> 503 ====================


def test_board_write_returns_503_when_context_repo_not_configured(monkeypatch, tmp_path):
    main, _context_root = _setup(monkeypatch, tmp_path, "wb-unconfigured.sqlite3", board_content=None)
    _patch_freeform(monkeypatch, "供应链专员", "示例科技六部", "10-16K")

    async def scenario():
        async for client in _client(main.app):
            thread_id, assistant_id = await _ingest(client, "供应链专员", "示例科技六部", "10-16K")
            await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            written = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/board-write",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert written.status_code == 503, written.text
            assert written.json()["error"]["message"]

    asyncio.run(scenario())


# ==================== d2. 看板缺「收集箱」段 -> 该条失败且文件不变 ====================


def test_board_write_fails_item_when_collect_section_missing(monkeypatch, tmp_path):
    main, context_root = _setup(
        monkeypatch, tmp_path, "wb-missing-section.sqlite3", board_content=BOARD_WITHOUT_COLLECT_SECTION
    )
    _patch_freeform(monkeypatch, "数据分析师", "示例科技四部", "18-28K")
    board_path = _board_path(context_root)

    async def scenario():
        async for client in _client(main.app):
            thread_id, assistant_id = await _ingest(client, "数据分析师", "示例科技四部", "18-28K")
            await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            written = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/board-write",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert written.status_code == 200, written.text  # 请求整体成功，单条标记失败
            result = written.json()["results"][0]
            assert result["ok"] is False
            assert "收集箱" in result["reason"]

            assert board_path.read_text(encoding="utf-8") == BOARD_WITHOUT_COLLECT_SECTION

    asyncio.run(scenario())


# ==================== e. 响应与消息 metadata 中无宿主机绝对路径 ====================


def test_board_write_response_never_leaks_host_path(monkeypatch, tmp_path):
    main, context_root = _setup(monkeypatch, tmp_path, "wb-no-leak.sqlite3")
    _patch_freeform(monkeypatch, "供应链专员", "示例科技六部", "10-16K")

    async def scenario():
        async for client in _client(main.app):
            thread_id, assistant_id = await _ingest(client, "供应链专员", "示例科技六部", "10-16K")
            await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            written = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/board-write",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert str(context_root) not in written.text

            detail = await client.get(f"/api/chat/threads/{thread_id}")
            assert str(context_root) not in detail.text

    asyncio.run(scenario())
