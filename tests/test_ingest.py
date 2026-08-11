"""ingest：只抽候选写聊天；用户 commit 才入库。全程不联网。"""

from __future__ import annotations

import asyncio
import base64
import importlib

import httpx

from backend.app.candidates import (
    CANDIDATE_COMMITTED,
    CANDIDATE_PENDING,
    CANDIDATE_SKIPPED,
    CANDIDATE_UI_ONLY_FIELDS,
    strip_ui_only_fields,
)
from backend.app.services import ai, bebee, chat_ingest, collectors, importer, ingest, telegram, wechat
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


def test_candidate_status_constants_match_expected_literals():
    """R5：状态常量值必须和历史上散落各处的字符串字面量一致，否则替换会悄悄改变行为。"""
    assert (CANDIDATE_PENDING, CANDIDATE_COMMITTED, CANDIDATE_SKIPPED) == ("pending", "committed", "skipped")


def test_strip_ui_only_fields_removes_only_ui_only_keys():
    """R5：strip_ui_only_fields 只剔除纯 UI 字段（含建议 advice），其余字段原样保留。

    `advice` 必须在这份集合里：Job 表没有这一列，漏剔会让 commit 时的 upsert 直接炸。
    """
    candidate = {
        "title": "资深BI工程师",
        "company_name": "示例科技",
        "status": CANDIDATE_PENDING,
        "job_id": None,
        "existing_job_id": 7,
        "duplicate_in_thread_id": 3,
        "advice": {"priority": "B", "direction": "邻近可接受", "next_action": "继续沟通"},
    }
    stripped = strip_ui_only_fields(candidate)
    assert stripped == {
        "title": "资深BI工程师",
        "company_name": "示例科技",
        "status": CANDIDATE_PENDING,
        "job_id": None,
    }
    # 浅拷贝：不原地修改传入的候选 dict。
    assert "existing_job_id" in candidate
    assert "duplicate_in_thread_id" in candidate
    assert "advice" in candidate
    assert set(CANDIDATE_UI_ONLY_FIELDS) == {"existing_job_id", "duplicate_in_thread_id", "advice"}


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
        lambda text, image_data_url=None, prior_candidates=None: [
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

    def boom(text, image_data_url=None, prior_candidates=None):
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

    monkeypatch.setattr(ai, "extract_jobs_freeform", lambda text, image_data_url=None, prior_candidates=None: [])
    result = ingest.run_ingest(
        "一段随便的文本，认不出岗位。",
        wechat_cfg={"source_label": "公众号", "fetch": {}},
        bebee_cfg={"source_label": "beBee"},
        ai_enabled=True,
    )
    assert result["ai_error"] is None
    assert result["candidate_count"] == 0


# ==================== 同一岗位跨图/跨消息补充：prior_candidates 上下文 ====================


def test_extract_jobs_freeform_includes_prior_candidates_in_prompt(monkeypatch):
    """碎片文本（只有「岗位职责」没有标题/公司）单独抽取本会 0 候选；带上已识别候选做上下文后，
    prompt 必须包含那个候选的标题，模型才有机会判断「这是补充」而不是「没内容」。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from backend.app.services import ai

    captured: dict = {}

    def fake_chat(system, user):
        captured["system"] = system
        captured["user"] = user
        return (
            '{"jobs":[{"title":"独立站运营","company_name":"未知公司","salary_text":"9-14K",'
            '"city":"青岛","area":"青岛","description":"岗位职责：负责独立站运营"}]}'
        )

    monkeypatch.setattr(ai, "_chat", fake_chat)

    prior = [
        {
            "title": "独立站运营",
            "company_name": "未知公司",
            "salary_text": "9-14K",
            "city": "青岛",
            "area": "青岛",
        }
    ]
    jobs = ai.extract_jobs_freeform("岗位职责：负责独立站运营", None, prior_candidates=prior)

    assert captured["user"].count("独立站运营") >= 1
    assert "已识别候选" in captured["user"]
    assert len(jobs) == 1
    assert jobs[0]["title"] == "独立站运营"


def test_extract_jobs_freeform_without_prior_candidates_matches_current_behavior(monkeypatch):
    """不传 prior_candidates（默认 None）时 prompt 不应包含上下文块，行为与改动前完全一致。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from backend.app.services import ai

    captured: dict = {}

    def fake_chat(system, user):
        captured["user"] = user
        return '{"jobs":[]}'

    monkeypatch.setattr(ai, "_chat", fake_chat)

    ai.extract_jobs_freeform("岗位职责：负责独立站运营", None)

    assert "已识别候选" not in captured["user"]


def test_persist_ingest_to_chat_feeds_target_thread_candidates_as_prior_context(monkeypatch, tmp_path):
    """相册/回复补充场景：追加进一个已有 ingest 线程时，必须把该线程上一条 assistant 消息的
    候选作为 prior_candidates 传给 run_ingest，且新消息追加进同一线程而不是新建线程。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-prior-candidates.sqlite3")
    from backend.app.models import ChatMessage, ChatThread
    from sqlmodel import select

    prior_candidate = {
        "title": "独立站运营",
        "company_name": "未知公司",
        "salary_text": "9-14K",
        "city": "青岛",
        "area": "青岛",
        "status": "pending",
        "job_id": None,
    }

    with db.Session(db.engine) as session:
        thread = ChatThread(kind="ingest", job_id=None, title="入库候选 · 独立站运营")
        session.add(thread)
        session.commit()
        session.refresh(thread)

        session.add(
            ChatMessage(
                thread_id=thread.id,
                role="user",
                content="独立站运营 未知公司 9-14K 青岛",
            )
        )
        session.add(
            ChatMessage(
                thread_id=thread.id,
                role="assistant",
                content="识别到 1 个候选岗位。",
                metadata_json={"candidates": [prior_candidate]},
            )
        )
        session.commit()

        captured: dict = {}

        def fake_run_ingest(text, **kwargs):
            captured["prior_candidates"] = kwargs.get("prior_candidates")
            return {
                "candidates": [],
                "candidate_count": 0,
                "sources_report": [],
                "unmatched": True,
                "needs_ai": False,
                "ai_error": None,
                "known_uncrawlable_hint": False,
            }

        monkeypatch.setattr(chat_ingest, "run_ingest", fake_run_ingest)

        result = chat_ingest._persist_ingest_to_chat(
            session, "岗位职责：负责独立站运营", None, target_thread_id=thread.id
        )

        assert captured["prior_candidates"] is not None
        assert any(c.get("title") == "独立站运营" for c in captured["prior_candidates"])

        assert result["thread"]["id"] == thread.id
        assert result["appended"] is True

        threads = session.exec(select(ChatThread)).all()
        assert len(threads) == 1
        user_msgs = session.exec(
            select(ChatMessage).where(ChatMessage.thread_id == thread.id, ChatMessage.role == "user")
        ).all()
        assert len(user_msgs) == 2


def test_persist_ingest_prior_context_is_order_independent_and_deduped(monkeypatch, tmp_path):
    """健壮性：prior_candidates 跨线程所有 assistant 消息累积，不依赖到达顺序，且按岗位去重。

    构造：最早一条 assistant 有「独立站运营」，随后一条 assistant 没识别出岗位（空候选，
    模拟碎片图先到主图后到里那张失败的），最新一条又重复出现「独立站运营」。
    只看最后一条会拿到重复；跨消息累积去重后应恰好保留一条独立站运营。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-prior-order.sqlite3")
    from backend.app.models import ChatMessage, ChatThread

    job = {"title": "独立站运营", "company_name": "未知公司", "salary_text": "9-14K", "city": "青岛", "area": "青岛"}

    with db.Session(db.engine) as session:
        thread = ChatThread(kind="ingest", job_id=None, title="入库候选 · 独立站运营")
        session.add(thread)
        session.commit()
        session.refresh(thread)

        import time as _time

        # 三条 assistant，按创建时间递增；中间一条空候选。
        for content, meta in [
            ("识别到 1 个候选岗位。", {"candidates": [job]}),
            ("未从链接、文本或截图中认出岗位。", {"candidates": []}),
            ("识别到 1 个候选岗位。", {"candidates": [dict(job)]}),
        ]:
            session.add(ChatMessage(thread_id=thread.id, role="assistant", content=content, metadata_json=meta))
            session.commit()
            _time.sleep(0.01)

        captured: dict = {}

        def fake_run_ingest(text, **kwargs):
            captured["prior_candidates"] = kwargs.get("prior_candidates")
            return {
                "candidates": [], "candidate_count": 0, "sources_report": [],
                "unmatched": True, "needs_ai": False, "ai_error": None, "known_uncrawlable_hint": False,
            }

        monkeypatch.setattr(chat_ingest, "run_ingest", fake_run_ingest)

        chat_ingest._persist_ingest_to_chat(session, "岗位职责：负责独立站运营", None, target_thread_id=thread.id)

        prior = captured["prior_candidates"]
        assert prior is not None
        titles = [c.get("title") for c in prior]
        assert titles.count("独立站运营") == 1, f"应去重为一条，实际 {titles}"


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
        lambda text, image_data_url=None, prior_candidates=None: [
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
        lambda text, image_data_url=None, prior_candidates=None: [{"title": "截图岗位", "company_name": "X", "url": None}],
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
        lambda text, image_data_url=None, prior_candidates=None: [{"title": "跳过岗位", "company_name": "Y"}],
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


# ==================== 候选「跳过」可恢复：restore ====================


def test_restore_skipped_candidate_becomes_pending_and_recommittable(monkeypatch, tmp_path):
    """commit 一条 -> 跳过另一条 -> restore 跳过的 -> 它变 pending 且能再次 commit。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-restore.sqlite3")
    from backend.app.models import Job
    from backend.app.services import ai
    from sqlmodel import select

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None, prior_candidates=None: [
            {"title": "岗位甲", "company_name": "甲司"},
            {"title": "岗位乙", "company_name": "乙司"},
        ],
    )

    async def scenario():
        async for client in _client(main.app):
            payload = (await client.post("/api/ingest", json={"text": "一段 JD 文本足够长"})).json()
            thread_id = payload["thread"]["id"]
            assistant_id = payload["assistant_message"]["id"]

            # 提交第 0 个，第 1 个先按「全部跳过」处理（此时只影响仍是 pending 的第 1 个）。
            commit0 = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert commit0.status_code == 200, commit0.text
            skip_rest = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": []},
            )
            assert skip_rest.status_code == 200, skip_rest.text
            candidates = skip_rest.json()["assistant_message"]["metadata_json"]["candidates"]
            assert candidates[0]["status"] == "committed"
            assert candidates[1]["status"] == "skipped"

            restore = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/restore",
                json={"message_id": assistant_id, "indexes": [1]},
            )
            assert restore.status_code == 200, restore.text
            restore_body = restore.json()
            assert restore_body["results"] == [{"index": 1, "ok": True, "reason": "已恢复为待选"}]
            restored_candidate = restore_body["assistant_message"]["metadata_json"]["candidates"][1]
            assert restored_candidate["status"] == "pending"

            commit1 = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [1]},
            )
            assert commit1.status_code == 200, commit1.text
            assert commit1.json()["created"] == 1

            with db.Session(db.engine) as session:
                jobs = session.exec(select(Job)).all()
                assert len(jobs) == 2

    asyncio.run(scenario())


def test_restore_rejects_committed_candidate(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-restore-committed.sqlite3")
    from backend.app.services import ai

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None, prior_candidates=None: [{"title": "岗位甲", "company_name": "甲司"}],
    )

    async def scenario():
        async for client in _client(main.app):
            payload = (await client.post("/api/ingest", json={"text": "一段 JD 文本足够长"})).json()
            thread_id = payload["thread"]["id"]
            assistant_id = payload["assistant_message"]["id"]

            commit = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/commit",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert commit.status_code == 200, commit.text

            restore = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/restore",
                json={"message_id": assistant_id, "indexes": [0]},
            )
            assert restore.status_code == 200, restore.text
            body = restore.json()
            assert body["results"] == [{"index": 0, "ok": False, "reason": "已入库无法恢复为待选"}]
            candidate = body["assistant_message"]["metadata_json"]["candidates"][0]
            assert candidate["status"] == "committed"  # 仍然入库状态，未被改动

    asyncio.run(scenario())


def test_restore_out_of_range_index_returns_400(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-restore-oob.sqlite3")
    from backend.app.services import ai

    monkeypatch.setattr(
        ai,
        "extract_jobs_freeform",
        lambda text, image_data_url=None, prior_candidates=None: [{"title": "岗位甲", "company_name": "甲司"}],
    )

    async def scenario():
        async for client in _client(main.app):
            payload = (await client.post("/api/ingest", json={"text": "一段 JD 文本足够长"})).json()
            thread_id = payload["thread"]["id"]
            assistant_id = payload["assistant_message"]["id"]

            restore = await client.post(
                f"/api/chat/threads/{thread_id}/candidates/restore",
                json={"message_id": assistant_id, "indexes": [5]},
            )
            assert restore.status_code == 400

    asyncio.run(scenario())


def test_ingest_endpoint_surfaces_ai_failure_reason_distinct_from_unmatched(monkeypatch, tmp_path):
    """修复 1：AI 已配置但调用失败时，聊天消息必须明确说「AI 抽取失败」，不能和「未认出」混在一起，
    也不能泄露密钥或裸 traceback；AI 正常返回 0 候选时仍是原来的「未认出」文案。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-liveTESTKEY1234567890")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-ai-fail-http.sqlite3")
    from backend.app.services import ai

    def boom(text, image_data_url=None, prior_candidates=None):
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

    monkeypatch.setattr(ai, "extract_jobs_freeform", lambda text, image_data_url=None, prior_candidates=None: [])

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
                lambda text, image_data_url=None, prior_candidates=None: [
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
                # R5 回归：strip_ui_only_fields 剔除的字段绝不能泄漏进 Job 表；
                # Job 模型压根没有这两列，一旦 upsert 收到它们就会直接抛异常而不是静默污染。
                assert not hasattr(jobs[0], "existing_job_id")
                assert not hasattr(jobs[0], "duplicate_in_thread_id")
                assert jobs[0].title == "资深BI工程师"
                assert jobs[0].company_name == "示例科技"
                assert jobs[0].salary_text == "25-35K"

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
        lambda text, image_data_url=None, prior_candidates=None: [
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
        lambda text, image_data_url=None, prior_candidates=None: [{"title": "资深BI工程师", "company_name": "示例科技", "city": "上海"}],
    )

    async def scenario():
        async for client in _client(main.app):
            first = await client.post("/api/ingest", json={"text": "第一条线索"})
            assert first.status_code == 200, first.text
            first_thread_id = first.json()["thread"]["id"]

            monkeypatch.setattr(
                ai,
                "extract_jobs_freeform",
                lambda text, image_data_url=None, prior_candidates=None: [
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
        lambda text, image_data_url=None, prior_candidates=None: [{"title": "资深BI工程师", "company_name": "示例科技", "city": "上海"}],
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
                lambda text, image_data_url=None, prior_candidates=None: [
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

    def fake_persist(session, text, image_data_url=None, target_thread_id=None, source_tg_message_id=None, edited_from_tg_message_id=None):
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


def test_extract_message_distinguishes_edited_message_from_new_message():
    """现状确认的核心 bug：过去 `message = update.get("message") or update.get("edited_message") or {}`
    把两者不加区分地合并，编辑事件被当成全新消息。这里断言 is_edit 标记和 message_id 都正确。"""
    fresh = telegram.extract_message(
        {"update_id": 1, "message": {"message_id": 800, "chat": {"id": 42}, "text": "原始文本"}}
    )
    assert fresh.is_edit is False
    assert fresh.message_id == 800

    edited = telegram.extract_message(
        {
            "update_id": 2,
            "edited_message": {"message_id": 800, "chat": {"id": 42}, "text": "编辑后的文本"},
        }
    )
    assert edited.is_edit is True
    assert edited.message_id == 800  # Telegram 编辑不分配新 id，和原消息完全一致
    assert edited.text == "编辑后的文本"
    assert edited.chat_id == 42


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


def test_poll_loop_edited_message_updates_original_thread(monkeypatch, tmp_path):
    """编辑一条本 bot 处理过的消息：归入原线程（不新建），回执明确说「已按编辑更新归入」，
    不是像回复回执那样说「已补充到」——两者语义不同，编辑是修正内容，不是追加新材料。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-edit-known.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    from backend.app.models import ChatMessage, ChatThread
    from sqlmodel import select

    batches = [
        [{"update_id": 50, "message": {"message_id": 800, "chat": {"id": 42}, "text": "第一条线索，没有可识别链接"}}],
        [],
    ]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    sent_texts: list[str] = []

    def fake_send(token, chat_id, text):
        sent_texts.append(text)
        return 9001

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
        user_msgs = session.exec(
            select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.role == "user")
        ).all()
        assert len(user_msgs) == 1
        assert user_msgs[0].metadata_json.get("source_tg_message_id") == 800

    # 第二批：编辑同一条消息。Telegram 编辑事件的 message_id 和原消息完全一致。
    batches.append(
        [
            {
                "update_id": 51,
                "edited_message": {
                    "message_id": 800,
                    "chat": {"id": 42},
                    "text": "第一条线索，改了个错别字",
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
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id, ChatMessage.role == "user")
            .order_by(ChatMessage.created_at.asc())
        ).all()
        assert len(user_msgs) == 2
        assert user_msgs[1].content == "第一条线索，改了个错别字"
        assert user_msgs[1].metadata_json.get("edited_from_tg_message_id") == 800

    assert any("已按编辑更新归入" in text for text in sent_texts)


def test_poll_loop_edited_unknown_message_is_silently_ignored(monkeypatch, tmp_path):
    """编辑一条本机没处理过的消息（太久远/不是本 bot 落盘的）：零线程零回执，静默忽略——
    这是用户抱怨的核心场景：手滑改个错别字不应该刷出新线索或打扰机主。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-edit-unknown.sqlite3")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    from backend.app.models import ChatThread
    from sqlmodel import select

    batches = [
        [
            {
                "update_id": 60,
                "edited_message": {
                    "message_id": 999999,
                    "chat": {"id": 42},
                    "text": "编辑了一条本机不认识的老消息",
                },
            }
        ],
        [],
    ]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    sent_texts: list[str] = []

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", lambda token, chat_id, text: sent_texts.append(text))
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    _poll_once(main)

    assert sent_texts == []
    with db.Session(db.engine) as session:
        assert session.exec(select(ChatThread)).all() == []


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


# ==================== 候选决策建议（手机上先给判断，不止入库） ====================


def _stub_freeform(monkeypatch, jobs):
    from backend.app.services import ai as ai_module

    monkeypatch.setattr(
        ai_module,
        "extract_jobs_freeform",
        lambda text, image_data_url=None, prior_candidates=None: list(jobs),
    )


def test_ingest_attaches_advice_and_commit_strips_it(monkeypatch, tmp_path):
    """识别到候选后要带上初步建议；建议是纯 UI 字段，入库时必须被剔除。

    漏剔会直接把 commit 打挂——Job 表没有 advice 列，upsert 会拿到未知字段。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-advice.sqlite3")
    from backend.app.models import Job
    from sqlmodel import select

    _stub_freeform(monkeypatch, [{"title": "独立站运营", "company_name": "示例科技", "salary_text": "12-18K", "city": "上海"}])

    async def scenario():
        async for client in _client(main.app):
            payload = (await client.post("/api/ingest", json={"text": "一段足够长的招聘 JD 正文文本"})).json()
            advice = payload["candidates"][0]["advice"]
            assert advice["priority"] and advice["direction"] and advice["next_action"]
            # conftest 把建议里的模型调用桩成 None：走规则引擎的确定性结论，标记为非 AI 结果。
            assert advice["ai_used"] is False
            # 建议也要写回消息 metadata，Web 刷新后仍看得到，不是只在这次响应里。
            stored = payload["assistant_message"]["metadata_json"]["candidates"][0]
            assert stored["advice"]["priority"] == advice["priority"]

            commit = await client.post(
                f"/api/chat/threads/{payload['thread']['id']}/candidates/commit",
                json={"message_id": payload["assistant_message"]["id"], "indexes": [0]},
            )
            assert commit.status_code == 200, commit.text
            assert commit.json()["created"] == 1

    asyncio.run(scenario())

    with db.Session(db.engine) as session:
        jobs = session.exec(select(Job)).all()
        assert len(jobs) == 1
        assert not hasattr(jobs[0], "advice")


def test_attach_candidate_advice_respects_config_switch(monkeypatch, tmp_path):
    """`ingest.advice=false` 时一条建议都不生成（也就不会有任何模型调用）。"""
    from dataclasses import replace

    db, main = _fresh_modules(monkeypatch, tmp_path, "ingest-advice-off.sqlite3")
    from backend.app.models import ChatMessage, ChatThread

    with db.Session(db.engine) as session:
        thread = ChatThread(kind="ingest", title="入库候选 · 测试")
        session.add(thread)
        session.commit()
        session.refresh(thread)
        message = ChatMessage(
            thread_id=thread.id or 0,
            role="assistant",
            content="识别到 1 个候选岗位。",
            metadata_json={"candidates": [{"title": "独立站运营", "company_name": "示例科技"}]},
        )
        session.add(message)
        session.commit()
        session.refresh(message)
        message_id = message.id

        off = replace(main.settings, config={**main.settings.config, "ingest": {"advice": False}})
        result = chat_ingest.attach_candidate_advice(session, message_id or 0, off)
        assert result["advice_count"] == 0
        assert result["advice_text"] == ""

        on = replace(main.settings, config={**main.settings.config, "ingest": {"advice": True}})
        assert chat_ingest.attach_candidate_advice(session, message_id or 0, on)["advice_count"] == 1


def test_format_advice_block_is_compact_and_notes_remainder():
    from backend.app.services.advice import format_advice_block

    candidates = [
        {
            "title": "独立站运营",
            "company_name": "示例科技",
            "salary_text": "12-18K",
            "advice": {
                "priority": "B",
                "direction": "邻近可接受",
                "next_action": "继续沟通",
                "reasons": ["命中目标方向"],
                "ask_first": ["确认薪资结构"],
                "hard_conditions": [],
            },
        },
        {"title": "没有建议的候选", "company_name": "乙司"},
    ]
    text = format_advice_block(candidates)
    assert "① 独立站运营 · 示例科技 · 12-18K" in text
    assert "建议：B / 邻近可接受 → 继续沟通" in text
    assert "理由：命中目标方向" in text
    assert "先问：确认薪资结构" in text
    assert "其余 1 个候选未生成建议" in text
    # 没有任何建议时返回空串，调用方据此决定「要不要多发一条消息」。
    assert format_advice_block([{"title": "甲"}]) == ""


def test_poll_loop_sends_advice_after_the_receipt(monkeypatch, tmp_path):
    """回执必须先到，建议单独再发一条——建议要额外做模型调用，捆在一起会让「已收到」迟到。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    _db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-advice.sqlite3")
    _stub_freeform(monkeypatch, [{"title": "独立站运营", "company_name": "示例科技", "salary_text": "12-18K"}])

    batches = [[{"update_id": 80, "message": {"message_id": 800, "chat": {"id": 42}, "text": "一段足够长的招聘 JD 正文文本"}}]]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    sent: list[str] = []

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", lambda token, chat_id, text: sent.append(text) or 9100)
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    _poll_once(main)

    assert len(sent) == 2
    assert "识别到 1 个候选" in sent[0] and "建议：" not in sent[0]
    assert sent[1].startswith("① 独立站运营")
    assert "建议：" in sent[1]


# ==================== 手机端追问：? / ？ / /ask 走决策链路，不产生候选 ====================


def test_parse_question_only_accepts_explicit_prefixes():
    """靠内容猜意图会把「回复回执补一段 JD」吃成提问 = 丢材料，所以只认显式前缀。"""
    assert telegram.parse_question("? 这个岗位值得聊吗") == "这个岗位值得聊吗"
    assert telegram.parse_question("？值得聊吗") == "值得聊吗"
    assert telegram.parse_question("/ask 薪资怎么谈") == "薪资怎么谈"
    assert telegram.parse_question("/ask@my_job_bot 薪资怎么谈") == "薪资怎么谈"
    # 不带前缀的一律是材料，绝不能被当成提问。
    assert telegram.parse_question("补充：这个岗位还要求英文写作") is None
    assert telegram.parse_question("https://mp.weixin.qq.com/s/abc") is None
    # 只有前缀没有内容：没有分析价值，交回既有的「请发送链接/文本/截图」提示。
    assert telegram.parse_question("?") is None
    assert telegram.parse_question("/ask   ") is None
    assert telegram.parse_question("") is None


def test_summarize_analysis_keeps_rule_mode_visible():
    analysis = {
        "summary": "岗位方向与画像较匹配。",
        "priority": "A",
        "direction": "核心优先",
        "next_action": "主动联系",
        "action_text": "先发一条针对岗位的沟通。",
        "risks": ["薪资结构待确认", "通勤偏远", "第三条应被截断"],
        "uncertainties": ["确认是否一人全包"],
    }
    text = telegram.summarize_analysis(analysis, ai_used=True)
    assert "判断：A / 核心优先 → 主动联系" in text
    assert "第三条应被截断" not in text
    assert "规则模式" not in text
    assert "规则模式" in telegram.summarize_analysis(analysis, ai_used=False)


def test_poll_loop_question_answers_in_mobile_thread_without_candidates(monkeypatch, tmp_path):
    """`?` 开头 = 提问：走决策链路回答，不建 ingest 候选，也不重复开新线程。"""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-ask.sqlite3")
    from backend.app.models import ChatMessage, ChatThread
    from sqlmodel import select

    batches = [
        [{"update_id": 90, "message": {"message_id": 900, "chat": {"id": 42}, "text": "? 这个岗位值得聊吗"}}],
        [],
        [{"update_id": 91, "message": {"message_id": 901, "chat": {"id": 42}, "text": "/ask 那薪资怎么谈"}}],
        [],
    ]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    sent: list[str] = []

    from backend.app.services import telegram as telegram_svc
    from dataclasses import replace

    monkeypatch.setattr(telegram_svc, "get_updates", fake_get_updates)
    monkeypatch.setattr(telegram_svc, "send_message", lambda token, chat_id, text: sent.append(text) or 9200)
    main.settings = replace(
        main.settings,
        config={**main.settings.config, "telegram": {"enabled": True, "allowed_chat_id": 42, "poll_timeout": 1}},
    )

    _poll_once(main)
    _poll_once(main)

    assert len(sent) == 2
    assert all("判断：" in text for text in sent)

    with db.Session(db.engine) as session:
        threads = session.exec(select(ChatThread)).all()
        # 两次提问共用同一条「手机提问」线程，不是每问一句刷一条新线程。
        assert len(threads) == 1
        assert threads[0].kind == "general"
        assert threads[0].title == "手机提问"
        messages = session.exec(select(ChatMessage).where(ChatMessage.thread_id == threads[0].id)).all()
        assert [item.role for item in messages] == ["user", "assistant", "user", "assistant"]
        # 提问不是材料：不产生任何候选。
        assert all(not (item.metadata_json or {}).get("candidates") for item in messages)
        # 前缀已剥掉，落盘的是问题本身。
        assert messages[0].content == "这个岗位值得聊吗"


def test_poll_loop_question_replying_to_receipt_lands_in_that_thread(monkeypatch, tmp_path):
    """回复某条回执再提问 → 落进那条线索（模型能看到该线索上下文），不落通用线程。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-ask-reply.sqlite3")
    from backend.app.models import ChatMessage, ChatThread
    from sqlmodel import select

    _stub_freeform(monkeypatch, [{"title": "独立站运营", "company_name": "示例科技"}])

    batches = [[{"update_id": 95, "message": {"message_id": 950, "chat": {"id": 42}, "text": "一段足够长的招聘 JD 正文文本"}}], []]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    sent: list[str] = []
    next_message_id = {"n": 9300}

    def fake_send(token, chat_id, text):
        sent.append(text)
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
        ingest_thread_id = threads[0].id
        receipt = session.exec(
            select(ChatMessage).where(ChatMessage.thread_id == ingest_thread_id, ChatMessage.role == "assistant")
        ).first()
        receipt_tg_id = receipt.metadata_json.get("receipt_tg_message_id")
    assert isinstance(receipt_tg_id, int)

    batches.append(
        [
            {
                "update_id": 96,
                "message": {
                    "message_id": 951,
                    "chat": {"id": 42},
                    "text": "? 这个值得继续聊吗",
                    "reply_to_message": {"message_id": receipt_tg_id},
                },
            }
        ]
    )
    batches.append([])
    _poll_once(main)

    with db.Session(db.engine) as session:
        threads = session.exec(select(ChatThread)).all()
        assert len(threads) == 1  # 没有另开「手机提问」线程
        messages = session.exec(select(ChatMessage).where(ChatMessage.thread_id == ingest_thread_id)).all()
        assert [item.content for item in messages].count("这个值得继续聊吗") == 1
    assert "判断：" in sent[-1]


# ==================== 追问锚定：这次问的到底是哪个岗位 ====================


def test_parse_candidate_index_accepts_digits_and_markers():
    """`?2 …` / `?② …` 指名问第几个候选；没有序号或只有序号时不当成指名。"""
    assert telegram.parse_candidate_index("2 这个值得聊吗") == (1, "这个值得聊吗")
    assert telegram.parse_candidate_index("②、这个值得聊吗") == (1, "这个值得聊吗")
    assert telegram.parse_candidate_index("1. 薪资怎么谈") == (0, "薪资怎么谈")
    assert telegram.parse_candidate_index("这个值得聊吗") == (None, "这个值得聊吗")
    # 只有一个数字、没有问题：别把它当指名，原样交回上层。
    assert telegram.parse_candidate_index("2") == (None, "2")


def test_reply_in_thread_anchors_to_thread_candidate(monkeypatch, tmp_path):
    """ingest 线索的 job_id 恒为 None，锚点必须来自该线索已识别的候选，否则模型不知道在答哪个岗位。

    覆盖三件事：默认第一个、`candidate_index` 指名第二个、岗位事实真的进了规则分析。
    """
    db, main = _fresh_modules(monkeypatch, tmp_path, "reply-anchor.sqlite3")
    from backend.app.models import ChatMessage, ChatThread
    from backend.app.services.decision_reply import reply_in_thread

    with db.Session(db.engine) as session:
        thread = ChatThread(kind="ingest", title="入库候选 · 两个岗位")
        session.add(thread)
        session.commit()
        session.refresh(thread)
        session.add(
            ChatMessage(
                thread_id=thread.id or 0,
                role="assistant",
                content="识别到 2 个候选岗位。",
                metadata_json={
                    "candidates": [
                        {"title": "独立站运营", "company_name": "未知公司", "salary_text": "12-18K", "city": "上海"},
                        {"title": "广告优化师", "company_name": "示例科技", "salary_text": "8-12K", "city": "上海"},
                    ]
                },
            )
        )
        session.commit()

        default_reply = reply_in_thread(session, thread, "这个值得聊吗")
        assert default_reply["anchor"]["kind"] == "candidate"
        assert default_reply["anchor"]["label"] == "① 独立站运营 · 未知公司"
        assert default_reply["anchor"]["total"] == 2
        # 回答正文要回显锚点，否则用户看到结论也不知道说的是哪个候选。
        assert default_reply["assistant_message"].content.startswith("针对 ① 独立站运营 · 未知公司")

        picked = reply_in_thread(session, thread, "那这个呢", candidate_index=1)
        assert picked["anchor"]["label"] == "② 广告优化师 · 示例科技"
        # 岗位事实确实进了规则引擎：确认清单里出现该候选的公司/岗位事实。
        facts = " ".join(item["text"] for item in picked["analysis"]["confirmed_facts"])
        assert "示例科技" in facts and "广告优化师" in facts


def test_reply_in_thread_without_candidates_keeps_previous_behaviour(monkeypatch, tmp_path):
    """通用线程（如「手机提问」）没有候选可锚：anchor=none，行为与加锚点之前一致。"""
    db, main = _fresh_modules(monkeypatch, tmp_path, "reply-anchor-none.sqlite3")
    from backend.app.models import ChatThread
    from backend.app.services.decision_reply import reply_in_thread

    with db.Session(db.engine) as session:
        thread = ChatThread(kind="general", title="手机提问")
        session.add(thread)
        session.commit()
        session.refresh(thread)
        reply = reply_in_thread(session, thread, "现在整体该怎么推进")
        assert reply["anchor"]["kind"] == "none"
        assert not reply["assistant_message"].content.startswith("针对")


def test_poll_loop_question_index_picks_that_candidate(monkeypatch, tmp_path):
    """手机上 `?2 …`：回答锚到第二个候选，并提示还能换着问。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    db, main = _fresh_modules(monkeypatch, tmp_path, "telegram-ask-index.sqlite3")
    from backend.app.models import ChatMessage, ChatThread
    from sqlmodel import select

    _stub_freeform(
        monkeypatch,
        [
            {"title": "独立站运营", "company_name": "未知公司", "salary_text": "12-18K"},
            {"title": "广告优化师", "company_name": "示例科技", "salary_text": "8-12K"},
        ],
    )

    batches = [[{"update_id": 97, "message": {"message_id": 970, "chat": {"id": 42}, "text": "一段足够长的招聘 JD 正文文本"}}], []]

    def fake_get_updates(token, offset, timeout):
        if batches:
            return batches.pop(0)
        raise KeyboardInterrupt

    sent: list[str] = []
    next_message_id = {"n": 9400}

    def fake_send(token, chat_id, text):
        sent.append(text)
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
        thread_id = session.exec(select(ChatThread)).all()[0].id
        receipt = session.exec(
            select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.role == "assistant")
        ).first()
        receipt_tg_id = receipt.metadata_json.get("receipt_tg_message_id")

    batches.append(
        [
            {
                "update_id": 98,
                "message": {
                    "message_id": 971,
                    "chat": {"id": 42},
                    "text": "?2 这个值得聊吗",
                    "reply_to_message": {"message_id": receipt_tg_id},
                },
            }
        ]
    )
    batches.append([])
    _poll_once(main)

    answer = sent[-1]
    assert answer.startswith("针对 ② 广告优化师 · 示例科技")
    assert "换一个问：?2" in answer  # 多候选时要告诉用户怎么换


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
        lambda session, text, image_data_url=None, target_thread_id=None, source_tg_message_id=None, edited_from_tg_message_id=None: calls[
            "persisted"
        ].append(text)
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


def test_advice_and_decision_reply_never_import_importer():
    """红线绊线（CLAUDE.md §2/§6）：建议与追问都是只读判断，不得成为第二条入库路径。

    `advice.py` 会构造一个**纯内存** Job 对象当规则引擎的输入载体，这里额外锁定它不会
    顺手 add/upsert：谁把入库塞进「给个建议」或「回答一句」的路径里，CI 立即翻红。
    """
    import inspect

    from backend.app.services import advice as advice_module
    from backend.app.services import decision_reply

    for module in (advice_module, decision_reply):
        imported = _imported_names(module)
        assert not any("importer" in name for name in imported), f"{module.__name__} 不得引用 importer"
        assert not any("upsert" in name.lower() for name in imported), f"{module.__name__} 不得引用 upsert_*"

    # AST 级（不受文档字符串/注释干扰）：advice 拿到的 session 只能用来读，绝不能 add/commit。
    import ast

    writes = {
        node.func.attr
        for node in ast.walk(ast.parse(inspect.getsource(advice_module)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "session"
        and node.func.attr in {"add", "add_all", "commit", "merge", "delete", "flush"}
    }
    assert not writes, f"advice 只读 session，不得调用 session.{'/'.join(sorted(writes))}"


def test_chat_ingest_module_never_imports_importer():
    """红线绊线（Phase R·R1 后）：ingest→chat 落盘助手下沉到 services/chat_ingest.py 后，
    仍不得引用 importer/upsert——把「落盘只写聊天、绝不自动入库」的保证钉在新家。

    落盘函数从 main.py 搬到 chat_ingest.py 只是搬家，红线不变；谁在这里塞回
    importer/upsert（= ingest 自动入库），CI 立即翻红。
    """
    imported = _imported_names(chat_ingest)
    assert not any("importer" in name for name in imported), "chat_ingest 不得引用 importer"
    assert not any("upsert" in name.lower() for name in imported), "chat_ingest 不得引用 upsert_*"


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
