from __future__ import annotations

import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from .config import ConfigError, get_config_path, get_settings, load_yaml_config, save_yaml_config
from .db import engine, get_session, init_db
from .models import (
    ApplicationEvent,
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
    UserProfile,
    utc_now,
)
from .schemas import (
    AppConfigUpdate,
    ApplicationEventCreate,
    CompanyUpdate,
    DraftCreate,
    FollowUpTaskCreate,
    FollowUpTaskUpdate,
    InterviewLogCreate,
    InterviewLogUpdate,
    JobBulkUpdate,
    JobCreate,
    JobUpdate,
    ProfileUpdate,
    ResearchItemCreate,
    WeChatCollectRequest,
)
from .services.ai import is_ai_available, tailor_interview_prep_llm
from .services.analytics import build_funnel_payload
from .services.companies import company_list_payload
from .services.context_repository import ContextRepository
from .services.collectors import TabularFileCollector, WeChatPasteCollector
from .services.exporter import (
    build_archive_payload,
    encode_json,
    export_archive_json,
    export_jobs_csv,
)
from .services.followup import find_stale_jobs
from .services.importer import get_or_create_company, upsert_job_record, upsert_job_records, upsert_job_records_with_ids
from .services.jobs import company_map, job_payload, latest_prep_map, latest_score_map, research_items_map, source_links_map
from .services.normalizer import canonical_job_key, normalize_record, parse_recruiter, parse_salary
from .services.prep import build_interview_prep
from .services.scoring import DEFAULT_WEIGHTS, score_job
from .services.sources import build_source_collector, get_source_definition, list_source_definitions, source_health, source_public_config
from .services.wechat import extract_mp_links


settings = get_settings()
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


SessionDep = Annotated[Session, Depends(get_session)]

CONFIG_TOP_LEVEL_ALLOWLIST = {"opencli", "job_sources", "general", "research", "wechat", "bebee", "scoring", "followup", "ai"}
SENSITIVE_CONFIG_KEYS = ("api_key", "apikey", "secret", "password", "token", "authorization")


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _error_payload(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    content = {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": _request_id(request),
        }
    }
    return JSONResponse(status_code=status_code, content=content)


@app.exception_handler(HTTPException)
async def app_http_exception_handler(request: Request, exc: HTTPException):
    if not request.url.path.startswith("/api/"):
        return await http_exception_handler(request, exc)
    detail = exc.detail
    message = detail if isinstance(detail, str) else "请求失败"
    code_by_status = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        502: "upstream_failed",
    }
    return _error_payload(
        request=request,
        status_code=exc.status_code,
        code=code_by_status.get(exc.status_code, "http_error"),
        message=message,
        details=None if isinstance(detail, str) else detail,
    )


@app.exception_handler(RequestValidationError)
async def app_validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error_payload(
        request=request,
        status_code=422,
        code="validation_error",
        message="请求参数校验失败",
        details=jsonable_encoder(exc.errors()),
    )


@app.exception_handler(ConfigError)
async def app_config_exception_handler(request: Request, exc: ConfigError):
    return _error_payload(
        request=request,
        status_code=500,
        code="config_error",
        message=str(exc),
    )


@app.exception_handler(SQLAlchemyError)
async def app_database_exception_handler(request: Request, exc: SQLAlchemyError):
    return _error_payload(
        request=request,
        status_code=503,
        code="database_error",
        message="数据库暂时不可用，请稍后重试或检查数据库连接。",
        details=str(exc.__class__.__name__),
    )


@app.exception_handler(Exception)
async def app_unhandled_exception_handler(request: Request, exc: Exception):
    return _error_payload(
        request=request,
        status_code=500,
        code="internal_error",
        message="服务端处理失败，请查看后端日志并带上 request_id 定位。",
        details=str(exc.__class__.__name__),
    )


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in SENSITIVE_CONFIG_KEYS):
                return True
            if _contains_sensitive_key(nested):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _strip_sensitive_keys(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in SENSITIVE_CONFIG_KEYS):
                continue
            cleaned[key] = _strip_sensitive_keys(nested)
        return cleaned
    if isinstance(value, list):
        return [_strip_sensitive_keys(item) for item in value]
    return value


def _strip_runtime_only_config(value: dict[str, Any]) -> dict[str, Any]:
    cleaned = _strip_sensitive_keys(value)
    opencli = cleaned.get("opencli")
    if isinstance(opencli, dict):
        opencli.pop("path", None)
    return cleaned


def _safe_config_response(config: dict[str, Any]) -> dict:
    return {
        "path": str(get_config_path()),
        "config": _strip_runtime_only_config(config),
        "env": {
            "openai_api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
            "openai_base_url_configured": bool(os.getenv("OPENAI_BASE_URL")),
            "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "database_url_configured": bool(os.getenv("JOB_ONE_STOP_DATABASE_URL") or os.getenv("DATABASE_URL")),
            "port": settings.port,
            "max_upload_mb": round(settings.max_upload_bytes / 1024 / 1024, 1),
        },
        "config_error": settings.config_error,
        "editable_sections": sorted(CONFIG_TOP_LEVEL_ALLOWLIST),
        "restart_recommended_after_save": ["general.data_dir", "JOB_ONE_STOP_DATABASE_URL", "JOB_ONE_STOP_CORS_ORIGINS"],
    }


def _validate_scoring_config(config: dict[str, Any]) -> None:
    scoring = config.get("scoring")
    if not isinstance(scoring, dict) or "weights" not in scoring:
        return

    weights = scoring.get("weights")
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


def _public_database_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    if not parsed.scheme or not parsed.netloc:
        return database_url
    if not parsed.username and not parsed.password:
        return database_url
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return parsed._replace(netloc=f"***:***@{host}{port}").geturl()


def _check(name: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message, **extra}


def _is_writable_path(path: Path) -> bool:
    target = path if path.exists() else path.parent
    return target.exists() and os.access(target, os.W_OK)


def _config_diagnostics() -> list[dict[str, Any]]:
    config_path = get_config_path()
    checks = [
        _check(
            "config_file",
            "error" if settings.config_error else "ok",
            settings.config_error or "配置文件可读取。",
            path=str(config_path),
            exists=config_path.exists(),
            writable=_is_writable_path(config_path),
        )
    ]
    if not config_path.exists():
        checks[0]["status"] = "warning"
        checks[0]["message"] = "配置文件不存在，系统将使用内置默认值；建议复制或创建 config.yaml。"
    if config_path.exists() and not _is_writable_path(config_path):
        checks.append(
            _check(
                "config_file_writable",
                "warning",
                "配置文件不可写，前端系统配置页可能无法保存。",
                path=str(config_path),
            )
        )

    try:
        _validate_scoring_config(settings.config)
    except HTTPException as exc:
        checks.append(_check("scoring_config", "warning", str(exc.detail)))
    else:
        checks.append(_check("scoring_config", "ok", "评分权重配置有效。"))
    return checks


def _database_diagnostics() -> dict[str, Any]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return _check(
            "database",
            "error",
            "数据库连接失败。",
            database_url=_public_database_url(settings.database_url),
            error=str(exc),
        )
    return _check(
        "database",
        "ok",
        "数据库连接正常。",
        database_url=_public_database_url(settings.database_url),
    )


def _frontend_diagnostics() -> dict[str, Any]:
    index_file = FRONTEND_DIST / "index.html"
    if index_file.is_file():
        return _check("frontend_build", "ok", "前端构建文件存在。", path=str(FRONTEND_DIST))
    return _check(
        "frontend_build",
        "warning",
        "未找到 frontend/dist/index.html。Docker 生产镜像应包含构建产物；开发模式可忽略。",
        path=str(FRONTEND_DIST),
    )


def _source_diagnostics() -> list[dict[str, Any]]:
    checks = []
    for source in list_source_definitions(settings):
        health = source_health(source)
        if not source.enabled:
            status = "ok"
        elif health["status"] == "host_import_required":
            status = "warning"
        elif health["configured"]:
            status = "ok"
        else:
            status = "warning"
        checks.append(
            _check(
                f"source:{source.key}",
                status,
                health["message"],
                key=source.key,
                label=source.label,
                source_status=health["status"],
                enabled=source.enabled,
                configured=health["configured"],
            )
        )
    return checks


def _deployment_diagnostics() -> dict[str, Any]:
    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    checks = [
        *_config_diagnostics(),
        _database_diagnostics(),
        _frontend_diagnostics(),
        _check(
            "cors",
            "ok" if settings.cors_origins else "warning",
            "CORS 来源已配置。" if settings.cors_origins else "未配置 CORS 来源；浏览器开发模式可能无法访问 API。",
            origins=settings.cors_origins,
        ),
        _check(
            "upload_limit",
            "ok",
            "上传大小限制已配置。",
            max_upload_mb=round(settings.max_upload_bytes / 1024 / 1024, 1),
        ),
        _check(
            "cloud_runtime",
            "ok",
            "已暴露云端常用运行参数。",
            host=settings.host,
            port=settings.port,
            database_url_env=bool(os.getenv("JOB_ONE_STOP_DATABASE_URL") or os.getenv("DATABASE_URL")),
            port_env=bool(os.getenv("PORT")),
        ),
        _check(
            "ai",
            "ok" if not ai_cfg.get("enabled") or is_ai_available() else "warning",
            "AI 已具备调用条件。"
            if bool(ai_cfg.get("enabled")) and is_ai_available()
            else "AI 未启用或未配置 OPENAI_API_KEY。",
            enabled_in_config=bool(ai_cfg.get("enabled")),
            api_key_configured=bool(os.getenv("OPENAI_API_KEY")),
            base_url_configured=bool(os.getenv("OPENAI_BASE_URL")),
        ),
        *_source_diagnostics(),
    ]
    if any(item["status"] == "error" for item in checks):
        status = "error"
    elif any(item["status"] == "warning" for item in checks):
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "generated_at": utc_now().isoformat(),
        "app": settings.app_name,
        "checks": checks,
    }


async def _read_upload_file(file: UploadFile) -> bytes:
    limit = settings.max_upload_bytes
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


def _latest_score_map(session: Session, job_ids: list[int]) -> dict[int, FitScore]:
    return latest_score_map(session, job_ids)


def _latest_prep_map(session: Session, job_ids: list[int]) -> dict[int, InterviewPrep]:
    return latest_prep_map(session, job_ids)


def _source_links_map(session: Session, job_ids: list[int]) -> dict[int, list[JobSourceLink]]:
    return source_links_map(session, job_ids)


def _job_payload(job: Job, latest: FitScore | None = None, source_links: list[JobSourceLink] | None = None) -> dict:
    return job_payload(job, latest, source_links)


def _job_response(session: Session, job: Job) -> dict:
    latest = _latest_score_map(session, [job.id or 0]).get(job.id or 0)
    links = _source_links_map(session, [job.id or 0]).get(job.id or 0, [])
    return _job_payload(job, latest, links)


def _query_jobs(
    session: Session,
    *,
    search: str | None = None,
    status: str | None = None,
    source: str | None = None,
    favorite: bool | None = None,
) -> tuple[list[Job], dict[int, list[JobSourceLink]]]:
    jobs = session.exec(select(Job).order_by(Job.favorite.desc(), Job.collected_at.desc())).all()
    source_links = _source_links_map(session, [job.id for job in jobs if job.id])
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


def _get_profile(session: Session) -> UserProfile:
    profile = session.exec(select(UserProfile)).first()
    if not profile:
        profile = UserProfile(weights=settings.scoring_weights)
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def _score_job_into_db(session: Session, job: Job, profile: UserProfile) -> FitScore:
    companies = company_map(session, [job.company_id] if job.company_id else [])
    research_by_company = research_items_map(session, [job.company_id] if job.company_id else [])
    company = companies.get(job.company_id or 0)
    result = score_job(job, company, research_by_company.get(job.company_id or 0, []), profile)
    score = FitScore(job_id=job.id or 0, total=result.total, hard_blocked=result.hard_blocked, details=result.details)
    session.add(score)
    session.commit()
    session.refresh(score)
    return score


def _application_events(session: Session, *, job_id: int | None = None) -> list[ApplicationEvent]:
    statement = select(ApplicationEvent).order_by(ApplicationEvent.event_date.desc(), ApplicationEvent.created_at.desc())
    if job_id is not None:
        statement = statement.where(ApplicationEvent.job_id == job_id)
    return session.exec(statement).all()


def _download_response(filename: str, content: str, media_type: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _delete_jobs_with_related(session: Session, job_ids: list[int]) -> int:
    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return 0
    for model in (JobSourceLink, FitScore, InterviewPrep, Draft, FollowUpTask, InterviewLog, ApplicationEvent):
        for item in session.exec(select(model).where(model.job_id.in_(unique_ids))).all():
            session.delete(item)
    jobs = session.exec(select(Job).where(Job.id.in_(unique_ids))).all()
    for job in jobs:
        session.delete(job)
    session.commit()
    return len(jobs)


def _score_and_prune_imported_jobs(session: Session, job_ids: list[int], keep_top: int) -> dict[str, int]:
    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        return {"scored": 0, "kept": 0, "deleted": 0}

    profile = _get_profile(session)
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

    deleted = _delete_jobs_with_related(session, dropped_ids)
    return {"scored": len(ranked), "kept": len(kept), "deleted": deleted}


def _recompute_job_status_from_events(session: Session, job: Job) -> None:
    """按事件集合里「已到达的最高阶段」重算岗位状态,新增/删除事件都走这里。
    优先级取最高阶段,因此补录较早阶段的事件(如已 offer 后补登记投递)不会把状态打回早期;
    没有任何事件时保持现状(无法得知事件前的状态,交由人工调整)。"""
    events = _application_events(session, job_id=job.id)
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


def _ai_ready() -> bool:
    """AI 真正可用 = config.yaml ai.enabled 为真且配置了密钥。"""
    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    return bool(ai_cfg.get("enabled")) and is_ai_available()


def _prep_ai_context(job: Job, company: Company | None, profile: UserProfile) -> dict[str, str]:
    location = " ".join(part for part in [job.city, job.area] if part) or "地点未披露"
    return {
        "title": job.title or "",
        "company_name": (company.name if company else job.company_name) or "",
        "requirements": (job.skills or job.description or "").strip(),
        "salary": job.salary_text or "薪资未披露",
        "location": location,
        "profile_skills": profile.skills or "",
        "profile_strengths": profile.strengths or "",
        "profile_experience": profile.work_experience or "",
        "salary_expectation": f"{profile.salary_min_k:g}-{profile.salary_max_k:g}K",
        "dealbreakers": profile.dealbreakers or "",
    }


def _build_prep_into_db(session: Session, job: Job, profile: UserProfile, *, use_ai: bool = True) -> InterviewPrep:
    company = session.get(Company, job.company_id) if job.company_id else None
    payload = build_interview_prep(job, company, profile)
    if use_ai and _ai_ready():
        tailored = tailor_interview_prep_llm(_prep_ai_context(job, company, profile), payload)
        if tailored:
            payload = tailored
    prep = InterviewPrep(job_id=job.id or 0, **payload)
    session.add(prep)
    session.add(Draft(job_id=job.id, kind="communication_draft", channel="manual", content=payload["communication_draft"]))
    session.add(Draft(job_id=job.id, kind="core_pitch", channel="manual", content=payload["core_pitch"]))
    session.add(Draft(job_id=job.id, kind="tailored_resume", channel="manual", content=payload["tailored_resume"]))
    session.commit()
    session.refresh(prep)
    return prep


def _job_markdown_row(rank: int, job: Job, score: FitScore) -> str:
    location = " ".join([part for part in [job.city, job.area] if part]) or "-"
    status = "硬阻断" if score.hard_blocked else job.status
    next_step = "补公司调研" if not job.company_id else "准备沟通/投递"
    link = job.url or ""
    title = f"[{job.title}]({link})" if link else job.title
    return (
        f"| {rank} | {score.total:g} | {job.company_name} | {title} | "
        f"{job.salary_text or '-'} | {location} | {status} | {next_step} |"
    )


def _build_sprint_markdown(
    *,
    profile: UserProfile,
    ranked: list[tuple[Job, FitScore]],
    prepared: list[tuple[Job, InterviewPrep]],
    tasks: list[FollowUpTask],
    stale: list[dict],
) -> str:
    lines = [
        "# 今日求职冲刺包",
        "",
        "## 个人画像",
        f"- 目标岗位：{profile.target_titles}",
        f"- 目标城市：{profile.target_cities}",
        f"- 薪资期望：{profile.salary_min_k:g}-{profile.salary_max_k:g}K",
        f"- 核心技能：{profile.skills}",
        f"- 排除项：{profile.dealbreakers}",
        "",
        "## Top 岗位清单",
        "| 排名 | 分数 | 公司 | 岗位 | 薪资 | 地点 | 状态 | 下一步 |",
        "|---:|---:|---|---|---|---|---|---|",
    ]
    lines.extend(_job_markdown_row(index, job, score) for index, (job, score) in enumerate(ranked, start=1))
    if not ranked:
        lines.append("| - | - | - | 暂无可用岗位 | - | - | - | 先采集或导入岗位 |")

    lines.extend(["", "## 面试准备重点"])
    if prepared:
        for index, (job, prep) in enumerate(prepared, start=1):
            lines.extend(
                [
                    "",
                    f"### {index}. {job.company_name} - {job.title}",
                    f"- JD 摘要：{prep.jd_summary}",
                    f"- 核心优势话术：{prep.core_pitch}",
                    f"- 简历强调点：{prep.resume_points}",
                    f"- 岗位定制简历：{prep.tailored_resume}",
                    f"- 反问问题：{prep.questions_to_ask}",
                    f"- 沟通草稿：{prep.communication_draft}",
                ]
            )
    else:
        lines.append("- 暂无可准备岗位；先导入岗位并生成评分。")

    lines.extend(["", "## 需跟进（fit/interview 久无进展）"])
    if stale:
        lines.extend(
            f"- {item['company_name']} - {item['title']}：{item['reason']}" for item in stale
        )
    else:
        lines.append("- 暂无久无进展的岗位。")

    lines.extend(["", "## 待办"])
    if tasks:
        lines.extend(f"- [ ] {task.title}" for task in tasks)
    else:
        lines.append("- [ ] 采集岗位并筛出 Top 5")
    return "\n".join(lines)


def _create_sprint_payload(
    session: Session,
    *,
    top_n: int,
    prep_n: int,
    create_tasks: bool,
    rescore: bool,
) -> dict:
    profile = _get_profile(session)
    jobs = session.exec(select(Job).order_by(Job.favorite.desc(), Job.collected_at.desc())).all()
    actionable_jobs = [job for job in jobs if job.status not in {"rejected", "archived"}]

    latest_scores = _latest_score_map(session, [job.id for job in actionable_jobs if job.id])
    ranked: list[tuple[Job, FitScore]] = []
    for job in actionable_jobs:
        score = latest_scores.get(job.id or 0)
        if rescore or score is None:
            score = _score_job_into_db(session, job, profile)
        ranked.append((job, score))

    ranked.sort(key=lambda item: (not item[1].hard_blocked, item[1].total, item[0].favorite), reverse=True)
    top_jobs = ranked[:top_n]
    prep_jobs = top_jobs[:prep_n]

    latest_preps = _latest_prep_map(session, [job.id for job, _ in prep_jobs if job.id])
    prepared: list[tuple[Job, InterviewPrep]] = []
    for job, _score in prep_jobs:
        prep = latest_preps.get(job.id or 0) or _build_prep_into_db(session, job, profile)
        prepared.append((job, prep))

    created_tasks: list[FollowUpTask] = []
    if create_tasks:
        prep_job_ids = [job.id for job, _ in prep_jobs if job.id]
        existing_tasks = (
            session.exec(select(FollowUpTask).where(FollowUpTask.job_id.in_(prep_job_ids))).all() if prep_job_ids else []
        )
        existing_keys = {(task.job_id, task.title) for task in existing_tasks}
        for job, _score in prep_jobs:
            title = f"待办 {job.company_name} - {job.title}"
            if (job.id, title) in existing_keys:
                continue
            task = FollowUpTask(job_id=job.id, title=title)
            session.add(task)
            created_tasks.append(task)
        if created_tasks:
            session.commit()
            for task in created_tasks:
                session.refresh(task)

    stale_jobs = find_stale_jobs(session, now=utc_now(), stale_days=settings.followup_stale_days)
    markdown = _build_sprint_markdown(profile=profile, ranked=top_jobs, prepared=prepared, tasks=created_tasks, stale=stale_jobs)
    return {
        "generated_at": utc_now().isoformat(),
        "profile": profile.model_dump(),
        "top_jobs": [{**job.model_dump(), "latest_score": score.model_dump()} for job, score in top_jobs],
        "prepared": [{"job": job.model_dump(), "prep": prep.model_dump()} for job, prep in prepared],
        "tasks_created": [task.model_dump() for task in created_tasks],
        "stale_jobs": stale_jobs,
        "markdown": markdown,
    }


@app.get("/api/config")
async def get_app_config() -> dict:
    return _safe_config_response(settings.config)


@app.put("/api/config")
async def update_app_config(payload: AppConfigUpdate) -> dict:
    global settings

    incoming = payload.config or {}
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="config must be an object")
    if _contains_sensitive_key(incoming):
        raise HTTPException(status_code=400, detail="敏感字段请放入 .env，不要写入 config.yaml")

    try:
        current = load_yaml_config()
    except ConfigError:
        current = {}
    next_config = dict(current)
    for key, value in incoming.items():
        if key not in CONFIG_TOP_LEVEL_ALLOWLIST:
            raise HTTPException(status_code=400, detail=f"不支持编辑配置段：{key}")
        if key == "opencli" and isinstance(value, dict):
            value = dict(value)
            value.pop("path", None)
        next_config[key] = value

    legacy_opencli = next_config.get("opencli")
    if isinstance(legacy_opencli, dict):
        legacy_opencli.pop("path", None)

    _validate_scoring_config(next_config)

    save_yaml_config(next_config)
    get_settings.cache_clear()
    settings = get_settings()
    return _safe_config_response(settings.config)


@app.get("/api/health")
async def health() -> dict:
    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    return {
        "status": "ok",
        "ai_enabled": bool(ai_cfg.get("enabled")) and is_ai_available(),
        "config_error": bool(settings.config_error),
    }


@app.get("/api/context/status")
async def context_status() -> dict:
    """检查外部个人操作仓库的只读集成状态，不返回绝对路径或正文。"""
    return ContextRepository(settings.context_repo_path).status()


@app.get("/api/ready")
async def ready() -> JSONResponse:
    diagnostics = _deployment_diagnostics()
    status_code = 503 if diagnostics["status"] == "error" else 200
    return JSONResponse(status_code=status_code, content=diagnostics)


@app.get("/api/diagnostics/deployment")
async def deployment_diagnostics() -> dict:
    return _deployment_diagnostics()


@app.get("/api/ai/status")
async def ai_status() -> dict:
    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    api_key_configured = bool(os.getenv("OPENAI_API_KEY"))
    return {
        "enabled_in_config": bool(ai_cfg.get("enabled")),
        "available": bool(ai_cfg.get("enabled")) and is_ai_available(),
        "provider": str(ai_cfg.get("provider") or "openai_compatible"),
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "api_key_configured": api_key_configured,
        "base_url_configured": bool(os.getenv("OPENAI_BASE_URL")),
    }


@app.get("/api/jobs")
async def list_jobs(
    session: SessionDep,
    search: str | None = None,
    status: str | None = None,
    source: str | None = None,
    favorite: bool | None = None,
) -> list[dict]:
    jobs, source_links = _query_jobs(session, search=search, status=status, source=source, favorite=favorite)
    latest = _latest_score_map(session, [job.id for job in jobs if job.id])
    return [_job_payload(job, latest.get(job.id), source_links.get(job.id or 0, [])) for job in jobs]


@app.post("/api/jobs")
async def create_job(payload: JobCreate, session: SessionDep) -> dict:
    normalized = normalize_record(payload.model_dump(), source=payload.source)
    job = upsert_job_record(session, normalized)
    return _job_response(session, job)


@app.patch("/api/jobs/bulk")
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
    latest = _latest_score_map(session, [job.id for job in jobs if job.id])
    links = _source_links_map(session, [job.id for job in jobs if job.id])
    return {
        "updated": len(jobs),
        "jobs": [_job_payload(job, latest.get(job.id), links.get(job.id or 0, [])) for job in jobs],
    }


@app.patch("/api/jobs/{job_id}")
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
    return _job_response(session, job)


@app.post("/api/jobs/import")
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
        payload.update(_score_and_prune_imported_jobs(session, result["job_ids"], keep_top_scored))
    return payload


@app.get("/api/collect/runs")
async def list_collect_runs(session: SessionDep) -> list[SourceRun]:
    return session.exec(select(SourceRun).order_by(SourceRun.started_at.desc())).all()


def _latest_run_for_source(session: Session, source_label: str) -> SourceRun | None:
    return session.exec(
        select(SourceRun).where(SourceRun.source == source_label).order_by(SourceRun.started_at.desc())
    ).first()


def _source_status_payload(session: Session, source) -> dict:
    health = source_health(source)
    latest = _latest_run_for_source(session, source.label)
    return {
        "key": source.key,
        "label": source.label,
        "kind": source.kind,
        "enabled": source.enabled,
        "configured": health["configured"],
        "status": health["status"],
        "message": health["message"],
        "doctor": health.get("doctor"),
        "config": source_public_config(source),
        "latest_run": latest.model_dump() if latest else None,
    }


@app.get("/api/sources")
async def list_sources(session: SessionDep) -> list[dict]:
    return [_source_status_payload(session, source) for source in list_source_definitions(settings)]


@app.post("/api/collect/runs")
async def run_collection(session: SessionDep, source: str = "boss") -> dict:
    source_key = source.lower().strip()
    if source_key == "boss":
        return _run_source(session, "boss")
    raise HTTPException(status_code=400, detail="请使用 /api/sources/{source_key}/collect 运行配置化采集来源")


def _run_collector(session: Session, source_label: str, collector, raw_config: dict | None = None) -> dict:
    """公用:跑一个配置驱动的采集器并记录一次 SourceRun(boss/beBee 等共用)。"""
    run = SourceRun(source=source_label, raw_config=raw_config or {})
    session.add(run)
    session.commit()
    session.refresh(run)
    try:
        records = collector.collect()
        result = upsert_job_records(session, records)
        run.status = "success"
        run.fetched_count = len(records)
        run.created_count = result["created"]
        run.updated_count = result["updated"]
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)
    finally:
        report = getattr(collector, "report", None)
        if report:
            run.raw_config = {**(raw_config or {}), **report}
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()
        session.refresh(run)
    return run.model_dump()


def _run_source(session: Session, source_key: str) -> dict:
    source = get_source_definition(settings, source_key)
    if not source:
        raise HTTPException(status_code=404, detail=f"未知采集来源：{source_key}")
    health = source_health(source)
    if not source.enabled:
        raise HTTPException(status_code=403, detail=f"{source.label} 已禁用")
    if not health["configured"]:
        raise HTTPException(status_code=400, detail=health["message"])
    collector = build_source_collector(source)
    raw_config = {
        "source_key": source.key,
        "kind": source.kind,
        **source_public_config(source),
    }
    return _run_collector(session, source.label, collector, raw_config)


@app.post("/api/sources/{source_key}/collect")
async def collect_source(source_key: str, session: SessionDep) -> dict:
    return _run_source(session, source_key)


@app.post("/api/collect/bebee")
async def collect_bebee(session: SessionDep) -> dict:
    """beBee 渠道:抓 config.yaml bebee.role_urls 列表页 → 解析 JobPosting → 入库。"""
    return _run_source(session, "bebee")


def _run_wechat_collection(
    session: Session, links: list[str], bodies: dict[str, str], source_label: str
) -> dict:
    """公用：给定 mp.weixin 链接（+可选手动正文），跑采集器并记录一次 SourceRun。"""
    wechat_cfg = settings.wechat_config
    ai_cfg = settings.config.get("ai", {})
    ai_enabled = bool(ai_cfg.get("enabled")) and is_ai_available()
    fetch_cfg = wechat_cfg.get("fetch", {})

    run = SourceRun(source=source_label, raw_config={"input_links": len(links)})
    session.add(run)
    session.commit()
    session.refresh(run)

    collector: WeChatPasteCollector | None = None
    try:
        collector = WeChatPasteCollector(
            links=links,
            bodies=bodies,
            cfg=wechat_cfg,
            ai_enabled=ai_enabled,
            min_jobs=int(wechat_cfg.get("min_jobs_before_llm_fallback", 1)),
            rate_limit_seconds=float(fetch_cfg.get("rate_limit_seconds", 0) or 0),
            source=source_label,
        )
        records = collector.collect()
        result = upsert_job_records(session, records)
        run.status = "success"
        run.fetched_count = len(records)
        run.created_count = result["created"]
        run.updated_count = result["updated"]
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)
    finally:
        if collector is not None:
            run.raw_config = {"input_links": len(links), **collector.report}
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()
        session.refresh(run)

    return run.model_dump()


@app.post("/api/collect/wechat")
async def collect_wechat(payload: WeChatCollectRequest, session: SessionDep) -> dict:
    """公众号渠道：粘贴元宝回答 / mp.weixin 链接 / 文章正文 → 抓取解析 → 入库。"""
    wechat_cfg = settings.wechat_config
    source_label = wechat_cfg.get("source_label", "公众号")

    # 汇总并去重链接（text 与 urls 都过一遍正则抽链）
    links: list[str] = []
    seen: set[str] = set()
    for blob in [payload.text, *(payload.urls or [])]:
        if not blob:
            continue
        for link in extract_mp_links(blob):
            if link not in seen:
                seen.add(link)
                links.append(link)
    bodies = payload.bodies or {}
    for key in bodies:  # 手动粘正文的 key 应为 mp.weixin 链接，确保它进入处理队列
        for link in extract_mp_links(key):
            if link not in seen:
                seen.add(link)
                links.append(link)

    if not links and not bodies:
        raise HTTPException(
            status_code=400,
            detail="未识别到 mp.weixin.qq.com 链接；请粘贴元宝回答/文章链接，或改用手动粘贴文章正文",
        )

    return _run_wechat_collection(session, links, bodies, source_label)


@app.post("/api/collect/yuanbao")
async def collect_yuanbao(session: SessionDep, prompt: str | None = None) -> dict:
    """可选：用 Playwright 自动驱动元宝网页抓链接，再走同一抓取/解析管线。

    默认关闭，需在 config.yaml 设 wechat.yuanbao_automation.enabled=true 并安装
    requirements-automation.txt（playwright）。
    """
    wechat_cfg = settings.wechat_config
    source_label = wechat_cfg.get("source_label", "公众号")
    auto_cfg = wechat_cfg.get("yuanbao_automation", {})
    if not auto_cfg.get("enabled"):
        raise HTTPException(
            status_code=403,
            detail="元宝自动化未启用（config.yaml wechat.yuanbao_automation.enabled=false）",
        )

    try:
        from .services.yuanbao import collect_yuanbao_links

        links = collect_yuanbao_links(auto_cfg, prompt or auto_cfg.get("prompt_template", ""))
    except Exception as exc:  # 缺 playwright / 登录失效 / 选择器变动
        raise HTTPException(status_code=502, detail=f"元宝自动化失败：{exc}")

    if not links:
        raise HTTPException(status_code=502, detail="元宝未返回任何 mp.weixin 链接（可能需重新扫码登录或调整选择器）")

    return _run_wechat_collection(session, links, {}, source_label)


@app.get("/api/companies")
async def list_companies(session: SessionDep) -> list[dict]:
    return company_list_payload(session)


@app.get("/api/companies/{company_id}")
async def get_company(company_id: int, session: SessionDep) -> dict:
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    research = session.exec(select(ResearchItem).where(ResearchItem.company_id == company_id).order_by(ResearchItem.captured_at.desc())).all()
    jobs = session.exec(select(Job).where(Job.company_id == company_id).order_by(Job.collected_at.desc())).all()
    return {**company.model_dump(), "research_items": research, "jobs": jobs}


@app.patch("/api/companies/{company_id}")
async def update_company(company_id: int, payload: CompanyUpdate, session: SessionDep) -> Company:
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, key, value)
    company.updated_at = utc_now()
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


@app.get("/api/companies/{company_id}/research")
async def list_research(company_id: int, session: SessionDep) -> list[ResearchItem]:
    return session.exec(select(ResearchItem).where(ResearchItem.company_id == company_id).order_by(ResearchItem.captured_at.desc())).all()


@app.post("/api/companies/{company_id}/research")
async def add_research(company_id: int, payload: ResearchItemCreate, session: SessionDep) -> ResearchItem:
    if payload.source_type not in settings.research_sources:
        raise HTTPException(status_code=400, detail=f"source_type must be one of: {', '.join(settings.research_sources)}")
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    item_payload = payload.model_dump(exclude_none=True)
    if not item_payload.get("source_url"):
        item_payload["source_url"] = "manual://local-note"
    item = ResearchItem(company_id=company_id, **item_payload)
    session.add(item)
    company.updated_at = utc_now()
    session.add(company)
    session.commit()
    session.refresh(item)
    return item


@app.get("/api/profile")
async def get_profile(session: SessionDep) -> UserProfile:
    return _get_profile(session)


@app.put("/api/profile")
async def update_profile(payload: ProfileUpdate, session: SessionDep) -> UserProfile:
    profile = _get_profile(session)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, key, value)
    profile.updated_at = utc_now()
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


@app.get("/api/analytics/funnel")
async def analytics_funnel(session: SessionDep) -> dict:
    jobs = session.exec(select(Job).order_by(Job.collected_at.desc())).all()
    scores = _latest_score_map(session, [job.id for job in jobs if job.id])
    stale_jobs = find_stale_jobs(session, now=utc_now(), stale_days=settings.followup_stale_days)
    return build_funnel_payload(jobs, scores, stale_jobs)


@app.get("/api/jobs/{job_id}/score")
async def list_scores(job_id: int, session: SessionDep) -> list[FitScore]:
    return session.exec(select(FitScore).where(FitScore.job_id == job_id).order_by(FitScore.created_at.desc())).all()


@app.post("/api/jobs/{job_id}/score")
async def create_score(job_id: int, session: SessionDep) -> FitScore:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _score_job_into_db(session, job, _get_profile(session))


@app.get("/api/jobs/{job_id}/prep")
async def get_prep(job_id: int, session: SessionDep) -> InterviewPrep | None:
    return session.exec(select(InterviewPrep).where(InterviewPrep.job_id == job_id).order_by(InterviewPrep.created_at.desc())).first()


@app.post("/api/jobs/{job_id}/prep")
async def create_prep(job_id: int, session: SessionDep, ai: bool = Query(default=True)) -> InterviewPrep:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _build_prep_into_db(session, job, _get_profile(session), use_ai=ai)


@app.get("/api/exports/{kind}")
async def export_data(
    kind: str,
    session: SessionDep,
    format: str = Query(default="json"),
    search: str | None = None,
    status: str | None = None,
    source: str | None = None,
    favorite: bool | None = None,
) -> Response:
    generated_at = utc_now().strftime("%Y%m%d-%H%M%S")
    format = format.lower().strip()
    kind = kind.lower().strip()

    if kind == "jobs":
        jobs, source_links = _query_jobs(session, search=search, status=status, source=source, favorite=favorite)
        latest = _latest_score_map(session, [job.id for job in jobs if job.id])
        payload = [_job_payload(job, latest.get(job.id), source_links.get(job.id or 0, [])) for job in jobs]
        if format == "csv":
            return _download_response(f"jobs-{generated_at}.csv", export_jobs_csv(payload), "text/csv; charset=utf-8")
        if format == "json":
            return _download_response(f"jobs-{generated_at}.json", encode_json(payload), "application/json; charset=utf-8")

    if kind == "archive":
        profile = _get_profile(session)
        archive = build_archive_payload(
            profile=profile,
            jobs=session.exec(select(Job).order_by(Job.collected_at.desc())).all(),
            companies=session.exec(select(Company).order_by(Company.updated_at.desc())).all(),
            research_items=session.exec(select(ResearchItem).order_by(ResearchItem.captured_at.desc())).all(),
            scores=session.exec(select(FitScore).order_by(FitScore.created_at.desc())).all(),
            preps=session.exec(select(InterviewPrep).order_by(InterviewPrep.created_at.desc())).all(),
            drafts=session.exec(select(Draft).order_by(Draft.created_at.desc())).all(),
            tasks=session.exec(select(FollowUpTask).order_by(FollowUpTask.created_at.desc())).all(),
            interviews=session.exec(select(InterviewLog).order_by(InterviewLog.created_at.desc())).all(),
            runs=session.exec(select(SourceRun).order_by(SourceRun.started_at.desc())).all(),
            events=_application_events(session),
        )
        return _download_response(
            f"archive-{generated_at}.json",
            export_archive_json(schema_version="0005_application_events", generated_at=utc_now().isoformat(), payload=archive),
            "application/json; charset=utf-8",
        )

    raise HTTPException(status_code=400, detail="Unsupported export kind or format")


@app.post("/api/sprint/brief")
async def create_sprint_brief(
    session: SessionDep,
    top_n: int = Query(default=20, ge=1, le=100),
    prep_n: int = Query(default=5, ge=0, le=20),
    create_tasks: bool = Query(default=True),
    rescore: bool = Query(default=False),
) -> dict:
    """生成当天求职冲刺包：补评分、挑 Top 岗位、补面试准备和待办。"""
    return _create_sprint_payload(session, top_n=top_n, prep_n=prep_n, create_tasks=create_tasks, rescore=rescore)


@app.get("/api/drafts")
async def list_drafts(session: SessionDep) -> list[Draft]:
    return session.exec(select(Draft).order_by(Draft.created_at.desc())).all()


@app.post("/api/drafts")
async def create_draft(payload: DraftCreate, session: SessionDep) -> Draft:
    draft = Draft(**payload.model_dump())
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


@app.get("/api/follow-ups")
async def list_follow_ups(session: SessionDep) -> list[FollowUpTask]:
    return session.exec(select(FollowUpTask).order_by(FollowUpTask.due_date.asc(), FollowUpTask.created_at.desc())).all()


@app.get("/api/follow-ups/stale")
async def list_stale_follow_ups(session: SessionDep) -> list[dict]:
    """需跟进岗位：fit/interview 超过 stale_days 天无活动。只提醒，不自动联系。"""
    return find_stale_jobs(session, now=utc_now(), stale_days=settings.followup_stale_days)


@app.post("/api/follow-ups")
async def create_follow_up(payload: FollowUpTaskCreate, session: SessionDep) -> FollowUpTask:
    task = FollowUpTask(**payload.model_dump())
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@app.patch("/api/follow-ups/{task_id}")
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


@app.delete("/api/follow-ups/{task_id}")
async def delete_follow_up(task_id: int, session: SessionDep) -> dict:
    task = session.get(FollowUpTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    session.delete(task)
    session.commit()
    return {"deleted": True, "id": task_id}


@app.get("/api/jobs/{job_id}/events")
async def list_job_events(job_id: int, session: SessionDep) -> list[ApplicationEvent]:
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return _application_events(session, job_id=job_id)


@app.post("/api/jobs/{job_id}/events")
async def create_job_event(job_id: int, payload: ApplicationEventCreate, session: SessionDep) -> ApplicationEvent:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    event = ApplicationEvent(job_id=job_id, **payload.model_dump())
    session.add(event)
    session.commit()
    _recompute_job_status_from_events(session, job)
    session.refresh(event)
    return event


@app.delete("/api/events/{event_id}")
async def delete_job_event(event_id: int, session: SessionDep) -> dict:
    event = session.get(ApplicationEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    job = session.get(Job, event.job_id)
    session.delete(event)
    session.commit()
    if job:
        _recompute_job_status_from_events(session, job)
    return {"deleted": True, "id": event_id}


def _ordered_interviews(statement):
    return statement.order_by(InterviewLog.interview_date.desc(), InterviewLog.created_at.desc())


@app.get("/api/interviews")
async def list_all_interviews(session: SessionDep) -> list[InterviewLog]:
    """全局面试复盘时间线：跨岗位汇总，供「面试复盘」页签追溯。"""
    return session.exec(_ordered_interviews(select(InterviewLog))).all()


@app.get("/api/jobs/{job_id}/interviews")
async def list_job_interviews(job_id: int, session: SessionDep) -> list[InterviewLog]:
    return session.exec(_ordered_interviews(select(InterviewLog).where(InterviewLog.job_id == job_id))).all()


@app.post("/api/jobs/{job_id}/interviews")
async def create_interview(job_id: int, payload: InterviewLogCreate, session: SessionDep) -> InterviewLog:
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    log = InterviewLog(job_id=job_id, **payload.model_dump())
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


@app.patch("/api/interviews/{log_id}")
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


@app.delete("/api/interviews/{log_id}")
async def delete_interview(log_id: int, session: SessionDep) -> dict:
    log = session.get(InterviewLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Interview log not found")
    session.delete(log)
    session.commit()
    return {"deleted": True, "id": log_id}


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")

    index_file = FRONTEND_DIST / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend build not found")

    frontend_root = FRONTEND_DIST.resolve()
    requested = (FRONTEND_DIST / full_path).resolve()
    try:
        requested.relative_to(frontend_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc

    if full_path and requested.is_file():
        return FileResponse(requested)
    return FileResponse(index_file)
