"""采集触发路由专属 helper（Phase R · R2）。

`run_collector` / `run_source` / `run_wechat_collection` / `latest_run_for_source` /
`source_status_payload`：原本堆在 main.py，只被 `routers/collect.py` 使用。这些函数会
调用 `upsert_job_records`——这是**用户主动触发的采集入库**（红线 §2 允许的路径，等价于
BOSS/beBee/公众号采集器一直以来的行为），不是 ingest 的自动入库；因此不得并入
`services/chat_ingest.py`（那个模块的绊线测试禁止引用 importer/upsert）。

纯逻辑、无 FastAPI app 依赖；只碰 session / models / services / config。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session, select

from ..config import get_settings
from ..models import SourceRun
from .ai import is_ai_available
from .collectors import WeChatPasteCollector
from .importer import upsert_job_records
from .sources import build_source_collector, get_source_definition, source_health, source_public_config


def latest_run_for_source(session: Session, source_label: str) -> SourceRun | None:
    return session.exec(
        select(SourceRun).where(SourceRun.source == source_label).order_by(SourceRun.started_at.desc())
    ).first()


def source_status_payload(session: Session, source) -> dict:
    health = source_health(source)
    latest = latest_run_for_source(session, source.label)
    return {
        "key": source.key,
        "label": source.label,
        "kind": source.kind,
        "enabled": source.enabled,
        "configured": health["configured"],
        "status": health["status"],
        "message": health["message"],
        "doctor": health.get("doctor"),
        "config": source_public_config(source),
        "latest_run": latest.model_dump() if latest else None,
    }


def run_collector(session: Session, source_label: str, collector, raw_config: dict | None = None) -> dict:
    """公用:跑一个配置驱动的采集器并记录一次 SourceRun(boss/beBee 等共用)。"""
    run = SourceRun(source=source_label, raw_config=raw_config or {})
    session.add(run)
    session.commit()
    session.refresh(run)
    try:
        records = collector.collect()
        result = upsert_job_records(session, records)
        run.status = "success"
        run.fetched_count = len(records)
        run.created_count = result["created"]
        run.updated_count = result["updated"]
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)
    finally:
        report = getattr(collector, "report", None)
        if report:
            run.raw_config = {**(raw_config or {}), **report}
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()
        session.refresh(run)
    return run.model_dump()


def run_source(session: Session, source_key: str) -> dict:
    settings = get_settings()
    source = get_source_definition(settings, source_key)
    if not source:
        raise HTTPException(status_code=404, detail=f"未知采集来源：{source_key}")
    health = source_health(source)
    if not source.enabled:
        raise HTTPException(status_code=403, detail=f"{source.label} 已禁用")
    if not health["configured"]:
        raise HTTPException(status_code=400, detail=health["message"])
    collector = build_source_collector(source)
    raw_config = {
        "source_key": source.key,
        "kind": source.kind,
        **source_public_config(source),
    }
    return run_collector(session, source.label, collector, raw_config)


def run_wechat_collection(session: Session, links: list[str], bodies: dict[str, str], source_label: str) -> dict:
    """公用：给定 mp.weixin 链接（+可选手动正文），跑采集器并记录一次 SourceRun。"""
    settings = get_settings()
    wechat_cfg = settings.wechat_config
    ai_cfg = settings.config.get("ai", {})
    ai_enabled = bool(ai_cfg.get("enabled")) and is_ai_available()
    fetch_cfg = wechat_cfg.get("fetch", {})

    run = SourceRun(source=source_label, raw_config={"input_links": len(links)})
    session.add(run)
    session.commit()
    session.refresh(run)

    collector: WeChatPasteCollector | None = None
    try:
        collector = WeChatPasteCollector(
            links=links,
            bodies=bodies,
            cfg=wechat_cfg,
            ai_enabled=ai_enabled,
            min_jobs=int(wechat_cfg.get("min_jobs_before_llm_fallback", 1)),
            rate_limit_seconds=float(fetch_cfg.get("rate_limit_seconds", 0) or 0),
            source=source_label,
        )
        records = collector.collect()
        result = upsert_job_records(session, records)
        run.status = "success"
        run.fetched_count = len(records)
        run.created_count = result["created"]
        run.updated_count = result["updated"]
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)
    finally:
        if collector is not None:
            run.raw_config = {"input_links": len(links), **collector.report}
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()
        session.refresh(run)

    return run.model_dump()
