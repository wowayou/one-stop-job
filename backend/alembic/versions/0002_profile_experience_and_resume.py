"""Add profile experience and tailored resume prep fields.

Revision ID: 0002_profile_experience_and_resume
Revises: 0001_job_source_links
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002_profile_experience_and_resume"
down_revision = "0001_job_source_links"
branch_labels = None
depends_on = None

DEFAULT_WORK_EXPERIENCE = "请填写真实公司、项目、指标和成果。"


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    tables = _table_names()
    if "user_profile" in tables and "work_experience" not in _columns("user_profile"):
        op.add_column(
            "user_profile",
            sa.Column("work_experience", sa.Text(), nullable=False, server_default=DEFAULT_WORK_EXPERIENCE),
        )

    if "interview_prep" in tables:
        prep_columns = _columns("interview_prep")
        if "core_pitch" not in prep_columns:
            op.add_column("interview_prep", sa.Column("core_pitch", sa.Text(), nullable=False, server_default=""))
        if "tailored_resume" not in prep_columns:
            op.add_column("interview_prep", sa.Column("tailored_resume", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    tables = _table_names()
    if "interview_prep" in tables:
        prep_columns = _columns("interview_prep")
        with op.batch_alter_table("interview_prep") as batch_op:
            if "tailored_resume" in prep_columns:
                batch_op.drop_column("tailored_resume")
            if "core_pitch" in prep_columns:
                batch_op.drop_column("core_pitch")

    if "user_profile" in tables and "work_experience" in _columns("user_profile"):
        with op.batch_alter_table("user_profile") as batch_op:
            batch_op.drop_column("work_experience")
