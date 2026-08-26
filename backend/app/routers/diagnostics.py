"""诊断与失败恢复路由（P3/P4.5）。

`GET /api/diagnostics/deployment`（部署自检）留在 main.py——它与模块级 `settings` 缓存
耦合，搬动有行为变化风险。本文件只放新增的三个：

- `GET  /api/diagnostics/runtime` — 只读状态汇总（版本/进程/.env/config.yaml/AI/数据目录/网络信号）
- `GET  /api/diagnostics/logs`    — 脱敏日志尾部（供「复制脱敏日志」）
- `POST /api/diagnostics/backup`  — 本地 SQLite 在线备份，口径同 `scripts/app.sh backup`

前两个纯只读；备份只往 `data/backups/<时间戳>/` 新建文件，不覆盖既有备份、不动原库。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from ..services import diagnostics

router = APIRouter(prefix="/api/diagnostics")


@router.get("/runtime")
async def runtime() -> dict:
    """诊断页的全部只读信息。绝不回传任何密钥值——`.env` 一侧只有变量名与布尔。"""
    return await run_in_threadpool(diagnostics.runtime_diagnostics)


@router.get("/logs")
async def logs(lines: int = Query(default=diagnostics.LOG_TAIL_MAX_LINES, ge=1, le=diagnostics.LOG_TAIL_MAX_LINES)) -> dict:
    """脱敏后的日志尾部。桌面端后端不落盘日志，此时 `available=false` 并说明原因。"""
    return await run_in_threadpool(diagnostics.log_tail, lines)


@router.post("/backup")
async def backup() -> dict:
    """把数据库与聊天附件在线备份到 `data/backups/<时间戳>/`（只新建，不删不覆盖）。"""
    return await run_in_threadpool(diagnostics.create_backup)
