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


@router.delete("/api/companies/{company_id}")
async def delete_company(company_id: int, session: SessionDep) -> dict:
    """软删除公司（移入回收站）。关联岗位不动——公司被隐藏后，岗位列表里公司名仍可见。"""
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if company.deleted_at is not None:
        return {"deleted": True, "id": company_id, "already_in_trash": True}
    company.deleted_at = utc_now()
    company.updated_at = utc_now()
    session.add(company)
    session.commit()
    return {"deleted": True, "id": company_id}


@router.post("/api/companies/{company_id}/restore")
async def restore_company(company_id: int, session: SessionDep) -> dict:
    """从回收站恢复公司。"""
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if company.deleted_at is None:
        return {"restored": True, "id": company_id, "already_active": True}
    company.deleted_at = None
    company.updated_at = utc_now()
    session.add(company)
    session.commit()
    return {"restored": True, "id": company_id}


@router.delete("/api/companies/{company_id}/purge")
async def purge_company(company_id: int, session: SessionDep) -> dict:
    """永久删除公司记录。不删关联岗位（岗位的 company_id 会被置空，company_name 保留）。"""
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    # 关联岗位的 company_id 置空，company_name 保留（岗位仍可见，只是不再链接到公司档案）
    jobs = session.exec(select(Job).where(Job.company_id == company_id)).all()
    for job in jobs:
        job.company_id = None
        job.updated_at = utc_now()
        session.add(job)
    # 调研证据也置空 company_id 引用——但 ResearchItem 有 NOT NULL 外键，直接删
    evidence = session.exec(select(ResearchItem).where(ResearchItem.company_id == company_id)).all()
    for item in evidence:
        session.delete(item)
    session.delete(company)
    session.commit()
    return {"purged": True, "id": company_id}


@router.get("/api/trash/companies")
async def list_trashed_companies(session: SessionDep) -> list[dict]:
    """回收站里的公司列表（已软删除）。"""
    return company_list_payload(session, include_deleted=True)


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
    if company.deleted_at is not None:
        raise HTTPException(status_code=400, detail="公司已移入回收站，请先恢复再编辑")
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
