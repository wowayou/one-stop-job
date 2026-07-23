"""共享 FastAPI 依赖：目前只有 `get_session` / `SessionDep`。

从 `main.py` 抽出（Phase R · R1），供未来按域拆分的路由模块（R2）统一 import，
避免每个 router 都要重新拼一遍 `Annotated[Session, Depends(get_session)]`。
刻意保持最小：settings/engine 仍留在 `config.py` / `db.py`，本文件只收「被多路由
共享的 FastAPI 依赖声明」。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from .db import get_session

SessionDep = Annotated[Session, Depends(get_session)]
