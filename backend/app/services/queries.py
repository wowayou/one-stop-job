"""跨路由共享的 DB 查询 / 落盘 helper（Phase R · R2 枢纽）。

原本散在 main.py，被 jobs / profile / analytics / scores / prep / export / sprint 等多个
端点共用。下沉到这里后，拆分出的路由模块可直接 import，避免循环依赖 main。
纯逻辑、无 FastAPI app 依赖；只碰 session / models / services / config。
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response
from sqlmodel import Session, select

from ..config import get_settings
from ..models import (
    ApplicationEvent,
    FitScore,
    Job,
    JobSourceLink,
    UserProfile,
)
from .jobs import (
    company_map,
    job_payload,
    latest_score_map,
    research_items_map,
    source_links_map,
)
from .scoring import DEFAULT_WEIGHTS, score_job


def query_jobs(
    session: Session,
    *,
    search: str | None = None,
    status: str | None = None,
    source: str | None = None,
    favorite: bool | None = None,
    include_deleted: bool = False,
) -> tuple[list[Job], dict[int, list[JobSourceLink]]]:
    stmt = select(Job)
    if not include_deleted:
        stmt = stmt.where(Job.deleted_at.is_(None))
    stmt = stmt.order_by(Job.favorite.desc(), Job.collected_at.desc())
    jobs = session.exec(stmt).all()
    source_links = source_links_map(session, [job.id for job in jobs if job.id])
    if search:
        needle = search.lower()
        jobs = [job for job in jobs if needle in " ".join(filter(None, [job.title, job.company_name, job.skills, job.area])).lower()]
    if status:
        jobs = [job for job in jobs if job.status == status]
    if source:
        jobs = [
            job
            for job in jobs
            if job.source == source or any(link.source == source for link in source_links.get(job.id or 0, []))
        ]
    if favorite is not None:
        jobs = [job for job in jobs if job.favorite == favorite]
    return jobs, source_links


def job_response(session: Session, job: Job) -> dict:
    latest = latest_score_map(session, [job.id or 0]).get(job.id or 0)
    links = source_links_map(session, [job.id or 0]).get(job.id or 0, [])
    return job_payload(job, latest, links)


def get_profile(session: Session) -> UserProfile:
    profile = session.exec(select(UserProfile)).first()
    if not profile:
        profile = UserProfile(weights=get_settings().scoring_weights)
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def score_job_into_db(session: Session, job: Job, profile: UserProfile) -> FitScore:
    companies = company_map(session, [job.company_id] if job.company_id else [])
    research_by_company = research_items_map(session, [job.company_id] if job.company_id else [])
    company = companies.get(job.company_id or 0)
    result = score_job(job, company, research_by_company.get(job.company_id or 0, []), profile)
    score = FitScore(job_id=job.id or 0, total=result.total, hard_blocked=result.hard_blocked, details=result.details)
    session.add(score)
    session.commit()
    session.refresh(score)
    return score


def application_events(session: Session, *, job_id: int | None = None) -> list[ApplicationEvent]:
    statement = select(ApplicationEvent).order_by(ApplicationEvent.event_date.desc(), ApplicationEvent.created_at.desc())
    if job_id is not None:
        statement = statement.where(ApplicationEvent.job_id == job_id)
    return session.exec(statement).all()


def download_response(filename: str, content: str, media_type: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def validate_weights(weights: Any) -> None:
    """校验一份 {维度: 权重} 字典：维度必须认识、权重必须是非负有限数字、合计不能超过 100。

    两处调用：config.yaml 里 scoring.weights（首次建画像时的一次性种子默认值）和
    `PUT /api/profile` 的 weights（scoring.py 实际读取、真正影响评分的那份）。
    """
    if not isinstance(weights, dict):
        raise HTTPException(status_code=400, detail="scoring.weights must be an object")

    unknown = sorted(str(key) for key in weights if key not in DEFAULT_WEIGHTS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"评分权重包含未知维度：{', '.join(unknown)}")

    total = 0.0
    for key, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise HTTPException(status_code=400, detail=f"评分权重必须为非负数字：{key}")
        if float(value) < 0:
            raise HTTPException(status_code=400, detail=f"评分权重必须为非负数字：{key}")
        total += float(value)

    if total > 100:
        raise HTTPException(status_code=400, detail=f"评分权重合计不能超过 100，当前为 {total:g}")
