"""002 agent checkpoints - durable conversation state for the agent graph

Adds the two tables LangGraph's checkpointer contract needs, so a follow-up turn
resumes from what the previous turn established rather than re-deriving it from
the prose of the earlier answer.

These carry member facts, so they get the same row-level security as
session_messages: delegate to the sessions policy, which is itself RLS-filtered
by employee_id or session_token. The cast is on the sessions side so a thread id
that is not a UUID (the eval harness, tests) cannot raise, it simply matches
nothing.

Revision ID: 002_agent_checkpoints
Revises: 001_initial
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "002_agent_checkpoints"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_checkpoints",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("checkpoint_ns", sa.Text(), primary_key=True, server_default=""),
        sa.Column("checkpoint_id", sa.Text(), primary_key=True),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", sa.LargeBinary(), nullable=False),
        sa.Column(
            "metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_agent_checkpoints_thread",
        "agent_checkpoints",
        ["thread_id", "checkpoint_ns"],
    )

    op.create_table(
        "agent_checkpoint_writes",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("checkpoint_ns", sa.Text(), primary_key=True, server_default=""),
        sa.Column("checkpoint_id", sa.Text(), primary_key=True),
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("idx", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), nullable=False, server_default=""),
    )

    # ── Row-Level Security ────────────────────────────────────────
    # Same delegation as session_messages: a checkpoint is visible only if its
    # thread is a session the caller can already see.
    for table in ("agent_checkpoints", "agent_checkpoint_writes"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_self_access ON {table}
                USING (thread_id IN (SELECT id::text FROM sessions))
                WITH CHECK (thread_id IN (SELECT id::text FROM sessions))
        """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS agent_checkpoint_writes_self_access "
        "ON agent_checkpoint_writes"
    )
    op.execute(
        "DROP POLICY IF EXISTS agent_checkpoints_self_access ON agent_checkpoints"
    )
    op.drop_table("agent_checkpoint_writes")
    op.drop_index("ix_agent_checkpoints_thread", table_name="agent_checkpoints")
    op.drop_table("agent_checkpoints")
