from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from .config import ConfigError, get_config_path, get_settings, load_yaml_config, save_yaml_config
from .db import engine, init_db
from .models import ChatMessage, utc_now
from .schemas import (
    AppConfigUpdate,
)
from .services.ai import is_ai_available, probe_ai_connection
from .routers import chat, collect, companies, drafts, followups, interviews, jobs, misc, scoring
from .services.queries import validate_weights as _validate_weights
from .services.chat_ingest import (
    _find_ingest_message_by_tg_id,
    _find_ingest_thread_by_receipt,
    _ingest_thread_title,
    _persist_ingest_to_chat,
    _save_chat_image,
)
from .services.context_repository import ContextRepository
from .services.job_ops import _read_upload_file
from .services.sources import list_source_definitions, source_health


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

            if extracted.is_edit:
                # 编辑消息：本质是"改了内容的老消息"，不是新线索。只有能在本机找到当初落盘的
                # 那条 user 消息时才处理(归入原线程，见 _find_ingest_message_by_tg_id)；找不到
                # (太久远/不是本 bot 处理过的消息)就静默忽略——不回执、不建线程，避免"手滑改
                # 个错别字"刷出一堆新线索(用户抱怨的核心，现状确认：之前 message/edited_message
                # 被 `or` 到一起不加区分，编辑等同于收到一条全新消息)。
                original_thread_id: int | None = None
                if extracted.message_id is not None:
                    with Session(engine) as session:
                        original_message = await run_in_threadpool(
                            _find_ingest_message_by_tg_id, session, extracted.message_id
                        )
                        original_thread_id = original_message.thread_id if original_message else None
                if original_thread_id is None:
                    continue

                edit_image_data_url: str | None = None
                if extracted.photo_file_id:
                    edit_image_data_url = await run_in_threadpool(
                        telegram.download_photo_data_url, token, extracted.photo_file_id
                    )
                elif extracted.document_file_id:
                    edit_image_error = telegram.classify_document_image(
                        extracted.document_mime_type, extracted.document_file_size
                    )
                    if edit_image_error is None:
                        edit_image_data_url = await run_in_threadpool(
                            telegram.download_photo_data_url, token, extracted.document_file_id
                        )

                edit_result: dict | None = None
                try:
                    with Session(engine) as session:
                        edit_result = await run_in_threadpool(
                            _persist_ingest_to_chat,
                            session,
                            text,
                            edit_image_data_url,
                            original_thread_id,
                            None,
                            extracted.message_id,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.warning("Telegram 编辑消息处理失败", exc_info=True)
                    continue

                if edit_result is not None:
                    thread_title = (edit_result.get("thread") or {}).get("title") or ""
                    await run_in_threadpool(
                        telegram.send_message, token, chat_id, f"已按编辑更新归入『{thread_title}』。"
                    )
                continue

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
                        _persist_ingest_to_chat,
                        session,
                        text,
                        image_data_url,
                        target_thread_id,
                        extracted.message_id,
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

# 按域拆分的路由（Phase R · R2）：逐组从 main.py 迁往 routers/，此处统一挂载。
app.include_router(followups.router)
app.include_router(interviews.router)
app.include_router(companies.router)
app.include_router(drafts.router)
app.include_router(jobs.router)
app.include_router(chat.router)
app.include_router(collect.router)
app.include_router(scoring.router)
app.include_router(misc.router)


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
    _validate_weights(scoring.get("weights"))


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
