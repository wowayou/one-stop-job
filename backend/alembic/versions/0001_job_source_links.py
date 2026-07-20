"""Add job source links and job freshness fields.

Revision ID: 0001_job_source_links
Revises:
Create Date: 2026-06-08
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from alembic import op


revision = "0001_job_source_links"
down_revision = None
branch_labels = None
depends_on = None

UNKNOWN_TITLES = {"", "未命名岗位", "未知岗位", "unknown", "n/a", "na", "-"}
UNKNOWN_COMPANIES = {"", "未知公司", "unknown", "n/a", "na", "-"}


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(index_name: str, table_name: str, columns: Iterable[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, list(columns))


def _canonical_job_key(title: Any, company_name: Any, city: Any, area: Any) -> str | None:
    normalized_title = re.sub(r"\s+", "", str(title or "").lower())
    normalized_company = re.sub(r"\s+", "", str(company_name or "").lower())
    if normalized_title in UNKNOWN_TITLES or normalized_company in UNKNOWN_COMPANIES:
        return None
    raw = "|".join(
        re.sub(r"\s+", "", str(part or "").lower())
        for part in [title or "", company_name or "", city or "", area or ""]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def upgrade() -> None:
    tables = _table_names()
    if "jobs" not in tables:
        return

    job_columns = _columns("jobs")
    if "recruitment_status" not in job_columns:
        op.add_column(
            "jobs",
            sa.Column("recruitment_status", sa.String(), nullable=False, server_default="unknown"),
        )
    if "published_at" not in job_columns:
        op.add_column("jobs", sa.Column("published_at", sa.Date(), nullable=True))
    if "last_seen_at" not in job_columns:
        op.add_column("jobs", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    if "canonical_key" not in job_columns:
        op.add_column("jobs", sa.Column("canonical_key", sa.String(), nullable=True))

    _create_index_if_missing("ix_jobs_recruitment_status", "jobs", ["recruitment_status"])
    _create_index_if_missing("ix_jobs_published_at", "jobs", ["published_at"])
    _create_index_if_missing("ix_jobs_last_seen_at", "jobs", ["last_seen_at"])
    _create_index_if_missing("ix_jobs_canonical_key", "jobs", ["canonical_key"])

    if "job_source_links" not in _table_names():
        op.create_table(
            "job_source_links",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("external_id", sa.String(), nullable=False),
            sa.Column("url", sa.String(), nullable=True),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("company_name", sa.String(), nullable=True),
            sa.Column("published_at", sa.Date(), nullable=True),
            sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source", "external_id", name="uq_job_source_link"),
        )

    for index_name, column_names in [
        ("ix_job_source_links_job_id", ["job_id"]),
        ("ix_job_source_links_source", ["source"]),
        ("ix_job_source_links_external_id", ["external_id"]),
        ("ix_job_source_links_url", ["url"]),
        ("ix_job_source_links_company_name", ["company_name"]),
        ("ix_job_source_links_published_at", ["published_at"]),
        ("ix_job_source_links_first_seen_at", ["first_seen_at"]),
        ("ix_job_source_links_last_seen_at", ["last_seen_at"]),
    ]:
        _create_index_if_missing(index_name, "job_source_links", column_names)

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE jobs
            SET last_seen_at = COALESCE(last_seen_at, collected_at, created_at, CURRENT_TIMESTAMP)
            WHERE last_seen_at IS NULL
            """
        )
    )

    rows = bind.execute(
        sa.text(
            """
            SELECT id, title, company_name, city, area
            FROM jobs
            WHERE canonical_key IS NULL
            """
        )
    ).mappings()
    for row in rows:
        canonical_key = _canonical_job_key(row["title"], row["company_name"], row["city"], row["area"])
        if canonical_key:
            bind.execute(
                sa.text("UPDATE jobs SET canonical_key = :canonical_key WHERE id = :job_id"),
                {"canonical_key": canonical_key, "job_id": row["id"]},
            )

    bind.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO job_source_links (
                job_id,
                source,
                external_id,
                url,
                title,
                company_name,
                published_at,
                raw_payload,
                first_seen_at,
                last_seen_at
            )
            SELECT
                id,
                source,
                external_id,
                url,
                title,
                company_name,
                published_at,
                '{}',
                COALESCE(created_at, CURRENT_TIMESTAMP),
                COALESCE(last_seen_at, collected_at, CURRENT_TIMESTAMP)
            FROM jobs
            WHERE source IS NOT NULL AND external_id IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    tables = _table_names()
    if "job_source_links" in tables:
        op.drop_table("job_source_links")

    if "jobs" not in _table_names():
        return

    job_indexes = _indexes("jobs")
    for index_name in [
        "ix_jobs_recruitment_status",
        "ix_jobs_published_at",
        "ix_jobs_last_seen_at",
        "ix_jobs_canonical_key",
    ]:
        if index_name in job_indexes:
            op.drop_index(index_name, table_name="jobs")

    job_columns = _columns("jobs")
    with op.batch_alter_table("jobs") as batch_op:
        for column_name in ["canonical_key", "last_seen_at", "published_at", "recruitment_status"]:
            if column_name in job_columns:
                batch_op.drop_column(column_name)
