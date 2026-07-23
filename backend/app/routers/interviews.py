"""面试复盘路由（Phase R · R2）。

/api/interviews 与 /api/jobs/{job_id}/interviews：跨岗位与单岗位的面试记录。
从 main.py 原样搬出，行为逐字不变。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ..deps import SessionDep
from ..models import InterviewLog, Job, utc_now
from ..schemas import InterviewLogCreate, InterviewLogUpdate

router = APIRouter()


def _ordered_interviews(statement):
    return statement.order_by(InterviewLog.interview_date.desc(), InterviewLog.created_at.desc())


@router.get("/api/interviews")
async def list_all_interviews(session: SessionDep) -> list[InterviewLog]:
    """全局面试复盘时间线：跨岗位汇总，供「面试复盘」页签追溯。"""
    return session.exec(_ordered_interviews(select(InterviewLog))).all()


@router.get("/api/jobs/{job_id}/interviews")
async def list_job_interviews(job_id: int, session: SessionDep) -> list[InterviewLog]:
    return session.exec(_ordered_interviews(select(InterviewLog).where(InterviewLog.job_id == job_id))).all()


@router.post("/api/jobs/{job_id}/interviews")
async def create_interview(job_id: int, payload: InterviewLogCreate, session: SessionDep) -> InterviewLog:
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    log = InterviewLog(job_id=job_id, **payload.model_dump())
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


@router.patch("/api/interviews/{log_id}")
async def update_interview(log_id: int, payload: InterviewLogUpdate, session: SessionDep) -> InterviewLog:
    log = session.get(InterviewLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Interview log not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(log, key, value)
    log.updated_at = utc_now()
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


@router.delete("/api/interviews/{log_id}")
async def delete_interview(log_id: int, session: SessionDep) -> dict:
    log = session.get(InterviewLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Interview log not found")
    session.delete(log)
    session.commit()
    return {"deleted": True, "id": log_id}
