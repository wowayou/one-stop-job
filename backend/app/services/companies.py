from __future__ import annotations

from sqlalchemy import func
from sqlmodel import Session, select

from ..models import Company, Job, ResearchItem


def company_list_payload(session: Session, include_deleted: bool = False) -> list[dict]:
    stmt = select(Company)
    if not include_deleted:
        stmt = stmt.where(Company.deleted_at.is_(None))
    stmt = stmt.order_by(Company.updated_at.desc())
    companies = session.exec(stmt).all()
    company_ids = [company.id for company in companies if company.id is not None]
    if not company_ids:
        return []

    job_counts = {
        company_id: count
        for company_id, count in session.exec(
            select(Job.company_id, func.count(Job.id)).where(Job.company_id.in_(company_ids)).group_by(Job.company_id)
        ).all()
        if company_id is not None
    }
    evidence_counts = {
        company_id: count
        for company_id, count in session.exec(
            select(ResearchItem.company_id, func.count(ResearchItem.id))
            .where(ResearchItem.company_id.in_(company_ids))
            .group_by(ResearchItem.company_id)
        ).all()
        if company_id is not None
    }
    return [
        {
            **company.model_dump(),
            "jobs_count": int(job_counts.get(company.id, 0)),
            "evidence_count": int(evidence_counts.get(company.id, 0)),
        }
        for company in companies
    ]
