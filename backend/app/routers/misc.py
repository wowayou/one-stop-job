"""导出 + 冲刺包路由（Phase R · R2）。

/api/exports/{kind} 与 /api/sprint/brief。从 main.py 原样搬出，行为逐字不变；
依赖仅来自 deps/models/schemas/services/config，不 import main。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response
from sqlmodel import select

from ..deps import SessionDep
from ..models import (
    AnalysisRun,
    ChatMessage,
    ChatThread,
    Company,
    Draft,
    FitScore,
    FollowUpTask,
    InterviewLog,
    InterviewPrep,
    Job,
    ResearchItem,
    SourceRun,
    utc_now,
)
from ..services.exporter import build_archive_payload, encode_json, export_archive_json, export_jobs_csv
from ..services.jobs import job_payload, latest_score_map
from ..services.queries import (
    application_events,
    download_response,
    get_profile,
    query_jobs,
)
from ..services.sprint_ops import create_sprint_payload

router = APIRouter()


@router.get("/api/exports/{kind}")
async def export_data(
    kind: str,
    session: SessionDep,
    format: str = Query(default="json"),
    search: str | None = None,
    status: str | None = None,
    source: str | None = None,
    favorite: bool | None = None,
    include_trashed: bool = Query(default=False),
) -> Response:
    generated_at = utc_now().strftime("%Y%m%d-%H%M%S")
    format = format.lower().strip()
    kind = kind.lower().strip()

    if kind == "jobs":
        jobs, source_links = query_jobs(session, search=search, status=status, source=source, favorite=favorite)
        latest = latest_score_map(session, [job.id for job in jobs if job.id])
        payload = [job_payload(job, latest.get(job.id), source_links.get(job.id or 0, [])) for job in jobs]
        if format == "csv":
            return download_response(f"jobs-{generated_at}.csv", export_jobs_csv(payload), "text/csv; charset=utf-8")
        if format == "json":
            return download_response(f"jobs-{generated_at}.json", encode_json(payload), "application/json; charset=utf-8")

    if kind == "archive":
        profile = get_profile(session)
        job_stmt = select(Job)
        company_stmt = select(Company)
        if not include_trashed:
            job_stmt = job_stmt.where(Job.deleted_at.is_(None))
            company_stmt = company_stmt.where(Company.deleted_at.is_(None))
        archive = build_archive_payload(
            profile=profile,
            jobs=session.exec(job_stmt.order_by(Job.collected_at.desc())).all(),
            companies=session.exec(company_stmt.order_by(Company.updated_at.desc())).all(),
            research_items=session.exec(select(ResearchItem).order_by(ResearchItem.captured_at.desc())).all(),
            scores=session.exec(select(FitScore).order_by(FitScore.created_at.desc())).all(),
            preps=session.exec(select(InterviewPrep).order_by(InterviewPrep.created_at.desc())).all(),
            drafts=session.exec(select(Draft).order_by(Draft.created_at.desc())).all(),
            tasks=session.exec(select(FollowUpTask).order_by(FollowUpTask.created_at.desc())).all(),
            interviews=session.exec(select(InterviewLog).order_by(InterviewLog.created_at.desc())).all(),
            runs=session.exec(select(SourceRun).order_by(SourceRun.started_at.desc())).all(),
            events=application_events(session),
            chat_threads=session.exec(select(ChatThread).order_by(ChatThread.updated_at.desc())).all(),
            chat_messages=session.exec(select(ChatMessage).order_by(ChatMessage.created_at.asc())).all(),
            analysis_runs=session.exec(select(AnalysisRun).order_by(AnalysisRun.created_at.asc())).all(),
        )
        return download_response(
            f"archive-{generated_at}.json",
            export_archive_json(schema_version="0006_decision_chat", generated_at=utc_now().isoformat(), payload=archive),
            "application/json; charset=utf-8",
        )

    raise HTTPException(status_code=400, detail="Unsupported export kind or format")


@router.post("/api/sprint/brief")
async def create_sprint_brief(
    session: SessionDep,
    top_n: int = Query(default=20, ge=1, le=100),
    prep_n: int = Query(default=5, ge=0, le=20),
    create_tasks: bool = Query(default=True),
    rescore: bool = Query(default=False),
) -> dict:
    """生成当天求职冲刺包：补评分、挑 Top 岗位、补面试准备和待办。"""
    return create_sprint_payload(session, top_n=top_n, prep_n=prep_n, create_tasks=create_tasks, rescore=rescore)
