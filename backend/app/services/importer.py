from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import tuple_
from sqlmodel import Session, select

from ..models import Company, Job, JobSourceLink


def get_or_create_company(session: Session, name: str) -> Company:
    clean_name = (name or "未知公司").strip()
    company = session.exec(select(Company).where(Company.name == clean_name)).first()
    if company:
        return company
    company = Company(name=clean_name)
    session.add(company)
    session.flush()
    return company


@dataclass
class ImportLookup:
    companies: dict[str, Company]
    source_links: dict[tuple[str, str], JobSourceLink]
    source_link_jobs: dict[tuple[str, str], Job]
    legacy_jobs: dict[tuple[str, str], Job]
    canonical_jobs: dict[str, Job]


def upsert_job_records(session: Session, records: list[dict[str, Any]]) -> dict[str, int]:
    result = upsert_job_records_with_ids(session, records)
    return {"created": result["created"], "updated": result["updated"]}


def upsert_job_records_with_ids(session: Session, records: list[dict[str, Any]]) -> dict[str, Any]:
    created = 0
    updated = 0
    job_ids: list[int] = []
    created_ids: list[int] = []
    updated_ids: list[int] = []
    now = datetime.now(timezone.utc)
    lookup = _prepare_import_lookup(session, records)

    for record in records:
        job, was_created = _upsert_job_record(session, record, now, lookup)
        if job.id is not None and job.id not in job_ids:
            job_ids.append(job.id)
        if was_created:
            created += 1
            if job.id is not None:
                created_ids.append(job.id)
        else:
            updated += 1
            if job.id is not None:
                updated_ids.append(job.id)

    session.commit()
    return {"created": created, "updated": updated, "job_ids": job_ids, "created_ids": created_ids, "updated_ids": updated_ids}


def upsert_job_record(session: Session, record: dict[str, Any]) -> Job:
    now = datetime.now(timezone.utc)
    lookup = _prepare_import_lookup(session, [record])
    job, _was_created = _upsert_job_record(session, record, now, lookup)
    session.commit()
    session.refresh(job)
    return job


def _prepare_import_lookup(session: Session, records: list[dict[str, Any]]) -> ImportLookup:
    companies = _companies_by_name(session, records)
    source_link_jobs, source_links = _jobs_by_source_link(session, records)
    legacy_jobs = _jobs_by_legacy_key(session, records)
    canonical_jobs = _jobs_by_canonical_key(session, records)
    return ImportLookup(
        companies=companies,
        source_links=source_links,
        source_link_jobs=source_link_jobs,
        legacy_jobs=legacy_jobs,
        canonical_jobs=canonical_jobs,
    )


def split_known_records(session: Session, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把一批采集记录拆成 (已在岗位池的, 全新的)。**只读**，一个字节都不写。

    采集人工初筛用：已在池子里的岗位是你早就筛过的，照旧 upsert 刷新快照（薪资/在招状态/
    last_seen_at）；只有全新的才需要你过目，进候选等勾选。

    命中判定与 `_upsert_job_record` 完全一致（来源链接 → 旧 source/external_id → canonical_key），
    复用同一个 `_find_existing_job`，避免两处判重逻辑漂移。

    注意：这里**不能**用 `_prepare_import_lookup`——它内部的 `_companies_by_name` 会
    `session.add` 出公司行（写操作），而本函数在「还没决定要不要入库」的阶段被调用，
    不该留下任何痕迹。`_find_existing_job` 只查三张岗位映射，companies 给空 dict 即可。
    """
    if not records:
        return [], []
    source_link_jobs, source_links = _jobs_by_source_link(session, records)
    lookup = ImportLookup(
        companies={},
        source_links=source_links,
        source_link_jobs=source_link_jobs,
        legacy_jobs=_jobs_by_legacy_key(session, records),
        canonical_jobs=_jobs_by_canonical_key(session, records),
    )
    known: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    for record in records:
        existing, _reason = _find_existing_job(record, lookup)
        (known if existing is not None else fresh).append(record)
    return known, fresh


def _companies_by_name(session: Session, records: list[dict[str, Any]]) -> dict[str, Company]:
    names = {(record.get("company_name") or "未知公司").strip() for record in records}
    existing = session.exec(select(Company).where(Company.name.in_(names))).all() if names else []
    companies = {company.name: company for company in existing}
    missing = [name for name in names if name and name not in companies]
    for name in missing:
        company = Company(name=name)
        session.add(company)
        companies[name] = company
    if missing:
        session.flush()
    return companies


def _jobs_by_source_link(session: Session, records: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], Job], dict[tuple[str, str], JobSourceLink]]:
    keys = list({(record["source"], record["external_id"]) for record in records})
    if not keys:
        return {}, {}
    links = session.exec(
        select(JobSourceLink).where(tuple_(JobSourceLink.source, JobSourceLink.external_id).in_(keys))
    ).all()
    job_ids = [link.job_id for link in links]
    jobs = session.exec(select(Job).where(Job.id.in_(job_ids))).all() if job_ids else []
    job_map = {job.id: job for job in jobs if job.id is not None}
    return (
        {(link.source, link.external_id): job_map[link.job_id] for link in links if link.job_id in job_map},
        {(link.source, link.external_id): link for link in links},
    )


def _jobs_by_legacy_key(session: Session, records: list[dict[str, Any]]) -> dict[tuple[str, str], Job]:
    keys = list({(record["source"], record["external_id"]) for record in records})
    if not keys:
        return {}
    jobs = session.exec(select(Job).where(tuple_(Job.source, Job.external_id).in_(keys))).all()
    return {(job.source, job.external_id): job for job in jobs}


def _jobs_by_canonical_key(session: Session, records: list[dict[str, Any]]) -> dict[str, Job]:
    keys = [key for key in {record.get("canonical_key") for record in records} if key]
    if not keys:
        return {}
    jobs = session.exec(select(Job).where(Job.canonical_key.in_(keys))).all()
    return {job.canonical_key: job for job in jobs if job.canonical_key}


def _upsert_job_record(session: Session, record: dict[str, Any], now: datetime, lookup: ImportLookup) -> tuple[Job, bool]:
    company = lookup.companies[(record.get("company_name") or "未知公司").strip()]
    existing, match_reason = _find_existing_job(record, lookup)

    payload = {
        **record,
        "company_id": company.id,
        "company_name": company.name,
        "last_seen_at": now,
        "updated_at": now,
    }
    if existing:
        keep_fields = {"id", "status", "favorite", "created_at"}
        if match_reason == "canonical_key":
            keep_fields.update({"source", "external_id", "url", "collected_at"})
        for key, value in payload.items():
            if key not in keep_fields and value is not None and hasattr(existing, key):
                setattr(existing, key, value)
        session.add(existing)
        session.flush()
        _upsert_source_link(session, existing, record, now, lookup)
        return existing, False

    job = Job(**payload)
    session.add(job)
    session.flush()
    _register_job_lookup(lookup, job, record)
    _upsert_source_link(session, job, record, now, lookup)
    return job, True


def _find_existing_job(record: dict[str, Any], lookup: ImportLookup) -> tuple[Job | None, str]:
    source_key = (record["source"], record["external_id"])
    existing = lookup.source_link_jobs.get(source_key)
    if existing is not None:
        return existing, "source_link"

    existing = lookup.legacy_jobs.get(source_key)
    if existing is not None:
        return existing, "legacy_key"

    canonical_key = record.get("canonical_key")
    if canonical_key:
        existing = lookup.canonical_jobs.get(canonical_key)
        if existing is not None:
            return existing, "canonical_key"

    return None, ""


def _register_job_lookup(lookup: ImportLookup, job: Job, record: dict[str, Any]) -> None:
    lookup.legacy_jobs[(job.source, job.external_id)] = job
    if job.canonical_key:
        lookup.canonical_jobs[job.canonical_key] = job
    lookup.source_link_jobs[(record["source"], record["external_id"])] = job


def _upsert_source_link(session: Session, job: Job, record: dict[str, Any], now: datetime, lookup: ImportLookup) -> None:
    if job.id is None:
        raise RuntimeError("Cannot create source link before job id is assigned")

    source_key = (record["source"], record["external_id"])
    link = lookup.source_links.get(source_key)
    payload = {
        "job_id": job.id or 0,
        "source": record["source"],
        "external_id": record["external_id"],
        "url": record.get("url"),
        "title": record.get("title"),
        "company_name": record.get("company_name"),
        "published_at": record.get("published_at"),
        "raw_payload": jsonable_encoder(record),
        "last_seen_at": now,
    }
    if link:
        for key, value in payload.items():
            setattr(link, key, value)
        session.add(link)
    else:
        link = JobSourceLink(**payload)
        session.add(link)
        lookup.source_links[source_key] = link
    lookup.source_link_jobs[source_key] = job
