from __future__ import annotations

from collections import defaultdict

from sqlmodel import Session, select

from ..models import Company, FitScore, InterviewPrep, Job, JobSourceLink, ResearchItem


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


def job_payload(job: Job, latest: FitScore | None = None, source_links: list[JobSourceLink] | None = None) -> dict:
    return {
        **job.model_dump(),
        "latest_score": latest.model_dump() if latest else None,
        "source_links": [link.model_dump() for link in (source_links or [])],
    }
