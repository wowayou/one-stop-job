"""升级发现路由（P0）：`GET /api/updates/check`。

只读、无副作用（除了一次出站 GET GitHub Releases），不下载也不安装任何东西。
业务逻辑全在 `services/updates.py`，这里只做参数解析与线程池调度——`updates` 用的是
同步 httpx.Client，必须丢进线程池，否则会阻塞事件循环里的其它请求。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from ..services import updates

router = APIRouter()


@router.get("/api/updates/check")
async def check_updates(
    force: bool = Query(default=False, description="true = 手动检查，绕过本地缓存"),
    startup: bool = Query(default=False, description="true = 启动静默检查，受 updates.check_on_startup 控制"),
) -> dict:
    """返回当前版本、最新正式版本与本机对应的安装包。

    `startup=true` 时若 `updates.check_on_startup=false`，直接回 `disabled` 不发请求——
    「启动是否静默检查」的策略留在 config.yaml 一处，不在前端重复一遍。
    """
    if startup and not updates.check_on_startup():
        return updates.disabled_result("启动时静默检查已关闭（updates.check_on_startup=false）。")
    return await run_in_threadpool(updates.check_for_updates, force=force)
