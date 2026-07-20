"""Add interview_logs table for interview retrospectives.

Revision ID: 0003_interview_logs
Revises: 0002_profile_experience_and_resume
Create Date: 2026-06-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003_interview_logs"
down_revision = "0002_profile_experience_and_resume"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    if "interview_logs" not in _table_names():
        op.create_table(
            "interview_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("round", sa.String(), nullable=False, server_default="一面"),
            sa.Column("interview_date", sa.Date(), nullable=True),
            sa.Column("interviewer", sa.String(), nullable=True),
            sa.Column("real_picture", sa.Text(), nullable=False, server_default=""),
            sa.Column("opportunity_score", sa.Float(), nullable=True),
            sa.Column("conclusion", sa.String(), nullable=False, server_default=""),
            sa.Column("score_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("qa_review", sa.Text(), nullable=False, server_default=""),
            sa.Column("weaknesses", sa.Text(), nullable=False, server_default=""),
            sa.Column("next_actions", sa.Text(), nullable=False, server_default=""),
            sa.Column("follow_up", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    existing = _indexes("interview_logs")
    for index_name, columns in [
        ("ix_interview_logs_job_id", ["job_id"]),
        ("ix_interview_logs_round", ["round"]),
        ("ix_interview_logs_interview_date", ["interview_date"]),
        ("ix_interview_logs_opportunity_score", ["opportunity_score"]),
        ("ix_interview_logs_conclusion", ["conclusion"]),
        ("ix_interview_logs_created_at", ["created_at"]),
    ]:
        if index_name not in existing:
            op.create_index(index_name, "interview_logs", columns)


def downgrade() -> None:
    if "interview_logs" in _table_names():
        op.drop_table("interview_logs")
