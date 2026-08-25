"""采集触发路由专属 helper（Phase R · R2）。

`run_collector` / `run_source` / `run_wechat_collection` / `latest_run_for_source` /
`source_status_payload`：原本堆在 main.py，只被 `routers/collect.py` 使用。

**采集不再直接落盘**（人工初筛）：一次采集回来的记录分三路——
1. 区域白名单挡掉的（`collect_filter`）：只记数进 `SourceRun.raw_config`，不静默丢（§7）；
2. 已在岗位池的：照旧 `upsert_job_records` 刷新快照（薪资/在招状态/last_seen_at）。这是
   你早就筛过的岗位，刷新不等于新增噪音，`created` 恒为 0；
3. 全新的：进 `kind="collect"` 聊天线索当候选，等你在 Web 勾选「入库选中」才 upsert
   （复用 ingest 那套候选卡与 commit 端点）。

因此本模块仍会 import `upsert_job_records`——只用于第 2 路的刷新，属于红线 §2 允许的
用户主动触发路径；但**新岗位不再由采集直接建 Job**。也因此不得并入
`services/chat_ingest.py`（那个模块的绊线测试禁止引用 importer/upsert）。

纯逻辑、无 FastAPI app 依赖；只碰 session / models / services / config。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session, select

from ..candidates import CANDIDATE_PENDING, Candidate
from ..config import get_settings
from ..models import SourceRun
from .ai import is_ai_available
from .chat_ingest import _candidate_dedupe_key, persist_collect_candidates, recent_collect_candidate_keys
from .collect_filter import apply_area_filter, apply_score_gate
from .collectors import WeChatPasteCollector
from .importer import split_known_records, upsert_job_records
from .jobs import attach_candidate_application_packs, attach_candidate_scores
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


def _dedupe_fresh(records: list[dict], seen: dict[str, str]) -> tuple[list[dict], int]:
    """去掉「已经在待筛列表里 / 已被你跳过」的记录，同时抹平本批次内部的重复。

    `seen` 是 `recent_collect_candidate_keys` 的结果（判重 key → 候选状态）。
    返回 (保留的记录, 挡掉的条数)。没有判重 key 的记录一律保留——宁可多看一条，不漏。
    """
    kept: list[dict] = []
    batch_keys: set[str] = set()
    dropped = 0
    for record in records:
        key = _candidate_dedupe_key(record)
        if key and (key in seen or key in batch_keys):
            dropped += 1
            continue
        if key:
            batch_keys.add(key)
        kept.append(record)
    return kept, dropped


def _triage_records(session: Session, source_label: str, records: list[dict]) -> dict:
    """采集记录 → (刷新已知岗位 + 全新岗位进候选)，返回汇总报告。

    `run_collector` 与 `run_wechat_collection` 共用；报告直接并进 `SourceRun.raw_config`，
    也是手机回执的数据来源（见 `telegram.summarize_collect_run`）。

    漏斗必须逐级收窄（区域 → 已知/重复 → 评分闸门 → 人工勾选）：评分闸门放在**评分之后**，
    因为它筛的就是分数与硬阻断标记，而评分需要先有候选结构。挡掉的只记数不静默丢（§7）。
    """
    settings = get_settings()
    kept, area_report = apply_area_filter(records, settings.area_filter_config)

    known, fresh = split_known_records(session, kept)
    refresh = upsert_job_records(session, known) if known else {"created": 0, "updated": 0}

    fresh, already_pending = _dedupe_fresh(fresh, recent_collect_candidate_keys(session))
    candidates: list[Candidate] = [{**record, "status": CANDIDATE_PENDING, "job_id": None} for record in fresh]
    candidates = attach_candidate_scores(session, candidates)
    candidates, score_report = apply_score_gate(candidates, settings.score_gate_config)
    pack_limit = int(settings.automation_config.get("max_application_packs_per_day", 10) or 10)
    materials_prepared = attach_candidate_application_packs(session, candidates, limit=pack_limit)

    report = {
        "area_filter": area_report,
        "score_gate": score_report,
        "known_refreshed": len(known),
        "already_pending": already_pending,
        "pending": len(candidates),
        "materials_prepared": materials_prepared,
    }
    persisted = persist_collect_candidates(
        session,
        source_label,
        candidates,
        f"{collect_run_summary(len(records), report)}请在下方勾选要入库的项；未勾选的不会进岗位池。",
    )
    thread = persisted.get("thread")
    if isinstance(thread, dict):
        report["thread_id"] = thread.get("id")
    report["refresh"] = refresh
    return report


def collect_run_summary(fetched: int, report: dict) -> str:
    """一次采集的计数摘要（一句话，以句号结尾）。

    采集线索里的正文与手机回执共用同一份措辞——两处各写一遍，改了一处另一处必然漂移。
    调用方各自在后面接自己的行动指引（Web 说「在下方勾选」，手机说「打开 Web 勾选」）。
    """
    area = report.get("area_filter") if isinstance(report.get("area_filter"), dict) else {}
    gate = report.get("score_gate") if isinstance(report.get("score_gate"), dict) else {}
    parts = [f"本次采集 {fetched} 条"]
    if area.get("enabled") and area.get("filtered"):
        parts.append(f"区域过滤 {area['filtered']} 条")
    if report.get("known_refreshed"):
        parts.append(f"已在岗位池 {report['known_refreshed']} 条（已刷新）")
    if report.get("already_pending"):
        parts.append(f"已在待筛/已跳过 {report['already_pending']} 条")
    # 闸门挡掉的必须逐项报出来，否则「抓了 30 条只剩 4 条待筛」看着像丢数据（§7）。
    if gate.get("enabled"):
        if gate.get("hard_blocked"):
            parts.append(f"硬排除 {gate['hard_blocked']} 条")
        if gate.get("below_score"):
            parts.append(f"低于 {float(gate.get('min_score') or 0):g} 分 {gate['below_score']} 条")
        if gate.get("truncated"):
            parts.append(f"超出单次上限暂缓 {gate['truncated']} 条")
    parts.append(f"待筛 {int(report.get('pending') or 0)} 条")
    return "，".join(parts) + "。"


def run_collector(session: Session, source_label: str, collector, raw_config: dict | None = None) -> dict:
    """公用:跑一个配置驱动的采集器并记录一次 SourceRun(boss/beBee 等共用)。

    `created_count` 在初筛模式下恒为 0——新岗位进候选，不由采集直接建 Job；
    `updated_count` 是已在池中岗位的刷新条数。
    """
    run = SourceRun(source=source_label, raw_config=raw_config or {})
    session.add(run)
    session.commit()
    session.refresh(run)
    triage: dict = {}
    try:
        records = collector.collect()
        triage = _triage_records(session, source_label, records)
        run.status = "success"
        run.fetched_count = len(records)
        run.created_count = triage["refresh"]["created"]
        run.updated_count = triage["refresh"]["updated"]
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)
    finally:
        report = getattr(collector, "report", None)
        run.raw_config = {**(raw_config or {}), **(report or {}), **{k: v for k, v in triage.items() if k != "refresh"}}
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
    if source.key == "boss":
        raw_config["reach_plan"] = source.config.get("reach_plan", {})
    return run_collector(session, source.label, collector, raw_config)


def run_wechat_collection(session: Session, links: list[str], bodies: dict[str, str], source_label: str) -> dict:
    """公用：给定 mp.weixin 链接（+可选手动正文），跑采集器并记录一次 SourceRun。

    与 `run_collector` 同一套初筛口径：新岗位进候选等勾选，已在池中的照旧刷新。
    """
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
    triage: dict = {}
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
        triage = _triage_records(session, source_label, records)
        run.status = "success"
        run.fetched_count = len(records)
        run.created_count = triage["refresh"]["created"]
        run.updated_count = triage["refresh"]["updated"]
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)
    finally:
        run.raw_config = {
            "input_links": len(links),
            **(collector.report if collector is not None else {}),
            **{k: v for k, v in triage.items() if k != "refresh"},
        }
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()
        session.refresh(run)

    return run.model_dump()
