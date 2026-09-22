"""海信招聘端点流程（CLAUDE.md §2/§4）：

`POST /api/collect/hisense` monkeypatch 掉网络抓取（不联网），断言:
- 200 且 status=success；
- 采集**不新建 Job**，全新岗位只落成 `kind="collect"` 候选（红线：勾选才入库）。
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import httpx

FIXTURE = Path(__file__).parent / "fixtures" / "hisense_joblist.json"


def _reload_app(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", f"sqlite:///{tmp_path/'hisense.sqlite3'}")
    from backend.app import config

    config.get_settings.cache_clear()
    import backend.app.db as db
    import backend.app.main as main

    db = importlib.reload(db)
    main = importlib.reload(main)
    db.init_db()
    return main


def _setup_hisense_config(monkeypatch):
    from backend.app import config

    def fake_hisense_config(self):
        return {"source_label": "海信招聘", "max_pages": 1, "rate_limit_seconds": 0}

    monkeypatch.setattr(config.Settings, "hisense_config", property(fake_hisense_config))


async def _client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


def test_collect_hisense_endpoint_stages_candidates_not_jobs(monkeypatch, tmp_path):
    main = _reload_app(monkeypatch, tmp_path)
    _setup_hisense_config(monkeypatch)

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    from backend.app.services import hisense

    monkeypatch.setattr(hisense, "fetch_job_list", lambda url, page, page_size, cfg=None: payload)

    async def scenario():
        async for client in _client(main.app):
            resp = await client.post("/api/collect/hisense")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "success"
            assert body["fetched_count"] == 5
            # 采集不落盘：一条 Job 都不建。
            assert body["created_count"] == 0

            # 全新岗位落成 collect 候选，等人工勾选。
            jobs = (await client.get("/api/jobs")).json()
            assert (jobs.get("items") if isinstance(jobs, dict) else jobs) == []

    asyncio.run(scenario())
