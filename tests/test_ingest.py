"""ingest：只抽候选写聊天；用户 commit 才入库。全程不联网。"""

from __future__ import annotations

import asyncio
import base64
import importlib

import httpx

from backend.app.services import bebee, ingest, telegram, wechat
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
