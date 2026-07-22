from __future__ import annotations

import asyncio
import base64
import copy
import logging
import math
import os
import re
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
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from .config import ConfigError, get_config_path, get_settings, load_yaml_config, save_yaml_config
from .db import engine, get_session, init_db
from .models import (
    AnalysisRun,
    ApplicationEvent,
    ChatMessage,
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
    UserProfile,
    utc_now,
)
from .schemas import (
    AppConfigUpdate,
    ApplicationEventCreate,
    CandidatesCommitRequest,
    ChatMessageCreate,
    ChatThreadCreate,
    ChatThreadUpdate,
    CompanyUpdate,
    DraftCreate,
    FollowUpTaskCreate,
    FollowUpTaskUpdate,
    InterviewLogCreate,
    InterviewLogUpdate,
    JobBulkUpdate,
    JobCreate,
    JobUpdate,
    IngestRequest,
    ProfileUpdate,
    ResearchItemCreate,
    WeChatCollectRequest,
)
from .services.ai import analyze_decision_chat_llm, configured_model, is_ai_available, probe_ai_connection, tailor_interview_prep_llm
from .services.analytics import build_funnel_payload
from .services.companies import company_list_payload
from .services.context_repository import ContextRepository, ContextRepositoryError
from .services.decision_chat import assistant_content, build_rule_analysis, mark_image_processed, merge_model_analysis
from .services.collectors import TabularFileCollector, WeChatPasteCollector
from .services.exporter import (
    build_archive_payload,
    encode_json,
    export_archive_json,
    export_jobs_csv,
)
from .services.followup import find_stale_jobs
from .services.importer import get_or_create_company, upsert_job_record, upsert_job_records, upsert_job_records_with_ids
from .services.ingest import run_ingest, score_job_ids
from .services.jobs import (
    company_map,
    job_ids_by_canonical_key,
    job_payload,
    latest_prep_map,
    latest_score_map,
    research_items_map,
    source_links_map,
)
from .services.normalizer import canonical_job_key, normalize_record, parse_recruiter, parse_salary
from .services.prep import build_interview_prep
from .services.scoring import DEFAULT_WEIGHTS, score_job
from .services.sources import build_source_collector, get_source_definition, list_source_definitions, source_health, source_public_config
from .services.board_write import write_candidate_to_board
from .services.wechat import extract_mp_links


settings = get_settings()
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
logger = logging.getLogger(__name__)


async def _telegram_poll_loop() -> None:
    """后台长轮询：拉取机主消息 → 抽取候选写入聊天 → 回执给机主本人。

    默认关闭；仅当 config.yaml telegram.enabled=true 且配置了 TELEGRAM_BOT_TOKEN 时启动。
    只处理白名单 allowed_chat_id 的消息；**不自动写 Job 表**，用户在 Web 聊天确认后才入库。
    """
    from .services import telegram

    tg_cfg = settings.telegram_config
    token = telegram.bot_token()
    raw_chat_id = tg_cfg.get("allowed_chat_id")
    try:
        allowed_chat_id = int(str(raw_chat_id).strip()) if str(raw_chat_id or "").strip() else None
    except ValueError:
        allowed_chat_id = None
    poll_timeout = int(tg_cfg.get("poll_timeout_seconds", tg_cfg.get("poll_timeout", 30)) or 30)
    if not token or allowed_chat_id is None:
        # enabled=true 但缺 token / chat id 时必须给出可见原因，否则「手机无回执」只能盲猜。
        logger.warning(
            "Telegram 已启用但轮询未启动：%s",
            "缺少 TELEGRAM_BOT_TOKEN（.env）" if not token else f"allowed_chat_id 无效：{raw_chat_id!r}（需要你本人的数字 chat id）",
        )
        return

    offset: int | None = None
    failure_streak = 0
    while True:
        try:
            updates = await run_in_threadpool(telegram.get_updates, token, offset, poll_timeout)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # 断网/接口异常时指数退避（5s → 10s → … 封顶 300s），避免网络恢复前空转刷日志。
            failure_streak += 1
            delay = min(5 * 2 ** (failure_streak - 1), 300)
            if failure_streak == 1:
                logger.warning("Telegram getUpdates 失败，稍后重试", exc_info=True)
            elif failure_streak < 4 or failure_streak % 4 == 0:
                logger.warning("Telegram getUpdates 持续失败（第 %d 次），%d 秒后重试", failure_streak, delay)
            await asyncio.sleep(delay)
            continue
        failure_streak = 0

        # 同一相册的多图会在同一批 updates 里各自成一条消息；只在本批次内记 media_group_id → thread_id，
        # 不跨批次持久化（跨批次到达的相册项走「回复回执」关联或干脆新建线程，属于可接受的降级）。
        media_group_threads: dict[str, int] = {}

        for update in updates:
            offset = int(update.get("update_id", 0)) + 1
            extracted = telegram.extract_message(update)
            chat_id = extracted.chat_id
            if chat_id != allowed_chat_id:
                continue
            text = extracted.text
            if text.strip() == "/start":
                # 使用说明：不建 ingest 线程，只回一条本机→本人的操作提示（§2 机主回执豁免）。
                await run_in_threadpool(
                    telegram.send_message,
                    token,
                    chat_id,
                    "发送岗位链接、复制的 JD 文本或一张招聘截图即可；BOSS/智联链接请配上文本或截图。"
                    "识别结果需在 Web 聊天确认后才入库。"
                    "回复某条回执可把补充材料归入同一条线索。",
                )
                continue

            image_data_url: str | None = None
            image_error: str | None = None
            if extracted.photo_file_id:
                image_data_url = await run_in_threadpool(telegram.download_photo_data_url, token, extracted.photo_file_id)
            elif extracted.document_file_id:
                # 「以文件发送」的图片：先按 mime/大小判断值不值得下载，避免为超限/不支持的文件白跑一趟网络。
                image_error = telegram.classify_document_image(extracted.document_mime_type, extracted.document_file_size)
                if image_error is None:
                    image_data_url = await run_in_threadpool(
                        telegram.download_photo_data_url, token, extracted.document_file_id
                    )
            if image_error:
                await run_in_threadpool(telegram.send_message, token, chat_id, image_error)
                continue
            if not text.strip() and not image_data_url:
                await run_in_threadpool(
                    telegram.send_message, token, chat_id, "请发送岗位链接、复制的招聘文本，或一张招聘截图。"
                )
                continue

            result: dict | None = None
            try:
                with Session(engine) as session:
                    # 关联到已有线索的优先级：本批相册分组 > 回复了某条回执；都没命中就照旧新建线程。
                    target_thread_id: int | None = None
                    if extracted.media_group_id and extracted.media_group_id in media_group_threads:
                        target_thread_id = media_group_threads[extracted.media_group_id]
                    elif extracted.reply_to_message_id:
                        target_thread_id = await run_in_threadpool(
                            _find_ingest_thread_by_receipt, session, extracted.reply_to_message_id
                        )
                    result = await run_in_threadpool(
                        _persist_ingest_to_chat, session, text, image_data_url, target_thread_id
                    )
                reply = telegram.summarize_ingest(result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Telegram ingest 失败", exc_info=True)
                reply = f"处理失败：{exc}"

            tg_message_id = await run_in_threadpool(telegram.send_message, token, chat_id, reply)

            if result is not None:
                thread_id = (result.get("thread") or {}).get("id")
                if extracted.media_group_id and isinstance(thread_id, int):
                    media_group_threads[extracted.media_group_id] = thread_id
                assistant_id = getattr(result.get("assistant_message"), "id", None)
                if isinstance(tg_message_id, int) and isinstance(assistant_id, int):
                    await run_in_threadpool(_record_telegram_receipt, assistant_id, tg_message_id)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    task: asyncio.Task | None = None
    if settings.telegram_config.get("enabled"):
        task = asyncio.create_task(_telegram_poll_loop())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


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

CONFIG_TOP_LEVEL_ALLOWLIST = {"opencli", "job_sources", "general", "research", "wechat", "bebee", "scoring", "followup", "ai", "ingest", "telegram"}
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

    # 个人上下文仓库：只报状态与 message，不回显绝对路径（红线 §10）。
    context_status_payload = ContextRepository(settings.context_repo_path).status()
    if not context_status_payload.get("configured"):
        checks.append(_check("context_repo", "ok", "未配置个人上下文仓库（可选）。"))
    elif context_status_payload.get("available"):
        checks.append(_check("context_repo", "ok", context_status_payload.get("message") or "个人上下文仓库只读连接正常。"))
    else:
        checks.append(
            _check(
                "context_repo",
                "warning",
                context_status_payload.get("message") or "个人上下文仓库不可用。",
            )
        )
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


def _chat_thread_payload(session: Session, thread: ChatThread) -> dict:
    message_count = session.exec(select(func.count(ChatMessage.id)).where(ChatMessage.thread_id == thread.id)).one()
    last_message = session.exec(
        select(ChatMessage.content).where(ChatMessage.thread_id == thread.id).order_by(ChatMessage.created_at.desc())
    ).first()
    job = session.get(Job, thread.job_id) if thread.job_id else None
    return {
        **jsonable_encoder(thread),
        "message_count": message_count,
        "last_message": last_message[:160] if last_message else None,
        "job": (
            {
                "id": job.id,
                "title": job.title,
                "company_name": job.company_name,
                "salary_text": job.salary_text,
                "city": job.city,
                "area": job.area,
            }
            if job
            else None
        ),
    }


def _decision_context() -> tuple[str, str, bool]:
    repository = ContextRepository(settings.context_repo_path)
    parts: list[str] = []
    rules_version = "local-profile"
    rules_loaded = False
    for key in ("decision_rules", "profile", "board"):
        try:
            document = repository.read_document(key)
        except ContextRepositoryError:
            continue
        if key == "decision_rules" and document.updated:
            rules_version = document.updated
        if key == "decision_rules":
            rules_loaded = True
        parts.append(f"[{key}]\n{document.content}")
    return "\n\n".join(parts)[:32000], rules_version, rules_loaded


def _job_context(job: Job | None) -> dict:
    """岗位事实（发送给 AI 的字段）。preview 与真正发送共用，避免两处漂移。"""
    if not job:
        return {}
    return {
        "title": job.title,
        "company_name": job.company_name,
        "salary": job.salary_text,
        "location": " · ".join(filter(None, [job.city, job.area])),
        "skills": job.skills,
        "description": job.description,
        "recruiter_message": job.recruiter,
    }


def _recent_conversation(messages: list[ChatMessage]) -> list[dict[str, str]]:
    """最近对话（最多 12 条，每条截断到 4000 字）。preview 与真正发送共用。"""
    return [{"role": item.role, "content": item.content[:4000]} for item in messages[-12:]]


def _save_chat_image(data_url: str, original_name: str | None) -> dict:
    header, encoded = data_url.split(",", 1)
    mime_type = header.removeprefix("data:").removesuffix(";base64")
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type)
    if extension is None:
        raise HTTPException(status_code=400, detail="截图格式不支持，仅支持 PNG、JPEG、WebP")
    attachment_id = f"{uuid.uuid4().hex}.{extension}"
    attachment_dir = settings.data_dir / "chat_attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)
    path = attachment_dir / attachment_id
    path.write_bytes(base64.b64decode(encoded, validate=True))
    return {
        "kind": "image",
        "id": attachment_id,
        "name": (original_name or "截图")[:180],
        "mime_type": mime_type,
        "size_bytes": path.stat().st_size,
    }


def _chat_attachment_path(attachment_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg|webp)", attachment_id):
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = (settings.data_dir / "chat_attachments" / attachment_id).resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    return path


def _remove_chat_attachment(attachment_id: str | None) -> None:
    """删除一个聊天截图文件；ID 非法或文件已缺失都容忍,不抛异常。"""
    if not attachment_id:
        return
    try:
        path = _chat_attachment_path(attachment_id)
    except HTTPException:
        return
    path.unlink(missing_ok=True)


@app.get("/api/chat/threads")
async def list_chat_threads(session: SessionDep) -> list[dict]:
    threads = session.exec(select(ChatThread).order_by(ChatThread.updated_at.desc())).all()
    return [_chat_thread_payload(session, thread) for thread in threads]


@app.get("/api/chat/attachments/{attachment_id}")
async def get_chat_attachment(attachment_id: str) -> FileResponse:
    path = _chat_attachment_path(attachment_id)
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[path.suffix.lower()]
    return FileResponse(path, media_type=media_type, filename=None)


@app.post("/api/chat/threads")
async def create_chat_thread(payload: ChatThreadCreate, session: SessionDep) -> dict:
    job = session.get(Job, payload.job_id) if payload.job_id else None
    if payload.kind == "job" and job is None:
        raise HTTPException(status_code=400, detail="岗位聊天必须关联一个存在的岗位")
    if payload.kind in {"general", "ingest"} and payload.job_id is not None:
        raise HTTPException(status_code=400, detail="通用/入库候选聊天不能关联岗位")

    if job is not None:
        existing = session.exec(
            select(ChatThread).where(ChatThread.kind == "job", ChatThread.job_id == job.id)
        ).first()
        if existing:
            return {**_chat_thread_payload(session, existing), "reused": True}

    title = payload.title or (f"{job.company_name} · {job.title}" if job else "新对话")
    thread = ChatThread(kind=payload.kind, job_id=job.id if job else None, title=title[:120])
    session.add(thread)
    session.commit()
    session.refresh(thread)
    return {**_chat_thread_payload(session, thread), "reused": False}


@app.get("/api/chat/threads/{thread_id}")
async def get_chat_thread(thread_id: int, session: SessionDep) -> dict:
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    messages = session.exec(
        select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.asc())
    ).all()
    return {"thread": _chat_thread_payload(session, thread), "messages": messages}


@app.get("/api/chat/threads/{thread_id}/context-preview")
async def chat_context_preview(thread_id: int, session: SessionDep) -> dict:
    """预览启用 AI 后本线程一次调用会发送给模型的内容，让用户发送前知道什么会离开本机。

    只读、与真正发送共用 `_decision_context` / `_job_context` / `_recent_conversation`，
    避免预览与实际发送漂移。不含本次草稿文字和截图（由发送时决定），也不返回宿主机绝对路径。
    """
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")

    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    ai_enabled = bool(ai_cfg.get("enabled")) and is_ai_available()

    repository = ContextRepository(settings.context_repo_path)
    sections: list[dict] = []
    for key in ("decision_rules", "profile", "board"):
        try:
            document = repository.read_document(key)
        except ContextRepositoryError:
            continue
        content = document.content or ""
        sections.append({"key": key, "chars": len(content), "content": content})

    job = session.get(Job, thread.job_id) if thread.job_id else None
    job_context = _job_context(job)
    history = session.exec(
        select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.asc())
    ).all()
    conversation = _recent_conversation(history)

    return {
        "ai_enabled": ai_enabled,
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini") if ai_enabled else None,
        "sections": sections,
        "context_chars_total": sum(section["chars"] for section in sections),
        "job_context": job_context,
        "conversation_count": len(conversation),
        "note": "以上是启用 AI 时本次调用会发送的固定上下文；本次输入的文字与截图会另外附上，未启用 AI 时不发送任何内容。",
    }


@app.patch("/api/chat/threads/{thread_id}")
async def update_chat_thread(thread_id: int, payload: ChatThreadUpdate, session: SessionDep) -> dict:
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    thread.title = payload.title[:120]
    thread.updated_at = utc_now()
    session.add(thread)
    session.commit()
    session.refresh(thread)
    return _chat_thread_payload(session, thread)


@app.delete("/api/chat/threads/{thread_id}")
async def delete_chat_thread(thread_id: int, session: SessionDep) -> dict:
    """删除整个聊天线程:连同全部消息与消息里引用的截图附件一起清理,不可恢复。"""
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")

    messages = session.exec(select(ChatMessage).where(ChatMessage.thread_id == thread_id)).all()
    attachment_ids = [
        message.metadata_json.get("attachment", {}).get("id")
        for message in messages
        if isinstance(message.metadata_json, dict) and isinstance(message.metadata_json.get("attachment"), dict)
    ]

    for message in messages:
        session.delete(message)
    session.delete(thread)
    session.commit()

    for attachment_id in attachment_ids:
        _remove_chat_attachment(attachment_id)

    return {"deleted": True, "id": thread_id}


@app.post("/api/chat/threads/{thread_id}/messages")
async def create_chat_message(thread_id: int, payload: ChatMessageCreate, session: SessionDep) -> dict:
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    job = session.get(Job, thread.job_id) if thread.job_id else None
    profile = _get_profile(session)

    attachment = _save_chat_image(payload.image_data_url, payload.image_name) if payload.image_data_url else None
    user_message = ChatMessage(
        thread_id=thread_id,
        role="user",
        content=payload.content,
        metadata_json={"attachment": attachment} if attachment else {},
    )
    session.add(user_message)
    thread.updated_at = utc_now()
    if thread.title == "新对话":
        thread.title = payload.content.replace("\n", " ")[:32]
    session.add(thread)
    session.commit()
    session.refresh(user_message)

    context_text, rules_version, context_available = _decision_context()
    rule_analysis = build_rule_analysis(
        message=payload.content,
        profile=profile,
        job=job,
        thread_kind=thread.kind,
        context_available=context_available,
        image_attached=bool(payload.image_data_url),
        policy_context=context_text,
    )
    history = session.exec(
        select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.asc())
    ).all()
    conversation = _recent_conversation(history)
    job_context = _job_context(job)

    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    ai_enabled = bool(ai_cfg.get("enabled")) and is_ai_available()
    model_analysis = None
    if ai_enabled:
        model_analysis = await run_in_threadpool(
            analyze_decision_chat_llm,
            context=context_text,
            conversation=conversation,
            job_context=job_context,
            rule_analysis=rule_analysis,
            image_data_url=payload.image_data_url,
        )
    ai_used = model_analysis is not None
    analysis = merge_model_analysis(rule_analysis, model_analysis)
    if ai_used and payload.image_data_url:
        mark_image_processed(analysis)
    run_status = "completed" if ai_used else ("fallback" if ai_enabled else "rules_only")
    provider = str(ai_cfg.get("provider") or "openai_compatible") if ai_enabled else "rules"

    assistant_message = ChatMessage(
        thread_id=thread_id,
        role="assistant",
        content=assistant_content(analysis, ai_used=ai_used),
        metadata_json={"analysis": analysis, "ai_used": ai_used, "run_status": run_status},
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)
    analysis_run = AnalysisRun(
        thread_id=thread_id,
        user_message_id=user_message.id or 0,
        assistant_message_id=assistant_message.id,
        rules_version=rules_version,
        provider=provider,
        model=configured_model() if ai_enabled else None,
        status=run_status,
        result_json=analysis,
    )
    session.add(analysis_run)
    session.commit()
    session.refresh(analysis_run)
    session.refresh(thread)
    session.refresh(user_message)
    session.refresh(assistant_message)

    return {
        "thread": _chat_thread_payload(session, thread),
        "user_message": user_message,
        "assistant_message": assistant_message,
        "analysis_run": analysis_run,
        "analysis": analysis,
        "ai_used": ai_used,
    }


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


@app.post("/api/ai/test")
async def ai_test() -> dict:
    """发一次不含个人信息的最小请求，验证 AI 是否真正可用并给出具体原因。

    与 /api/ai/status 的区别：status 只看 key 字符串是否存在；本端点真的发一次调用，
    区分「未配置 / 调用成功 / 调用失败（401/404/429/超时…）」，避免聊天里静默回退。
    """
    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    if not bool(ai_cfg.get("enabled")):
        return {"ok": False, "stage": "config", "reason": "config.yaml 未启用 ai.enabled。", "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini")}
    return await run_in_threadpool(probe_ai_connection)


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


def _ingest_thread_title(text: str, has_image: bool) -> str:
    """压缩空白、剔除 URL 后取前 24 字生成线程标题；全是链接/空文本时回退到可辨识占位。

    原文可能是几百字的招聘文案或裸链接；不截断会把整段文字怼进线程标题，在聊天头部
    （`.chat-head` / 侧栏 `.chat-thread`）撑出超宽内容，真机上表现为标题被撑破、
    聊天区出现异常留白（前端另配 CSS 截断兜底，见 styles.css）。
    """
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    without_urls = re.sub(r"https?://\S+", "", collapsed)
    without_urls = re.sub(r"\s+", " ", without_urls).strip()
    if without_urls:
        snippet = without_urls[:24]
        if len(without_urls) > 24:
            snippet += "…"
        return f"入库候选 · {snippet}"
    if collapsed:
        # 原文非空但全是链接：没有可读文字可截取，用时间戳兜底，避免标题变成裸链接或空字符串。
        return f"入库候选 · {utc_now().strftime('%m%d %H:%M')}"
    if has_image:
        return "入库候选 · 截图入库"
    return "入库候选"


def _find_ingest_thread_by_receipt(session: Session, reply_to_message_id: int) -> int | None:
    """按 Telegram「回复了哪条回执」找回对应的 ingest 线程 id；找不到返回 None。

    单用户、量小：只看最近 ~50 个 ingest 线程里任意一条 assistant 消息的
    `metadata_json.receipt_tg_message_id` 是否匹配即可，不加表不加列不做 JSON 索引。
    """
    threads = session.exec(
        select(ChatThread).where(ChatThread.kind == "ingest").order_by(ChatThread.updated_at.desc()).limit(50)
    ).all()
    thread_ids = [t.id for t in threads if t.id is not None]
    if not thread_ids:
        return None
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.thread_id.in_(thread_ids), ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc())
    ).all()
    for message in messages:
        if (message.metadata_json or {}).get("receipt_tg_message_id") == reply_to_message_id:
            return message.thread_id
    return None


def _record_telegram_receipt(chat_message_id: int, tg_message_id: int) -> None:
    """把这次回执的 Telegram message_id 记到对应 assistant 消息 metadata，供下次「回复回执」关联。

    必须整段重新赋值 metadata_json（深拷贝后加键再赋值），原地改共享的嵌套 dict 会被
    SQLAlchemy 判定成未变更而静默丢弃（上一批已经踩过的坑）。失败只记日志，不影响已完成的
    落盘/回执——这只是「下次能不能自动关联」的锦上添花，不是关键路径。
    """
    try:
        with Session(engine) as session:
            message = session.get(ChatMessage, chat_message_id)
            if message is None:
                return
            meta = copy.deepcopy(message.metadata_json or {})
            meta["receipt_tg_message_id"] = tg_message_id
            message.metadata_json = meta
            session.add(message)
            session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("记录 Telegram 回执 message_id 失败 chat_message_id=%s", chat_message_id, exc_info=True)


def _persist_ingest_to_chat(
    session: Session,
    text: str | None,
    image_data_url: str | None = None,
    target_thread_id: int | None = None,
) -> dict:
    """抽取候选 → 写入 kind=ingest 聊天线程（**不写 Job 表**）。HTTP 与 Telegram 共用。

    即便 unmatched 也建线程，保留原文/截图原料，满足「资料别删」。

    `target_thread_id`：可选。命中一个已有的 `kind="ingest"` 线程时，这次的 user/assistant
    消息追加进该线程而不是新建（用于 Telegram「回复回执」把补充材料关联回同一条线索，或
    同一相册的多张图片归到一起）；线程不存在或不是 ingest 类型时静默回退到新建线程——
    对应「回复的不是回执/太久远」的现状行为不变。
    """
    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    ai_enabled = bool(ai_cfg.get("enabled")) and is_ai_available()
    text = (text or "").strip()

    extract = run_ingest(
        text,
        wechat_cfg=settings.wechat_config,
        bebee_cfg=settings.bebee_config,
        ai_enabled=ai_enabled,
        image_data_url=image_data_url,
        manual_source=str(settings.ingest_config.get("manual_source") or "manual"),
    )

    candidates = extract["candidates"]
    # 只读标注：命中已入库岗位的 canonical_key 就打上 existing_job_id，供前端标「已在岗位池」
    # 并默认不勾选；仍允许勾选提交（commit 端点走 importer 合并已有记录，不重复建 Job，这里只读不写）。
    existing_by_key = job_ids_by_canonical_key(session, [c.get("canonical_key") for c in candidates])
    for candidate in candidates:
        key = candidate.get("canonical_key")
        if key and key in existing_by_key:
            candidate["existing_job_id"] = existing_by_key[key]

    reused_thread = None
    if target_thread_id is not None:
        maybe_thread = session.get(ChatThread, target_thread_id)
        if maybe_thread is not None and maybe_thread.kind == "ingest":
            reused_thread = maybe_thread

    if reused_thread is not None:
        thread = reused_thread
    else:
        title = _ingest_thread_title(text, bool(image_data_url))
        thread = ChatThread(kind="ingest", job_id=None, title=title)
        session.add(thread)
        session.commit()
        session.refresh(thread)

    attachment = _save_chat_image(image_data_url, None) if image_data_url else None
    user_content = text or ("[截图]" if attachment else "")
    user_message = ChatMessage(
        thread_id=thread.id or 0,
        role="user",
        content=user_content,
        metadata_json={"attachment": attachment} if attachment else {},
    )
    session.add(user_message)

    n = extract["candidate_count"]
    ai_error = extract.get("ai_error")
    if n:
        assistant_text = f"识别到 {n} 个候选岗位。请在下方勾选要入库的项；原文和截图已保留在本对话。"
    elif ai_error:
        # AI 已启用且理应能连通，但本次调用真的失败了：必须跟「AI 未启用」「AI 正常但没识别出岗位」区分开，
        # 否则用户只会看到「未认出」，误以为是内容问题而反复重试（真实场景常是模型不支持图片输入）。
        assistant_text = f"AI 抽取失败：{ai_error}。若发送的是截图，请确认所配模型支持图片输入（OPENAI_MODEL）。原料已保留。"
    elif extract.get("needs_ai"):
        assistant_text = "未认出可抓取链接，且 AI 未启用，无法从文本/截图抽取岗位。原料已保留；启用 AI 后可重发。"
    else:
        assistant_text = "未从链接、文本或截图中认出岗位。原料已保留，可补充更完整的 JD 或更清晰的截图后重发。"

    if extract.get("known_uncrawlable_hint"):
        assistant_text += (
            "\n检测到 BOSS/智联链接：该平台受风控无法直接抓取公开页，"
            "请复制 JD 文本或随手发一张截图（可与链接同一条消息）。链接已随原料保留。"
        )

    assistant_message = ChatMessage(
        thread_id=thread.id or 0,
        role="assistant",
        content=assistant_text,
        metadata_json={
            "candidates": candidates,
            "sources_report": extract["sources_report"],
            "unmatched": extract["unmatched"],
            "needs_ai": extract.get("needs_ai", False),
            "ai_error": ai_error,
            "known_uncrawlable_hint": extract.get("known_uncrawlable_hint", False),
            "run_status": (
                "rules_only" if not ai_enabled else "ai_failed" if ai_error else "completed" if n else "fallback"
            ),
        },
    )
    session.add(assistant_message)
    thread.updated_at = utc_now()
    session.add(thread)
    session.commit()
    session.refresh(thread)
    session.refresh(user_message)
    session.refresh(assistant_message)

    return {
        "thread": _chat_thread_payload(session, thread),
        "user_message": user_message,
        "assistant_message": assistant_message,
        "candidate_count": n,
        "unmatched": extract["unmatched"],
        "needs_ai": extract.get("needs_ai", False),
        "ai_error": ai_error,
        "known_uncrawlable_hint": extract.get("known_uncrawlable_hint", False),
        "sources_report": extract["sources_report"],
        "candidates": candidates,
        "appended": reused_thread is not None,
    }


@app.post("/api/ingest")
async def ingest_text(payload: IngestRequest, session: SessionDep) -> dict:
    """抽取候选岗位并写入聊天；**默认不入库**，用户确认后再写 Job 表。

    认识的链接走专用采集器；其余文本/截图走 LLM freeform。原料（原文/截图）落在聊天附件与消息里。
    """
    return await run_in_threadpool(_persist_ingest_to_chat, session, payload.text, payload.image_data_url)


@app.post("/api/chat/threads/{thread_id}/candidates/commit")
async def commit_candidates(thread_id: int, payload: CandidatesCommitRequest, session: SessionDep) -> dict:
    """用户明确勾选后，把聊天里的候选岗位写入 Job 表并尽力评分。"""
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    message = session.get(ChatMessage, payload.message_id)
    if not message or message.thread_id != thread_id or message.role != "assistant":
        raise HTTPException(status_code=404, detail="Candidate message not found")

    # 深拷贝理由同 board_write_candidates:浅拷贝下嵌套候选 dict 仍与原对象共享,
    # 原地改完再赋值会被 SQLAlchemy 判为未变更而静默丢弃。
    meta = copy.deepcopy(message.metadata_json or {})
    candidates = list(meta.get("candidates") or [])
    if not candidates:
        raise HTTPException(status_code=400, detail="该消息没有可入库的候选岗位")

    indexes = sorted({int(i) for i in payload.indexes if isinstance(i, int) or str(i).isdigit()})
    if not indexes:
        # 空 indexes = 全部跳过
        for item in candidates:
            if item.get("status") == "pending":
                item["status"] = "skipped"
        meta["candidates"] = candidates
        message.metadata_json = meta
        session.add(message)
        session.commit()
        session.refresh(message)
        return {
            "thread": _chat_thread_payload(session, thread),
            "assistant_message": message,
            "created": 0,
            "updated": 0,
            "scored": 0,
            "skipped": len(candidates),
        }

    to_upsert: list[dict] = []
    selected_positions: list[int] = []
    for idx in indexes:
        if idx < 0 or idx >= len(candidates):
            raise HTTPException(status_code=400, detail=f"候选索引越界：{idx}")
        item = candidates[idx]
        if item.get("status") == "committed" and item.get("job_id"):
            continue
        # existing_job_id 只是「已在岗位池」的只读标注，不是 Job 表字段，upsert 前必须剔除。
        record = {k: v for k, v in item.items() if k not in {"status", "job_id", "existing_job_id"}}
        to_upsert.append(record)
        selected_positions.append(idx)

    created = updated = scored = 0
    created_ids: list[int] = []
    if to_upsert:
        # 逐条 upsert，保证 candidate 索引与 job_id 一一对应（跨来源去重时 zip 会对不齐）。
        for pos, record in zip(selected_positions, to_upsert):
            result = upsert_job_records_with_ids(session, [record])
            created += result["created"]
            updated += result["updated"]
            job_id = (result.get("job_ids") or [None])[0]
            candidates[pos]["status"] = "committed"
            candidates[pos]["job_id"] = job_id
            created_ids.extend(result.get("created_ids") or [])

        scored = score_job_ids(session, created_ids, _get_profile(session))

        run = SourceRun(
            source="ingest_commit",
            status="success",
            fetched_count=len(to_upsert),
            created_count=created,
            updated_count=updated,
            raw_config={"indexes": indexes, "thread_id": thread_id, "message_id": payload.message_id},
            finished_at=utc_now(),
        )
        session.add(run)

    meta["candidates"] = candidates
    message.metadata_json = meta
    session.add(message)
    thread.updated_at = utc_now()
    session.add(thread)
    session.commit()
    session.refresh(message)
    session.refresh(thread)
    return {
        "thread": _chat_thread_payload(session, thread),
        "assistant_message": message,
        "created": created,
        "updated": updated,
        "scored": scored,
    }


@app.post("/api/chat/threads/{thread_id}/candidates/board-write")
async def board_write_candidates(thread_id: int, payload: CandidatesCommitRequest, session: SessionDep) -> dict:
    """本人在已入库候选上点「写入看板」：把一行卡片插入个人操作仓库看板「收集箱」列。

    每个 index 必须已 committed 且未 board_written，否则该条跳过并在响应里标注原因
    （幂等，重复调用安全，不会重复写入）。上下文仓库未配置/不可用整体 503；单条写入
    失败（例如看板缺「收集箱」列）只影响该条，不影响其它候选，也不改动 Job 表。
    """
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    message = session.get(ChatMessage, payload.message_id)
    if not message or message.thread_id != thread_id or message.role != "assistant":
        raise HTTPException(status_code=404, detail="Candidate message not found")

    # 深拷贝：避免和 message.metadata_json 共享嵌套 dict 引用。原地改共享对象会让
    # SQLAlchemy 在没有中间 flush 的情况下把 old/new 值判等,从而认为该列未变更、
    # 静默丢弃这次写入(纯 dict()/list() 浅拷贝挡不住这个坑)。
    meta = copy.deepcopy(message.metadata_json or {})
    candidates = meta.get("candidates") or []
    if not candidates:
        raise HTTPException(status_code=400, detail="该消息没有候选岗位")

    indexes = sorted({int(i) for i in payload.indexes if isinstance(i, int) or str(i).isdigit()})
    if not indexes:
        raise HTTPException(status_code=400, detail="请至少选择一个候选")
    for idx in indexes:
        if idx < 0 or idx >= len(candidates):
            raise HTTPException(status_code=400, detail=f"候选索引越界：{idx}")

    try:
        ContextRepository(settings.context_repo_path).read_document("board")
    except ContextRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    results: list[dict] = []
    for idx in indexes:
        item = candidates[idx]
        if item.get("status") != "committed" or not item.get("job_id"):
            results.append({"index": idx, "ok": False, "reason": "候选尚未入库，无法写回看板"})
            continue
        if item.get("board_written"):
            results.append({"index": idx, "ok": True, "reason": "已写入看板", "skipped": True})
            continue
        job = session.get(Job, item["job_id"])
        if not job:
            results.append({"index": idx, "ok": False, "reason": "对应岗位不存在"})
            continue
        try:
            write_candidate_to_board(settings, job)
        except ContextRepositoryError as exc:
            results.append({"index": idx, "ok": False, "reason": str(exc)})
            continue
        item["board_written"] = True
        results.append({"index": idx, "ok": True, "reason": "已写入看板"})

    meta["candidates"] = candidates
    message.metadata_json = meta
    session.add(message)
    thread.updated_at = utc_now()
    session.add(thread)
    session.commit()
    session.refresh(message)
    session.refresh(thread)
    return {
        "thread": _chat_thread_payload(session, thread),
        "assistant_message": message,
        "results": results,
    }


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
            chat_threads=session.exec(select(ChatThread).order_by(ChatThread.updated_at.desc())).all(),
            chat_messages=session.exec(select(ChatMessage).order_by(ChatMessage.created_at.asc())).all(),
            analysis_runs=session.exec(select(AnalysisRun).order_by(AnalysisRun.created_at.asc())).all(),
        )
        return _download_response(
            f"archive-{generated_at}.json",
            export_archive_json(schema_version="0006_decision_chat", generated_at=utc_now().isoformat(), payload=archive),
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
