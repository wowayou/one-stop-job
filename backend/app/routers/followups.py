"""跟进待办路由（Phase R · R2）。

/api/follow-ups：本地待办清单，source-agnostic。只提醒不自动联系（红线 §2）。
从 main.py 原样搬出，行为逐字不变；依赖仅来自 deps/models/schemas/services/config。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlmodel import select

from ..config import get_settings
from ..deps import SessionDep
from ..models import FollowUpTask, utc_now
from ..schemas import FollowUpTaskCreate, FollowUpTaskUpdate
from ..services.context_repository import ContextRepositoryError
from ..services.daily_digest import build_daily_digest
from ..services.followup import find_stale_jobs

router = APIRouter()


@router.get("/api/follow-ups")
async def list_follow_ups(session: SessionDep) -> list[FollowUpTask]:
    return session.exec(select(FollowUpTask).order_by(FollowUpTask.due_date.asc(), FollowUpTask.created_at.desc())).all()


@router.get("/api/follow-ups/stale")
async def list_stale_follow_ups(session: SessionDep) -> list[dict]:
    """需跟进岗位：fit/interview 超过 stale_days 天无活动。只提醒，不自动联系。"""
    return find_stale_jobs(session, now=utc_now(), stale_days=get_settings().followup_stale_days)


@router.get("/api/follow-ups/board-sla")
async def board_sla_digest(session: SessionDep, day: str | None = None) -> dict:
    """看板到期动作日清单：解析个人上下文仓库看板「下一步」里的日期（只读、只提醒）。

    返回今日必发 / 今日跟进 / 今日收口 / 未来 7 天，以及库内 stale 岗位和可直接
    发给机主本人的摘要文本。看板未配置或缺文件时按 503 报出，不静默返回空清单。
    """
    try:
        target = date.fromisoformat(day) if day else date.today()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="day 参数需为 YYYY-MM-DD") from exc
    try:
        return build_daily_digest(session, target)
    except ContextRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/follow-ups")
async def create_follow_up(payload: FollowUpTaskCreate, session: SessionDep) -> dict:
    """新增待办；同一 job_id + 相同标题（去首尾空白）+ 状态未完成的待办已存在时不再新建，
    直接把已有记录原样返回并带 duplicate=True，供前端提示「已存在」——避免用户手滑连点
    （或前端重复提交）在待办清单里堆出一串重复项。已完成（status="done"）的旧待办不算重复，
    允许针对同一件事再开一条新的跟进。"""
    title = payload.title.strip()
    existing_tasks = session.exec(
        select(FollowUpTask).where(FollowUpTask.job_id == payload.job_id, FollowUpTask.status != "done")
    ).all()
    duplicate = next((t for t in existing_tasks if t.title.strip() == title), None)
    if duplicate is not None:
        return {**jsonable_encoder(duplicate), "duplicate": True}

    task = FollowUpTask(**payload.model_dump())
    session.add(task)
    session.commit()
    session.refresh(task)
    return {**jsonable_encoder(task), "duplicate": False}


@router.patch("/api/follow-ups/{task_id}")
async def update_follow_up(task_id: int, payload: FollowUpTaskUpdate, session: SessionDep) -> FollowUpTask:
    task = session.get(FollowUpTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, key, value)
    task.updated_at = utc_now()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@router.delete("/api/follow-ups/{task_id}")
async def delete_follow_up(task_id: int, session: SessionDep) -> dict:
    task = session.get(FollowUpTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    session.delete(task)
    session.commit()
    return {"deleted": True, "id": task_id}
