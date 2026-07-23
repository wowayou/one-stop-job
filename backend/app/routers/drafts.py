"""草稿路由（Phase R · R2）。

/api/drafts：本地打招呼/回复草稿，供多次迭代累积。从 main.py 原样搬出。
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import select

from ..deps import SessionDep
from ..models import Draft
from ..schemas import DraftCreate

router = APIRouter()


@router.get("/api/drafts")
async def list_drafts(session: SessionDep) -> list[Draft]:
    return session.exec(select(Draft).order_by(Draft.created_at.desc())).all()


@router.post("/api/drafts")
async def create_draft(payload: DraftCreate, session: SessionDep) -> Draft:
    draft = Draft(**payload.model_dump())
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft
