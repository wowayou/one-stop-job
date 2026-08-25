from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import re
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

from . import config as config_module
from .config import ConfigError, get_config_path, get_settings, load_yaml_config, save_yaml_config
from .db import engine, init_db
from .models import ChatMessage, ChatThread, utc_now
from .schemas import (
    AiCredentialUpdate,
    AppConfigUpdate,
)
from .services.ai import active_provider_display as ai_active_provider_display, is_ai_available, probe_ai_connection
from .routers import chat, collect, companies, drafts, followups, interviews, jobs, misc, scoring
from .services.queries import validate_weights as _validate_weights
from .services.chat_ingest import (
    _find_ingest_message_by_tg_id,
    _find_ingest_thread_by_receipt,
    _ingest_thread_title,
    _persist_ingest_to_chat,
    _save_chat_image,
    attach_candidate_advice,
)
from .services.context_repository import ContextRepository
from .services.decision_reply import find_or_create_mobile_thread, reply_in_thread
from .services.job_ops import _read_upload_file
from .services.sources import list_source_definitions, source_health


settings = get_settings()
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
logger = logging.getLogger(__name__)

# 晨间定时采集与 Telegram `/collect` 手动补采跑的是同一个来源；写死两遍迟早漂移。
_DIGEST_COLLECT_SOURCE = "boss"


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

            # 命令只在纯文字消息上生效：一张截图配 `/collect` 这样的 caption 仍是材料，
            # 当成命令会把那张截图静默丢掉（§7 不静默丢数据）。
            has_attachment = bool(extracted.photo_file_id or extracted.document_file_id)
            command = None if has_attachment else telegram.parse_command(text)
            if command == "start":
                # 使用说明：不建 ingest 线程，只回一条本机→本人的操作提示（§2 机主回执豁免）。
                await run_in_threadpool(
                    telegram.send_message,
                    token,
                    chat_id,
                    "发送岗位链接、复制的 JD 文本或一张招聘截图即可；BOSS/智联链接请配上文本或截图。"
                    "识别结果需在 Web 聊天确认后才入库。"
                    "回复某条回执可把补充材料归入同一条线索。"
                    "想直接问我，就用 ? 或 /ask 开头（例：? 这个岗位值得聊吗）；"
                    "回复某条回执提问时，我会带上那条线索的上下文。"
                    "晨间采集失败时，发送 /collect 可手动重跑一次。",
                )
                continue

            if command == "collect":
                # 手动补采：晨间定时采集失败后手机上唯一的补救手段（此前只能等回到电脑前开 Web）。
                # 这是**本人显式触发**的一次人工采集，与 Web 上那颗采集按钮等价，红线 §3.3 一直
                # 允许；不是自动重试——定时那次失败仍然只记日志，绝不自行重跑。
                from datetime import date

                from .services.collect_ops import run_source
                from .services.daily_digest import build_new_jobs_text, mark_collect_success

                # 先回一句「开始了」：opencli 多关键词一跑就是一两分钟，其间轮询被占住，
                # 手机端没有任何反馈会以为消息石沉大海。
                await run_in_threadpool(
                    telegram.send_message, token, chat_id, "开始重跑采集，完成后回执（可能要一两分钟）。"
                )
                try:
                    with Session(engine) as session:
                        run = await run_in_threadpool(run_source, session, _DIGEST_COLLECT_SOURCE)
                        reply = telegram.summarize_collect_run(run)
                        if run.get("status") == "success":
                            await run_in_threadpool(mark_collect_success, settings.data_dir, date.today().isoformat())
                            # 补采成功就把日清单里空掉的「新岗位」段补上，否则还是得开 Web 才看得到。
                            new_jobs_text = await run_in_threadpool(build_new_jobs_text, session)
                            if new_jobs_text:
                                reply = f"{reply}\n{new_jobs_text}"
                except HTTPException as exc:
                    # run_source 对未知/禁用/未配置来源抛 HTTPException，detail 才是人话。
                    reply = f"采集未启动：{exc.detail}"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 补采失败只回一条可读原因，不影响轮询继续
                    logger.warning("Telegram 手动补采失败", exc_info=True)
                    reply = f"采集失败：{exc}"
                await run_in_threadpool(telegram.send_long_message, token, chat_id, reply)
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

            # 追问（? / ？ / /ask 开头）：这是提问，不是新材料，不走抽取、不产生候选。走和 Web
            # 决策聊天**完全相同**的分析链路（services/decision_reply），把结论直接发回手机——
            # 仍是本机→本人的通知（§2 机主回执豁免），不对外发送任何消息。
            # 回复某条回执时提问 → 落到那条 ingest 线索里，模型能看到该线索的对话上下文；
            # 否则落到固定的「手机提问」通用线程，避免每问一句就刷出一条新线程。
            question = telegram.parse_question(text)
            if question is not None:
                # `?2 这个值得聊吗`：一条线索里有多个候选时指名问第几个（回执/建议里的序号）。
                candidate_index, question = telegram.parse_candidate_index(question)
                answer = "分析失败，请稍后再试。"
                assistant_message_id: int | None = None
                try:
                    with Session(engine) as session:
                        thread: ChatThread | None = None
                        if extracted.reply_to_message_id:
                            replied_thread_id = await run_in_threadpool(
                                _find_ingest_thread_by_receipt, session, extracted.reply_to_message_id
                            )
                            thread = session.get(ChatThread, replied_thread_id) if replied_thread_id else None
                        if thread is None:
                            thread = await run_in_threadpool(find_or_create_mobile_thread, session)
                        answer_result = await run_in_threadpool(
                            reply_in_thread,
                            session,
                            thread,
                            question,
                            image_data_url=image_data_url,
                            candidate_index=candidate_index,
                        )
                        answer = telegram.summarize_analysis(
                            answer_result["analysis"],
                            ai_used=answer_result["ai_used"],
                            anchor=answer_result["anchor"],
                        )
                        assistant_message_id = getattr(answer_result.get("assistant_message"), "id", None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 追问失败只回一条可读原因，不影响轮询继续
                    logger.warning("Telegram 追问处理失败", exc_info=True)
                    answer = f"分析失败：{exc}"
                answer_tg_message_id = await run_in_threadpool(telegram.send_message, token, chat_id, answer)
                # 记下这条回答的 tg message_id：回复它继续追问或补材料时能找回同一条线索。
                if isinstance(answer_tg_message_id, int) and isinstance(assistant_message_id, int):
                    await run_in_threadpool(_record_telegram_receipt, assistant_message_id, answer_tg_message_id)
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

                # 决策建议：**回执发完之后**才算，单独再发一条。建议要额外做模型调用（provider
                # 不通时还有重试退避），捆在回执里会让「已收到」这句迟迟不到，手机端只能盲等。
                if isinstance(assistant_id, int):
                    try:
                        with Session(engine) as session:
                            advice = await run_in_threadpool(attach_candidate_advice, session, assistant_id)
                            advice_text = advice["advice_text"]
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - 建议是锦上添花，失败不回执错误、不影响已落盘候选
                        logger.warning("Telegram 候选建议生成失败", exc_info=True)
                        advice_text = ""
                    if advice_text:
                        await run_in_threadpool(telegram.send_message, token, chat_id, advice_text)


async def _daily_digest_loop() -> None:
    """每日晨间日清单：看板到期动作 + 库内需跟进岗位，经 Telegram 发给机主本人。

    默认关闭；仅当 config.yaml schedule.digest.enabled=true 且 Telegram 渠道可用
    （telegram.allowed_chat_id + TELEGRAM_BOT_TOKEN）时启动。只给机主本人发提醒
    （红线 §2 机主回执豁免），绝不向招聘方或任何第三方发消息；看板本身只读。
    """
    from datetime import date, datetime

    from .services import daily_digest, telegram
    from .services.context_repository import ContextRepositoryError
    from .services.daily_digest import build_daily_digest

    while True:
        current_settings = get_settings()
        raw_digest_cfg = current_settings.schedule_config.get("digest")
        digest_cfg = raw_digest_cfg if isinstance(raw_digest_cfg, dict) else {}
        digest_enabled = bool(digest_cfg.get("enabled"))
        autopilot_enabled = (
            str(current_settings.automation_config.get("mode") or "manual") == "autopilot"
            and bool(current_settings.automation_config.get("daily_scan", True))
        )
        if not digest_enabled and not autopilot_enabled:
            await asyncio.sleep(30)
            continue
        try:
            hour = int(digest_cfg.get("hour", 8))
            minute = int(digest_cfg.get("minute", 20))
        except (TypeError, ValueError):
            hour, minute = 8, 20
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            hour, minute = 8, 20

        token = telegram.bot_token()
        raw_chat_id = current_settings.telegram_config.get("allowed_chat_id")
        try:
            allowed_chat_id = int(str(raw_chat_id).strip()) if str(raw_chat_id or "").strip() else None
        except ValueError:
            allowed_chat_id = None

        # 复用同一状态文件和每日一次幂等位；自动驾驶不依赖 Telegram，通知只是可选消费端。
        state_path = daily_digest.digest_state_path(current_settings.data_dir)
        # 晨间日清单原有的显式采集开关保持兼容；自动驾驶只是把它默认打开。
        collect_first = autopilot_enabled or bool(digest_cfg.get("collect_first"))
        now = datetime.now()
        state = daily_digest.read_state(state_path)
        today_iso = date.today().isoformat()
        collection_due = (now.hour, now.minute) >= (hour, minute) and state.get("last_collected") != today_iso
        if collect_first and collection_due:
            collect_note = ""
            try:
                from .services.collect_ops import run_source

                with Session(engine) as session:
                    result = await run_in_threadpool(run_source, session, _DIGEST_COLLECT_SOURCE)
                if isinstance(result, dict) and result.get("status") != "success":
                    logger.warning("自动驾驶定时采集失败：%s", result.get("error"))
                    collect_note = daily_digest.format_collect_failure(result.get("error"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("自动驾驶定时采集失败，跳过本日采集", exc_info=True)
                collect_note = daily_digest.format_collect_failure(exc)
            daily_digest.write_state(state_path, last_collected=today_iso, collect_note=collect_note)
            state = daily_digest.read_state(state_path)

        if digest_enabled and daily_digest.should_send_now(now, state.get("last_sent"), hour, minute):
            # 回收站自动清理：晨间日清单时顺便清理超过保留期的软删除记录
            if not digest_enabled:
                await asyncio.sleep(900)
                continue
            if not token or allowed_chat_id is None:
                logger.warning(
                    "晨间日清单未发送：%s",
                    "缺少 TELEGRAM_BOT_TOKEN（.env）" if not token else f"telegram.allowed_chat_id 无效：{raw_chat_id!r}",
                )
                await asyncio.sleep(900)
                continue
            try:
                from .services.job_ops import auto_purge_trash
                with Session(engine) as session:
                    purged = auto_purge_trash(session)
                if purged["jobs"] or purged["companies"]:
                    logger.info("回收站自动清理：永久删除 %d 个岗位、%d 个公司", purged["jobs"], purged["companies"])
            except Exception:  # noqa: BLE001
                logger.warning("回收站自动清理失败", exc_info=True)

            try:
                with Session(engine) as session:
                    payload = await run_in_threadpool(build_daily_digest, session, date.today())
                text = payload.get("digest_text") or ""
                # 采集失败附注只在「就是今天采的」时候带上，避免昨天的失败漏进今天的清单。
                note = daily_digest.last_collected_note(state, today_iso)
                if note:
                    text = f"{text}\n\n{note}" if text else note
                delivered = True
                if text:
                    results = await run_in_threadpool(telegram.send_long_message, token, allowed_chat_id, text)
                    # send_message 吞异常返回 None（回执失败不该炸主流程），所以「发出去了」只能
                    # 看返回值。分段里有一段成功就算已发：重试会把成功那段重复发一遍，更糟。
                    delivered = any(item is not None for item in results)
                if delivered:
                    daily_digest.write_state(state_path, last_sent=today_iso)
                else:
                    # 关键：发送失败绝不能写 last_sent。早期版本无条件写，于是网络不通那天的
                    # 清单被永久标记为「已发」，直接丢掉（实测：Telegram 不可达当天一条没收到）。
                    logger.warning("晨间日清单未送达（Telegram 不可达？），保留未发状态，下个周期重试")
            except asyncio.CancelledError:
                raise
            except ContextRepositoryError as exc:
                logger.warning("晨间日清单跳过：看板不可读（%s）", exc)
            except Exception:  # noqa: BLE001 - 单次失败不终止循环，下个周期重试
                logger.warning("晨间日清单生成或发送失败", exc_info=True)
        await asyncio.sleep(900)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    tasks: list[asyncio.Task] = []
    if settings.telegram_config.get("enabled"):
        tasks.append(asyncio.create_task(_telegram_poll_loop()))
    # 循环常驻但在两种能力都关闭时只做低频配置检查；这样 Web 切换自动驾驶后无需重启。
    tasks.append(asyncio.create_task(_daily_digest_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title=settings.app_name, version="0.2.1", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_origins != ["*"],
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


CONFIG_TOP_LEVEL_ALLOWLIST = {"opencli", "job_sources", "general", "research", "wechat", "bebee", "collect", "scoring", "followup", "ai", "ingest", "telegram", "schedule", "automation", "reach"}
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


def _is_sensitive_key_name(key: Any) -> bool:
    """键名是否判为敏感（密钥类）。

    `*_env` 结尾的键（如 `api_key_env`/`base_url_env`/`model_env`）存的是环境变量名，
    不是密钥本身，`ai.providers` 靠它们在 config.yaml 里指名去哪个 `.env` 变量读取
    真实密钥（见 services/ai.py::_normalize_provider）——放行；字面量密钥键
    （`api_key`/`token`/`secret`/`password`/`authorization` 等，不以 `_env` 结尾）
    仍然拦截，红线不变：真实密钥值绝不允许进 config.yaml。
    """
    normalized = str(key).lower().replace("-", "_")
    if normalized.endswith("_env"):
        return False
    return any(marker in normalized for marker in SENSITIVE_CONFIG_KEYS)


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _is_sensitive_key_name(key):
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
            if _is_sensitive_key_name(key):
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
    return _safe_config_response(get_settings().config)


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


def _provider_key_status(ai_cfg: dict[str, Any]) -> dict[str, bool]:
    """按 `ai.providers` 里每条的 `api_key_env`，只回布尔「该 env 变量是否有值」。

    绝不读出/回传密钥本身——只喂 `os.getenv(name)` 的真值判断给前端，用来在设置页
    每张 provider 卡上显示「已配置 / 未配置」徽标（CLAUDE.md 红线：key 绝不进任何 GET 响应）。
    """
    providers_cfg = ai_cfg.get("providers")
    if not isinstance(providers_cfg, list):
        return {}
    status: dict[str, bool] = {}
    for entry in providers_cfg:
        if not isinstance(entry, dict):
            continue
        env_name = entry.get("api_key_env")
        if isinstance(env_name, str) and env_name:
            status[env_name] = bool(os.getenv(env_name))
    return status


@app.get("/api/ai/status")
async def ai_status() -> dict:
    ai_cfg = settings.config.get("ai", {})
    ai_cfg = ai_cfg if isinstance(ai_cfg, dict) else {}
    # model/key/base_url 反映「_chat 实际会先用的那个 provider」（配了 ai.providers 就是第一张卡），
    # 否则回退单一 OPENAI_*——修正过去恒显 OPENAI_MODEL 造成的「配 qwen 却显示 deepseek」不一致。
    active = ai_active_provider_display()
    return {
        "enabled_in_config": bool(ai_cfg.get("enabled")),
        "available": bool(ai_cfg.get("enabled")) and is_ai_available(),
        "provider": str(ai_cfg.get("provider") or "openai_compatible"),
        "model": active["model"],
        "api_key_configured": active["api_key_configured"],
        "base_url_configured": active["base_url_configured"],
        "provider_keys": _provider_key_status(ai_cfg),
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


_ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _clean_env_credential_value(value: str) -> str:
    """校验/清理写入 `.env` 的 value；不合规直接抛 400。

    绝不在异常消息里回显 value 本身（哪怕是截断片段）——错误信息只描述"哪种问题"。
    """
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="value 不能为空")
    try:
        cleaned.encode("ascii")
    except UnicodeEncodeError:
        raise HTTPException(status_code=400, detail="value 含非 ASCII 字符，请重新粘贴为纯英文数字") from None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cleaned):
        raise HTTPException(status_code=400, detail="value 不能包含换行符、回车符或其它控制字符")
    return cleaned


def _write_env_var(env_path: Path, env_name: str, value: str) -> None:
    """原地替换/追加 `.env` 里的 `ENV_NAME=...` 一行,保留其它所有行与注释;原子写入。"""
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    prefix = f"{env_name}="
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{env_name}={value}"
            break
    else:
        lines.append(f"{env_name}={value}")
    content = "\n".join(lines)
    if content:
        content += "\n"
    tmp_path = env_path.with_name(env_path.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, env_path)


@app.post("/api/ai/credentials")
async def set_ai_credential(payload: AiCredentialUpdate) -> dict:
    """把 AI provider 的密钥写入本机 `PROJECT_DIR/.env`,供设置页「设置 API Key」使用。

    安全边界(§3.4 密钥只进 .env):
    - 只接受大写环境变量名(`^[A-Z][A-Z0-9_]{0,63}$`),拒绝小写/特殊字符/`../` 等,防止
      被诱导写坏 `.env` 或做路径穿越联想攻击(env_name 只参与文件内容拼接,不参与路径)。
    - value 必须非空、纯 ASCII、不含换行/控制字符,防止把额外行注入 `.env`。
    - 响应体只回 `{"ok", "env_name"}`,**绝不返回/记录 value 明文**。
    - 写完立刻 `os.environ[env_name] = value` + `get_settings.cache_clear()`,同进程内
      "重新测试连接"就能读到新值,不需要重启。
    """
    env_name = payload.env_name
    if not _ENV_NAME_PATTERN.match(env_name or ""):
        raise HTTPException(
            status_code=400,
            detail="env_name 必须是大写环境变量名，匹配 ^[A-Z][A-Z0-9_]{0,63}$（大写字母开头，仅含大写字母/数字/下划线）",
        )
    cleaned_value = _clean_env_credential_value(payload.value)

    env_path = config_module.PROJECT_DIR / ".env"
    await run_in_threadpool(_write_env_var, env_path, env_name, cleaned_value)

    os.environ[env_name] = cleaned_value
    get_settings.cache_clear()
    return {"ok": True, "env_name": env_name}


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
