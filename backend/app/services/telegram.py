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

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"


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


def send_message(token: str, chat_id: int, text: str) -> None:
    """给指定 chat 发文本回执。仅用于给机主本人发系统通知，失败仅记日志不抛。"""
    import httpx

    url = f"{_API_BASE}/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, json={"chat_id": chat_id, "text": text[:4000]})
            resp.raise_for_status()
    except Exception:  # noqa: BLE001 - 回执失败不影响已完成的落盘
        logger.warning("Telegram 回执发送失败 chat_id=%s", chat_id, exc_info=True)


def extract_message(update: dict) -> tuple[int | None, str, str | None]:
    """从一条 update 里取出 (chat_id, text, photo_file_id)。

    链接不是唯一事实源：用户常直接发一张招聘截图。这里同时取出文字/caption 和
    最大尺寸的照片 file_id（若有），供调用方下载后走截图抽取。
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

    return (chat_id if isinstance(chat_id, int) else None), str(text), photo_file_id


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
    """把 ingest→聊天 的结果转成机主回执。默认不入库，只提示去 Web 确认。"""
    n = int(result.get("candidate_count") or 0)
    if n > 0:
        return (
            f"识别到 {n} 个候选岗位，已写入本地聊天（未入库）。"
            "打开 Web「聊天」勾选要入库的项；原文和截图已保留。"
        )
    if result.get("needs_ai"):
        return "未认出可抓取链接，且 AI 未启用，无法从文本/截图抽取。原料已保留；启用 AI 后可重发。"
    return "未从链接、文本或截图中认出岗位。原料已保留，可补充更完整的 JD 或更清晰的截图后重发。"
