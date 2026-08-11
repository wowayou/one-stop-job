"""决策聊天 + 候选入库路由（Phase R · R2，重点）。

/api/chat/* 与 /api/ingest：聊天线程/消息、候选岗位的确认入库/恢复/写看板。
从 main.py 原样搬出，行为逐字不变；依赖仅来自 deps/models/schemas/services/config，
不 import main。

红线（CLAUDE.md §2/§6）：`commit_candidates` 调 `upsert_job_records_with_ids`、
`board_write_candidates` 调 `write_candidate_to_board`、`create_chat_message` 按
`use_ai` 门控调用 AI——这些都是端点自身的既有行为，等价于原 main.py，允许留在这里。
但本文件不得把这些逻辑塞回 `services/chat_ingest.py`（那个模块有绊线测试禁止
import importer/upsert，只负责「只写聊天、不入库」的 ingest 落盘）。
"""

from __future__ import annotations

import copy
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import select
from starlette.concurrency import run_in_threadpool

from ..candidates import (
    CANDIDATE_COMMITTED,
    CANDIDATE_PENDING,
    CANDIDATE_SKIPPED,
    Candidate,
    strip_ui_only_fields,
)
from ..config import get_settings
from ..deps import SessionDep
from ..models import ChatMessage, ChatThread, Job, SourceRun, utc_now
from ..schemas import (
    CandidatesCommitRequest,
    ChatMessageCreate,
    ChatThreadBatchDeleteRequest,
    ChatThreadCreate,
    ChatThreadUpdate,
    IngestRequest,
)
from ..services.ai import is_ai_available
from ..services.board_write import write_candidate_to_board
from ..services.chat_ingest import (
    _chat_attachment_path,
    _chat_thread_payload,
    _delete_chat_thread,
    _persist_ingest_to_chat,
    attach_candidate_advice,
)
from ..services.chat_support import job_context, recent_conversation
from ..services.context_repository import ContextRepository, ContextRepositoryError
from ..services.decision_reply import reply_in_thread
from ..services.importer import upsert_job_records_with_ids
from ..services.ingest import score_job_ids
from ..services.queries import get_profile

router = APIRouter()


@router.get("/api/chat/threads")
async def list_chat_threads(session: SessionDep) -> list[dict]:
    threads = session.exec(select(ChatThread).order_by(ChatThread.updated_at.desc())).all()
    return [_chat_thread_payload(session, thread) for thread in threads]


@router.get("/api/chat/attachments/{attachment_id}")
async def get_chat_attachment(attachment_id: str) -> FileResponse:
    path = _chat_attachment_path(attachment_id, get_settings())
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[path.suffix.lower()]
    return FileResponse(path, media_type=media_type, filename=None)


@router.post("/api/chat/threads")
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


@router.get("/api/chat/threads/{thread_id}")
async def get_chat_thread(thread_id: int, session: SessionDep) -> dict:
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    messages = session.exec(
        select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.asc())
    ).all()
    return {"thread": _chat_thread_payload(session, thread), "messages": messages}


@router.get("/api/chat/threads/{thread_id}/context-preview")
async def chat_context_preview(thread_id: int, session: SessionDep) -> dict:
    """预览启用 AI 后本线程一次调用会发送给模型的内容，让用户发送前知道什么会离开本机。

    只读、与真正发送共用 `decision_context` / `job_context` / `recent_conversation`，
    避免预览与实际发送漂移。不含本次草稿文字和截图（由发送时决定），也不返回宿主机绝对路径。
    """
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")

    settings = get_settings()
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
    job_ctx = job_context(job)
    history = session.exec(
        select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.asc())
    ).all()
    conversation = recent_conversation(history)

    return {
        "ai_enabled": ai_enabled,
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini") if ai_enabled else None,
        "sections": sections,
        "context_chars_total": sum(section["chars"] for section in sections),
        "job_context": job_ctx,
        "conversation_count": len(conversation),
        "note": "以上是启用 AI 时本次调用会发送的固定上下文；本次输入的文字与截图会另外附上，未启用 AI 时不发送任何内容。",
    }


@router.patch("/api/chat/threads/{thread_id}")
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


@router.delete("/api/chat/threads/{thread_id}")
async def delete_chat_thread(thread_id: int, session: SessionDep) -> dict:
    """删除整个聊天线程:连同全部消息与消息里引用的截图附件一起清理,不可恢复。"""
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")

    _delete_chat_thread(session, thread, get_settings())
    session.commit()

    return {"deleted": True, "id": thread_id}


@router.post("/api/chat/threads/batch-delete")
async def batch_delete_chat_threads(payload: ChatThreadBatchDeleteRequest, session: SessionDep) -> dict:
    """批量删除聊天线程：逐个复用 `_delete_chat_thread`，整批只 commit 一次。

    不存在的 id 只在响应里标注 `not_found`,不影响其余 id 的删除,也不会让整个请求失败。
    """
    if len(payload.ids) > 100:
        raise HTTPException(status_code=400, detail="一次最多删除 100 个线程")

    settings = get_settings()
    ids = list(dict.fromkeys(payload.ids))  # 去重且保持提交顺序
    results: list[dict] = []
    for thread_id in ids:
        thread = session.get(ChatThread, thread_id)
        if not thread:
            results.append({"id": thread_id, "ok": False, "reason": "not_found"})
            continue
        _delete_chat_thread(session, thread, settings)
        results.append({"id": thread_id, "ok": True})

    session.commit()

    return {"results": results, "deleted": sum(1 for item in results if item["ok"])}


@router.post("/api/chat/threads/{thread_id}/messages")
async def create_chat_message(thread_id: int, payload: ChatMessageCreate, session: SessionDep) -> dict:
    """一轮决策问答。核心已下沉到 `services/decision_reply.reply_in_thread`，与 Telegram
    「手机上追问」共用同一条判断/落盘代码，避免两处结论漂移。"""
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")

    return await run_in_threadpool(
        reply_in_thread,
        session,
        thread,
        payload.content,
        image_data_url=payload.image_data_url,
        image_name=payload.image_name,
        use_ai=payload.use_ai,
        candidate_index=payload.candidate_index,
        settings=get_settings(),
    )


@router.post("/api/ingest")
async def ingest_text(payload: IngestRequest, session: SessionDep) -> dict:
    """抽取候选岗位并写入聊天；**默认不入库**，用户确认后再写 Job 表。

    认识的链接走专用采集器；其余文本/截图走 LLM freeform。原料（原文/截图）落在聊天附件与消息里。

    落盘后再补决策建议（`ingest.advice`）：两步是刻意的，见 `attach_candidate_advice` 的说明。
    Web 是前台请求、用户本来就在等，所以这里一次返回带建议的完整结果；建议失败不影响落盘结果。
    """
    result = await run_in_threadpool(_persist_ingest_to_chat, session, payload.text, payload.image_data_url)
    assistant_message_id = getattr(result.get("assistant_message"), "id", None)
    if isinstance(assistant_message_id, int):
        advice = await run_in_threadpool(attach_candidate_advice, session, assistant_message_id, get_settings())
        if advice["advice_count"]:
            # 建议写回的是消息 metadata 里的候选副本，这里同步替换响应中的对应字段，
            # 免得前端拿到「消息里有建议、candidates 里没有」的半旧状态。
            result["assistant_message"] = advice["assistant_message"]
            result["candidates"] = advice["candidates"]
    return result


@router.post("/api/chat/threads/{thread_id}/candidates/commit")
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
    candidates: list[Candidate] = list(meta.get("candidates") or [])
    if not candidates:
        raise HTTPException(status_code=400, detail="该消息没有可入库的候选岗位")

    indexes = sorted({int(i) for i in payload.indexes if isinstance(i, int) or str(i).isdigit()})
    if not indexes:
        # 空 indexes = 全部跳过
        for item in candidates:
            if item.get("status") == CANDIDATE_PENDING:
                item["status"] = CANDIDATE_SKIPPED
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
        if item.get("status") == CANDIDATE_COMMITTED and item.get("job_id"):
            continue
        # existing_job_id / duplicate_in_thread_id 是纯 UI 字段（分别是「已在岗位池」「与近期候选
        # 重复」），统一交给 strip_ui_only_fields 剔除；status/job_id 是候选自身的生命周期记账
        # 字段（不是 Job 表字段，且 Job.status 语义完全不同），upsert 前单独剔除。
        record = strip_ui_only_fields(item)
        record.pop("status", None)
        record.pop("job_id", None)
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
            candidates[pos]["status"] = CANDIDATE_COMMITTED
            candidates[pos]["job_id"] = job_id
            created_ids.extend(result.get("created_ids") or [])

        scored = score_job_ids(session, created_ids, get_profile(session))

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


@router.post("/api/chat/threads/{thread_id}/candidates/restore")
async def restore_candidates(thread_id: int, payload: CandidatesCommitRequest, session: SessionDep) -> dict:
    """把之前「跳过」的候选恢复成待选(pending),让用户能再次勾选入库。

    已入库(committed)的候选拒绝恢复(要撤销入库应去岗位池处理,这里不做反向 upsert)；
    已经是 pending 的幂等跳过。索引校验与 commit_candidates/board_write_candidates 一致。
    """
    thread = session.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    message = session.get(ChatMessage, payload.message_id)
    if not message or message.thread_id != thread_id or message.role != "assistant":
        raise HTTPException(status_code=404, detail="Candidate message not found")

    # 深拷贝原因同 commit_candidates/board_write_candidates：浅拷贝下嵌套候选 dict 仍与原对象
    # 共享引用，原地改完再整体赋值会被 SQLAlchemy 判为未变更而静默丢弃。
    meta = copy.deepcopy(message.metadata_json or {})
    candidates: list[Candidate] = meta.get("candidates") or []
    if not candidates:
        raise HTTPException(status_code=400, detail="该消息没有候选岗位")

    indexes = sorted({int(i) for i in payload.indexes if isinstance(i, int) or str(i).isdigit()})
    if not indexes:
        raise HTTPException(status_code=400, detail="请至少选择一个候选")
    for idx in indexes:
        if idx < 0 or idx >= len(candidates):
            raise HTTPException(status_code=400, detail=f"候选索引越界：{idx}")

    results: list[dict] = []
    for idx in indexes:
        item = candidates[idx]
        status = item.get("status") or CANDIDATE_PENDING
        if status == CANDIDATE_COMMITTED:
            results.append({"index": idx, "ok": False, "reason": "已入库无法恢复为待选"})
            continue
        if status == CANDIDATE_PENDING:
            results.append({"index": idx, "ok": True, "reason": "已是待选", "skipped": True})
            continue
        item["status"] = CANDIDATE_PENDING
        results.append({"index": idx, "ok": True, "reason": "已恢复为待选"})

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


@router.post("/api/chat/threads/{thread_id}/candidates/board-write")
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
    candidates: list[Candidate] = meta.get("candidates") or []
    if not candidates:
        raise HTTPException(status_code=400, detail="该消息没有候选岗位")

    indexes = sorted({int(i) for i in payload.indexes if isinstance(i, int) or str(i).isdigit()})
    if not indexes:
        raise HTTPException(status_code=400, detail="请至少选择一个候选")
    for idx in indexes:
        if idx < 0 or idx >= len(candidates):
            raise HTTPException(status_code=400, detail=f"候选索引越界：{idx}")

    settings = get_settings()
    try:
        ContextRepository(settings.context_repo_path).read_document("board")
    except ContextRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    results: list[dict] = []
    for idx in indexes:
        item = candidates[idx]
        if item.get("status") != CANDIDATE_COMMITTED or not item.get("job_id"):
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
