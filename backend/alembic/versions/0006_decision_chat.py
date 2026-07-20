"""Add persisted, read-only decision chat tables.

Revision ID: 0006_decision_chat
Revises: 0005_application_events
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006_decision_chat"
down_revision = "0005_application_events"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _table_names()
    if "jobs" not in tables:
        return

    if "chat_threads" not in tables:
        op.create_table(
            "chat_threads",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False, server_default="general"),
            sa.Column("job_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_threads_kind", "chat_threads", ["kind"])
        op.create_index("ix_chat_threads_job_id", "chat_threads", ["job_id"])
        op.create_index("ix_chat_threads_created_at", "chat_threads", ["created_at"])
        op.create_index("ix_chat_threads_updated_at", "chat_threads", ["updated_at"])

    if "chat_messages" not in tables:
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("thread_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_messages_thread_id", "chat_messages", ["thread_id"])
        op.create_index("ix_chat_messages_role", "chat_messages", ["role"])
        op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])

    if "analysis_runs" not in tables:
        op.create_table(
            "analysis_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("thread_id", sa.Integer(), nullable=False),
            sa.Column("user_message_id", sa.Integer(), nullable=False),
            sa.Column("assistant_message_id", sa.Integer(), nullable=True),
            sa.Column("rules_version", sa.String(), nullable=False, server_default="local-profile"),
            sa.Column("provider", sa.String(), nullable=False, server_default="rules"),
            sa.Column("model", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="rules_only"),
            sa.Column("result_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"]),
            sa.ForeignKeyConstraint(["user_message_id"], ["chat_messages.id"]),
            sa.ForeignKeyConstraint(["assistant_message_id"], ["chat_messages.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_analysis_runs_thread_id", "analysis_runs", ["thread_id"])
        op.create_index("ix_analysis_runs_user_message_id", "analysis_runs", ["user_message_id"])
        op.create_index("ix_analysis_runs_assistant_message_id", "analysis_runs", ["assistant_message_id"])
        op.create_index("ix_analysis_runs_status", "analysis_runs", ["status"])
        op.create_index("ix_analysis_runs_created_at", "analysis_runs", ["created_at"])


def downgrade() -> None:
    tables = _table_names()
    if "analysis_runs" in tables:
        op.drop_table("analysis_runs")
    if "chat_messages" in tables:
        op.drop_table("chat_messages")
    if "chat_threads" in tables:
        op.drop_table("chat_threads")
