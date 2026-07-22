"""ingest：只抽候选写聊天；用户 commit 才入库。全程不联网。"""

from __future__ import annotations

import asyncio
import base64
import importlib

import httpx

from backend.app.services import ai, bebee, collectors, importer, ingest, telegram, wechat
from backend.app.services.wechat import ArticleFetch


def test_extract_bebee_links_dedupes_and_strips_punctuation():
    blob = "看看 https://bebee.com/cn/job/abc123，还有 https://www.bebee.com/cn/job/def456。以及重复的 https://bebee.com/cn/job/abc123"
    links = bebee.extract_bebee_links(blob)
    assert links == ["https://bebee.com/cn/job/abc123", "https://www.bebee.com/cn/job/def456"]


def test_classify_links_splits_by_source():
    blob = (
        "微信文 https://mp.weixin.qq.com/s/AbC123 "
        "beBee https://bebee.com/cn/job/xyz "
        "无关 https://example.com/whatever"
    )
    classified = ingest.classify_links(blob)
    assert len(classified["wechat"]) == 1
    assert len(classified["bebee"]) == 1
    assert all("example.com" not in link for links in classified.values() for link in links)


def _fresh_modules(monkeypatch, tmp_path, name):
    monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", f"sqlite:///{tmp_path / name}")
    from backend.app import config

    config.get_settings.cache_clear()
    import backend.app.db as db
    import backend.app.main as main

    db = importlib.reload(db)
    main = importlib.reload(main)
    db.init_db()
    return db, main


async def _client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


# ==================== run_ingest：只返回候选，不写库 ====================


def test_run_ingest_returns_candidates_without_session():
    """run_ingest 不再需要 session，也不落库。"""
    body = "岗位：SEO专员\n公司：示例科技\n薪资：8-12K\n工作地点：示例市"

    def fake_fetch(target, cfg=None):
        return ArticleFetch(url=target, ok=True, og_title="示例科技招聘", body_text=body)

    # monkeypatch via wechat module used by collectors
    import backend.app.services.wechat as wechat_mod

    original = wechat_mod.fetch_article
    wechat_mod.fetch_article = fake_fetch
    try:
        result = ingest.run_ingest(
            "投递看看 https://mp.weixin.qq.com/s/AbC123",
            wechat_cfg={"source_label": "公众号", "fetch": {}},
            bebee_cfg={"source_label": "beBee"},
            ai_enabled=False,
        )
    finally:
        wechat_mod.fetch_article = original

    assert result["unmatched"] is False
    assert result["candidate_count"] >= 1
    assert all(c.get("status") == "pending" for c in result["candidates"])
    assert "created" not in result


def test_run_ingest_unmatched_when_no_known_links():
    result = ingest.run_ingest(
        "只是随便聊聊 https://example.com/foo",
        wechat_cfg={"source_label": "公众号", "fetch": {}},
        bebee_cfg={"source_label": "beBee"},
        ai_enabled=False,
    )
    assert result["unmatched"] is True
    assert result["candidate_count"] == 0
    assert result["needs_ai"] is True  # 有残余文本但 AI 关


def test_run_ingest_freeform_when_ai_enabled(monkeypatch):
    from backend.app.services import ai

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None: [
            {"title": "BOSS 外贸运营", "company_name": "示例公司", "salary_text": "9-13K", "city": "示例市"}
        ],
    )
    result = ingest.run_ingest(
        "【BOSS直聘】外贸运营 9-13K 示例市",
        wechat_cfg={"source_label": "公众号", "fetch": {}},
        bebee_cfg={"source_label": "beBee"},
        ai_enabled=True,
        manual_source="manual",
    )
    assert result["candidate_count"] >= 1
    assert result["candidates"][0]["source"] == "manual"
    assert result["candidates"][0]["status"] == "pending"


# ==================== HTTP：写聊天不写 Job；commit 才入库 ====================


def test_ingest_endpoint_writes_chat_not_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-chat.sqlite3")
    from backend.app.models import ChatMessage, ChatThread, Job
    from backend.app.services import ai
    from sqlmodel import select

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None: [{"title": "截图岗位", "company_name": "X", "url": None}],
    )

    async def scenario():
        async for client in _client(main.app):
            resp = await client.post(
                "/api/ingest",
                json={"image_data_url": "data:image/png;base64,iVBORw0KGgo="},
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["candidate_count"] >= 1
            assert payload["thread"]["kind"] == "ingest"
            thread_id = payload["thread"]["id"]
            assistant_id = payload["assistant_message"]["id"]

            with db.Session(db.engine) as session:
                assert session.exec(select(Job)).first() is None  # 未入库
                assert session.get(ChatThread, thread_id) is not None
                msg = session.get(ChatMessage, assistant_id)
                assert msg is not None
                assert (msg.metadata_json or {}).get("candidates")

            # commit 第 0 个候选
            commit = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert commit.status_code == 200, commit.text
            body = commit.json()
            assert body["created"] + body["updated"] >= 1
            with db.Session(db.engine) as session:
                assert session.exec(select(Job)).first() is not None
                msg = session.get(ChatMessage, assistant_id)
                cand = (msg.metadata_json or {}).get("candidates")[0]
                assert cand["status"] == "committed"
                assert cand.get("job_id")

    asyncio.run(scenario())


def test_commit_empty_indexes_skips_all(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-skip-all.sqlite3")
    from backend.app.models import Job
    from backend.app.services import ai
    from sqlmodel import select

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None: [{"title": "跳过岗位", "company_name": "Y"}],
    )

    async def scenario():
        async for client in _client(main.app):
            payload = (
                await client.post("/api/ingest", json={"text": "一段 JD 文本足够长"})
            ).json()
            thread_id = payload["thread"]["id"]
            assistant_id = payload["assistant_message"]["id"]
            commit = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": []},
            )
            assert commit.status_code == 200, commit.text
            assert commit.json()["created"] == 0
            with db.Session(db.engine) as session:
                assert session.exec(select(Job)).first() is None

    asyncio.run(scenario())


# ==================== Telegram ====================


def test_extract_message_picks_largest_photo_and_caption():
    update = {
        "update_id": 8,
        "message": {
            "chat": {"id": 42},
            "caption": "BOSS 截图",
            "photo": [
                {"file_id": "small", "width": 90, "height": 90},
                {"file_id": "largest", "width": 800, "height": 800},
            ],
        },
    }
    chat_id, text, photo = telegram.extract_message(update)
    assert chat_id == 42
    assert text == "BOSS 截图"
    assert photo == "largest"


def test_summarize_ingest_pending_not_committed():
    msg = telegram.summarize_ingest({"candidate_count": 2, "unmatched": False})
    assert "候选" in msg
    assert "未入库" in msg or "确认" in msg


def test_poll_loop_persists_chat_not_jobs(monkeypatch, tmp_path):
    _db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-poll.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    batches = [
        [
            {"update_id": 10, "message": {"chat": {"id": 999}, "text": "attacker"}},
            {"update_id": 11, "message": {"chat": {"id": 42}, "text": "owner text"}},
        ],
        [],
    ]
    calls = {"sent": [], "persisted": []}

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    def fake_send(token, chat_id, text):
        calls["sent"].append((chat_id, text))

    def fake_persist(session, text, image_data_url=None):
        calls["persisted"].append((text, image_data_url))
        return {"candidate_count": 1, "unmatched": False, "needs_ai": False}

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", fake_send)
    monkeypatch.setattr(main, "_persist_ingest_to_chat", fake_persist)
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    async def run_once():
        try:
            await main._telegram_poll_loop()
        except KeyboardInterrupt:
            pass

    asyncio.run(run_once())
    assert calls["persisted"] == [("owner text", None)]
    assert len(calls["sent"]) == 1
    assert calls["sent"][0][0] == 42


def test_download_photo_data_url_builds_data_url(monkeypatch):
    import httpx

    calls = {"urls": []}

    def fake_get(self, url, params=None):
        calls["urls"].append(url)
        if "getFile" in url:
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_path": "photos/file_1.jpg"}},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(200, content=b"\xff\xd8\xff", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    data_url = telegram.download_photo_data_url("tok", "fid")
    assert data_url == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff").decode()
    assert any("getFile" in u for u in calls["urls"])


# ==================== 路径自检 ====================


def test_wsl_path_conversion_variants(monkeypatch):
    from backend.app import config

    monkeypatch.setattr(config.os, "name", "posix", raising=False)
    assert config._to_wsl_path("D:\\006-Overseas") == "/mnt/d/006-Overseas"
    assert config._to_wsl_path("mnt/d/006-Overseas") == "/mnt/d/006-Overseas"
    assert config._to_wsl_path("/mnt/d/already") == "/mnt/d/already"


def test_env_absolute_path_error_mentions_os(monkeypatch):
    from backend.app import config
    import pytest

    monkeypatch.setattr(config.os, "name", "posix", raising=False)
    monkeypatch.setenv("JOB_ONE_STOP_CONTEXT_REPO_PATH", "relative/not/absolute")
    with pytest.raises(config.ConfigError) as exc:
        config._env_absolute_path("JOB_ONE_STOP_CONTEXT_REPO_PATH")
    assert "WSL" in str(exc.value) or "posix" in str(exc.value).lower()


# ==================== 配置回环与 chat id 容错 ====================


def test_config_roundtrip_keeps_telegram_section(monkeypatch, tmp_path):
    """GET /api/config → PUT 回环必须成功：config.yaml 自带 telegram 段，
    白名单漏掉它会让 Web 设置保存与系统冒烟同时 400。"""
    import shutil
    from pathlib import Path

    cfg = tmp_path / "config.yaml"
    shutil.copy(Path(__file__).resolve().parents[1] / "config.yaml", cfg)
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(cfg))
    _db, main = _fresh_modules(monkeypatch, tmp_path, "config-roundtrip.sqlite3")

    async def scenario():
        async for client in _client(main.app):
            got = (await client.get("/api/config")).json()
            assert "telegram" in got["config"]
            resp = await client.put("/api/config", json={"config": got["config"]})
            assert resp.status_code == 200, resp.text
            assert resp.json()["config"]["telegram"]["enabled"] is False

    asyncio.run(scenario())


def test_poll_loop_accepts_numeric_string_chat_id(monkeypatch, tmp_path):
    """config.yaml 默认把 allowed_chat_id 写成字符串；数字字符串必须照常生效，
    而不是静默不启动轮询（P0 真机联调最容易踩的坑）。"""
    _db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-strid.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    batches = [[{"update_id": 20, "message": {"chat": {"id": 42}, "text": "owner text"}}]]
    calls = {"sent": [], "persisted": []}

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", lambda token, chat_id, text: calls["sent"].append(chat_id))
    monkeypatch.setattr(
        main,
        "_persist_ingest_to_chat",
        lambda session, text, image_data_url=None: calls["persisted"].append(text)
        or {"candidate_count": 1, "unmatched": False, "needs_ai": False},
    )
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": "42", "poll_timeout_seconds": 1}},
    )

    async def run_once():
        try:
            await main._telegram_poll_loop()
        except KeyboardInterrupt:
            pass

    asyncio.run(run_once())
    assert calls["persisted"] == ["owner text"]
    assert calls["sent"] == [42]


def test_poll_loop_backs_off_exponentially_and_resets_after_success(monkeypatch, tmp_path):
    """断网时指数退避（5s → 10s → … 封顶 300s）；一次成功拉取后失败计数清零，
    下次失败应重新从 5s 起，不能一直停留在封顶值。"""
    _db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-backoff.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    # 前 7 次失败触发退避封顶；第 8 次成功（空列表）清零计数；第 9 次再失败一次
    # 验证退避重新从 5s 起；随后 call_plan 耗尽抛 KeyboardInterrupt 跳出无限循环。
    call_plan = ["fail"] * 7 + ["ok", "fail"]

    def fake_get_updates(token, offset, timeout):
        if not call_plan:
            raise KeyboardInterrupt
        step = call_plan.pop(0)
        if step == "fail":
            raise RuntimeError("network down")
        return []

    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    async def run_once():
        try:
            await main._telegram_poll_loop()
        except KeyboardInterrupt:
            pass

    asyncio.run(run_once())

    assert sleeps == [5, 10, 20, 40, 80, 160, 300, 5]


# ==================== 红线绊线：入库只能发生在用户确认的 commit ====================


def _imported_names(module) -> set[str]:
    """收集模块源码里 import 的模块名与符号名（AST 级，不受注释/文档字符串干扰）。"""
    import ast
    import inspect

    names: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
    return names


def test_ingest_and_telegram_modules_never_import_importer():
    """红线绊线（CLAUDE.md §2/§6）：ingest 只产候选，telegram 只做传输。

    任何人往这两个模块里塞回 importer/upsert（= ingest 自动入库），这里立即翻红；
    改动前必须先改产品决策并更新 CLAUDE.md。
    """
    for module in (ingest, telegram):
        imported = _imported_names(module)
        assert not any("importer" in name for name in imported), f"{module.__name__} 不得引用 importer"
        assert not any("upsert" in name.lower() for name in imported), f"{module.__name__} 不得引用 upsert_*"


def test_persist_ingest_and_poll_loop_write_chat_only():
    """红线绊线：HTTP 与 Telegram 共用的落盘函数只写聊天，不碰 Job 表。"""
    import inspect

    import backend.app.main as main

    for func in (main._persist_ingest_to_chat, main._telegram_poll_loop):
        source = inspect.getsource(func)
        assert "upsert" not in source, f"{func.__name__} 不得出现 upsert 调用"
        assert "Job(" not in source, f"{func.__name__} 不得直接构造 Job"


# ==================== 红线绊线：写回看板只能走 ContextWriter，且只能由确认后的按钮触发 ====================


def test_pipeline_modules_never_reference_context_writer():
    """红线绊线（CLAUDE.md §3.10）：采集/管线模块不得引用写回能力。

    只有 board_write.py（经 main.py 的 board_write_candidates 端点）能触发写入，
    任何人往采集/解析/传输模块里塞 ContextWriter 或 insert_line_in_section，这里立即翻红。
    """
    import inspect

    for module in (ingest, telegram, collectors, importer, wechat, bebee, ai):
        imported = _imported_names(module)
        assert "ContextWriter" not in imported, f"{module.__name__} 不得 import ContextWriter"
        source = inspect.getsource(module)
        assert "ContextWriter" not in source, f"{module.__name__} 不得引用 ContextWriter"
        assert "insert_line_in_section" not in source, f"{module.__name__} 不得调用看板写入方法"


def test_context_writer_reference_allowlist():
    """`ContextWriter` 的引用只允许出现在 context_repository.py（自身）、board_write.py、
    main.py 与测试；任何其它源码文件引用它都说明写入口被绕过了白名单。"""
    import pathlib

    backend_app = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app"
    allowed = {
        backend_app / "services" / "context_repository.py",
        backend_app / "services" / "board_write.py",
        backend_app / "main.py",
    }
    offenders = []
    for path in backend_app.rglob("*.py"):
        if path in allowed or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "ContextWriter" in text:
            offenders.append(str(path))
    assert not offenders, f"以下文件不应引用 ContextWriter：{offenders}"
