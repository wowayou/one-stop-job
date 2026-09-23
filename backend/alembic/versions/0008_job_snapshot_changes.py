"""Add job_snapshot_changes table for tracking snapshot field changes.

Revision ID: 0008_job_snapshot_changes
Revises: 0007_soft_delete
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008_job_snapshot_changes"
down_revision = "0007_soft_delete"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    if "jobs" not in _table_names():
        return
    if "job_snapshot_changes" not in _table_names():
        op.create_table(
            "job_snapshot_changes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("changes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    for index_name, columns in [
        ("ix_job_snapshot_changes_job_id", ["job_id"]),
        ("ix_job_snapshot_changes_changed_at", ["changed_at"]),
    ]:
        _create_index_if_missing(index_name, "job_snapshot_changes", columns)


def downgrade() -> None:
    if "job_snapshot_changes" in _table_names():
        op.drop_table("job_snapshot_changes")
