"""岗位路由专属 helper（Phase R · R2）：批量清理、导入评分裁剪、上传解析、状态重算。

原本堆在 main.py，只被 `routers/jobs.py` 使用；下沉到这里让路由文件保持薄。
纯逻辑、无 FastAPI app 依赖；只碰 session / models / services / config。
"""

from __future__ import annotations

from io import BytesIO

import pandas as pd
from fastapi import HTTPException, UploadFile
from sqlmodel import Session, select

from ..config import get_settings
from ..models import (
    ApplicationEvent,
    ChatThread,
    Draft,
    FitScore,
    FollowUpTask,
    InterviewLog,
    InterviewPrep,
    Job,
    JobSourceLink,
    utc_now,
)
from .collectors import TabularFileCollector
from .jobs import company_map, research_items_map
from .queries import application_events, get_profile
from .scoring import score_job


def delete_jobs_with_related(session: Session, job_ids: list[int]) -> int:
    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return 0
    for thread in session.exec(select(ChatThread).where(ChatThread.job_id.in_(unique_ids))).all():
        thread.job_id = None
        thread.updated_at = utc_now()
        session.add(thread)
    for model in (JobSourceLink, FitScore, InterviewPrep, Draft, FollowUpTask, InterviewLog, ApplicationEvent):
        for item in session.exec(select(model).where(model.job_id.in_(unique_ids))).all():
            session.delete(item)
    jobs = session.exec(select(Job).where(Job.id.in_(unique_ids))).all()
    for job in jobs:
        session.delete(job)
    session.commit()
    return len(jobs)


def score_and_prune_imported_jobs(session: Session, job_ids: list[int], keep_top: int) -> dict[str, int]:
    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return {"scored": 0, "kept": 0, "deleted": 0}

    profile = get_profile(session)
    jobs = session.exec(select(Job).where(Job.id.in_(unique_ids))).all()
    companies = company_map(session, [job.company_id for job in jobs if job.company_id])
    research_by_company = research_items_map(session, [job.company_id for job in jobs if job.company_id])
    ranked: list[tuple[Job, FitScore]] = []
    for job in jobs:
        company = companies.get(job.company_id or 0)
        research = research_by_company.get(job.company_id or 0, [])
        result = score_job(job, company, research, profile)
        score = FitScore(job_id=job.id or 0, total=result.total, hard_blocked=result.hard_blocked, details=result.details)
        ranked.append((job, score))

    ranked.sort(key=lambda item: (not item[1].hard_blocked, item[1].total, item[0].favorite, item[0].collected_at), reverse=True)
    kept = ranked[:keep_top]
    dropped = ranked[keep_top:]
    dropped_ids = [job.id for job, _score in dropped if job.id is not None]

    for _job, score in kept:
        session.add(score)
    session.commit()

    deleted = delete_jobs_with_related(session, dropped_ids)
    return {"scored": len(ranked), "kept": len(kept), "deleted": deleted}


def recompute_job_status_from_events(session: Session, job: Job) -> None:
    """按事件集合里「已到达的最高阶段」重算岗位状态,新增/删除事件都走这里。
    优先级取最高阶段,因此补录较早阶段的事件(如已 offer 后补登记投递)不会把状态打回早期;
    没有任何事件时保持现状(无法得知事件前的状态,交由人工调整)。"""
    events = application_events(session, job_id=job.id)
    if not events:
        return
    event_types = {event.event_type for event in events}
    next_status = None
    if "offer" in event_types:
        next_status = "offer"
    elif "interview_invite" in event_types:
        next_status = "interview"
    elif "rejected" in event_types:
        next_status = "rejected"
    elif "withdrawn" in event_types:
        next_status = "archived"
    elif event_types & {"applied", "reply"}:
        next_status = "applied"
    if next_status and job.status != next_status:
        job.status = next_status
        job.status_changed_at = utc_now()
        job.updated_at = utc_now()
        session.add(job)
        session.commit()


async def _read_upload_file(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            max_mb = round(limit / 1024 / 1024, 1)
            raise HTTPException(status_code=413, detail=f"上传文件过大，当前限制为 {max_mb:g} MB")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    return content


def _dataframes_from_upload(content: bytes, filename: str | None) -> list[pd.DataFrame]:
    name = (filename or "").lower()
    try:
        if name.endswith(".xlsx"):
            sheets = pd.read_excel(BytesIO(content), sheet_name=None)
            return list(sheets.values())
        if name.endswith(".xls"):
            raise HTTPException(status_code=400, detail="暂不支持旧版 .xls 文件，请另存为 .xlsx 或 CSV 后导入")
        return [pd.read_csv(BytesIO(content), encoding="utf-8", on_bad_lines="skip")]
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV 必须使用 UTF-8 编码，请转码后重新导入") from exc
    except pd.errors.ParserError as exc:
        raise HTTPException(status_code=400, detail=f"CSV 解析失败：{exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"文件解析失败：{exc}") from exc


async def _records_from_uploads(files: list[UploadFile], source: str) -> list[dict]:
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")

    records: list[dict] = []
    for file in files:
        content = await _read_upload_file(file)
        frames = _dataframes_from_upload(content, file.filename)
        for df in frames:
            records.extend(TabularFileCollector(df=df, source=source).collect())
    return records
