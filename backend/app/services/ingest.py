"""统一 ingest 分派器：一段文本/截图 → 抽链 → 分派采集器或 freeform LLM → **候选列表**。

设计要点（见 CLAUDE.md）：
- Telegram / HTTP 只是触发方式；真正的 `Job.source` 仍由各采集器决定（§8）。
- **默认不入库**。本模块只产出规范化候选 dict；是否写入 Job 表由用户在聊天里点「入库」决定。
- 原文/截图由调用方写入聊天记录保留；本模块不删任何原料。
- 仍只走 normalizer 产出规范化 dict，commit 时才走 importer（§6）。
"""

from __future__ import annotations

import logging

from sqlmodel import Session

from ..models import FitScore, Job, UserProfile
from . import bebee, wechat
from .collectors import BeBeeCollector, WeChatPasteCollector
from .jobs import company_map, research_items_map
from .normalizer import normalize_record
from .scoring import score_job

logger = logging.getLogger(__name__)


def classify_links(text: str) -> dict[str, list[str]]:
    """把一段文本里的链接按来源归类。返回 {"wechat": [...], "bebee": [...]}。"""
    return {
        "wechat": wechat.extract_mp_links(text),
        "bebee": bebee.extract_bebee_links(text),
    }


def score_job_ids(session: Session, job_ids: list[int], profile: UserProfile) -> int:
    """给已入库岗位评分（commit 路径用）。单条失败仅记日志，返回成功数。"""
    if not job_ids:
        return 0
    jobs = [session.get(Job, jid) for jid in job_ids]
    jobs = [job for job in jobs if job is not None]
    company_ids = [job.company_id for job in jobs if job.company_id]
    companies = company_map(session, company_ids)
    research_by_company = research_items_map(session, company_ids)

    scored = 0
    for job in jobs:
        try:
            company = companies.get(job.company_id or 0)
            research = research_by_company.get(job.company_id or 0, [])
            result = score_job(job, company, research, profile)
            session.add(
                FitScore(
                    job_id=job.id or 0,
                    total=result.total,
                    hard_blocked=result.hard_blocked,
                    details=result.details,
                )
            )
            scored += 1
        except Exception:  # noqa: BLE001
            logger.warning("ingest 评分失败 job_id=%s", job.id, exc_info=True)
    session.commit()
    return scored


def _residual_text(text: str, classified: dict[str, list[str]]) -> str:
    """去掉已被专用采集器认领的链接后剩下的文本。"""
    residual = text or ""
    for links in classified.values():
        for link in links:
            residual = residual.replace(link, " ")
    return residual.strip()


def _collect_records(collector) -> tuple[list[dict], dict]:
    """跑采集器，返回 (规范化 records, report)。失败时 records=[] 且 report 带 error。"""
    report = getattr(collector, "report", None) or {}
    try:
        records = collector.collect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest 采集失败 source=%s", getattr(collector, "source", "?"), exc_info=True)
        report = {**report, "error": str(exc), "skipped": list(report.get("skipped", [])) + [{"url": None, "reason": str(exc)}]}
        return [], report
    return list(records or []), dict(getattr(collector, "report", report) or report)


def run_ingest(
    text: str,
    *,
    wechat_cfg: dict,
    bebee_cfg: dict,
    ai_enabled: bool,
    image_data_url: str | None = None,
    manual_source: str = "manual",
) -> dict:
    """文本（可含链接）和/或截图 → 候选岗位列表（**不写库**）。

    返回：
    {
      candidates: [normalized_dict + status=pending ...],
      sources_report: [{source, jobs, skipped, error?}],
      links_total, unmatched, needs_ai
    }
    """
    text = text or ""
    classified = classify_links(text)
    links_total = sum(len(v) for v in classified.values())
    candidates: list[dict] = []
    sources_report: list[dict] = []
    seen_external: set[str] = set()

    def _append_records(source_label: str, records: list[dict], report: dict) -> None:
        added = 0
        for rec in records:
            ext = rec.get("external_id")
            if ext and ext in seen_external:
                continue
            if ext:
                seen_external.add(ext)
            candidates.append({**rec, "status": "pending", "job_id": None})
            added += 1
        sources_report.append(
            {
                "source": source_label,
                "jobs": added,
                "skipped": list(report.get("skipped", [])),
                **({"error": report["error"]} if report.get("error") else {}),
            }
        )

    wechat_links = classified["wechat"]
    if wechat_links:
        wechat_label = wechat_cfg.get("source_label", "公众号")
        fetch_cfg = wechat_cfg.get("fetch", {})
        collector = WeChatPasteCollector(
            links=wechat_links,
            bodies={},
            cfg=wechat_cfg,
            ai_enabled=ai_enabled,
            min_jobs=int(wechat_cfg.get("min_jobs_before_llm_fallback", 1)),
            rate_limit_seconds=float(fetch_cfg.get("rate_limit_seconds", 0) or 0),
            source=wechat_label,
        )
        records, report = _collect_records(collector)
        _append_records(wechat_label, records, report)

    bebee_links = classified["bebee"]
    if bebee_links:
        bebee_label = bebee_cfg.get("source_label", "beBee")
        collector = BeBeeCollector(urls=bebee_links, cfg=bebee_cfg, source=bebee_label)
        records, report = _collect_records(collector)
        _append_records(bebee_label, records, report)

    residual = _residual_text(text, classified)
    needs_ai = bool((residual or image_data_url) and not ai_enabled)
    if (residual or image_data_url) and ai_enabled:
        from .ai import extract_jobs_freeform

        try:
            raw_jobs = extract_jobs_freeform(residual, image_data_url)
        except Exception:  # noqa: BLE001
            logger.warning("freeform 抽取失败", exc_info=True)
            raw_jobs = []
            report = {"skipped": [{"url": None, "reason": "AI 抽取失败"}], "error": "AI 抽取失败"}
        else:
            report = {"skipped": []} if raw_jobs else {"skipped": [{"url": None, "reason": "AI 未从文本/截图中认出岗位"}]}
        records = []
        for raw in raw_jobs or []:
            if not str(raw.get("title") or "").strip():
                continue
            records.append(normalize_record(raw, source=manual_source))
        _append_records(manual_source, records, report)

    return {
        "candidates": candidates,
        "sources_report": sources_report,
        "links_total": links_total,
        "candidate_count": len(candidates),
        "unmatched": len(candidates) == 0,
        "needs_ai": needs_ai and len(candidates) == 0,
    }
