"""A conversation's people: the employees an employer is asking about (at most 4).

Most recent first: [{"ref", "display_name", "first_name", "last_name",
"department", "employer", "birth_year"}]. `ref` is the internal MEAB ID --
validated by code against records.MEAB_ID_PATTERN before storing, never shown.
Names are kept so a returning name is recognised without a query; the transcript
already holds them, under the same row-level policies, which cover this column.
Not the principal: `sessions.employee_id` is who is chatting and scopes RLS.

Revision ID: 009_session_subjects
Revises: 008_answer_evidence
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "009_session_subjects"
down_revision = "008_answer_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("subjects", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
    )
    op.create_check_constraint(
        "ck_sessions_subjects_at_most_four", "sessions",
        "jsonb_typeof(subjects) = 'array' AND jsonb_array_length(subjects) <= 4",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sessions_subjects_at_most_four", "sessions", type_="check")
    op.drop_column("sessions", "subjects")
