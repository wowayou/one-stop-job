"""Add application_events table for job funnel tracking.

Revision ID: 0005_application_events
Revises: 0004_job_status_changed_at
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005_application_events"
down_revision = "0004_job_status_changed_at"
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
    if "application_events" not in _table_names():
        op.create_table(
            "application_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("event_date", sa.Date(), nullable=False),
            sa.Column("channel", sa.String(), nullable=True),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    for index_name, columns in [
        ("ix_application_events_job_id", ["job_id"]),
        ("ix_application_events_event_type", ["event_type"]),
        ("ix_application_events_event_date", ["event_date"]),
        ("ix_application_events_created_at", ["created_at"]),
    ]:
        _create_index_if_missing(index_name, "application_events", columns)


def downgrade() -> None:
    if "application_events" in _table_names():
        op.drop_table("application_events")
