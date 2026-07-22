"""Telegram 传输层：把手机发来的消息取回后端，再把落盘/评分摘要回执给机主本人。

设计要点（见 CLAUDE.md）：
- Telegram 只是"触发方式"，不是新数据源；真正的 `Job.source` 仍由各采集器决定（§8 来源解耦）。
- 长轮询（getUpdates）是后端主动向 api.telegram.org 发出站请求，后端无需对外暴露端口。
- 回执只发给白名单里的机主本人（`allowed_chat_id`），绝不向招聘方或任何外部对象发消息（§2 红线）。
- 网络统一走 httpx，带超时；token 只从 .env 读，不进 config.yaml（§配置约定）。
- 纯函数为主，便于单测：`get_updates` / `send_message` 可在测试里 monkeypatch。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"

# 「以文件发送」的图片：只认这三种 mime（与 schemas.IngestRequest.image_data_url 的 data URL 前缀一致）。
_SUPPORTED_DOCUMENT_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
# 6MB 对齐 IngestRequest.image_data_url 的 max_length=6_000_000（近似值，够用即可，不追求字节级精确）。
_MAX_DOCUMENT_IMAGE_BYTES = 6_000_000


def bot_token() -> str | None:
    """从环境变量读取 bot token；未配置时返回 None（渠道自然关闭）。"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    return token.strip() if token and token.strip() else None


def get_updates(token: str, offset: int | None, timeout: int) -> list[dict]:
    """长轮询拉取新消息。返回 update 列表；失败抛异常由调用方处理（不静默吞）。

    timeout 是 Telegram 端的长轮询秒数；httpx 读超时额外留出余量。
    """
    import httpx

    params: dict = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    url = f"{_API_BASE}/bot{token}/getUpdates"
    with httpx.Client(timeout=timeout + 10) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getUpdates 失败: {data.get('description')}")
    result = data.get("result")
    return result if isinstance(result, list) else []


def send_message(token: str, chat_id: int, text: str) -> int | None:
    """给指定 chat 发文本回执，返回 Telegram 侧的 message_id。

    仅用于给机主本人发系统通知；失败仅记日志不抛，返回 None——调用方（如「记录回执
    message_id 供后续回复关联」）不应强依赖非 None，取不到就跳过关联，不影响主流程。
    """
    import httpx

    url = f"{_API_BASE}/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, json={"chat_id": chat_id, "text": text[:4000]})
            resp.raise_for_status()
            data = resp.json()
    except Exception:  # noqa: BLE001 - 回执失败不影响已完成的落盘
        logger.warning("Telegram 回执发送失败 chat_id=%s", chat_id, exc_info=True)
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    message_id = (data.get("result") or {}).get("message_id")
    return message_id if isinstance(message_id, int) else None


@dataclass
class ExtractedMessage:
    """从一条 Telegram update 里解析出的关键信息，供轮询循环分派处理。"""

    chat_id: int | None
    text: str
    photo_file_id: str | None = None
    # 「以文件发送」的图片走 document，而不是 photo；mime/size 由调用方按策略判断是否下载。
    document_file_id: str | None = None
    document_mime_type: str | None = None
    document_file_size: int | None = None
    # 用户回复了哪条消息（如某次回执）；用于把补充材料关联回同一条 ingest 线索。
    reply_to_message_id: int | None = None
    # 同一相册（多图一次发送）里的分组 id；同批次内相同 id 的后续消息应追加到同一线程。
    media_group_id: str | None = None


def extract_message(update: dict) -> ExtractedMessage:
    """从一条 update 里解析出关键信息。

    链接不是唯一事实源：用户常直接发一张招聘截图，或「以文件发送」一张图片（document）。
    同时取出 reply_to_message_id（用户回复了哪条消息）和 media_group_id（相册分组），
    供轮询循环判断「这次材料应该追加到哪条已有线索」。
    """
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text") or message.get("caption") or ""

    photo_file_id: str | None = None
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        # Telegram 按尺寸从小到大给多份，取最后一份（最大）以便 OCR/识别。
        largest = photos[-1]
        if isinstance(largest, dict) and isinstance(largest.get("file_id"), str):
            photo_file_id = largest["file_id"]

    document_file_id: str | None = None
    document_mime_type: str | None = None
    document_file_size: int | None = None
    document = message.get("document")
    if isinstance(document, dict) and isinstance(document.get("file_id"), str):
        document_file_id = document["file_id"]
        mime = document.get("mime_type")
        document_mime_type = mime if isinstance(mime, str) else None
        size = document.get("file_size")
        document_file_size = size if isinstance(size, int) else None

    reply_to = message.get("reply_to_message")
    reply_to_message_id = (
        reply_to.get("message_id")
        if isinstance(reply_to, dict) and isinstance(reply_to.get("message_id"), int)
        else None
    )

    media_group_id = message.get("media_group_id")
    media_group_id = media_group_id if isinstance(media_group_id, str) else None

    return ExtractedMessage(
        chat_id=chat_id if isinstance(chat_id, int) else None,
        text=str(text),
        photo_file_id=photo_file_id,
        document_file_id=document_file_id,
        document_mime_type=document_mime_type,
        document_file_size=document_file_size,
        reply_to_message_id=reply_to_message_id,
        media_group_id=media_group_id,
    )


def classify_document_image(mime_type: str | None, file_size: int | None) -> str | None:
    """判断「以文件发送」的图片是否可以下载。返回 None 表示可以下载；否则是要回执的原因文案。

    只接受 PNG/JPEG/WebP；超过 6MB（对齐 IngestRequest 上限）也拒绝，避免下载一个注定
    会在 /api/ingest 校验里被拒的大文件——两处不必要地做重复工作。
    """
    mime = (mime_type or "").lower()
    if mime not in _SUPPORTED_DOCUMENT_IMAGE_MIME_TYPES:
        return "仅支持 PNG/JPEG/WebP 格式的图片，请转换后重发。"
    if file_size and file_size > _MAX_DOCUMENT_IMAGE_BYTES:
        return "图片超过 6MB 限制，请压缩后重发。"
    return None


def download_photo_data_url(token: str, file_id: str) -> str | None:
    """按 file_id 下载 Telegram 照片，返回 data URL（供截图抽取）。失败返回 None。

    两步：getFile 拿到 file_path，再从 file 端点下载字节；只处理常见图片类型。
    """
    import base64

    import httpx

    try:
        with httpx.Client(timeout=30) as client:
            meta = client.get(f"{_API_BASE}/bot{token}/getFile", params={"file_id": file_id})
            meta.raise_for_status()
            data = meta.json()
            if not data.get("ok"):
                return None
            file_path = (data.get("result") or {}).get("file_path")
            if not isinstance(file_path, str) or not file_path:
                return None
            blob = client.get(f"{_API_BASE}/file/bot{token}/{file_path}")
            blob.raise_for_status()
            content = blob.content
    except Exception:  # noqa: BLE001 - 下载失败降级，由调用方按纯文本处理
        logger.warning("Telegram 照片下载失败 file_id=%s", file_id, exc_info=True)
        return None

    suffix = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "jpg"
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(suffix, "image/jpeg")
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def summarize_ingest(result: dict) -> str:
    """把 ingest→聊天 的结果转成机主回执。默认不入库，只提示去 Web 确认。

    三种「没有可入库候选」的原因必须能区分开，否则「AI 已配置但调用失败」会被误读成
    「AI 未启用」或「内容认不出岗位」：
    - AI 调用异常（ai_error 非空）→ 明确说“调用失败”，不是「未认出」。
    - AI 未启用（needs_ai）→ 明确说「未启用」，提示去配置。
    - 前两种都不是 → 才是「规则/AI 都跑了，确实没认出岗位」。

    `appended`（配合 `thread` 里的 title）：本次材料被追加到一条已有 ingest 线程（回复回执
    或同一相册的后续图片），回执要明确说「已补充到『<线程标题>』」，而不是像新建线程那样
    说「已写入本地聊天」——否则用户分不清这是不是又开了一条新线索。
    """
    n = int(result.get("candidate_count") or 0)
    candidates = result.get("candidates") or []
    existing = sum(1 for c in candidates if isinstance(c, dict) and c.get("existing_job_id"))
    ai_error = result.get("ai_error")
    appended_title = (result.get("thread") or {}).get("title") if result.get("appended") else None

    parts: list[str] = []
    if appended_title and n > 0:
        parts.append(f"已补充到『{appended_title}』；识别到 {n} 个新候选（未入库），可在 Web「聊天」里确认。")
        if existing:
            parts.append(f"其中 {existing} 个已在岗位池。")
    elif appended_title:
        parts.append(f"已补充到『{appended_title}』，材料已保留。")
    elif n > 0:
        parts.append(
            f"识别到 {n} 个候选岗位，已写入本地聊天（未入库）。"
            "打开 Web「聊天」勾选要入库的项；原文和截图已保留。"
        )
        if existing:
            parts.append(f"其中 {existing} 个已在岗位池。")
    elif ai_error:
        parts.append(f"AI 抽取失败：{ai_error}。若发送的是截图，请确认所配模型支持图片输入（OPENAI_MODEL）。原料已保留。")
    elif result.get("needs_ai"):
        parts.append("未认出可抓取链接，且 AI 未启用，无法从文本/截图抽取。原料已保留；启用 AI 后可重发。")
    else:
        parts.append("未从链接、文本或截图中认出岗位。原料已保留，可补充更完整的 JD 或更清晰的截图后重发。")

    if result.get("known_uncrawlable_hint"):
        parts.append(
            "检测到 BOSS/智联链接：该平台受风控无法直接抓取公开页，"
            "请复制 JD 文本或随手发一张截图（可与链接同一条消息）。链接已随原料保留。"
        )
    return " ".join(parts)
