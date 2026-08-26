"""Telegram 传输层：把手机发来的消息取回后端，再把落盘/评分摘要回执给机主本人。

设计要点（见 CLAUDE.md）：
- Telegram 只是"触发方式"，不是新数据源；真正的 `Job.source` 仍由各采集器决定（§8 来源解耦）。
- 长轮询（getUpdates）是后端主动向 api.telegram.org 发出站请求，后端无需对外暴露端口。
- 回执只发给白名单里的机主本人（`allowed_chat_id`），绝不向招聘方或任何外部对象发消息（§2 红线）。
- 网络统一走 httpx，带超时；token 只从 .env 读，不进 config.yaml（§配置约定）。
- 纯函数为主，便于单测：`get_updates` / `send_message` 可在测试里 monkeypatch。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"

# 「这是一个提问」的显式前缀：`?` / `？` / `/ask`（可带 `@botname`）。见 parse_question。
_QUESTION_PREFIX = re.compile(r"^(?:/ask(?:@\S+)?|[?？])\s*", re.IGNORECASE)

# 「整条消息就是一个斜杠命令」：`/start` / `/collect`（Telegram 在群里会补 `@botname`）。
# 必须要求命令后没有正文，否则 `/ask 这个岗位怎么样` 会被当成命令吃掉，追问就没了。见 parse_command。
_COMMAND = re.compile(r"^/([A-Za-z_]+)(?:@\S+)?$")

# 「以文件发送」的图片：只认这三种 mime（与 schemas.IngestRequest.image_data_url 的 data URL 前缀一致）。
_SUPPORTED_DOCUMENT_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
# 6MB 对齐 IngestRequest.image_data_url 的 max_length=6_000_000（近似值，够用即可，不追求字节级精确）。
_MAX_DOCUMENT_IMAGE_BYTES = 6_000_000


def redact_token(text: str, token: str | None) -> str:
    """把 bot token 从任何将要抛出或写日志的文本里抹掉。

    **为什么必须有这一层**：Telegram 要求 token 出现在 URL 路径里，而 httpx 的
    `HTTPStatusError` 消息带完整 URL——`raise_for_status()` 抛出后被上层
    `logger.warning(..., exc_info=True)` 一记，明文 token 就永久留在 `data/app/backend.log`
    里了（实测踩到：409 Conflict 那批日志每行都带着 token）。红线 §3.4 不泄密，日志同样算。

    连 token 的后半段（`<bot_id>:<secret>` 里的 secret）也一起抹——只泄后半段照样能被拼回去。
    """
    if not token or not text:
        return text
    cleaned = text.replace(token, "***")
    secret = token.split(":", 1)[-1]
    if secret and secret != token and len(secret) >= 8:
        cleaned = cleaned.replace(secret, "***")
    return cleaned


def bot_token() -> str | None:
    """从环境变量读取 bot token；未配置时返回 None（渠道自然关闭）。"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    return token.strip() if token and token.strip() else None


def get_updates(token: str, offset: int | None, timeout: int) -> list[dict]:
    """长轮询拉取新消息。返回 update 列表；失败抛异常由调用方处理（不静默吞）。

    timeout 是 Telegram 端的长轮询秒数；httpx 读超时额外留出余量。

    **异常一律经 `redact_token` 重包成 RuntimeError，并 `from None` 断开异常链**——
    `raise_for_status()` 的原始异常消息带着含 token 的完整 URL，`from exc` 会让上层
    `exc_info=True` 把它连同 __cause__ 一起打进日志。调用方（main.py 的轮询循环）本来就
    catch 宽泛 Exception 做指数退避，换成 RuntimeError 不改变任何行为。
    """
    import httpx

    params: dict = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    url = f"{_API_BASE}/bot{token}/getUpdates"
    try:
        with httpx.Client(timeout=timeout + 10) as client:
            resp = client.get(url, params=params)
            status = resp.status_code
            body = resp.text
    except Exception as exc:  # noqa: BLE001 - 传输层异常消息也可能带上 URL
        raise RuntimeError(
            f"Telegram getUpdates 请求失败: {redact_token(f'{type(exc).__name__}: {exc}', token)}"
        ) from None
    if status >= 400:
        raise RuntimeError(f"Telegram getUpdates HTTP {status}: {redact_token(body[:300], token)}")
    try:
        data = json.loads(body)
    except ValueError:
        raise RuntimeError("Telegram getUpdates 返回了非 JSON 内容") from None
    if not isinstance(data, dict) or not data.get("ok"):
        description = data.get("description") if isinstance(data, dict) else None
        raise RuntimeError(f"Telegram getUpdates 失败: {redact_token(str(description), token)}")
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
    except Exception as exc:  # noqa: BLE001 - 回执失败不影响已完成的落盘
        # 不用 exc_info：异常消息里的 URL 带着 token（见 redact_token 的说明）。
        logger.warning(
            "Telegram 回执发送失败 chat_id=%s：%s",
            chat_id,
            redact_token(f"{type(exc).__name__}: {exc}", token),
        )
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    message_id = (data.get("result") or {}).get("message_id")
    return message_id if isinstance(message_id, int) else None


def split_message(text: str, chunk_size: int = 3500) -> list[str]:
    """把长文本按行边界拆成不超过 chunk_size 的段（Telegram 单条上限 4096）。

    纯函数便于单测；单行超长时硬切，绝不丢内容。空文本返回空列表。
    """
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:chunk_size])
            line = line[chunk_size:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > chunk_size:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_long_message(token: str, chat_id: int, text: str) -> list[int | None]:
    """分段发送长文本（如晨间日清单），每段一条消息；返回各段 message_id。

    与 send_message 相同的机主回执边界：只发给白名单机主本人。
    """
    return [send_message(token, chat_id, chunk) for chunk in split_message(text)]


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
    # 这条消息自身的 Telegram message_id；编辑事件的 message_id 和被编辑的原消息完全一致
    # （Telegram 编辑不会分配新 id），因此可以用它反查「这是不是在编辑本 bot 处理过的消息」。
    message_id: int | None = None
    # True 表示这条 update 来自 edited_message（用户编辑了一条老消息），不是全新消息。
    is_edit: bool = False


def extract_message(update: dict) -> ExtractedMessage:
    """从一条 update 里解析出关键信息。

    链接不是唯一事实源：用户常直接发一张招聘截图，或「以文件发送」一张图片（document）。
    同时取出 reply_to_message_id（用户回复了哪条消息）和 media_group_id（相册分组），
    供轮询循环判断「这次材料应该追加到哪条已有线索」。

    `message` 和 `edited_message` 必须分开处理，不能简单 `or` 到一起：过去两者被当成同一件事，
    结果是用户编辑一条老消息（哪怕只是改个错别字）会被当成全新消息重新走一遍完整 ingest，
    刷出一堆多余的新线程/新回执——这正是本次要修的问题，见 main.py 里对 is_edit 的处理。
    """
    edited = update.get("edited_message")
    is_edit = isinstance(edited, dict)
    message = edited if is_edit else (update.get("message") or {})
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text") or message.get("caption") or ""
    raw_message_id = message.get("message_id")
    message_id = raw_message_id if isinstance(raw_message_id, int) else None

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
        message_id=message_id,
        is_edit=is_edit,
    )


def parse_command(text: str) -> str | None:
    """整条消息就是一个斜杠命令时返回小写命令名（不含 `/`），否则 None。

    只认「光杆命令」：`/collect`、`/start`、`/collect@mybot`。带正文的一律不是命令——
    `/ask 这个值得聊吗` 仍归 `parse_question`，`/xxx` 之外的普通文本仍归材料处理。
    调用方只处理自己认识的命令名，不认识的原样落回既有分支（如光杆 `/ask` 仍按材料走）。
    """
    match = _COMMAND.match((text or "").strip())
    return match.group(1).lower() if match else None


def parse_question(text: str) -> str | None:
    """区分「向 AI 追问」和「补充材料」：只认显式前缀，返回去掉前缀后的问题，否则 None。

    为什么用显式前缀而不是「回复回执的纯文字就算提问」：回复回执补一段 JD 文本是已有的
    正常用法（多图/多条消息合并成同一岗位），靠内容猜意图必然误判，把材料吃成提问就等于
    丢材料。前缀是零歧义的：`?` / `？` / `/ask`（后两种大小写与空格都容忍）。

    只带前缀、后面没有实际内容时返回 None——空问题没有分析价值，交给既有的「请发送岗位
    链接/文本/截图」提示即可。
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    match = _QUESTION_PREFIX.match(stripped)
    if not match:
        return None
    return stripped[match.end() :].strip() or None


def parse_candidate_index(question: str) -> tuple[int | None, str]:
    """从问题开头取出「问第几个候选」，返回 (0 基索引 或 None, 去掉序号后的问题)。

    一条线索里常有好几个候选（一次发来两张截图 = 两个岗位）。没有指名手段的话，回答只能
    默认第一个，你却无从知道也无从更换——`?2 这个值得聊吗` 就是那个指名手段。
    认阿拉伯数字和 ①-⑩ 两种写法（回执里用的正是 ①②，直接照着打即可）。
    """
    stripped = (question or "").strip()
    match = re.match(r"^(?:([1-9]\d?)|([①②③④⑤⑥⑦⑧⑨⑩]))[\s、.，,:：]*", stripped)
    if not match:
        return None, stripped
    rest = stripped[match.end() :].strip()
    if not rest:
        # 只发了个序号、没有问题：当成普通文本交回上层，避免把「2」这种残缺输入当成指名。
        return None, stripped
    if match.group(1):
        return int(match.group(1)) - 1, rest
    return "①②③④⑤⑥⑦⑧⑨⑩".index(match.group(2)), rest


def summarize_analysis(analysis: dict, *, ai_used: bool, anchor: dict | None = None) -> str:
    """把一次决策分析压成手机上能一眼读完的回答。

    字段口径与 Web 决策卡完全一致（同一份 analysis），只是排版更短：结论、下一步、最多两条
    风险、最多两条待确认。规则模式要明说，否则用户分不清「模型给的判断」和「AI 没跑起来
    时的模板兜底」。

    `anchor`：这次答的是哪个候选（`decision_reply.resolve_thread_anchor` 的结果）。必须回显——
    一条线索里有多个候选时，光看「B / 邻近可接受」根本不知道说的是哪个；同一条线索还有别的
    候选时再补一句怎么换（`?2 …`）。
    """
    lines: list[str] = []
    if anchor and anchor.get("kind") == "candidate" and anchor.get("label"):
        lines.append(f"针对 {anchor['label']}")
    lines.append(str(analysis.get("summary") or "已完成分析"))
    lines.append(
        f"判断：{analysis.get('priority') or '待确认'} / {analysis.get('direction') or '待确认'}"
        f" → {analysis.get('next_action') or '补充信息'}"
    )
    if action_text := str(analysis.get("action_text") or "").strip():
        lines.append(f"下一步：{action_text}")
    risks = [str(item) for item in analysis.get("risks") or [] if str(item).strip()][:2]
    if risks:
        lines.append(f"风险：{'；'.join(risks)}")
    uncertainties = [str(item) for item in analysis.get("uncertainties") or [] if str(item).strip()][:2]
    if uncertainties:
        lines.append(f"待确认：{'；'.join(uncertainties)}")
    if anchor and anchor.get("kind") == "candidate" and (anchor.get("total") or 0) > 1:
        lines.append(f"这条线索有 {anchor['total']} 个候选；换一个问：?2 你的问题")
    if not ai_used:
        lines.append("（规则模式：AI 未启用或本次调用不可用）")
    return "\n".join(lines)


def summarize_collect_run(run: dict) -> str:
    """把一次手动补采（`/collect`）的 SourceRun 结果压成一条手机可读的回执。

    成功时报的是**初筛口径**：抓取多少、区域过滤掉多少、已在池中刷新多少、待筛多少
    （计数措辞与采集线索里那条消息共用 `collect_ops.collect_run_summary`）。新岗位不再由
    采集直接入库，所以有待筛项时必须给出「去哪儿处理」，否则手机上看完不知道下一步。

    失败原因复用日清单那套抬头压缩：多关键词采集器会把每个关键词的 dict repr 塞进 error，
    原样发到手机上就是几 KB 噪音。逐条原因留在 backend.log 与 Web 采集面板。

    这是本机→本人的状态通知（§2 机主回执豁免），不对外发送任何消息。
    """
    # 局部导入：collect_ops 会拉起采集器/importer 一整条链路，传输层没必要在模块级依赖它。
    from .collect_ops import collect_run_summary
    from .daily_digest import collect_failure_headline

    # 来源名直接贴在「采集」前面（「BOSS直聘采集完成…」）；取不到就退成「采集完成…」，
    # 中间不留空格，免得缺来源时读成「采集 采集失败」。
    label = str(run.get("source") or "").strip()
    if run.get("status") != "success":
        return f"{label}采集失败：{collect_failure_headline(run.get('error')) or '原因未知'}。详情见 Web 采集面板。"

    report = run.get("raw_config")
    report = report if isinstance(report, dict) else {}
    text = f"{label}采集完成。{collect_run_summary(int(run.get('fetched_count') or 0), report)}"
    if int(report.get("pending") or 0):
        text += "打开 Web「聊天」里的采集线索勾选入库；不勾的不会进岗位池。"
    return text


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

    `duplicate_merge`：本次候选和某条既有线索的候选**全部**重复，被自动并入那条线程（不是
    用户主动关联），措辞要和 `appended` 的「已补充」区分开，明确说「与已有线索重复」，
    避免用户误以为是自己回复了回执。`duplicate_count`：即便新建了线程，其中部分候选和
    近期线索重复时，仍要提示一句，但不改变「未入库」的整体结论。

    注意本回执**不含决策建议**：建议要额外做模型调用，算完再发会拖慢「已收到」这句本身，
    因此由轮询循环在发完本回执后另发一条（见 main.py 与 `chat_ingest.attach_candidate_advice`）。
    """
    n = int(result.get("candidate_count") or 0)
    candidates = result.get("candidates") or []
    existing = sum(1 for c in candidates if isinstance(c, dict) and c.get("existing_job_id"))
    duplicate_count = int(result.get("duplicate_count") or 0)
    ai_error = result.get("ai_error")
    thread_title = (result.get("thread") or {}).get("title")
    appended_title = thread_title if result.get("appended") and not result.get("duplicate_merge") else None
    duplicate_merge_title = thread_title if result.get("duplicate_merge") else None

    parts: list[str] = []
    if duplicate_merge_title:
        parts.append(f"与已有线索重复，已归入『{duplicate_merge_title}』（未入库）。")
    elif appended_title and n > 0:
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
        if duplicate_count:
            parts.append(f"其中 {duplicate_count} 个与近期候选重复。")
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
