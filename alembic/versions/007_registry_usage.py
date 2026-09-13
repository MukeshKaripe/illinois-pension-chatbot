"""Registry usage: what is load-bearing, rather than what earned approval.

The registry's columns recorded review state — who approved a recipe and when.
Nothing recorded what the system actually leans on, which is the first thing a
reviewer needs: an error in the recipe that has quietly produced four hundred
figures costs more than an error in one that has produced none.

`contested_count` comes from `input_overrides`, which already detects a member
stating a value their record contradicts and until now only mentioned it in the
answer's prose. It is a count, not a verdict — the member may be wrong, or the
record may be out of date — and counts are for prioritising attention.

No RLS policy changes: `formula_registry` is not user-scoped. It is read through
the agent and written through `get_admin_session()`.

Revision ID: 007_registry_usage
Revises: 006_extra_facts
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "007_registry_usage"
down_revision = "006_extra_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "formula_registry",
        sa.Column("reuse_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "formula_registry",
        sa.Column("last_reused_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "formula_registry",
        sa.Column("contested_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "formula_registry",
        sa.Column("superseded_by", sa.Text(), nullable=True),
    )
    # The reviewer's own ordering: most load-bearing first.
    op.create_index(
        "ix_formula_registry_reuse_count",
        "formula_registry",
        [sa.text("reuse_count DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_formula_registry_reuse_count", table_name="formula_registry")
    op.drop_column("formula_registry", "superseded_by")
    op.drop_column("formula_registry", "contested_count")
    op.drop_column("formula_registry", "last_reused_at")
    op.drop_column("formula_registry", "reuse_count")
