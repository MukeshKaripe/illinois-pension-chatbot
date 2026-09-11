"""Fund-specific profile facts: employees.extra_facts (spec §6, Phase 3b).

Some funds' formulas read a fact no other fund has -- §14-108's accrual rate
turns on whether a state employee is covered by Social Security. Those facts
arrive one at a time, as a recipe for that fund needs them, so they share one
JSON column rather than a column each; `funds.Fund.extra_fields` declares which
names a fund may store, and `personas.validate` refuses any other.

Additive: existing rows get an empty object. No policy changes -- the column
lives on `employees`, whose policies already scope every row.

Revision ID: 006_extra_facts
Revises: 005_accounts
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "006_extra_facts"
down_revision = "005_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column(
            "extra_facts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("employees", "extra_facts")
