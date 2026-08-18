from __future__ import annotations

import logging
from collections import defaultdict

from sqlmodel import Session, select

from ..models import Company, FitScore, InterviewPrep, Job, JobSourceLink, ResearchItem

logger = logging.getLogger(__name__)


def latest_score_map(session: Session, job_ids: list[int]) -> dict[int, FitScore]:
    if not job_ids:
        return {}
    scores = session.exec(select(FitScore).where(FitScore.job_id.in_(job_ids)).order_by(FitScore.created_at.desc())).all()
    latest: dict[int, FitScore] = {}
    for score in scores:
        latest.setdefault(score.job_id, score)
    return latest


def latest_prep_map(session: Session, job_ids: list[int]) -> dict[int, InterviewPrep]:
    if not job_ids:
        return {}
    preps = session.exec(
        select(InterviewPrep).where(InterviewPrep.job_id.in_(job_ids)).order_by(InterviewPrep.created_at.desc())
    ).all()
    latest: dict[int, InterviewPrep] = {}
    for prep in preps:
        latest.setdefault(prep.job_id, prep)
    return latest


def source_links_map(session: Session, job_ids: list[int]) -> dict[int, list[JobSourceLink]]:
    if not job_ids:
        return {}
    links = session.exec(
        select(JobSourceLink).where(JobSourceLink.job_id.in_(job_ids)).order_by(JobSourceLink.first_seen_at.asc())
    ).all()
    grouped: dict[int, list[JobSourceLink]] = {}
    for link in links:
        grouped.setdefault(link.job_id, []).append(link)
    return grouped


def job_ids_by_canonical_key(session: Session, keys: list[str | None]) -> dict[str, int]:
    """按 canonical_key 批量查已入库岗位 id；用于 ingest 候选标注「已在岗位池」，只读不写。"""
    unique_keys = list({key for key in keys if key})
    if not unique_keys:
        return {}
    rows = session.exec(select(Job.id, Job.canonical_key).where(Job.canonical_key.in_(unique_keys))).all()
    result: dict[str, int] = {}
    for job_id, key in rows:
        if key and job_id is not None:
            result.setdefault(key, job_id)
    return result


def company_map(session: Session, company_ids: list[int]) -> dict[int, Company]:
    if not company_ids:
        return {}
    companies = session.exec(select(Company).where(Company.id.in_(company_ids))).all()
    return {company.id: company for company in companies if company.id is not None}


def research_items_map(session: Session, company_ids: list[int]) -> dict[int, list[ResearchItem]]:
    if not company_ids:
        return {}
    items = session.exec(
        select(ResearchItem).where(ResearchItem.company_id.in_(company_ids)).order_by(ResearchItem.captured_at.desc())
    ).all()
    grouped: dict[int, list[ResearchItem]] = defaultdict(list)
    for item in items:
        grouped[item.company_id].append(item)
    return dict(grouped)


def attach_candidate_scores(session: Session, candidates: list[dict]) -> list[dict]:
    """给候选算匹配分（写进纯 UI 字段 `score`）并按分降序返回。

    采集初筛用：一次采回十几条，没有分数就只能一行行读标题。分数口径与岗位池里的
    `FitScore` **完全一致**——同一个 `scoring.score_job` 纯函数，不新写第二套。

    只读：`score_job` 不碰 session；公司/调研只按名字做只读查询，绝不 `get_or_create`
    （候选还没决定入库，不该先建出公司行）。算不出分的候选 `score=None`，排在最后，
    绝不因为单条评分异常丢掉整批候选。
    """
    # 局部导入：queries → jobs、advice → queries，模块级引用会成环。
    from .advice import candidate_job
    from .queries import get_profile
    from .scoring import score_job

    if not candidates:
        return []
    profile = get_profile(session)
    names = {str(item.get("company_name") or "").strip() for item in candidates}
    names.discard("")
    companies = (
        {company.name: company for company in session.exec(select(Company).where(Company.name.in_(names))).all()}
        if names
        else {}
    )
    research = research_items_map(session, [company.id for company in companies.values() if company.id is not None])
    for candidate in candidates:
        company = companies.get(str(candidate.get("company_name") or "").strip())
        try:
            result = score_job(
                candidate_job(candidate),
                company,
                research.get(company.id, []) if company and company.id is not None else [],
                profile,
            )
            candidate["score"] = round(float(result.total), 1)
            candidate["hard_blocked"] = result.hard_blocked
        except Exception:  # noqa: BLE001 - 单条评分失败不影响其余候选，排序时沉底
            logger.warning("候选评分失败：%s", candidate.get("title"), exc_info=True)
            candidate["score"] = None
            candidate["hard_blocked"] = False
    return sorted(candidates, key=lambda item: item.get("score") if item.get("score") is not None else -1, reverse=True)


def job_payload(job: Job, latest: FitScore | None = None, source_links: list[JobSourceLink] | None = None) -> dict:
    return {
        **job.model_dump(),
        "latest_score": latest.model_dump() if latest else None,
        "source_links": [link.model_dump() for link in (source_links or [])],
    }
