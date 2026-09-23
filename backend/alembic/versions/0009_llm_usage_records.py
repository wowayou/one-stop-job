"""Add llm_usage_records table for tracking LLM token usage.

Revision ID: 0009_llm_usage_records
Revises: 0008_job_snapshot_changes
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_llm_usage_records"
down_revision = "0008_job_snapshot_changes"
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
    if "llm_usage_records" not in _table_names():
        op.create_table(
            "llm_usage_records",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("provider_label", sa.String(), nullable=True),
            sa.Column("model", sa.String(), nullable=True),
            sa.Column("purpose", sa.String(), nullable=False, server_default="other"),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("total_tokens", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    for index_name, columns in [
        ("ix_llm_usage_records_created_at", ["created_at"]),
        ("ix_llm_usage_records_model", ["model"]),
        ("ix_llm_usage_records_purpose", ["purpose"]),
    ]:
        _create_index_if_missing(index_name, "llm_usage_records", columns)


def downgrade() -> None:
    if "llm_usage_records" in _table_names():
        op.drop_table("llm_usage_records")
