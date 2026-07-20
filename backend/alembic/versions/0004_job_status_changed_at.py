"""Add jobs.status_changed_at for follow-up staleness detection.

Revision ID: 0004_job_status_changed_at
Revises: 0003_interview_logs
Create Date: 2026-06-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_job_status_changed_at"
down_revision = "0003_interview_logs"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    # 迁移先于 create_all 运行：全新库里 jobs 还不存在，跳过即可（create_all 会建带该列的表）。
    if "jobs" not in _table_names():
        return
    columns = _columns("jobs")
    if "status_changed_at" in columns:
        return
    op.add_column("jobs", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True))
    # 存量行回填：优先用 updated_at（更接近“最近一次变更”），极旧库无该列时退回当前时间。
    fallback = "updated_at" if "updated_at" in columns else "CURRENT_TIMESTAMP"
    op.execute(f"UPDATE jobs SET status_changed_at = {fallback} WHERE status_changed_at IS NULL")


def downgrade() -> None:
    if "jobs" in _table_names() and "status_changed_at" in _columns("jobs"):
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.drop_column("status_changed_at")
