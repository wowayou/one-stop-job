"""Add soft-delete (deleted_at) columns to jobs and companies.

Revision ID: 0007_soft_delete
Revises: 0006_decision_chat
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007_soft_delete"
down_revision = "0006_decision_chat"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "jobs" in _table_names():
        cols = _columns("jobs")
        if "deleted_at" not in cols:
            op.add_column("jobs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
            op.create_index("ix_jobs_deleted_at", "jobs", ["deleted_at"])

    if "companies" in _table_names():
        cols = _columns("companies")
        if "deleted_at" not in cols:
            op.add_column("companies", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
            op.create_index("ix_companies_deleted_at", "companies", ["deleted_at"])


def downgrade() -> None:
    if "jobs" in _table_names() and "deleted_at" in _columns("jobs"):
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.drop_column("deleted_at")

    if "companies" in _table_names() and "deleted_at" in _columns("companies"):
        with op.batch_alter_table("companies") as batch_op:
            batch_op.drop_column("deleted_at")
