"""岗位路由（Phase R · R2）。

/api/jobs 系：列表/创建/批量更新/单条更新/导入/事件。从 main.py 原样搬出，行为逐字不变；
依赖仅来自 deps/models/schemas/services/config，不 import main。
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlmodel import select

from ..deps import SessionDep
from ..models import ApplicationEvent, Job, utc_now
from ..schemas import ApplicationEventCreate, JobBulkUpdate, JobCreate, JobUpdate
from ..services.importer import get_or_create_company, upsert_job_record, upsert_job_records_with_ids
from ..services.job_ops import (
    _records_from_uploads,
    recompute_job_status_from_events,
    score_and_prune_imported_jobs,
    soft_delete_jobs,
    restore_jobs,
    purge_jobs,
)
from ..services.jobs import job_payload, latest_score_map, source_links_map
from ..services.normalizer import canonical_job_key, normalize_record, parse_recruiter, parse_salary
from ..services.queries import application_events, job_response, query_jobs

router = APIRouter()


@router.get("/api/jobs")
async def list_jobs(
    session: SessionDep,
    search: str | None = None,
    status: str | None = None,
    source: str | None = None,
    favorite: bool | None = None,
) -> list[dict]:
    jobs, source_links = query_jobs(session, search=search, status=status, source=source, favorite=favorite)
    latest = latest_score_map(session, [job.id for job in jobs if job.id])
    return [job_payload(job, latest.get(job.id), source_links.get(job.id or 0, [])) for job in jobs]


@router.post("/api/jobs")
async def create_job(payload: JobCreate, session: SessionDep) -> dict:
    normalized = normalize_record(payload.model_dump(), source=payload.source)
    job = upsert_job_record(session, normalized)
    return job_response(session, job)


@router.patch("/api/jobs/bulk")
async def bulk_update_jobs(payload: JobBulkUpdate, session: SessionDep) -> dict:
    ids = list(dict.fromkeys(payload.ids))
    if not ids:
        return {"updated": 0, "jobs": []}

    updates = payload.model_dump(exclude={"ids"}, exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    jobs = session.exec(select(Job).where(Job.id.in_(ids))).all()
    for job in jobs:
        old_status = job.status
        for key, value in updates.items():
            setattr(job, key, value)
        job.updated_at = utc_now()
        if job.status != old_status:
            job.status_changed_at = job.updated_at
        session.add(job)
    session.commit()
    for job in jobs:
        session.refresh(job)

    order = {job_id: index for index, job_id in enumerate(ids)}
    jobs.sort(key=lambda job: order.get(job.id or 0, len(order)))
    latest = latest_score_map(session, [job.id for job in jobs if job.id])
    links = source_links_map(session, [job.id for job in jobs if job.id])
    return {
        "updated": len(jobs),
        "jobs": [job_payload(job, latest.get(job.id), links.get(job.id or 0, [])) for job in jobs],
    }


@router.patch("/api/jobs/{job_id}")
async def update_job(job_id: int, payload: JobUpdate, session: SessionDep) -> dict:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    updates = payload.model_dump(exclude_unset=True)
    old_status = job.status

    for key, value in list(updates.items()):
        if isinstance(value, str):
            updates[key] = value.strip()
    if updates.get("title") == "":
        raise HTTPException(status_code=400, detail="Job title cannot be empty")
    if updates.get("company_name") == "":
        raise HTTPException(status_code=400, detail="Company name cannot be empty")

    if "company_name" in updates and updates["company_name"]:
        company = get_or_create_company(session, updates["company_name"])
        job.company_id = company.id
        job.company_name = company.name
        updates.pop("company_name")
    for key, value in updates.items():
        setattr(job, key, value)
    job.updated_at = utc_now()
    if job.status != old_status:
        job.status_changed_at = job.updated_at
    if "salary_text" in updates:
        for key, value in parse_salary(job.salary_text).items():
            setattr(job, key, value)
    if "recruiter" in updates:
        for key, value in parse_recruiter(job.recruiter).items():
            setattr(job, key, value)
    if {"title", "company_name", "city", "area"} & set(payload.model_fields_set):
        job.canonical_key = canonical_job_key(job.title, job.company_name, job.city, job.area)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job_response(session, job)


@router.post("/api/jobs/import")
async def import_jobs(
    session: SessionDep,
    file: list[UploadFile] = File(...),
    source: str = Query(default="导入文件"),
    keep_top_scored: int | None = Query(default=None, ge=1, le=200),
) -> dict:
    records = await _records_from_uploads(file, source)
    result = upsert_job_records_with_ids(session, records)
    payload = {"fetched": len(records), "created": result["created"], "updated": result["updated"]}
    if keep_top_scored:
        payload.update(score_and_prune_imported_jobs(session, result["job_ids"], keep_top_scored))
    return payload


@router.get("/api/jobs/{job_id}/events")
async def list_job_events(job_id: int, session: SessionDep) -> list[ApplicationEvent]:
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return application_events(session, job_id=job_id)


@router.post("/api/jobs/{job_id}/events")
async def create_job_event(job_id: int, payload: ApplicationEventCreate, session: SessionDep) -> ApplicationEvent:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    event = ApplicationEvent(job_id=job_id, **payload.model_dump())
    session.add(event)
    session.commit()
    recompute_job_status_from_events(session, job)
    session.refresh(event)
    return event


@router.delete("/api/events/{event_id}")
async def delete_job_event(event_id: int, session: SessionDep) -> dict:
    event = session.get(ApplicationEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    job = session.get(Job, event.job_id)
    session.delete(event)
    session.commit()
    if job:
        recompute_job_status_from_events(session, job)
    return {"deleted": True, "id": event_id}


@router.delete("/api/jobs/{job_id}")
async def delete_job(job_id: int, session: SessionDep) -> dict:
    """软删除岗位（移入回收站），不删数据。可在回收站恢复或永久删除。"""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.deleted_at is not None:
        return {"deleted": True, "id": job_id, "already_in_trash": True}
    count = soft_delete_jobs(session, [job_id])
    return {"deleted": True, "id": job_id, "count": count}


@router.post("/api/jobs/{job_id}/restore")
async def restore_job(job_id: int, session: SessionDep) -> dict:
    """从回收站恢复岗位。"""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.deleted_at is None:
        return {"restored": True, "id": job_id, "already_active": True}
    count = restore_jobs(session, [job_id])
    return {"restored": True, "id": job_id, "count": count}


@router.delete("/api/jobs/{job_id}/purge")
async def purge_job(job_id: int, session: SessionDep) -> dict:
    """永久删除岗位及其全部关联数据。不可恢复。"""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    count = purge_jobs(session, [job_id])
    return {"purged": True, "id": job_id, "count": count}


@router.get("/api/trash/jobs")
async def list_trashed_jobs(session: SessionDep) -> list[dict]:
    """回收站里的岗位列表（已软删除）。"""
    from ..services.jobs import latest_score_map, source_links_map
    jobs, source_links = query_jobs(session, include_deleted=True)
    trashed = [job for job in jobs if job.deleted_at is not None]
    latest = latest_score_map(session, [job.id for job in trashed if job.id])
    return [job_payload(job, latest.get(job.id), source_links.get(job.id or 0, [])) for job in trashed]
