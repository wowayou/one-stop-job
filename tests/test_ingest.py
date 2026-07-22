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


# ==================== 修复 1：AI 抽取异常不得被静默吞成「未识别」 ====================


def test_run_ingest_ai_exception_surfaces_reason_without_leaking_key(monkeypatch):
    """视觉/文本抽取抛异常时，run_ingest 必须把可读原因带出来，且不泄露密钥。"""
    from backend.app.services import ai

    def boom(text, image_data_url=None):
        raise RuntimeError(
            "model does not support image input; Authorization: Bearer sk-liveTESTKEY1234567890 rejected"
        )

    monkeypatch.setattr(ai, "extract_jobs_freeform", boom)

    result = ingest.run_ingest(
        "这是一段没有可识别链接的招聘正文，请帮忙识别岗位。",
        wechat_cfg={"source_label": "公众号", "fetch": {}},
        bebee_cfg={"source_label": "beBee"},
        ai_enabled=True,
    )

    assert result["candidate_count"] == 0
    assert result["ai_error"]
    assert "RuntimeError" in result["ai_error"]
    assert "sk-liveTESTKEY1234567890" not in result["ai_error"]
    assert "Bearer [key]" in result["ai_error"] or "[key]" in result["ai_error"]
    assert any("AI 抽取失败" in (item.get("error") or "") for item in result["sources_report"])


def test_run_ingest_ai_disabled_has_no_ai_error():
    """AI 未启用时是 needs_ai，不是 ai_error——两种「没候选」的原因不能混。"""
    result = ingest.run_ingest(
        "只是随便聊聊 https://example.com/foo",
        wechat_cfg={"source_label": "公众号", "fetch": {}},
        bebee_cfg={"source_label": "beBee"},
        ai_enabled=False,
    )
    assert result["ai_error"] is None
    assert result["needs_ai"] is True


def test_run_ingest_ai_enabled_zero_candidates_has_no_ai_error(monkeypatch):
    """AI 正常调用但没认出岗位：ai_error 必须是 None，不能被误判为调用失败。"""
    from backend.app.services import ai

    monkeypatch.setattr(ai, "extract_jobs_freeform", lambda text, image_data_url=None: [])
    result = ingest.run_ingest(
        "一段随便的文本，认不出岗位。",
        wechat_cfg={"source_label": "公众号", "fetch": {}},
        bebee_cfg={"source_label": "beBee"},
        ai_enabled=True,
    )
    assert result["ai_error"] is None
    assert result["candidate_count"] == 0


# ==================== 修复 2：BOSS/智联链接给出针对性提示 ====================


def test_run_ingest_flags_known_uncrawlable_link_hint():
    result = ingest.run_ingest(
        "内推一个岗位 https://www.zhipin.com/job_detail/abc123.html 感兴趣的看看",
        wechat_cfg={"source_label": "公众号", "fetch": {}},
        bebee_cfg={"source_label": "beBee"},
        ai_enabled=False,
    )
    assert result["known_uncrawlable_hint"] is True
    assert any("zhipin.com" in link for link in result["known_uncrawlable_links"])


def test_run_ingest_zhipin_link_with_jd_text_still_extracts_normally(monkeypatch):
    """zhipin 链接 + JD 文本同发：AI 抽取正常进行，不受链接识别影响。"""
    from backend.app.services import ai

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None: [
            {"title": "资深BI工程师", "company_name": "示例科技", "salary_text": "20-30K", "city": "上海"}
        ],
    )
    result = ingest.run_ingest(
        "岗位详情 https://www.zhipin.com/job_detail/abc123.html\n"
        "资深BI工程师 20-30K 上海 负责数据看板建设",
        wechat_cfg={"source_label": "公众号", "fetch": {}},
        bebee_cfg={"source_label": "beBee"},
        ai_enabled=True,
        manual_source="manual",
    )
    assert result["candidate_count"] >= 1
    assert result["candidates"][0]["title"] == "资深BI工程师"
    # 设计取舍：提示是否出现只看这一轮有没有 wechat/bebee 专用链接，与 AI 是否认出其它岗位无关——
    # 即便正文里的 JD 被 AI 认出，zhipin 链接本身依旧抓不到，提示仍然有意义，因此这里应为 True。
    assert result["known_uncrawlable_hint"] is True


def test_classify_links_recognizes_zhipin_and_zhaopin():
    classified = ingest.classify_links(
        "https://www.zhipin.com/job_detail/abc.html 和 https://www.zhaopin.com/job/xyz.html"
    )
    assert len(classified["known_uncrawlable"]) == 2


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


def test_ingest_endpoint_surfaces_ai_failure_reason_distinct_from_unmatched(monkeypatch, tmp_path):
    """修复 1：AI 已配置但调用失败时，聊天消息必须明确说「AI 抽取失败」，不能和「未认出」混在一起，
    也不能泄露密钥或裸 traceback；AI 正常返回 0 候选时仍是原来的「未认出」文案。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-liveTESTKEY1234567890")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-ai-fail-http.sqlite3")
    from backend.app.services import ai

    def boom(text, image_data_url=None):
        raise RuntimeError("vision model rejected request (key=sk-liveTESTKEY1234567890)")

    monkeypatch.setattr(ai, "extract_jobs_freeform", boom)

    async def scenario():
        async for client in _client(main.app):
            resp = await client.post(
                "/api/ingest",
                json={"image_data_url": "data:image/png;base64,iVBORw0KGgo="},
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            content = payload["assistant_message"]["content"]
            assert "AI 抽取失败" in content
            assert "sk-liveTESTKEY1234567890" not in content
            assert "Traceback" not in content
            assert payload["assistant_message"]["metadata_json"]["ai_error"]
            assert payload["candidate_count"] == 0

    asyncio.run(scenario())


def test_ingest_endpoint_unmatched_text_still_says_not_recognized(monkeypatch, tmp_path):
    """AI 正常调用（未抛异常）但没识别出岗位时，回执必须仍是原来的「未认出」文案，不能被误判成 AI 失败。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-unmatched-http.sqlite3")
    from backend.app.services import ai

    monkeypatch.setattr(ai, "extract_jobs_freeform", lambda text, image_data_url=None: [])

    async def scenario():
        async for client in _client(main.app):
            resp = await client.post("/api/ingest", json={"text": "随便聊聊，没有岗位信息"})
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            content = payload["assistant_message"]["content"]
            assert "未从链接、文本或截图中认出岗位" in content
            assert "AI 抽取失败" not in content

    asyncio.run(scenario())


def test_ingest_endpoint_zhipin_link_hint_appended(monkeypatch, tmp_path):
    """修复 2：只发 zhipin 链接时，回执要额外提示改发文本/截图。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-zhipin-http.sqlite3")

    async def scenario():
        async for client in _client(main.app):
            resp = await client.post(
                "/api/ingest",
                json={"text": "帮忙看看这个 https://www.zhipin.com/job_detail/abc123.html"},
            )
            assert resp.status_code == 200, resp.text
            content = resp.json()["assistant_message"]["content"]
            assert "BOSS/智联" in content
            assert "风控" in content

    asyncio.run(scenario())


def test_ingest_endpoint_flags_existing_job_and_commit_reuses_job(monkeypatch, tmp_path):
    """修复 3：候选命中岗位池里已有岗位时打 existing_job_id；勾选提交只应合并，不新建 Job。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-existing-job.sqlite3")
    from backend.app.services import ai
    from backend.app.models import Job
    from sqlmodel import select

    async def scenario():
        async for client in _client(main.app):
            created = await client.post(
                "/api/jobs",
                json={"title": "资深BI工程师", "company_name": "示例科技", "city": "上海"},
            )
            assert created.status_code == 200, created.text
            existing_job_id = created.json()["id"]

            monkeypatch.setattr(
                ai,
                "extract_jobs_freeform",
                lambda text, image_data_url=None: [
                    {"title": "资深BI工程师", "company_name": "示例科技", "city": "上海", "salary_text": "25-35K"}
                ],
            )
            resp = await client.post("/api/ingest", json={"text": "看看这个岗位靠不靠谱"})
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["candidate_count"] >= 1
            candidate = payload["assistant_message"]["metadata_json"]["candidates"][0]
            assert candidate["existing_job_id"] == existing_job_id

            with db.Session(db.engine) as session:
                assert len(session.exec(select(Job)).all()) == 1

            thread_id = payload["thread"]["id"]
            assistant_id = payload["assistant_message"]["id"]
            commit = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert commit.status_code == 200, commit.text
            body = commit.json()
            assert body["created"] == 0
            assert body["updated"] >= 1

            with db.Session(db.engine) as session:
                jobs = session.exec(select(Job)).all()
                assert len(jobs) == 1
                assert jobs[0].id == existing_job_id

    asyncio.run(scenario())


def test_ingest_full_duplicate_merges_into_existing_thread(monkeypatch, tmp_path):
    """同一条链接内容 ingest 两次(候选完全一致)：第二次不新建线程,直接并入第一次那条,
    回执明确说「已归入」，不是「已补充」。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-dup-merge.sqlite3")
    from backend.app.services import ai
    from backend.app.models import ChatThread
    from sqlmodel import select

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None: [
            {"title": "资深BI工程师", "company_name": "示例科技", "city": "上海", "salary_text": "25-35K"}
        ],
    )

    async def scenario():
        async for client in _client(main.app):
            first = await client.post("/api/ingest", json={"text": "看看这个岗位靠不靠谱"})
            assert first.status_code == 200, first.text
            first_thread_id = first.json()["thread"]["id"]

            second = await client.post("/api/ingest", json={"text": "同一个链接又发了一遍"})
            assert second.status_code == 200, second.text
            payload = second.json()
            assert payload["thread"]["id"] == first_thread_id
            assert payload["duplicate_merge"] is True
            assert "已归入" in payload["assistant_message"]["content"]

            with db.Session(db.engine) as session:
                threads = session.exec(select(ChatThread).where(ChatThread.kind == "ingest")).all()
                assert len(threads) == 1  # 没有新建线程

    asyncio.run(scenario())


def test_ingest_partial_duplicate_tags_candidate_and_creates_new_thread(monkeypatch, tmp_path):
    """一批候选里只有部分和近期线索重复：仍新建线程，重复项打 duplicate_in_thread_id
    标注供前端默认不勾选，回执追加「其中 N 个与近期候选重复」，原料不丢。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-dup-partial.sqlite3")
    from backend.app.services import ai

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None: [{"title": "资深BI工程师", "company_name": "示例科技", "city": "上海"}],
    )

    async def scenario():
        async for client in _client(main.app):
            first = await client.post("/api/ingest", json={"text": "第一条线索"})
            assert first.status_code == 200, first.text
            first_thread_id = first.json()["thread"]["id"]

            monkeypatch.setattr(
                ai,
                "extract_jobs_freeform",
                lambda text, image_data_url=None: [
                    {"title": "资深BI工程师", "company_name": "示例科技", "city": "上海"},
                    {"title": "前端工程师", "company_name": "另一家公司", "city": "北京"},
                ],
            )
            second = await client.post("/api/ingest", json={"text": "第二条线索，附带新岗位"})
            assert second.status_code == 200, second.text
            payload = second.json()
            assert payload["thread"]["id"] != first_thread_id  # 部分重复仍新建线程
            assert payload["duplicate_merge"] is False
            assert payload["duplicate_count"] == 1

            candidates = payload["assistant_message"]["metadata_json"]["candidates"]
            assert len(candidates) == 2
            dup = next(c for c in candidates if c["title"] == "资深BI工程师")
            fresh = next(c for c in candidates if c["title"] == "前端工程师")
            assert dup["duplicate_in_thread_id"] == first_thread_id
            assert fresh.get("duplicate_in_thread_id") is None
            assert "其中 1 个与近期候选重复" in payload["assistant_message"]["content"]

    asyncio.run(scenario())


def test_ingest_duplicate_badge_coexists_with_existing_job_id(monkeypatch, tmp_path):
    """同一候选既命中岗位池里已入库的 Job(existing_job_id)，又和近期未入库线索重复
    (duplicate_in_thread_id)：两个只读标注互不冲突，都应出现。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-dup-existing-combo.sqlite3")
    from backend.app.services import ai

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None: [{"title": "资深BI工程师", "company_name": "示例科技", "city": "上海"}],
    )

    async def scenario():
        async for client in _client(main.app):
            first = await client.post("/api/ingest", json={"text": "第一条线索"})
            first_payload = first.json()
            first_thread_id = first_payload["thread"]["id"]
            first_assistant_id = first_payload["assistant_message"]["id"]

            commit = await client.post(
                f"/api/chat/threads/{first_thread_id}/candidates/commit",
                json={"message_id": first_assistant_id, "indexes": [0]},
            )
            assert commit.status_code == 200, commit.text

            monkeypatch.setattr(
                ai,
                "extract_jobs_freeform",
                lambda text, image_data_url=None: [
                    {"title": "资深BI工程师", "company_name": "示例科技", "city": "上海"},
                    {"title": "前端工程师", "company_name": "另一家公司", "city": "北京"},
                ],
            )
            second = await client.post("/api/ingest", json={"text": "第二条线索，附带新岗位"})
            assert second.status_code == 200, second.text
            candidates = second.json()["assistant_message"]["metadata_json"]["candidates"]
            dup = next(c for c in candidates if c["title"] == "资深BI工程师")
            assert dup["existing_job_id"] is not None
            assert dup["duplicate_in_thread_id"] == first_thread_id

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
    extracted = telegram.extract_message(update)
    assert extracted.chat_id == 42
    assert extracted.text == "BOSS 截图"
    assert extracted.photo_file_id == "largest"


def test_summarize_ingest_pending_not_committed():
    msg = telegram.summarize_ingest({"candidate_count": 2, "unmatched": False})
    assert "候选" in msg
    assert "未入库" in msg or "确认" in msg


def test_summarize_ingest_ai_failure_distinct_from_other_states():
    """修复 1：三种「没有候选」的原因必须能区分——AI 失败 / AI 未启用 / 正常没认出。"""
    failed = telegram.summarize_ingest({"candidate_count": 0, "unmatched": True, "ai_error": "RuntimeError：连接超时"})
    assert "AI 抽取失败" in failed
    assert "RuntimeError" in failed

    disabled = telegram.summarize_ingest({"candidate_count": 0, "unmatched": True, "needs_ai": True})
    assert "未启用" in disabled
    assert "AI 抽取失败" not in disabled

    not_found = telegram.summarize_ingest({"candidate_count": 0, "unmatched": True})
    assert "未从链接、文本或截图中认出岗位" in not_found
    assert "AI 抽取失败" not in not_found
    assert "未启用" not in not_found


def test_summarize_ingest_appends_uncrawlable_hint():
    msg = telegram.summarize_ingest({"candidate_count": 0, "unmatched": True, "known_uncrawlable_hint": True})
    assert "BOSS/智联" in msg
    assert "风控" in msg


def test_summarize_ingest_reports_existing_job_count():
    msg = telegram.summarize_ingest(
        {
            "candidate_count": 2,
            "unmatched": False,
            "candidates": [{"existing_job_id": 7}, {"existing_job_id": None}],
        }
    )
    assert "已在岗位池" in msg
    assert "1" in msg


def test_poll_loop_start_command_sends_usage_and_skips_ingest(monkeypatch, tmp_path):
    """修复 5：/start 直接回使用说明，不建 ingest 线程。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-start.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    batches = [[{"update_id": 30, "message": {"chat": {"id": 42}, "text": "/start"}}]]
    calls = {"sent": [], "persisted": []}

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    def fail_persist(*args, **kwargs):
        calls["persisted"].append(True)
        raise AssertionError("/start 不应触发 ingest 落盘")

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", lambda token, chat_id, text: calls["sent"].append((chat_id, text)))
    monkeypatch.setattr(main, "_persist_ingest_to_chat", fail_persist)
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

    assert calls["persisted"] == []
    assert len(calls["sent"]) == 1
    assert calls["sent"][0][0] == 42
    reply_text = calls["sent"][0][1]
    assert "链接" in reply_text and "截图" in reply_text

    from backend.app.models import ChatThread
    from sqlmodel import select

    with db.Session(db.engine) as session:
        assert session.exec(select(ChatThread)).first() is None


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

    def fake_persist(session, text, image_data_url=None, target_thread_id=None):
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


# ==================== 修复：Telegram 补充材料关联 ====================
# 背景：同一岗位的补充材料（如通勤地图截图）会孤立成新线程；「以文件发送」的图片被静默忽略。


def test_send_message_returns_message_id(monkeypatch):
    import httpx

    def fake_post(self, url, json=None):
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 4321, "chat": {"id": 42}}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    assert telegram.send_message("tok", 42, "hello") == 4321


def test_send_message_returns_none_on_failure(monkeypatch):
    import httpx

    def fake_post(self, url, json=None):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    assert telegram.send_message("tok", 42, "hello") is None


def test_extract_message_picks_document_reply_and_media_group():
    update = {
        "update_id": 9,
        "message": {
            "message_id": 55,
            "chat": {"id": 42},
            "media_group_id": "grp-1",
            "reply_to_message": {"message_id": 30},
            "document": {"file_id": "doc-1", "mime_type": "image/png", "file_size": 2048},
        },
    }
    extracted = telegram.extract_message(update)
    assert extracted.document_file_id == "doc-1"
    assert extracted.document_mime_type == "image/png"
    assert extracted.document_file_size == 2048
    assert extracted.reply_to_message_id == 30
    assert extracted.media_group_id == "grp-1"


def test_classify_document_image_rules():
    assert telegram.classify_document_image("image/png", 1000) is None
    assert telegram.classify_document_image("image/jpeg", None) is None
    rejected_mime = telegram.classify_document_image("application/pdf", 1000)
    assert rejected_mime is not None and "PNG/JPEG/WebP" in rejected_mime
    rejected_size = telegram.classify_document_image("image/png", 7_000_000)
    assert rejected_size is not None and "6MB" in rejected_size


def _poll_once(main):
    async def run_once():
        try:
            await main._telegram_poll_loop()
        except KeyboardInterrupt:
            pass

    asyncio.run(run_once())


def test_poll_loop_reply_to_receipt_appends_to_same_thread(monkeypatch, tmp_path):
    """回复某条回执 = 把补充材料关联回同一条 ingest 线索，而不是又开一条新线程（测试 a）。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-reply-append.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    from backend.app.models import ChatMessage, ChatThread
    from sqlmodel import select

    batches = [
        [{"update_id": 40, "message": {"message_id": 500, "chat": {"id": 42}, "text": "第一条招聘线索，没有可识别链接"}}],
        [],
    ]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    sent_texts: list[str] = []
    next_message_id = {"n": 9000}

    def fake_send(token, chat_id, text):
        sent_texts.append(text)
        next_message_id["n"] += 1
        return next_message_id["n"]

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", fake_send)
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    _poll_once(main)

    with db.Session(db.engine) as session:
        threads = session.exec(select(ChatThread)).all()
        assert len(threads) == 1
        thread_id = threads[0].id
        assistant_msgs = session.exec(
            select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.role == "assistant")
        ).all()
        assert len(assistant_msgs) == 1
        receipt_id = assistant_msgs[0].metadata_json.get("receipt_tg_message_id")
        assert receipt_id == next_message_id["n"]

    # 第二批：机主回复了第一次回执，补一句话。
    batches.append(
        [
            {
                "update_id": 41,
                "message": {
                    "message_id": 501,
                    "chat": {"id": 42},
                    "text": "补充：通勤地图截图，同一个岗位",
                    "reply_to_message": {"message_id": receipt_id},
                },
            }
        ]
    )
    batches.append([])

    _poll_once(main)

    with db.Session(db.engine) as session:
        threads = session.exec(select(ChatThread)).all()
        assert len(threads) == 1  # 没有新建线程
        user_msgs = session.exec(
            select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.role == "user")
        ).all()
        assert len(user_msgs) == 2

    assert any("已补充到" in text for text in sent_texts[1:])


def test_poll_loop_reply_to_unknown_message_falls_back_to_new_thread(monkeypatch, tmp_path):
    """回复的不是回执（或太久远）→ 现状回退：照常新建线程（测试 b）。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-reply-unknown.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    from backend.app.models import ChatThread
    from sqlmodel import select

    batches = [
        [
            {
                "update_id": 42,
                "message": {
                    "message_id": 502,
                    "chat": {"id": 42},
                    "text": "随手回复了一条不相关的老消息",
                    "reply_to_message": {"message_id": 999999},
                },
            }
        ],
        [],
    ]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", lambda token, chat_id, text: 1)
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    _poll_once(main)

    with db.Session(db.engine) as session:
        threads = session.exec(select(ChatThread)).all()
        assert len(threads) == 1  # 正常新建了一条线程，没有报错/被吞


def test_poll_loop_document_image_downloads_pdf_rejected(monkeypatch, tmp_path):
    """document 类型图片可以走截图路径；不支持的格式给出明确提示，不能静默忽略（测试 c）。"""
    _db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-document.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    batches = [
        [
            {
                "update_id": 50,
                "message": {
                    "message_id": 600,
                    "chat": {"id": 42},
                    "document": {"file_id": "doc-png", "mime_type": "image/png", "file_size": 12345},
                },
            },
            {
                "update_id": 51,
                "message": {
                    "message_id": 601,
                    "chat": {"id": 42},
                    "document": {"file_id": "doc-pdf", "mime_type": "application/pdf", "file_size": 12345},
                },
            },
        ],
        [],
    ]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    sent: list[str] = []
    downloaded_ids: list[str] = []

    def fake_send(token, chat_id, text):
        sent.append(text)
        return 7001 + len(sent)

    def fake_download(token, file_id):
        downloaded_ids.append(file_id)
        return "data:image/png;base64,iVBORw0KGgo="

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", fake_send)
    monkeypatch.setattr(telegram_svc, "download_photo_data_url", fake_download)
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    _poll_once(main)

    assert downloaded_ids == ["doc-png"]  # pdf 完全不下载
    assert any("仅支持" in text for text in sent)
    assert not any("处理失败" in text for text in sent)  # 不崩溃


def test_poll_loop_media_group_shares_one_thread(monkeypatch, tmp_path):
    """同批相册两张图归到同一条线索：一条线程、两条 user 消息（测试 d）。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-album.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    from backend.app.models import ChatMessage, ChatThread
    from sqlmodel import select

    batches = [
        [
            {
                "update_id": 60,
                "message": {
                    "message_id": 700,
                    "chat": {"id": 42},
                    "media_group_id": "album-1",
                    "photo": [{"file_id": "p1", "width": 100, "height": 100}],
                },
            },
            {
                "update_id": 61,
                "message": {
                    "message_id": 701,
                    "chat": {"id": 42},
                    "media_group_id": "album-1",
                    "photo": [{"file_id": "p2", "width": 100, "height": 100}],
                },
            },
        ],
        [],
    ]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", lambda token, chat_id, text: 8000)
    monkeypatch.setattr(telegram_svc, "download_photo_data_url", lambda token, file_id: "data:image/png;base64,iVBORw0KGgo=")
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    _poll_once(main)

    with db.Session(db.engine) as session:
        threads = session.exec(select(ChatThread)).all()
        assert len(threads) == 1
        user_msgs = session.exec(
            select(ChatMessage).where(ChatMessage.thread_id == threads[0].id, ChatMessage.role == "user")
        ).all()
        assert len(user_msgs) == 2


def test_poll_loop_start_command_mentions_reply_to_append(monkeypatch, tmp_path):
    """/start 帮助文案要提到「回复回执可归入同一条线索」，不然这个新能力没人知道。"""
    _db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-start-reply-hint.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    batches = [[{"update_id": 70, "message": {"chat": {"id": 42}, "text": "/start"}}]]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    sent: list[str] = []

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", lambda token, chat_id, text: sent.append(text))
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    _poll_once(main)

    assert len(sent) == 1
    assert "回复" in sent[0] and "线索" in sent[0]


# ==================== 修复 4：线程标题压缩空白 / 剔除 URL / 截断 ====================


def test_ingest_thread_title_truncates_long_text():
    import backend.app.main as main

    text = "这是一段很长很长很长很长很长很长很长很长很长的招聘正文，超过二十四个字就应该被截断加省略号"
    title = main._ingest_thread_title(text, has_image=False)
    assert title.startswith("入库候选 · ")
    snippet = title.removeprefix("入库候选 · ")
    assert snippet.endswith("…")
    assert len(snippet) == 25  # 24 字 + 省略号


def test_ingest_thread_title_strips_urls_and_collapses_whitespace():
    import backend.app.main as main

    text = "  看看这个 \n\n https://mp.weixin.qq.com/s/AbC123   岗位怎么样  "
    title = main._ingest_thread_title(text, has_image=False)
    assert "https://" not in title
    assert title == "入库候选 · 看看这个 岗位怎么样"


def test_ingest_thread_title_falls_back_to_timestamp_when_all_urls():
    import backend.app.main as main
    from datetime import datetime, timezone

    text = "https://www.zhipin.com/job_detail/abc123.html"
    before = datetime.now(timezone.utc)
    title = main._ingest_thread_title(text, has_image=False)
    after = datetime.now(timezone.utc)
    assert "https://" not in title
    assert title.startswith("入库候选 · ")
    stamp = title.removeprefix("入库候选 · ")
    # 时间戳兜底格式 MMDD HH:MM；只校验落在测试执行的分钟窗口内，容忍跨分钟边界的极小概率抖动。
    assert stamp in {before.strftime("%m%d %H:%M"), after.strftime("%m%d %H:%M")}


def test_ingest_thread_title_pure_image_no_text():
    import backend.app.main as main

    assert main._ingest_thread_title("", has_image=True) == "入库候选 · 截图入库"


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
        lambda session, text, image_data_url=None, target_thread_id=None: calls["persisted"].append(text)
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
