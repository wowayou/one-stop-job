"""公司调研路由（Phase R · R2）。

/api/companies 与 /api/companies/{id}/research：公司档案与证据沉淀。
从 main.py 原样搬出，行为逐字不变。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ..config import get_settings
from ..deps import SessionDep
from ..models import Company, Job, ResearchItem, utc_now
from ..schemas import CompanyUpdate, ResearchItemCreate
from ..services.companies import company_list_payload

router = APIRouter()


@router.get("/api/companies")
async def list_companies(session: SessionDep) -> list[dict]:
    return company_list_payload(session)


@router.get("/api/companies/{company_id}")
async def get_company(company_id: int, session: SessionDep) -> dict:
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    research = session.exec(select(ResearchItem).where(ResearchItem.company_id == company_id).order_by(ResearchItem.captured_at.desc())).all()
    jobs = session.exec(select(Job).where(Job.company_id == company_id).order_by(Job.collected_at.desc())).all()
    return {**company.model_dump(), "research_items": research, "jobs": jobs}


@router.patch("/api/companies/{company_id}")
async def update_company(company_id: int, payload: CompanyUpdate, session: SessionDep) -> Company:
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, key, value)
    company.updated_at = utc_now()
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


@router.get("/api/companies/{company_id}/research")
async def list_research(company_id: int, session: SessionDep) -> list[ResearchItem]:
    return session.exec(select(ResearchItem).where(ResearchItem.company_id == company_id).order_by(ResearchItem.captured_at.desc())).all()


@router.post("/api/companies/{company_id}/research")
async def add_research(company_id: int, payload: ResearchItemCreate, session: SessionDep) -> ResearchItem:
    settings = get_settings()
    if payload.source_type not in settings.research_sources:
        raise HTTPException(status_code=400, detail=f"source_type must be one of: {', '.join(settings.research_sources)}")
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    item_payload = payload.model_dump(exclude_none=True)
    if not item_payload.get("source_url"):
        item_payload["source_url"] = "manual://local-note"
    item = ResearchItem(company_id=company_id, **item_payload)
    session.add(item)
    company.updated_at = utc_now()
    session.add(company)
    session.commit()
    session.refresh(item)
    return item
