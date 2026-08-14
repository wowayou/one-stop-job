import asyncio
import base64
import importlib
import os
import sqlite3
from datetime import timedelta

import httpx
import pytest


def _fresh_app(monkeypatch, tmp_path, name: str):
    monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", f"sqlite:///{tmp_path / name}")

    from backend.app import config

    config.get_settings.cache_clear()
    import backend.app.db as db
    import backend.app.main as main

    db = importlib.reload(db)
    main = importlib.reload(main)
    db.init_db()
    return main.app


def _write_context_fixture(root):
    (root / "toolkit/job-pipeline/cards").mkdir(parents=True)
    (root / "README.md").write_text("# Entry\n", encoding="utf-8")
    (root / "toolkit/24-job-search-decision-rules.md").write_text(
        "# Rules\n\n> Updated: 2026-07-19\n",
        encoding="utf-8",
    )
    (root / "toolkit/job-pipeline/PROFILE.md").write_text("# Profile\n", encoding="utf-8")
    (root / "toolkit/23-job-pipeline.md").write_text("# Board\n", encoding="utf-8")
    (root / "toolkit/job-pipeline/cards/acme-seo.md").write_text("# Acme SEO\n", encoding="utf-8")


async def _client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


def test_context_status_endpoint_is_read_only_and_hides_absolute_path(monkeypatch, tmp_path):
    context_root = tmp_path / "personal-context"
    _write_context_fixture(context_root)
    monkeypatch.setenv("JOB_ONE_STOP_CONTEXT_REPO_PATH", str(context_root))
    app = _fresh_app(monkeypatch, tmp_path, "context-status.sqlite3")

    async def scenario():
        async for client in _client(app):
            response = await client.get("/api/context/status")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["configured"] is True
            assert payload["available"] is True
            assert "card_count" not in payload
            assert "decision_rules_updated" not in payload
            assert str(context_root) not in response.text

    asyncio.run(scenario())


def test_decision_chat_persists_job_thread_and_falls_back_to_rules(monkeypatch, tmp_path):
    context_root = tmp_path / "personal-context"
    _write_context_fixture(context_root)
    monkeypatch.setenv("JOB_ONE_STOP_CONTEXT_REPO_PATH", str(context_root))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    app = _fresh_app(monkeypatch, tmp_path, "decision-chat.sqlite3")

    async def scenario():
        async for client in _client(app):
            profile = await client.put(
                "/api/profile",
                json={
                    "target_titles": "SEO运营",
                    "target_cities": "示例市",
                    "salary_min_k": 8,
                    "dealbreakers": "单休",
                },
            )
            assert profile.status_code == 200, profile.text
            job = (
                await client.post(
                    "/api/jobs",
                    json={
                        "title": "SEO运营",
                        "company_name": "示例科技",
                        "salary_text": "4-5K",
                        "city": "示例市",
                        "description": "负责官网 SEO，单休",
                    },
                )
            ).json()

            created = await client.post("/api/chat/threads", json={"kind": "job", "job_id": job["id"]})
            assert created.status_code == 200, created.text
            thread = created.json()
            reused = await client.post("/api/chat/threads", json={"kind": "job", "job_id": job["id"]})
            assert reused.json()["id"] == thread["id"]
            assert reused.json()["reused"] is True

            renamed = await client.patch(
                f"/api/chat/threads/{thread['id']}",
                json={"title": "示例科技沟通判断"},
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["title"] == "示例科技沟通判断"

            invalid_title = await client.patch(
                f"/api/chat/threads/{thread['id']}",
                json={"title": "   "},
            )
            assert invalid_title.status_code == 422

            reply = await client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "这个岗位值得继续聊吗？"},
            )
            assert reply.status_code == 200, reply.text
            payload = reply.json()
            assert payload["ai_used"] is False
            assert payload["analysis"]["priority"] == "D"
            assert payload["analysis"]["next_action"] == "放弃"
            assert any(item["status"] == "fail" for item in payload["analysis"]["rule_checks"])
            assert payload["analysis_run"]["rules_version"] == "2026-07-19"

            detail = await client.get(f"/api/chat/threads/{thread['id']}")
            assert detail.status_code == 200, detail.text
            assert [item["role"] for item in detail.json()["messages"]] == ["user", "assistant"]
            listed = await client.get("/api/chat/threads")
            assert listed.json()[0]["message_count"] == 2

    asyncio.run(scenario())


def test_decision_chat_can_refine_with_configured_ai_without_overriding_rule_failure(monkeypatch, tmp_path):
    context_root = tmp_path / "personal-context"
    _write_context_fixture(context_root)
    monkeypatch.setenv("JOB_ONE_STOP_CONTEXT_REPO_PATH", str(context_root))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    app = _fresh_app(monkeypatch, tmp_path, "decision-chat-ai.sqlite3")

    # 分析核心已下沉到 services/decision_reply（与 Telegram 追问共用），打桩目标随之下移。
    import backend.app.services.decision_reply as decision_reply

    def fake_analysis(**_kwargs):
        return {
            "summary": "模型结合上下文给出的简短判断。",
            "priority": "A",
            "direction": "核心优先",
            "next_action": "继续沟通",
            "action_text": "先确认唯一关键条件。",
            "reply_draft": "你好，方便补充一下岗位的核心目标吗？",
        }

    monkeypatch.setattr(decision_reply, "analyze_decision_chat_llm", fake_analysis)

    async def scenario():
        async for client in _client(app):
            thread = (await client.post("/api/chat/threads", json={"kind": "general"})).json()
            reply = await client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "我拿不准下一步应该先确认什么。"},
            )
            assert reply.status_code == 200, reply.text
            payload = reply.json()
            assert payload["ai_used"] is True
            assert payload["analysis"]["summary"] == "模型结合上下文给出的简短判断。"
            assert payload["analysis_run"]["status"] == "completed"
            assert payload["assistant_message"]["metadata_json"]["ai_used"] is True
            assert payload["assistant_message"]["metadata_json"]["run_status"] == "completed"

    asyncio.run(scenario())


def test_decision_chat_marks_fallback_when_ai_enabled_but_call_fails(monkeypatch, tmp_path):
    """AI 已启用但本次调用失败：run_status 应是 fallback，与「从没配 AI」的 rules_only 区分开。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    app = _fresh_app(monkeypatch, tmp_path, "decision-chat-fallback.sqlite3")

    # 分析核心已下沉到 services/decision_reply（与 Telegram 追问共用），打桩目标随之下移。
    import backend.app.services.decision_reply as decision_reply

    # 模拟坏 key / 端点不通：analyze 返回 None，端点应回退规则但标记 fallback。
    monkeypatch.setattr(decision_reply, "analyze_decision_chat_llm", lambda **_kwargs: None)

    async def scenario():
        async for client in _client(app):
            thread = (await client.post("/api/chat/threads", json={"kind": "general"})).json()
            reply = await client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "这个岗位值得继续聊吗？"},
            )
            assert reply.status_code == 200, reply.text
            payload = reply.json()
            assert payload["ai_used"] is False
            assert payload["analysis_run"]["status"] == "fallback"
            assert payload["assistant_message"]["metadata_json"]["run_status"] == "fallback"

    asyncio.run(scenario())


def test_decision_chat_use_ai_false_skips_model_call(monkeypatch, tmp_path):
    """聊天composer里「本条不用 AI」开关关闭 → payload.use_ai=False：即便全局 ai.enabled=true
    且 OPENAI_API_KEY 已配置，也不应调用模型一次；复用既有的「未启用/不可用」降级路径，
    run_status 落在既有的 rules_only 标记上，不新写分支。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    app = _fresh_app(monkeypatch, tmp_path, "decision-chat-use-ai-false.sqlite3")

    # 分析核心已下沉到 services/decision_reply（与 Telegram 追问共用），打桩目标随之下移。
    import backend.app.services.decision_reply as decision_reply

    calls: list[dict] = []

    def fake_analysis(**kwargs):
        calls.append(kwargs)
        return {
            "summary": "不应该被调用到。",
            "priority": "A",
            "direction": "核心优先",
            "next_action": "继续沟通",
            "action_text": "先确认唯一关键条件。",
            "reply_draft": "你好。",
        }

    monkeypatch.setattr(decision_reply, "analyze_decision_chat_llm", fake_analysis)

    async def scenario():
        async for client in _client(app):
            thread = (await client.post("/api/chat/threads", json={"kind": "general"})).json()
            reply = await client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "这个岗位值得继续聊吗？", "use_ai": False},
            )
            assert reply.status_code == 200, reply.text
            payload = reply.json()
            assert calls == []  # 零调用
            assert payload["ai_used"] is False
            assert payload["analysis_run"]["status"] == "rules_only"
            assert payload["analysis_run"]["provider"] == "rules"
            assert payload["assistant_message"]["metadata_json"]["run_status"] == "rules_only"
            assert payload["assistant_message"]["metadata_json"]["ai_used"] is False

            # 默认(不传 use_ai)应该仍然是走 AI 的现状行为，证明这不是全局开关被误改。
            second = await client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "默认应该还是会调用模型。"},
            )
            assert second.status_code == 200, second.text
            assert len(calls) == 1
            assert second.json()["ai_used"] is True

    asyncio.run(scenario())


def test_profile_weights_roundtrip_and_used_by_scoring(monkeypatch, tmp_path):
    """审计发现：score_job() 实际读的是 UserProfile.weights（scoring.py），不是 config.yaml 的
    scoring.weights —— 后者只在首次建画像时当一次性种子默认值，改了不影响之后的评分。
    权重可定制因此必须走 PUT /api/profile：这里验证保存 → 读回 → 真正影响评分三件事。"""
    app = _fresh_app(monkeypatch, tmp_path, "profile-weights-roundtrip.sqlite3")

    async def scenario():
        async for client in _client(app):
            job = (
                await client.post(
                    "/api/jobs",
                    json={"title": "SEO专员", "company_name": "示例科技", "city": "上海"},
                )
            ).json()

            updated = await client.put(
                "/api/profile",
                json={
                    "weights": {
                        "role_match": 100,
                        "salary_city": 0,
                        "growth": 0,
                        "stability": 0,
                        "reputation": 0,
                        "commute_rest": 0,
                        "interview_roi": 0,
                    }
                },
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["weights"]["role_match"] == 100

            fetched = await client.get("/api/profile")
            assert fetched.json()["weights"]["role_match"] == 100

            score = await client.post(f"/api/jobs/{job['id']}/score")
            assert score.status_code == 200, score.text
            dims = score.json()["details"]["dimensions"]
            # 其它维度权重清零：不论命中比例多少，分数必须是 0——直接证明权重被读取并生效。
            assert dims["salary_city"]["score"] == 0
            assert dims["growth"]["score"] == 0
            assert dims["stability"]["score"] == 0
            assert dims["role_match"]["weight"] == 100

    asyncio.run(scenario())


def test_profile_weights_rejects_invalid_payload(monkeypatch, tmp_path):
    """PUT /api/profile 的 weights 校验复用 _validate_weights，和 config.yaml 那条口径一致：
    未知维度、合计超 100 都要拒绝，不静默接受垃圾权重。"""
    app = _fresh_app(monkeypatch, tmp_path, "profile-weights-invalid.sqlite3")

    async def scenario():
        async for client in _client(app):
            unknown = await client.put("/api/profile", json={"weights": {"unknown_dim": 10}})
            assert unknown.status_code == 400
            assert "未知维度" in unknown.text

            too_high = await client.put(
                "/api/profile",
                json={"weights": {"role_match": 80, "salary_city": 30}},
            )
            assert too_high.status_code == 400
            assert "合计不能超过 100" in too_high.text

    asyncio.run(scenario())


def test_job_list_and_score_endpoint_expose_score_dimensions(monkeypatch, tmp_path):
    """评分透明化不是新功能——score_job() 早就把逐维度 score/weight/note 存进 FitScore.details，
    API 也一直原样透出。这里断言它端到端确实可见：评分端点和岗位列表都能读到完整分解。"""
    app = _fresh_app(monkeypatch, tmp_path, "score-dimensions.sqlite3")

    async def scenario():
        async for client in _client(app):
            job = (
                await client.post(
                    "/api/jobs",
                    json={"title": "SEO专员", "company_name": "示例科技", "city": "上海"},
                )
            ).json()
            scored = await client.post(f"/api/jobs/{job['id']}/score")
            assert scored.status_code == 200, scored.text
            dims = scored.json()["details"]["dimensions"]
            expected_keys = {
                "role_match",
                "salary_city",
                "growth",
                "stability",
                "reputation",
                "commute_rest",
                "interview_roi",
            }
            assert expected_keys <= set(dims)
            for key in expected_keys:
                assert set(dims[key]) >= {"score", "weight", "note"}

            listed = (await client.get("/api/jobs")).json()
            assert listed[0]["latest_score"]["details"]["dimensions"] == dims

    asyncio.run(scenario())


def test_chat_context_preview_shows_what_would_be_sent_without_leaking_path(monkeypatch, tmp_path):
    """发送前预览：列出启用 AI 时会发送的固定上下文，且不返回宿主机绝对路径。"""
    context_root = tmp_path / "personal-context"
    _write_context_fixture(context_root)
    monkeypatch.setenv("JOB_ONE_STOP_CONTEXT_REPO_PATH", str(context_root))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "preview-model")
    app = _fresh_app(monkeypatch, tmp_path, "chat-context-preview.sqlite3")

    async def scenario():
        async for client in _client(app):
            thread = (await client.post("/api/chat/threads", json={"kind": "general"})).json()
            await client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "这个岗位值得继续聊吗？"},
            )
            preview = await client.get(f"/api/chat/threads/{thread['id']}/context-preview")
            assert preview.status_code == 200, preview.text
            payload = preview.json()

            assert payload["ai_enabled"] is True
            assert payload["model"] == "preview-model"
            section_keys = {section["key"] for section in payload["sections"]}
            assert {"decision_rules", "profile", "board"} <= section_keys
            assert payload["context_chars_total"] > 0
            # 已发过 1 轮：user + assistant 两条进入最近对话预览。
            assert payload["conversation_count"] == 2
            assert str(context_root) not in preview.text

    asyncio.run(scenario())


def test_decision_chat_stores_supported_screenshot_locally(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f'general:\n  data_dir: "{tmp_path / "chat-data"}"\n', encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    app = _fresh_app(monkeypatch, tmp_path, "decision-chat-image.sqlite3")
    image_data_url = "data:image/png;base64,iVBORw0KGgo="

    async def scenario():
        async for client in _client(app):
            thread = (await client.post("/api/chat/threads", json={"kind": "general"})).json()
            reply = await client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "请分析截图", "image_data_url": image_data_url, "image_name": "jd.png"},
            )
            assert reply.status_code == 200, reply.text
            payload = reply.json()
            attachment = payload["user_message"]["metadata_json"]["attachment"]
            assert "data_url" not in attachment
            assert attachment["name"] == "jd.png"
            stored = await client.get(f"/api/chat/attachments/{attachment['id']}")
            assert stored.status_code == 200
            assert stored.content == base64.b64decode(image_data_url.split(",", 1)[1])
            image_check = next(item for item in payload["analysis"]["rule_checks"] if item["code"] == "image_evidence")
            assert image_check["status"] == "unknown"

            unsupported = await client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "bad", "image_data_url": "data:image/svg+xml;base64,PHN2Zz4="},
            )
            assert unsupported.status_code == 422

    asyncio.run(scenario())


def test_save_chat_image_rejects_unknown_mime_defensively(monkeypatch, tmp_path):
    """Schema 校验已挡掉非 PNG/JPEG/WebP，但 `_save_chat_image` 自身也要防御性拒绝而不是 KeyError。"""
    app = _fresh_app(monkeypatch, tmp_path, "chat-image-mime.sqlite3")

    import backend.app.main as main
    from dataclasses import replace
    from fastapi import HTTPException

    main.settings = replace(main.settings, data_dir=tmp_path / "chat-data")

    try:
        main._save_chat_image("data:image/gif;base64,R0lGODlh", None)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "PNG" in exc.detail and "JPEG" in exc.detail and "WebP" in exc.detail
    else:
        raise AssertionError("expected HTTPException for unsupported mime type")


def test_delete_chat_thread_removes_messages_and_attachment_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f'general:\n  data_dir: "{tmp_path / "chat-data"}"\n', encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    app = _fresh_app(monkeypatch, tmp_path, "chat-delete.sqlite3")
    image_data_url = "data:image/png;base64,iVBORw0KGgo="

    import backend.app.main as main

    async def scenario():
        async for client in _client(app):
            thread = (await client.post("/api/chat/threads", json={"kind": "general"})).json()
            reply = await client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "请看这张截图", "image_data_url": image_data_url, "image_name": "shot.png"},
            )
            assert reply.status_code == 200, reply.text
            attachment = reply.json()["user_message"]["metadata_json"]["attachment"]
            attachment_path = main.settings.data_dir / "chat_attachments" / attachment["id"]
            assert attachment_path.is_file()

            delete_resp = await client.delete(f"/api/chat/threads/{thread['id']}")
            assert delete_resp.status_code == 200, delete_resp.text
            assert delete_resp.json() == {"deleted": True, "id": thread["id"]}

            missing_thread = await client.get(f"/api/chat/threads/{thread['id']}")
            assert missing_thread.status_code == 404
            assert not attachment_path.exists()

    asyncio.run(scenario())

    conn = sqlite3.connect(str(tmp_path / "chat-delete.sqlite3"))
    try:
        count = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        assert count == 0
        count = conn.execute("SELECT COUNT(*) FROM chat_threads").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_delete_chat_thread_404_for_missing_thread(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "chat-delete-404.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.delete("/api/chat/threads/999999")
            assert resp.status_code == 404

    asyncio.run(scenario())


def test_delete_chat_thread_does_not_affect_other_threads(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f'general:\n  data_dir: "{tmp_path / "chat-data"}"\n', encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    app = _fresh_app(monkeypatch, tmp_path, "chat-delete-isolated.sqlite3")
    image_data_url = "data:image/png;base64,iVBORw0KGgo="

    import backend.app.main as main

    async def scenario():
        async for client in _client(app):
            thread_a = (await client.post("/api/chat/threads", json={"kind": "general"})).json()
            thread_b = (await client.post("/api/chat/threads", json={"kind": "general"})).json()

            reply_a = await client.post(
                f"/api/chat/threads/{thread_a['id']}/messages",
                json={"content": "线程 A 截图", "image_data_url": image_data_url, "image_name": "a.png"},
            )
            reply_b = await client.post(
                f"/api/chat/threads/{thread_b['id']}/messages",
                json={"content": "线程 B 截图", "image_data_url": image_data_url, "image_name": "b.png"},
            )
            attachment_a = reply_a.json()["user_message"]["metadata_json"]["attachment"]
            attachment_b = reply_b.json()["user_message"]["metadata_json"]["attachment"]
            path_a = main.settings.data_dir / "chat_attachments" / attachment_a["id"]
            path_b = main.settings.data_dir / "chat_attachments" / attachment_b["id"]
            assert path_a.is_file() and path_b.is_file()

            delete_resp = await client.delete(f"/api/chat/threads/{thread_a['id']}")
            assert delete_resp.status_code == 200, delete_resp.text

            assert not path_a.exists()
            assert path_b.is_file()

            still_there = await client.get(f"/api/chat/threads/{thread_b['id']}")
            assert still_there.status_code == 200
            assert len(still_there.json()["messages"]) == 2  # user + assistant

    asyncio.run(scenario())


def test_batch_delete_chat_threads_removes_messages_and_attachments(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f'general:\n  data_dir: "{tmp_path / "chat-data"}"\n', encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    app = _fresh_app(monkeypatch, tmp_path, "chat-batch-delete.sqlite3")
    image_data_url = "data:image/png;base64,iVBORw0KGgo="

    import backend.app.main as main

    async def scenario():
        async for client in _client(app):
            thread_a = (await client.post("/api/chat/threads", json={"kind": "general"})).json()
            thread_b = (await client.post("/api/chat/threads", json={"kind": "general"})).json()
            thread_c = (await client.post("/api/chat/threads", json={"kind": "general"})).json()

            reply_a = await client.post(
                f"/api/chat/threads/{thread_a['id']}/messages",
                json={"content": "线程 A 截图", "image_data_url": image_data_url, "image_name": "a.png"},
            )
            attachment_a = reply_a.json()["user_message"]["metadata_json"]["attachment"]
            path_a = main.settings.data_dir / "chat_attachments" / attachment_a["id"]
            assert path_a.is_file()

            resp = await client.post(
                "/api/chat/threads/batch-delete",
                json={"ids": [thread_a["id"], thread_b["id"], thread_c["id"]]},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["deleted"] == 3
            assert {r["id"]: r["ok"] for r in body["results"]} == {
                thread_a["id"]: True,
                thread_b["id"]: True,
                thread_c["id"]: True,
            }

            assert not path_a.exists()
            for thread in (thread_a, thread_b, thread_c):
                missing = await client.get(f"/api/chat/threads/{thread['id']}")
                assert missing.status_code == 404

    asyncio.run(scenario())

    conn = sqlite3.connect(str(tmp_path / "chat-batch-delete.sqlite3"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM chat_threads").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
    finally:
        conn.close()


def test_batch_delete_chat_threads_reports_not_found_without_failing_others(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "chat-batch-delete-partial.sqlite3")

    async def scenario():
        async for client in _client(app):
            thread = (await client.post("/api/chat/threads", json={"kind": "general"})).json()

            resp = await client.post(
                "/api/chat/threads/batch-delete",
                json={"ids": [thread["id"], 999999]},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["deleted"] == 1
            results_by_id = {r["id"]: r for r in body["results"]}
            assert results_by_id[thread["id"]]["ok"] is True
            assert results_by_id[999999] == {"id": 999999, "ok": False, "reason": "not_found"}

            missing = await client.get(f"/api/chat/threads/{thread['id']}")
            assert missing.status_code == 404

    asyncio.run(scenario())


def test_batch_delete_chat_threads_rejects_over_limit(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "chat-batch-delete-over-limit.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.post(
                "/api/chat/threads/batch-delete",
                json={"ids": list(range(1, 102))},
            )
            assert resp.status_code == 400

    asyncio.run(scenario())


def _create_pre_resume_schema_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            source VARCHAR NOT NULL,
            external_id VARCHAR NOT NULL,
            url VARCHAR,
            title VARCHAR NOT NULL,
            company_name VARCHAR NOT NULL,
            city VARCHAR,
            area VARCHAR,
            collected_at DATETIME,
            created_at DATETIME
        )
        """
    )
    conn.execute(
        """
        INSERT INTO jobs (
            id, source, external_id, url, title, company_name, city, area, collected_at, created_at
        ) VALUES (
            1, 'manual', 'abc', 'https://example.com/job', 'SEO', 'Acme', 'ExampleCity', 'NorthDistrict',
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE user_profile (
            id INTEGER PRIMARY KEY,
            target_titles VARCHAR NOT NULL,
            target_cities VARCHAR NOT NULL,
            salary_min_k FLOAT NOT NULL,
            salary_max_k FLOAT NOT NULL,
            skills VARCHAR NOT NULL,
            strengths VARCHAR NOT NULL,
            dealbreakers VARCHAR NOT NULL,
            commute_preferences VARCHAR NOT NULL,
            weights JSON NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO user_profile (
            id, target_titles, target_cities, salary_min_k, salary_max_k, skills, strengths,
            dealbreakers, commute_preferences, weights, updated_at
        ) VALUES (
            1, 'SEO', 'ExampleCity', 8, 20, 'SEO,Analytics', '增长复盘',
            '单休', '示例市优先', '{}', CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE interview_prep (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            jd_summary TEXT NOT NULL,
            skill_gaps TEXT NOT NULL,
            resume_points TEXT NOT NULL,
            star_stories TEXT NOT NULL,
            questions_to_ask TEXT NOT NULL,
            communication_draft TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO interview_prep (
            id, job_id, jd_summary, skill_gaps, resume_points, star_stories,
            questions_to_ask, communication_draft, created_at, updated_at
        ) VALUES (
            1, 1, 'summary', 'gaps', 'points', 'star', 'questions', 'draft',
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


def test_init_db_auto_migrates_profile_experience_fields(monkeypatch, tmp_path):
    db_path = tmp_path / "old-startup.sqlite3"
    _create_pre_resume_schema_db(db_path)
    monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", f"sqlite:///{db_path}")

    from backend.app import config

    config.get_settings.cache_clear()
    import backend.app.db as db

    db = importlib.reload(db)
    db.init_db()

    conn = sqlite3.connect(db_path)
    table_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    profile_columns = {row[1] for row in conn.execute("PRAGMA table_info(user_profile)").fetchall()}
    prep_columns = {row[1] for row in conn.execute("PRAGMA table_info(interview_prep)").fetchall()}
    work_experience = conn.execute("SELECT work_experience FROM user_profile WHERE id = 1").fetchone()[0]
    core_pitch, tailored_resume = conn.execute(
        "SELECT core_pitch, tailored_resume FROM interview_prep WHERE id = 1"
    ).fetchone()
    conn.close()

    assert "work_experience" in profile_columns
    assert "core_pitch" in prep_columns
    assert "tailored_resume" in prep_columns
    assert {"chat_threads", "chat_messages", "analysis_runs"} <= table_names
    assert work_experience
    assert core_pitch == ""
    assert tailored_resume == ""


def test_job_score_and_prep_flow(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "test.sqlite3")

    async def scenario():
        async for client in _client(app):
            created = await client.post(
                "/api/jobs",
                json={
                    "title": "SEO运营",
                    "company_name": "示例市增长科技",
                    "salary_text": "8-12K",
                    "city": "示例市",
                    "area": "示例区",
                    "skills": "SEO,独立站,数据分析",
                },
            )
            assert created.status_code == 200, created.text
            job = created.json()

            score = await client.post(f"/api/jobs/{job['id']}/score")
            assert score.status_code == 200, score.text
            assert score.json()["total"] > 0

            profile = await client.put(
                "/api/profile",
                json={
                    "skills": "SEO,独立站,Google Analytics,内容增长",
                    "strengths": "从关键词机会到页面改版和数据复盘能闭环推进",
                    "work_experience": "负责独立站 SEO 项目，完成关键词分层、内容规划和落地页优化，带来询盘增长。",
                },
            )
            assert profile.status_code == 200, profile.text

            prep = await client.post(f"/api/jobs/{job['id']}/prep?ai=false")
            assert prep.status_code == 200, prep.text
            prep_payload = prep.json()
            assert "独立站 SEO 项目" in prep_payload["core_pitch"]
            assert "独立站 SEO 项目" in prep_payload["communication_draft"]
            assert "定制简历" in prep_payload["tailored_resume"]

            drafts = (await client.get("/api/drafts")).json()
            draft_kinds = {draft["kind"] for draft in drafts}
            assert {"communication_draft", "core_pitch", "tailored_resume"} <= draft_kinds

            jobs = (await client.get("/api/jobs")).json()
            assert jobs[0]["latest_score"]["total"] == score.json()["total"]

    asyncio.run(scenario())


def test_ai_status_endpoint_hides_secret_values(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  enabled: true\n  provider: openai_compatible\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    app = _fresh_app(monkeypatch, tmp_path, "ai-status.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.get("/api/ai/status")
            assert resp.status_code == 200, resp.text
            payload = resp.json()

            assert payload["enabled_in_config"] is True
            assert payload["available"] is True
            assert payload["api_key_configured"] is True
            assert payload["base_url_configured"] is True
            assert payload["model"] == "test-model"
            assert "sk-test-secret" not in resp.text
            assert "example.invalid" not in resp.text

    asyncio.run(scenario())


def test_ai_status_endpoint_reports_per_provider_key_booleans_only(monkeypatch, tmp_path):
    """每张 provider 卡靠 provider_keys[env_name] 显示「已配置/未配置」；响应绝不含 key 值。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "ai:\n"
        "  enabled: true\n"
        "  provider: openai_compatible\n"
        "  providers:\n"
        "    - label: 阿里 Qwen 视觉\n"
        "      api_key_env: PROVIDER_A_KEY\n"
        "      base_url: https://a.example.invalid/v1\n"
        "      model: qwen-vl-max\n"
        "    - label: 备用\n"
        "      api_key_env: PROVIDER_B_KEY\n"
        "      model: gpt-4o-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("PROVIDER_A_KEY", "sk-provider-a-secret")
    monkeypatch.delenv("PROVIDER_B_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = _fresh_app(monkeypatch, tmp_path, "ai-status-provider-keys.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.get("/api/ai/status")
            assert resp.status_code == 200, resp.text
            payload = resp.json()

            assert payload["provider_keys"] == {"PROVIDER_A_KEY": True, "PROVIDER_B_KEY": False}
            assert "sk-provider-a-secret" not in resp.text

    asyncio.run(scenario())


def test_ai_test_endpoint_reports_success_without_leaking_secret(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  enabled: true\n  provider: openai_compatible\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    app = _fresh_app(monkeypatch, tmp_path, "ai-test-ok.sqlite3")

    import backend.app.services.ai as ai

    monkeypatch.setattr(ai, "_client", lambda: object())
    monkeypatch.setattr(ai, "_chat", lambda *args, **kwargs: "ok")

    async def scenario():
        async for client in _client(app):
            resp = await client.post("/api/ai/test")
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["ok"] is True
            assert payload["stage"] == "call"
            assert payload["model"] == "test-model"
            assert isinstance(payload["latency_ms"], int)
            assert "sk-test-secret" not in resp.text

    asyncio.run(scenario())


def test_ai_test_endpoint_classifies_auth_failure(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  enabled: true\n  provider: openai_compatible\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad-key")
    app = _fresh_app(monkeypatch, tmp_path, "ai-test-401.sqlite3")

    import backend.app.services.ai as ai

    class _AuthError(Exception):
        status_code = 401

    def _raise(*args, **kwargs):
        raise _AuthError("unauthorized")

    monkeypatch.setattr(ai, "_client", lambda: object())
    monkeypatch.setattr(ai, "_chat", _raise)

    async def scenario():
        async for client in _client(app):
            resp = await client.post("/api/ai/test")
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["ok"] is False
            assert payload["stage"] == "call"
            assert "401" in payload["reason"]
            assert "sk-bad-key" not in resp.text

    asyncio.run(scenario())


def test_ai_test_endpoint_flags_config_disabled(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    app = _fresh_app(monkeypatch, tmp_path, "ai-test-disabled.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.post("/api/ai/test")
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload["ok"] is False
            assert payload["stage"] == "config"
            assert "sk-test-secret" not in resp.text

    asyncio.run(scenario())


def _prep_ai_credentials_env(monkeypatch, tmp_path):
    """给 /api/ai/credentials 测试隔离一个假 PROJECT_DIR，绝不碰真实仓库的 .env。"""
    from backend.app import config

    env_home = tmp_path / "fake-project-dir"
    env_home.mkdir()
    monkeypatch.setattr(config, "PROJECT_DIR", env_home)
    return env_home / ".env"


def test_ai_credentials_endpoint_writes_env_and_applies_immediately(monkeypatch, tmp_path):
    env_path = _prep_ai_credentials_env(monkeypatch, tmp_path)
    app = _fresh_app(monkeypatch, tmp_path, "ai-credentials-write.sqlite3")
    monkeypatch.delenv("MY_TEST_KEY", raising=False)

    async def scenario():
        async for client in _client(app):
            resp = await client.post(
                "/api/ai/credentials", json={"env_name": "MY_TEST_KEY", "value": "sk-abc123"}
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            assert payload == {"ok": True, "env_name": "MY_TEST_KEY"}
            assert "sk-abc123" not in resp.text

    asyncio.run(scenario())

    assert env_path.read_text(encoding="utf-8").strip() == "MY_TEST_KEY=sk-abc123"
    # 即时生效：不重启进程也能读到新值。
    assert os.getenv("MY_TEST_KEY") == "sk-abc123"


def test_ai_credentials_endpoint_replaces_existing_line_without_duplicating(monkeypatch, tmp_path):
    env_path = _prep_ai_credentials_env(monkeypatch, tmp_path)
    env_path.write_text("FOO=bar\nMY_TEST_KEY=old-value\nBAZ=qux\n", encoding="utf-8")
    app = _fresh_app(monkeypatch, tmp_path, "ai-credentials-replace.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.post(
                "/api/ai/credentials", json={"env_name": "MY_TEST_KEY", "value": "sk-new-value"}
            )
            assert resp.status_code == 200, resp.text
            assert "sk-new-value" not in resp.text
            assert "old-value" not in resp.text

    asyncio.run(scenario())

    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert lines == ["FOO=bar", "MY_TEST_KEY=sk-new-value", "BAZ=qux"]
    assert sum(1 for line in lines if line.startswith("MY_TEST_KEY=")) == 1


@pytest.mark.parametrize(
    "env_name",
    ["my_test_key", "MY-TEST-KEY", "../ETC_PASSWD", "1LEADING_DIGIT", "MY TEST KEY", ""],
)
def test_ai_credentials_endpoint_rejects_invalid_env_name(monkeypatch, tmp_path, env_name):
    env_path = _prep_ai_credentials_env(monkeypatch, tmp_path)
    app = _fresh_app(monkeypatch, tmp_path, "ai-credentials-bad-name.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.post(
                "/api/ai/credentials", json={"env_name": env_name, "value": "sk-should-not-be-written"}
            )
            assert resp.status_code == 400, resp.text
            assert "sk-should-not-be-written" not in resp.text

    asyncio.run(scenario())

    assert not env_path.exists()


@pytest.mark.parametrize(
    "value",
    ["contains\nnewline", "contains\rcarriage", "contains\x00null", "非ASCII密钥值", "   "],
)
def test_ai_credentials_endpoint_rejects_dangerous_value(monkeypatch, tmp_path, value):
    env_path = _prep_ai_credentials_env(monkeypatch, tmp_path)
    app = _fresh_app(monkeypatch, tmp_path, "ai-credentials-bad-value.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.post("/api/ai/credentials", json={"env_name": "MY_TEST_KEY", "value": value})
            assert resp.status_code == 400, resp.text
            assert value not in resp.text

    asyncio.run(scenario())

    assert not env_path.exists()


def test_config_endpoint_updates_yaml_and_rejects_secrets(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "opencli:\n  path: 'C:\\\\Path\\\\To\\\\opencli.cmd'\n  boss_cmd: ['opencli', 'boss']\n"
        "ai:\n  enabled: false\n  provider: openai_compatible\n"
        "  api_key: sk-config-secret\n"
        "bebee:\n  enabled: true\n  role_urls: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    app = _fresh_app(monkeypatch, tmp_path, "config-ui.sqlite3")

    async def scenario():
        async for client in _client(app):
            initial = await client.get("/api/config")
            assert initial.status_code == 200, initial.text
            assert initial.json()["config"]["ai"]["enabled"] is False
            assert "sk-config-secret" not in initial.text
            assert "api_key" not in initial.json()["config"]["ai"]
            assert "path" not in initial.json()["config"]["opencli"]

            rejected = await client.put("/api/config", json={"config": {"ai": {"api_key": "sk-test-secret"}}})
            assert rejected.status_code == 400
            assert "sk-test-secret" not in config_path.read_text(encoding="utf-8")

            updated = await client.put(
                "/api/config",
                json={
                    "config": {
                        "ai": {"enabled": True, "provider": "openai_compatible"},
                        "bebee": {
                            "enabled": False,
                            "source_label": "beBee",
                            "role_urls": ["https://example.com/jobs"],
                        },
                    }
                },
            )
            assert updated.status_code == 200, updated.text
            payload = updated.json()
            assert payload["config"]["ai"]["enabled"] is True
            assert payload["config"]["bebee"]["enabled"] is False
            assert payload["env"]["openai_api_key_configured"] is False

            ai_status = (await client.get("/api/ai/status")).json()
            assert ai_status["enabled_in_config"] is True
            saved = config_path.read_text(encoding="utf-8")
            assert "https://example.com/jobs" in saved
            assert "sk-config-secret" not in saved
            assert "Path\\\\To\\\\opencli" not in saved

    asyncio.run(scenario())


def test_config_endpoint_allows_provider_env_names_but_rejects_literal_keys(monkeypatch, tmp_path):
    """`ai.providers` 的 `*_env` 字段存的是环境变量名，不是密钥本身，应该放行；
    但同一批 providers 里混进字面量密钥（`api_key`）仍要被拦截，且不落盘。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  enabled: false\n  provider: openai_compatible\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    app = _fresh_app(monkeypatch, tmp_path, "config-providers.sqlite3")

    async def scenario():
        async for client in _client(app):
            accepted = await client.put(
                "/api/config",
                json={
                    "config": {
                        "ai": {
                            "enabled": True,
                            "provider": "openai_compatible",
                            "providers": [
                                {
                                    "api_key_env": "DASHSCOPE_API_KEY",
                                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                    "model": "qwen-vl-max",
                                },
                                {
                                    "api_key_env": "OPENAI_API_KEY",
                                    "base_url_env": "OPENAI_BASE_URL",
                                    "model_env": "OPENAI_MODEL",
                                },
                            ],
                        }
                    }
                },
            )
            assert accepted.status_code == 200, accepted.text
            saved_providers = accepted.json()["config"]["ai"]["providers"]
            assert saved_providers[0]["api_key_env"] == "DASHSCOPE_API_KEY"
            assert saved_providers[0]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
            assert saved_providers[1]["model_env"] == "OPENAI_MODEL"

            reread = await client.get("/api/config")
            assert reread.status_code == 200, reread.text
            reread_providers = reread.json()["config"]["ai"]["providers"]
            assert reread_providers[0]["api_key_env"] == "DASHSCOPE_API_KEY"
            assert reread_providers[1]["base_url_env"] == "OPENAI_BASE_URL"

            saved_yaml = config_path.read_text(encoding="utf-8")
            assert "DASHSCOPE_API_KEY" in saved_yaml
            assert "api_key_env" in saved_yaml

            rejected = await client.put(
                "/api/config",
                json={
                    "config": {
                        "ai": {
                            "enabled": True,
                            "provider": "openai_compatible",
                            "providers": [
                                {"api_key": "sk-real-secret", "base_url": "https://example.com/v1", "model": "m"},
                            ],
                        }
                    }
                },
            )
            assert rejected.status_code == 400
            assert "sk-real-secret" not in config_path.read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_invalid_config_is_reported_and_can_be_repaired(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  enabled: true\n    bad-indent: true\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    app = _fresh_app(monkeypatch, tmp_path, "bad-config.sqlite3")

    async def scenario():
        async for client in _client(app):
            health = await client.get("/api/health")
            assert health.status_code == 200, health.text
            assert health.json()["config_error"] is True

            config_resp = await client.get("/api/config")
            assert config_resp.status_code == 200, config_resp.text
            assert "YAML" in config_resp.json()["config_error"]

            ready = await client.get("/api/ready")
            assert ready.status_code == 503, ready.text
            payload = ready.json()
            assert payload["status"] == "error"
            assert any(check["name"] == "config_file" and check["status"] == "error" for check in payload["checks"])

            repaired = await client.put(
                "/api/config",
                json={"config": {"ai": {"enabled": False, "provider": "openai_compatible"}}},
            )
            assert repaired.status_code == 200, repaired.text
            assert repaired.json()["config_error"] is None
            assert "bad-indent" not in config_path.read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_error_response_includes_request_id(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "error-envelope.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.patch(
                "/api/jobs/999999",
                headers={"x-request-id": "test-request-123"},
                json={"status": "researching"},
            )
            assert resp.status_code == 404
            payload = resp.json()
            assert payload["error"]["code"] == "not_found"
            assert payload["error"]["message"] == "Job not found"
            assert payload["error"]["request_id"] == "test-request-123"
            assert resp.headers["x-request-id"] == "test-request-123"

    asyncio.run(scenario())


def test_ready_endpoint_reports_deployment_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_ONE_STOP_OPENCLI_SERVER_ENABLED", "false")
    app = _fresh_app(monkeypatch, tmp_path, "ready.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.get("/api/diagnostics/deployment")
            assert resp.status_code == 200, resp.text
            payload = resp.json()
            checks = {check["name"]: check for check in payload["checks"]}

            assert payload["status"] in {"ok", "degraded"}
            assert checks["database"]["status"] == "ok"
            assert checks["cloud_runtime"]["port"] == 8000
            assert checks["upload_limit"]["max_upload_mb"] == 20
            assert checks["source:boss"]["source_status"] == "host_import_required"

            ready = await client.get("/api/ready")
            assert ready.status_code == 200, ready.text

    asyncio.run(scenario())


def test_config_endpoint_rejects_invalid_scoring_weights_without_writing(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "scoring:\n"
        "  weights:\n"
        "    role_match: 25\n"
        "    salary_city: 15\n"
        "    growth: 15\n"
        "    stability: 15\n"
        "    reputation: 10\n"
        "    commute_rest: 10\n"
        "    interview_roi: 10\n",
        encoding="utf-8",
    )
    original = config_path.read_text(encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    app = _fresh_app(monkeypatch, tmp_path, "config-scoring.sqlite3")

    async def scenario():
        async for client in _client(app):
            too_high = await client.put(
                "/api/config",
                json={
                    "config": {
                        "scoring": {
                            "weights": {
                                "role_match": 80,
                                "salary_city": 15,
                                "growth": 15,
                                "stability": 15,
                                "reputation": 10,
                                "commute_rest": 10,
                                "interview_roi": 10,
                            }
                        }
                    }
                },
            )
            assert too_high.status_code == 400
            assert "合计不能超过 100" in too_high.text
            assert config_path.read_text(encoding="utf-8") == original

            unknown = await client.put("/api/config", json={"config": {"scoring": {"weights": {"unknown": 1}}}})
            assert unknown.status_code == 400
            assert "未知维度" in unknown.text
            assert config_path.read_text(encoding="utf-8") == original

            negative = await client.put("/api/config", json={"config": {"scoring": {"weights": {"role_match": -1}}}})
            assert negative.status_code == 400
            assert "非负数字" in negative.text
            assert config_path.read_text(encoding="utf-8") == original

            not_number = await client.put("/api/config", json={"config": {"scoring": {"weights": {"role_match": "25"}}}})
            assert not_number.status_code == 400
            assert "非负数字" in not_number.text
            assert config_path.read_text(encoding="utf-8") == original

    asyncio.run(scenario())


def test_upload_reader_rejects_empty_and_oversized_files(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_ONE_STOP_MAX_UPLOAD_MB", "1")
    _fresh_app(monkeypatch, tmp_path, "import-limits.sqlite3")

    import backend.app.main as main

    class FakeUpload:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def read(self, _size: int) -> bytes:
            return self.chunks.pop(0) if self.chunks else b""

    async def scenario():
        empty = FakeUpload([])
        with pytest.raises(main.HTTPException) as empty_exc:
            await main._read_upload_file(empty)
        assert empty_exc.value.status_code == 400
        assert "上传文件为空" in str(empty_exc.value.detail)

        too_large = FakeUpload([b"x" * 1024 * 1024, b"x"])
        with pytest.raises(main.HTTPException) as large_exc:
            await main._read_upload_file(too_large)
        assert large_exc.value.status_code == 413
        assert "上传文件过大" in str(large_exc.value.detail)

    asyncio.run(scenario())


def test_sources_endpoint_exposes_generic_sources(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "opencli:\n"
        "  path: 'C:\\\\Path\\\\To\\\\opencli.cmd'\n"
        "  boss_cmd: ['opencli', 'boss', 'search', 'SEO', '--format', 'csv']\n"
        "job_sources:\n"
        "  zhilian:\n"
        "    enabled: false\n"
        "    label: 智联招聘\n"
        "    command: ['opencli', 'zhilian', 'search', 'SEO', '--format', 'csv']\n"
        "bebee:\n"
        "  enabled: true\n"
        "  source_label: beBee\n"
        "  role_urls: ['https://example.com/jobs']\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    app = _fresh_app(monkeypatch, tmp_path, "sources.sqlite3")
    from backend.app.services import sources as source_services

    monkeypatch.setattr(
        source_services,
        "inspect_opencli",
        lambda *_args, **_kwargs: {
            "configured": False,
            "status": "not_found",
            "message": "OpenCLI 未找到",
        },
    )

    async def scenario():
        async for client in _client(app):
            resp = await client.get("/api/sources")
            assert resp.status_code == 200, resp.text
            sources = {item["key"]: item for item in resp.json()}

            assert {"boss", "bebee", "zhilian"} <= set(sources)
            assert sources["boss"]["kind"] == "opencli_csv"
            assert sources["boss"]["configured"] is False
            assert "opencli_path" not in sources["boss"]["config"]
            assert sources["boss"]["config"]["host_collection"]["script"] == "tools\\host_collect_boss.bat"
            assert "OpenCLI" in sources["boss"]["message"]
            assert sources["bebee"]["configured"] is True
            assert sources["zhilian"]["enabled"] is False

            rejected = await client.post("/api/sources/zhilian/collect")
            assert rejected.status_code == 403

    asyncio.run(scenario())


def test_opencli_sources_require_host_import_when_server_disabled(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "opencli:\n"
        "  path: opencli\n"
        "  boss_cmd: ['opencli', 'boss', 'search', 'SEO', '--format', 'csv']\n"
        "job_sources:\n"
        "  zhilian:\n"
        "    enabled: true\n"
        "    label: 智联招聘\n"
        "    command: ['opencli', 'zhilian', 'search', 'SEO', '--format', 'csv']\n"
        "bebee:\n"
        "  enabled: true\n"
        "  source_label: beBee\n"
        "  role_urls: ['https://example.com/jobs']\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("JOB_ONE_STOP_OPENCLI_SERVER_ENABLED", "false")
    app = _fresh_app(monkeypatch, tmp_path, "host-import.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.get("/api/sources")
            assert resp.status_code == 200, resp.text
            sources = {item["key"]: item for item in resp.json()}

            assert sources["boss"]["status"] == "host_import_required"
            assert sources["boss"]["configured"] is False
            assert sources["boss"]["doctor"]["runtime"] == "host"
            assert "opencli_path" not in sources["boss"]["config"]
            assert sources["boss"]["config"]["host_collection"]["script"] == "tools\\host_collect_boss.bat"
            assert sources["zhilian"]["status"] == "host_import_required"
            assert "宿主机" in sources["zhilian"]["message"]

            rejected = await client.post("/api/sources/boss/collect")
            assert rejected.status_code == 400
            assert "宿主机" in rejected.text

    asyncio.run(scenario())


def test_manual_create_and_file_import_share_source_links(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "source-links.sqlite3")

    async def scenario():
        async for client in _client(app):
            first = await client.post(
                "/api/jobs",
                json={
                    "title": "SEO运营",
                    "company_name": "示例市增长科技",
                    "salary_text": "8-12K",
                    "city": "示例市",
                    "area": "示例区",
                },
            )
            assert first.status_code == 200, first.text
            first_job = first.json()
            assert {link["source"] for link in first_job["source_links"]} == {"manual"}

            duplicate = await client.post(
                "/api/jobs",
                json={
                    "title": "SEO运营",
                    "company_name": "示例市增长科技",
                    "salary_text": "10-14K",
                    "city": "示例市",
                    "area": "示例区",
                },
            )
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json()["id"] == first_job["id"]

            csv_bytes = "title,company,salary,area\nSEO运营,示例市增长科技,9-13K,示例市·示例区\n".encode()
            imported = await client.post(
                "/api/jobs/import?source=导入文件",
                files={"file": ("jobs.csv", csv_bytes, "text/csv")},
            )
            assert imported.status_code == 200, imported.text
            assert imported.json() == {"fetched": 1, "created": 0, "updated": 1}

            jobs = (await client.get("/api/jobs")).json()
            assert len(jobs) == 1
            assert jobs[0]["salary_text"] == "9-13K"
            assert {link["source"] for link in jobs[0]["source_links"]} == {"manual", "导入文件"}

            imported_source_jobs = (await client.get("/api/jobs", params={"source": "导入文件"})).json()
            assert [job["id"] for job in imported_source_jobs] == [first_job["id"]]

    asyncio.run(scenario())


def test_file_import_accepts_multiple_files(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "multi-file-import.sqlite3")

    async def scenario():
        async for client in _client(app):
            first_csv = "title,company,salary,area\nSEO运营,示例市增长科技,9-13K,示例市·示例区\n".encode()
            second_csv = "title,company,salary,area\n外贸独立站运营,示例市跨境科技,8-12K,示例市·北区\n".encode()

            imported = await client.post(
                "/api/jobs/import?source=导入文件",
                files=[
                    ("file", ("first.csv", first_csv, "text/csv")),
                    ("file", ("second.csv", second_csv, "text/csv")),
                ],
            )

            assert imported.status_code == 200, imported.text
            assert imported.json() == {"fetched": 2, "created": 2, "updated": 0}

            jobs = (await client.get("/api/jobs")).json()
            assert {job["title"] for job in jobs} == {"SEO运营", "外贸独立站运营"}

    asyncio.run(scenario())


def test_xlsx_import_reads_all_sheets(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "multi-sheet-import.sqlite3")

    async def scenario():
        import pandas as pd
        from io import BytesIO

        async for client in _client(app):
            content = BytesIO()
            with pd.ExcelWriter(content) as writer:
                pd.DataFrame(
                    [{"title": "SEO运营", "company": "示例市增长科技", "salary": "9-13K", "area": "示例市·示例区"}]
                ).to_excel(writer, index=False, sheet_name="SEO")
                pd.DataFrame(
                    [{"title": "外贸独立站运营", "company": "示例市跨境科技", "salary": "8-12K", "area": "示例市·北区"}]
                ).to_excel(writer, index=False, sheet_name="独立站")

            imported = await client.post(
                "/api/jobs/import?source=导入文件",
                files={
                    "file": (
                        "jobs.xlsx",
                        content.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )

            assert imported.status_code == 200, imported.text
            assert imported.json() == {"fetched": 2, "created": 2, "updated": 0}

            jobs = (await client.get("/api/jobs")).json()
            assert {job["title"] for job in jobs} == {"SEO运营", "外贸独立站运营"}

    asyncio.run(scenario())


def test_file_import_can_keep_top_scored_jobs(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "import-keep-top.sqlite3")

    async def scenario():
        async for client in _client(app):
            rows = ["title,company,salary,area,skills"]
            for index in range(20):
                rows.append(f"外贸独立站运营{index},示例市跨境科技{index},8-12K,示例市·北区,独立站 SEO 外贸运营")
            for index in range(5):
                rows.append(f"信息流优化师{index},示例市广告科技{index},8-12K,示例市·北区,巨量引擎 广告投放")
            csv_bytes = ("\n".join(rows) + "\n").encode()

            imported = await client.post(
                "/api/jobs/import?source=导入文件&keep_top_scored=20",
                files={"file": ("jobs.csv", csv_bytes, "text/csv")},
            )

            assert imported.status_code == 200, imported.text
            assert imported.json() == {"fetched": 25, "created": 25, "updated": 0, "scored": 25, "kept": 20, "deleted": 5}

            jobs = (await client.get("/api/jobs")).json()
            assert len(jobs) == 20
            assert all("外贸独立站运营" in job["title"] for job in jobs)
            assert all(job["latest_score"] for job in jobs)

    asyncio.run(scenario())


def test_job_update_recomputes_editable_derived_fields(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "job-edit.sqlite3")

    async def scenario():
        async for client in _client(app):
            created = (
                await client.post(
                    "/api/jobs",
                    json={
                        "title": "SEO运营",
                        "company_name": "示例市增长科技",
                        "salary_text": "8-12K",
                        "city": "示例市",
                        "area": "示例区",
                        "recruiter": "张三·招聘经理",
                    },
                )
            ).json()

            updated = await client.patch(
                f"/api/jobs/{created['id']}",
                json={
                    "title": "外贸SEO运营",
                    "company_name": "示例市品牌出海科技",
                    "url": "https://example.com/jobs/seo",
                    "salary_text": "10-16K·13薪",
                    "city": "示例市",
                    "area": "北区",
                    "recruiter": "李四·HR",
                    "published_at": "2026-06-17",
                    "recruitment_status": "active",
                },
            )
            assert updated.status_code == 200, updated.text
            payload = updated.json()
            assert payload["company_name"] == "示例市品牌出海科技"
            assert payload["company_id"] != created["company_id"]
            assert payload["url"] == "https://example.com/jobs/seo"
            assert payload["salary_min_k"] == 10
            assert payload["salary_max_k"] == 16
            assert payload["annual_salary_w"] == 16.9
            assert payload["recruiter_title"] == "HR"
            assert payload["recruiter_is_hr"] is True
            assert payload["canonical_key"] != created["canonical_key"]
            assert payload["published_at"] == "2026-06-17"
            assert payload["recruitment_status"] == "active"

            rejected = await client.patch(f"/api/jobs/{created['id']}", json={"title": "  "})
            assert rejected.status_code == 400

    asyncio.run(scenario())


def test_bulk_job_update_status_and_favorite(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "bulk-jobs.sqlite3")

    async def scenario():
        async for client in _client(app):
            first = (
                await client.post(
                    "/api/jobs",
                    json={"title": "SEO运营", "company_name": "示例市增长科技", "salary_text": "8-12K", "city": "示例市"},
                )
            ).json()
            second = (
                await client.post(
                    "/api/jobs",
                    json={"title": "外贸SEO", "company_name": "示例市出海科技", "salary_text": "10-15K", "city": "示例市"},
                )
            ).json()

            empty = await client.patch("/api/jobs/bulk", json={"ids": [], "status": "researching"})
            assert empty.status_code == 200, empty.text
            assert empty.json() == {"updated": 0, "jobs": []}

            no_updates = await client.patch("/api/jobs/bulk", json={"ids": [first["id"]]})
            assert no_updates.status_code == 400

            updated = await client.patch(
                "/api/jobs/bulk",
                json={"ids": [first["id"], second["id"], 99999], "status": "researching", "favorite": True},
            )
            assert updated.status_code == 200, updated.text
            payload = updated.json()
            assert payload["updated"] == 2
            assert [job["id"] for job in payload["jobs"]] == [first["id"], second["id"]]
            assert all(job["status"] == "researching" for job in payload["jobs"])
            assert all(job["favorite"] is True for job in payload["jobs"])
            assert all(job["source_links"] for job in payload["jobs"])

            jobs = (await client.get("/api/jobs", params={"status": "researching"})).json()
            assert {job["id"] for job in jobs} == {first["id"], second["id"]}

    asyncio.run(scenario())


def test_wechat_collect_flow(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "wechat.sqlite3")

    from backend.app.services import wechat

    def fake_fetch(url, cfg=None):
        return wechat.ArticleFetch(
            url=url,
            ok=True,
            og_title="示例市招聘汇总",
            body_text=(
                "【SEO运营】\n薪资：8-12K\n工作地点：示例市\n岗位职责：负责SEO优化\n任职要求：熟悉SEO\n"
                "【外贸专员】\n薪资：6-9K\n工作地点：示例市\n岗位职责：海外推广\n任职要求：英语流利"
            ),
        )

    monkeypatch.setattr(wechat, "fetch_article", fake_fetch)

    async def scenario():
        async for client in _client(app):
            resp = await client.post(
                "/api/collect/wechat",
                json={"text": "看看这个 https://mp.weixin.qq.com/s/AbC123dEf456"},
            )
            assert resp.status_code == 200, resp.text
            run = resp.json()
            assert run["status"] == "success", run
            assert run["created_count"] >= 2  # 一篇文章拆出多个岗位

            jobs = (await client.get("/api/jobs", params={"source": "公众号"})).json()
            assert len(jobs) >= 2
            assert all(job["source"] == "公众号" for job in jobs)
            assert all("mp.weixin.qq.com" in (job.get("url") or "") for job in jobs)
            # external_id 不应碰撞，两个岗位都应入库
            assert len({job["external_id"] for job in jobs}) == len(jobs)

    asyncio.run(scenario())


def test_wechat_collect_rejects_blob_without_links(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "wechat2.sqlite3")

    async def scenario():
        async for client in _client(app):
            resp = await client.post("/api/collect/wechat", json={"text": "这里没有任何公众号链接"})
            assert resp.status_code == 400

    asyncio.run(scenario())


def test_bebee_collect_exposes_skipped_when_no_jobs(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "bebee-empty.sqlite3")

    from backend.app.services import bebee

    monkeypatch.setattr(bebee, "fetch_listing", lambda url, cfg=None: "<html><body>no structured data</body></html>")

    async def scenario():
        async for client in _client(app):
            resp = await client.post("/api/collect/bebee")
            assert resp.status_code == 200, resp.text
            run = resp.json()

            assert run["status"] == "success"
            assert run["fetched_count"] == 0
            assert run["created_count"] == 0
            assert run["updated_count"] == 0
            assert run["raw_config"]["jobs"] == 0
            assert run["raw_config"]["skipped"]
            assert "JobPosting" in run["raw_config"]["skipped"][0]["reason"] or "JS" in run["raw_config"]["skipped"][0]["reason"]

            runs = (await client.get("/api/collect/runs")).json()
            assert runs[0]["raw_config"]["skipped"] == run["raw_config"]["skipped"]

    asyncio.run(scenario())


def test_sprint_brief_scores_preps_and_tasks(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "sprint.sqlite3")

    async def scenario():
        async for client in _client(app):
            first = (
                await client.post(
                    "/api/jobs",
                    json={
                        "title": "独立站SEO增长",
                        "company_name": "示例市出海增长实验室",
                        "salary_text": "10-15K",
                        "city": "示例市",
                        "area": "北区",
                        "skills": "SEO,独立站,数据分析,内容增长",
                    },
                )
            ).json()
            second = (
                await client.post(
                    "/api/jobs",
                    json={
                        "title": "外贸SEO运营",
                        "company_name": "示例市品牌出海科技",
                        "salary_text": "8-12K",
                        "city": "示例市",
                        "area": "市北",
                        "skills": "SEO,外贸运营,Google Analytics",
                    },
                )
            ).json()

            resp = await client.post("/api/sprint/brief", params={"top_n": 100, "prep_n": 20})
            assert resp.status_code == 200, resp.text
            brief = resp.json()

            created_ids = {first["id"], second["id"]}
            top_ids = {job["id"] for job in brief["top_jobs"]}
            prepared_ids = {item["job"]["id"] for item in brief["prepared"]}
            task_job_ids = {task["job_id"] for task in brief["tasks_created"]}

            assert created_ids <= top_ids
            assert created_ids <= prepared_ids
            assert created_ids <= task_job_ids
            assert "今日求职冲刺包" in brief["markdown"]
            assert "示例市出海增长实验室" in brief["markdown"]

            repeated = await client.post("/api/sprint/brief", params={"top_n": 100, "prep_n": 20})
            assert repeated.status_code == 200, repeated.text
            assert repeated.json()["tasks_created"] == []

    asyncio.run(scenario())


def test_follow_up_task_update_and_delete(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "tasks.sqlite3")

    async def scenario():
        async for client in _client(app):
            created = await client.post(
                "/api/follow-ups",
                json={"title": "调研公司官网", "status": "todo", "due_date": "2026-06-09"},
            )
            assert created.status_code == 200, created.text
            task = created.json()

            updated = await client.patch(
                f"/api/follow-ups/{task['id']}",
                json={"title": "调研公司官网和招聘页", "status": "done", "due_date": "2026-06-10"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["title"] == "调研公司官网和招聘页"
            assert updated.json()["status"] == "done"
            assert updated.json()["due_date"] == "2026-06-10"

            deleted = await client.delete(f"/api/follow-ups/{task['id']}")
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {"deleted": True, "id": task["id"]}

            tasks = (await client.get("/api/follow-ups")).json()
            assert tasks == []

    asyncio.run(scenario())


def test_follow_up_task_create_dedupes_identical_pending_task(monkeypatch, tmp_path):
    """同一 job_id + 相同标题（去首尾空白）+ 状态未完成的待办重复创建两次：只应有一条，
    第二次返回已有记录并带 duplicate=True（复现用户实测「待办可无限重复加」的 bug）。"""
    app = _fresh_app(monkeypatch, tmp_path, "tasks-dedupe.sqlite3")

    async def scenario():
        async for client in _client(app):
            job = (
                await client.post("/api/jobs", json={"title": "数据分析师", "company_name": "示例科技", "city": "上海"})
            ).json()

            first = await client.post(
                "/api/follow-ups",
                json={"title": "  跟进面试安排  ", "job_id": job["id"]},
            )
            assert first.status_code == 200, first.text
            first_task = first.json()
            assert first_task.get("duplicate") is False

            second = await client.post(
                "/api/follow-ups",
                json={"title": "跟进面试安排", "job_id": job["id"]},
            )
            assert second.status_code == 200, second.text
            second_task = second.json()
            assert second_task["id"] == first_task["id"]
            assert second_task.get("duplicate") is True

            tasks = (await client.get("/api/follow-ups")).json()
            assert len(tasks) == 1

            # 标记完成后，同名待办应该允许再开一条新的跟进（不是同一件事的重复提交）。
            await client.patch(f"/api/follow-ups/{first_task['id']}", json={"status": "done"})
            third = await client.post(
                "/api/follow-ups",
                json={"title": "跟进面试安排", "job_id": job["id"]},
            )
            assert third.status_code == 200, third.text
            assert third.json().get("duplicate") is False
            assert third.json()["id"] != first_task["id"]

    asyncio.run(scenario())


def test_company_counts_and_validation_guards(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "company-counts.sqlite3")

    async def scenario():
        async for client in _client(app):
            first = (
                await client.post(
                    "/api/jobs",
                    json={"title": "SEO运营", "company_name": "示例市增长科技", "city": "示例市"},
                )
            ).json()
            second = (
                await client.post(
                    "/api/jobs",
                    json={"title": "外贸独立站运营", "company_name": "示例市增长科技", "city": "示例市"},
                )
            ).json()

            invalid_bulk = await client.patch("/api/jobs/bulk", json={"ids": [first["id"]], "status": "later"})
            assert invalid_bulk.status_code == 422, invalid_bulk.text

            invalid_task = await client.post("/api/follow-ups", json={"title": "回访", "status": "later"})
            assert invalid_task.status_code == 422, invalid_task.text

            companies = await client.get("/api/companies")
            assert companies.status_code == 200, companies.text
            company = companies.json()[0]
            assert company["jobs_count"] == 2
            assert company["evidence_count"] == 0

            invalid_company = await client.patch(f"/api/companies/{company['id']}", json={"risk_level": "urgent"})
            assert invalid_company.status_code == 422, invalid_company.text

            invalid_research = await client.post(
                f"/api/companies/{company['id']}/research",
                json={"source_type": "manual_note", "title": "脉脉", "summary": "反馈一般", "sentiment": "mixed"},
            )
            assert invalid_research.status_code == 422, invalid_research.text

            research = await client.post(
                f"/api/companies/{company['id']}/research",
                json={"source_type": "manual_note", "title": "官网", "summary": "业务在扩张", "sentiment": "positive"},
            )
            assert research.status_code == 200, research.text

            refreshed = await client.get("/api/companies")
            payload = refreshed.json()[0]
            assert payload["jobs_count"] == 2
            assert payload["evidence_count"] == 1
            assert second["company_id"] == first["company_id"] == payload["id"]

    asyncio.run(scenario())


def test_interview_log_crud_flow(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "interviews.sqlite3")

    async def scenario():
        async for client in _client(app):
            job = (
                await client.post(
                    "/api/jobs",
                    json={"title": "SEO运营", "company_name": "示例市增长科技", "city": "示例市"},
                )
            ).json()

            # 不存在的岗位 → 404，不静默
            missing = await client.post("/api/jobs/999999/interviews", json={"round": "一面"})
            assert missing.status_code == 404

            first = await client.post(
                f"/api/jobs/{job['id']}/interviews",
                json={
                    "round": "一面",
                    "interview_date": "2026-06-10",
                    "interviewer": "SEO主管",
                    "real_picture": "更偏独立站 SEO，有 GSC/GA4 数据",
                    "score_details": {"现金流": 20, "岗位匹配": 18, "业务闭环": 16, "团队资源": 12, "作息风险": 8, "成长价值": 8},
                    "opportunity_score": 82,
                    "conclusion": "重点推进",
                    "qa_review": "问了关键词研究和 GSC 数据",
                    "weaknesses": "Google Ads 经验偏弱",
                    "next_actions": "补一个 GSC 数据分析案例",
                    "follow_up": "当天发感谢+匹配点",
                },
            )
            assert first.status_code == 200, first.text
            first_log = first.json()
            assert first_log["opportunity_score"] == 82
            assert first_log["score_details"]["现金流"] == 20
            assert first_log["conclusion"] == "重点推进"

            second = await client.post(
                f"/api/jobs/{job['id']}/interviews",
                json={"round": "二面", "interview_date": "2026-06-12", "conclusion": "继续观察"},
            )
            assert second.status_code == 200, second.text
            second_log = second.json()

            # 该岗位下两轮，按面试日期降序（二面在前）→ 追溯
            job_logs = (await client.get(f"/api/jobs/{job['id']}/interviews")).json()
            assert [log["id"] for log in job_logs] == [second_log["id"], first_log["id"]]

            # 全局时间线包含这两条
            all_logs = (await client.get("/api/interviews")).json()
            assert {log["id"] for log in all_logs} >= {first_log["id"], second_log["id"]}

            # 迭代：改结论
            patched = await client.patch(f"/api/interviews/{first_log['id']}", json={"conclusion": "保底"})
            assert patched.status_code == 200, patched.text
            assert patched.json()["conclusion"] == "保底"
            assert patched.json()["opportunity_score"] == 82  # 未传的字段保持不变

            deleted = await client.delete(f"/api/interviews/{second_log['id']}")
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {"deleted": True, "id": second_log["id"]}

            remaining = (await client.get(f"/api/jobs/{job['id']}/interviews")).json()
            assert [log["id"] for log in remaining] == [first_log["id"]]

            # 删除不存在的复盘 → 404
            missing_delete = await client.delete("/api/interviews/999999")
            assert missing_delete.status_code == 404

    asyncio.run(scenario())


def test_prep_uses_ai_when_ready_and_respects_ai_false(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  enabled: true\n  provider: openai_compatible\n", encoding="utf-8")
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    app = _fresh_app(monkeypatch, tmp_path, "prep-ai.sqlite3")

    import backend.app.services.prep_ops as prep_ops

    def fake_tailor(context, base):
        # 模拟 LLM：只覆盖两个字段，其余沿用模板基线（验证逐键合并）。
        return {**base, "communication_draft": "AI定制：" + context["title"], "core_pitch": "AI定制pitch"}

    monkeypatch.setattr(prep_ops, "tailor_interview_prep_llm", fake_tailor)

    async def scenario():
        async for client in _client(app):
            job = (
                await client.post(
                    "/api/jobs",
                    json={"title": "独立站运营", "company_name": "示例公司", "city": "示例市", "skills": "独立站,SEO"},
                )
            ).json()

            ai_prep = (await client.post(f"/api/jobs/{job['id']}/prep")).json()
            assert ai_prep["communication_draft"] == "AI定制：独立站运营"
            assert ai_prep["core_pitch"] == "AI定制pitch"
            assert ai_prep["jd_summary"]  # 未被 AI 覆盖的字段仍是模板内容

            tpl_prep = (await client.post(f"/api/jobs/{job['id']}/prep", params={"ai": "false"})).json()
            assert not tpl_prep["communication_draft"].startswith("AI定制")

    asyncio.run(scenario())


def test_stale_follow_ups_endpoint_and_sprint_section(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "stale.sqlite3")

    import backend.app.db as db
    from sqlmodel import Session

    from backend.app.models import Job, utc_now

    async def scenario():
        async for client in _client(app):
            stale_job = (
                await client.post("/api/jobs", json={"title": "独立站运营", "company_name": "示例公司", "city": "示例市"})
            ).json()
            fresh_job = (
                await client.post("/api/jobs", json={"title": "SEO运营", "company_name": "B公司", "city": "示例市"})
            ).json()
            new_job = (
                await client.post("/api/jobs", json={"title": "内容运营", "company_name": "C公司", "city": "示例市"})
            ).json()

            for jid in (stale_job["id"], fresh_job["id"]):
                resp = await client.patch(f"/api/jobs/{jid}", json={"status": "interview"})
                assert resp.status_code == 200, resp.text

            # 把其中一个的状态变更时间回拨 10 天；另一个保持“刚变更”。
            with Session(db.engine) as session:
                job_row = session.get(Job, stale_job["id"])
                assert job_row.status_changed_at is not None  # 状态变更应写入时间戳
                job_row.status_changed_at = utc_now() - timedelta(days=10)
                session.add(job_row)
                session.commit()

            stale = (await client.get("/api/follow-ups/stale")).json()
            stale_ids = {item["job_id"] for item in stale}
            assert stale_job["id"] in stale_ids
            assert fresh_job["id"] not in stale_ids  # 刚变更，不算过期
            assert new_job["id"] not in stale_ids  # new 状态不纳入

            item = next(entry for entry in stale if entry["job_id"] == stale_job["id"])
            assert item["status"] == "interview"
            assert item["days"] >= 10

            brief = (await client.post("/api/sprint/brief")).json()
            assert stale_job["id"] in {entry["job_id"] for entry in brief["stale_jobs"]}
            assert "## 需跟进" in brief["markdown"]
            assert "示例公司" in brief["markdown"]

    asyncio.run(scenario())


def test_export_endpoints_cover_core_artifacts(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "exports.sqlite3")

    async def scenario():
        async for client in _client(app):
            job = (
                await client.post(
                    "/api/jobs",
                    json={
                        "title": "外贸独立站运营",
                        "company_name": "示例市远航科技",
                        "salary_text": "10-15K",
                        "city": "示例市",
                        "skills": "SEO,独立站,外贸,Google Analytics",
                    },
                )
            ).json()

            score = await client.post(f"/api/jobs/{job['id']}/score")
            assert score.status_code == 200, score.text

            prep = await client.post(f"/api/jobs/{job['id']}/prep?ai=false")
            assert prep.status_code == 200, prep.text

            task = await client.post(
                "/api/follow-ups",
                json={"title": "跟进远航岗位", "job_id": job["id"], "due_date": "2026-06-20"},
            )
            assert task.status_code == 200, task.text

            interview = await client.post(
                f"/api/jobs/{job['id']}/interviews",
                json={"round": "一面", "interview_date": "2026-06-18", "conclusion": "继续观察"},
            )
            assert interview.status_code == 200, interview.text

            jobs_export = await client.get("/api/exports/jobs?format=csv")
            assert jobs_export.status_code == 200, jobs_export.text
            assert "company_name,title,status" in jobs_export.text
            assert "示例市远航科技" in jobs_export.text

            # 导出已收敛为 jobs CSV + archive JSON；其余 kind 应明确 400，而不是静默支持。
            for kind in ["profile", "companies", "prep", "tasks", "interviews", "sprint"]:
                removed = await client.get(f"/api/exports/{kind}?format=markdown")
                assert removed.status_code == 400, f"{kind}: {removed.text}"

            archive_export = await client.get("/api/exports/archive?format=json")
            assert archive_export.status_code == 200, archive_export.text
            payload = archive_export.json()
            assert payload["schema_version"] == "0006_decision_chat"
            assert payload["jobs"][0]["company_name"] == "示例市远航科技"
            assert "application_events" in payload
            assert "chat_threads" in payload

    asyncio.run(scenario())


def test_application_events_drive_funnel_and_status(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path, "events.sqlite3")

    async def scenario():
        async for client in _client(app):
            job1 = (
                await client.post(
                    "/api/jobs",
                    json={"title": "SEO运营", "company_name": "甲公司", "city": "示例市", "skills": "SEO,独立站"},
                )
            ).json()
            job2 = (
                await client.post(
                    "/api/jobs",
                    json={"title": "外贸独立站建设维护与SEO推广", "company_name": "乙公司", "city": "示例市", "skills": "外贸,SEO,WordPress"},
                )
            ).json()

            for job_id in (job1["id"], job2["id"]):
                score = await client.post(f"/api/jobs/{job_id}/score")
                assert score.status_code == 200, score.text

            created_ids = []
            for job_id, event_type, event_date in [
                (job1["id"], "applied", "2026-06-10"),
                (job1["id"], "reply", "2026-06-12"),
                (job1["id"], "interview_invite", "2026-06-13"),
                (job2["id"], "applied", "2026-06-11"),
            ]:
                resp = await client.post(
                    f"/api/jobs/{job_id}/events",
                    json={"event_type": event_type, "event_date": event_date, "channel": "BOSS", "note": f"note-{event_type}"},
                )
                assert resp.status_code == 200, resp.text
                created_ids.append(resp.json()["id"])

            listed = await client.get(f"/api/jobs/{job1['id']}/events")
            assert listed.status_code == 200, listed.text
            events = listed.json()
            assert [item["event_type"] for item in events] == ["interview_invite", "reply", "applied"]

            job1_after = await client.get("/api/jobs")
            statuses = {item["id"]: item["status"] for item in job1_after.json()}
            assert statuses[job1["id"]] == "interview"
            assert statuses[job2["id"]] == "applied"

            funnel = await client.get("/api/analytics/funnel")
            assert funnel.status_code == 200, funnel.text
            summary = funnel.json()["summary"]
            assert summary["applied_jobs"] == 2
            assert summary["interview_jobs"] == 1
            assert summary["offer_jobs"] == 0

            deleted = await client.delete(f"/api/events/{created_ids[2]}")
            assert deleted.status_code == 200, deleted.text

            listed_after = await client.get(f"/api/jobs/{job1['id']}/events")
            assert listed_after.status_code == 200, listed_after.text
            assert [item["event_type"] for item in listed_after.json()] == ["reply", "applied"]

            jobs_after_delete = await client.get("/api/jobs")
            statuses_after_delete = {item["id"]: item["status"] for item in jobs_after_delete.json()}
            assert statuses_after_delete[job1["id"]] == "applied"

    asyncio.run(scenario())


def _digest_loop_main(monkeypatch, tmp_path):
    """把 main 重载到一套 tmp 配置上，返回 (main 模块, 状态文件路径)。

    日清单状态文件落在 `settings.data_dir`，默认就是真实的 `./data/job_one_stop`——
    用例必须先把 data_dir 指到 tmp，否则会覆盖机主真实的 daily_digest_state.json。
    """
    import yaml

    data_dir = tmp_path / "digest-data"
    config_path = tmp_path / "digest-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "general": {"data_dir": str(data_dir)},
                "telegram": {"enabled": False, "allowed_chat_id": "99"},
                "schedule": {"digest": {"enabled": True, "hour": 0, "minute": 0, "collect_first": True}},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", f"sqlite:///{tmp_path / 'digest-loop.sqlite3'}")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    from backend.app import config

    config.get_settings.cache_clear()
    import backend.app.db as db
    import backend.app.main as main

    db = importlib.reload(db)
    main = importlib.reload(main)
    db.init_db()
    return main, data_dir / "daily_digest_state.json"


def test_daily_digest_loop_retries_until_delivered_and_collects_once(monkeypatch, tmp_path):
    """发送失败不许标记「今天已发」，下个周期要重试；重试时不能再跑一遍晨间采集。

    现网踩过的两个坑：Telegram 不可达那天，`send_message` 吞异常返回 None，循环照旧写
    last_sent，当天清单被永久丢弃（本人一条没收到）；而采集若跟着发送一起重试，定时采集
    就破掉了「每日一次」的合规上限（CLAUDE.md §3.3）。顺带断言采集失败要写进推送正文——
    `run_source` 失败只置 SourceRun.failed 并返回，静默的结果是「今天没有合适岗位」。
    """
    import contextlib
    import json
    from datetime import date

    main, state_path = _digest_loop_main(monkeypatch, tmp_path)

    from backend.app.services import collect_ops, daily_digest, telegram

    collect_calls: list[str] = []
    sent_texts: list[str] = []
    delivery = {"ok": False}

    def fake_run_source(session, source_key):
        collect_calls.append(source_key)
        return {"status": "failed", "error": "全部关键词采集失败:\n  Browser Bridge extension not connected"}

    def fake_send_long(token, chat_id, text):
        sent_texts.append(text)
        return [11] if delivery["ok"] else [None]

    async def direct(func, *args, **kwargs):  # 绕开 AnyIO 线程池（本环境下会卡住，见 CLAUDE.md §4）
        return func(*args, **kwargs)

    monkeypatch.setattr(collect_ops, "run_source", fake_run_source)
    monkeypatch.setattr(daily_digest, "build_daily_digest", lambda session, today: {"digest_text": "今天要做的事"})
    monkeypatch.setattr(telegram, "send_long_message", fake_send_long)
    monkeypatch.setattr(main, "run_in_threadpool", direct)

    async def one_cycle():
        before = len(sent_texts)
        task = asyncio.create_task(main._daily_digest_loop())
        for _ in range(200):
            await asyncio.sleep(0.005)
            if len(sent_texts) > before:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    today = date.today().isoformat()

    asyncio.run(one_cycle())
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "last_sent" not in state  # 没送达就不能算已发，明早之前还有机会补
    assert state["last_collected"] == today  # 采集当天已做，重试不许再跑
    assert "Browser Bridge" in state["collect_note"]
    assert "⚠️ 今日晨间采集未成功" in sent_texts[0]  # 「今天没岗位」必须给出可见原因

    delivery["ok"] = True
    asyncio.run(one_cycle())
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_sent"] == today  # 这次真送出去了
    assert collect_calls == ["boss"]  # 全程只采集一次
    assert len(sent_texts) == 2
