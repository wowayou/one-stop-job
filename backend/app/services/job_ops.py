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
    Company,
    Draft,
    FitScore,
    FollowUpTask,
    InterviewLog,
    InterviewPrep,
    Job,
    JobSourceLink,
    ResearchItem,
    SourceRun,
    utc_now,
)
from .collectors import TabularFileCollector
from .jobs import company_map, research_items_map
from .queries import application_events, get_profile
from .scoring import score_job


def soft_delete_jobs(session: Session, job_ids: list[int]) -> int:
    """软删除岗位：打 deleted_at 时间戳，不删数据行。关联的评分/准备/待办/事件/复盘保留，
    从列表/搜索/统计里隐藏。可在回收站里恢复或永久删除。"""
    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return 0
    now = utc_now()
    jobs = session.exec(
        select(Job).where(Job.id.in_(unique_ids)).where(Job.deleted_at.is_(None))
    ).all()
    for job in jobs:
        job.deleted_at = now
        job.updated_at = now
        session.add(job)
    # 聊天线程上的 job 引用保留（不置空），恢复后还能从线程跳回岗位。
    session.commit()
    return len(jobs)


def restore_jobs(session: Session, job_ids: list[int]) -> int:
    """从回收站恢复岗位。"""
    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return 0
    jobs = session.exec(
        select(Job).where(Job.id.in_(unique_ids)).where(Job.deleted_at.is_not(None))
    ).all()
    for job in jobs:
        job.deleted_at = None
        job.updated_at = utc_now()
        session.add(job)
    session.commit()
    return len(jobs)


def purge_jobs(session: Session, job_ids: list[int]) -> int:
    """永久删除岗位及其关联数据。不可恢复。"""
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


def delete_jobs_with_related(session: Session, job_ids: list[int]) -> int:
    """永久删除岗位及其关联数据（内部导入裁剪用，score_and_prune 导入过滤用）。

    保留旧函数名给 score_and_prune_imported_jobs 使用——导入裁剪是用户刚导入的岗位
    立即被裁掉，走永久删除而非软删除更合理（回收站不该堆导入垃圾）。
    """
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


# 回收站自动清理：软删除超过此天数的岗位/公司自动永久删除。
TRASH_RETENTION_DAYS = 30


def auto_purge_trash(session: Session) -> dict[str, int]:
    """清理回收站里超过 TRASH_RETENTION_DAYS 天的软删除记录。

    在 init_db() 启动时和 daily_digest 循环中调用。返回 {"jobs": N, "companies": M}。
    清理失败不影响启动——这是辅助清理，不该阻断数据库初始化。
    """
    from datetime import timedelta
    cutoff = utc_now() - timedelta(days=TRASH_RETENTION_DAYS)

    try:
        # 永久删除过期的软删除岗位
        expired_jobs = session.exec(
            select(Job).where(Job.deleted_at.is_not(None)).where(Job.deleted_at < cutoff)
        ).all()
        if expired_jobs:
            purge_jobs(session, [job.id for job in expired_jobs if job.id is not None])

        # 永久删除过期的软删除公司
        expired_companies = session.exec(
            select(Company).where(Company.deleted_at.is_not(None)).where(Company.deleted_at < cutoff)
        ).all()
        for company in expired_companies:
            if company.id is None:
                continue
            # 关联岗位的 company_id 置空
            for job in session.exec(select(Job).where(Job.company_id == company.id)).all():
                job.company_id = None
                job.updated_at = utc_now()
                session.add(job)
            # 调研证据直接删
            for item in session.exec(select(ResearchItem).where(ResearchItem.company_id == company.id)).all():
                session.delete(item)
            session.delete(company)
        if expired_companies:
            session.commit()

        return {"jobs": len(expired_jobs), "companies": len(expired_companies)}
    except Exception:  # noqa: BLE001
        # 回收站清理是辅助功能，任何异常都不该阻断 init_db 或 daily_digest
        import logging
        logging.getLogger(__name__).warning("回收站自动清理失败", exc_info=True)
        return {"jobs": 0, "companies": 0}


# SourceRun 保留条数：超过此数的旧采集记录自动清理（可选功能，默认不启用）。
DEFAULT_SOURCE_RUN_KEEP = 100


def cleanup_source_runs(session: Session, keep: int = DEFAULT_SOURCE_RUN_KEEP) -> int:
    """清理旧的 SourceRun 记录，只保留最近 keep 条。

    可选功能：在 init_db() 启动时和 daily_digest 循环中调用。
    SourceRun 只是采集运行的历史日志，不影响岗位数据。
    """
    try:
        total = session.exec(select(SourceRun)).all()
        if len(total) <= keep:
            return 0
        # 按 started_at 降序排，保留最近 keep 条，删其余
        total.sort(key=lambda r: r.started_at, reverse=True)
        to_delete = total[keep:]
        for run in to_delete:
            session.delete(run)
        session.commit()
        return len(to_delete)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("SourceRun 清理失败", exc_info=True)
        return 0


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
