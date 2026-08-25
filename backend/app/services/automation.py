"""自动驾驶薄服务层：只复用现有采集/评分/候选链路。"""

from __future__ import annotations

from sqlmodel import Session, select

from ..config import get_settings
from ..models import Company, FitScore, Job, ResearchItem
from .chat_ingest import recent_collect_candidates
from .jobs import attach_candidate_application_packs, attach_candidate_scores, company_map, research_items_map
from .queries import get_profile
from .scoring import score_job_configured


AUTOMATION_MODES = {"manual", "autopilot"}


def automation_mode() -> str:
    value = str(get_settings().automation_config.get("mode") or "manual").strip().lower()
    return value if value in AUTOMATION_MODES else "manual"


def rescore_all_jobs(session: Session) -> int:
    jobs = session.exec(select(Job).where(Job.deleted_at.is_(None))).all()
    profile = get_profile(session)
    companies = company_map(session, [job.company_id for job in jobs if job.company_id])
    research = research_items_map(session, [job.company_id for job in jobs if job.company_id])
    count = 0
    for job in jobs:
        result = score_job_configured(
            job,
            companies.get(job.company_id or 0),
            research.get(job.company_id or 0, []),
            profile,
        )
        session.add(FitScore(job_id=job.id or 0, total=result.total, hard_blocked=result.hard_blocked, details=result.details))
        count += 1
    session.commit()
    return count


def rescore_pending_candidates(session: Session) -> int:
    """原位更新近期待筛候选；不恢复 skipped、不改候选状态或看板。"""
    from ..models import ChatMessage

    candidates = recent_collect_candidates(session)
    by_thread: dict[int, list[dict]] = {}
    for candidate in candidates:
        if str(candidate.get("status") or "pending") != "pending" or candidate.get("thread_id") is None:
            continue
        by_thread.setdefault(int(candidate["thread_id"]), []).append(candidate)

    updated = 0
    settings = get_settings()
    pack_limit = int(settings.automation_config.get("max_application_packs_per_day", 10) or 10)
    for thread_id in by_thread:
        # 一个采集线程可能因建议/补充产生多条 assistant 消息，但候选列表属于同一条
        # 线程状态。只重评最新消息，避免同一候选被重复计数或重复生成材料包。
        messages = session.exec(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc())
        ).all()
        for message in messages:
            stored = (message.metadata_json or {}).get("candidates")
            if not isinstance(stored, list):
                continue
            pending = [item for item in stored if isinstance(item, dict) and str(item.get("status") or "pending") == "pending"]
            attach_candidate_scores(session, pending)
            attach_candidate_application_packs(session, pending, limit=pack_limit)
            updated += len(pending)
            message.metadata_json = {**(message.metadata_json or {}), "candidates": stored}
            session.add(message)
            break
    session.commit()
    return updated
