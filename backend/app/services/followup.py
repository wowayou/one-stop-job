"""跟进过期检测：找出处于 fit/interview 但长期无活动的岗位，提醒人工跟进。

与来源无关（§3.8）：只看 Job.status 与活动时间戳，不关心岗位来自哪个平台。
只产出提醒，绝不自动投递或发消息（§3.2）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from ..models import FollowUpTask, InterviewLog, Job

# 只有进入“值得推进”的状态才需要跟进；new/researching/rejected/archived 不算。
STALE_STATUSES = ("fit", "interview")


def _naive_utc(value: datetime | None) -> datetime | None:
    """SQLite 取回的时间多为 naive；统一成 naive-UTC，避免 aware/naive 相减报错。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def is_stale(status: str, last_activity_at: datetime | None, now: datetime, stale_days: int) -> tuple[bool, int]:
    """纯函数：返回 (是否过期, 距上次活动天数)。仅对 STALE_STATUSES 判定。"""
    if status not in STALE_STATUSES or last_activity_at is None:
        return (False, 0)
    last = _naive_utc(last_activity_at)
    moment = _naive_utc(now)
    days = (moment - last).days
    return (days >= stale_days, days)


def find_stale_jobs(session: Session, *, now: datetime, stale_days: int) -> list[dict]:
    """列出需跟进的岗位。last_activity = max(状态变更时间, 最近一次面试复盘, 最近一次跟进任务更新)。"""
    jobs = session.exec(select(Job).where(Job.status.in_(STALE_STATUSES))).all()
    job_ids = [job.id for job in jobs if job.id is not None]
    latest_logs = _latest_interview_log_map(session, job_ids)
    latest_done_tasks = _latest_done_task_map(session, job_ids)
    stale: list[dict] = []
    for job in jobs:
        candidates: list[datetime] = []
        if job.status_changed_at is not None:
            candidates.append(job.status_changed_at)
        log = latest_logs.get(job.id or 0)
        if log is not None:
            candidates.append(log.created_at)
        # 只把“已完成”的跟进任务算作活动：待办提醒（含冲刺包自动建的）不应反过来掩盖过期。
        task = latest_done_tasks.get(job.id or 0)
        if task is not None:
            candidates.append(task.updated_at)

        activities = [moment for moment in (_naive_utc(c) for c in candidates) if moment is not None]
        last_activity = max(activities) if activities else None
        overdue, days = is_stale(job.status, last_activity, now, stale_days)
        if overdue:
            stale.append(
                {
                    "job_id": job.id,
                    "title": job.title,
                    "company_name": job.company_name,
                    "status": job.status,
                    "days": days,
                    "reason": f"{job.status} 状态已 {days} 天无跟进",
                }
            )
    stale.sort(key=lambda item: item["days"], reverse=True)
    return stale


def _latest_interview_log_map(session: Session, job_ids: list[int]) -> dict[int, InterviewLog]:
    if not job_ids:
        return {}
    logs = session.exec(
        select(InterviewLog).where(InterviewLog.job_id.in_(job_ids)).order_by(InterviewLog.job_id, InterviewLog.created_at.desc())
    ).all()
    latest: dict[int, InterviewLog] = {}
    for log in logs:
        latest.setdefault(log.job_id, log)
    return latest


def _latest_done_task_map(session: Session, job_ids: list[int]) -> dict[int, FollowUpTask]:
    if not job_ids:
        return {}
    tasks = session.exec(
        select(FollowUpTask)
        .where(FollowUpTask.job_id.in_(job_ids), FollowUpTask.status == "done")
        .order_by(FollowUpTask.job_id, FollowUpTask.updated_at.desc())
    ).all()
    latest: dict[int, FollowUpTask] = {}
    for task in tasks:
        if task.job_id is not None:
            latest.setdefault(task.job_id, task)
    return latest
