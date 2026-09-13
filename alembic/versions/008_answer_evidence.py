"""Verified retrieval memory: which sections answered a kind of question before.

A row says only that an answer resting on these sections passed verification.
It is offered back as enrichment on a search result and never as a filter, so a
section that once got lucky cannot entrench itself by suppressing the search
that would have found a better one.

`embedding` is JSONB rather than a pgvector column. The pilot's row count is
small enough that cosine in Python over an Article-filtered slice is
unmeasurably fast, and it keeps the schema free of an extension the deployment
does not otherwise need. Swapping it later is a migration, not a redesign.

**No RLS policy.** The table holds section numbers and a de-identified question
phrase — the member's own particulars are stripped before the concept is
stored, and no row is keyed to an employee or an account. It is read by the
agent and written by the agent, and there is no per-user slice of it to enforce:
what it remembers is about the Pension Code, not about a person. `session_id` is
kept for tracing a row back to the turn that produced it.

Revision ID: 008_answer_evidence
Revises: 007_registry_usage
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "008_answer_evidence"
down_revision = "007_registry_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "answer_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("concept", sa.Text(), nullable=False),
        sa.Column(
            "concept_source", sa.Text(), nullable=False, server_default="question"
        ),
        sa.Column(
            "embedding",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("article", sa.Text(), nullable=True),
        sa.Column("tier", sa.SmallInteger(), nullable=True),
        sa.Column(
            "sections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_answer_evidence_article_tier", "answer_evidence", ["article", "tier"]
    )


def downgrade() -> None:
    op.drop_index("ix_answer_evidence_article_tier", table_name="answer_evidence")
    op.drop_table("answer_evidence")
