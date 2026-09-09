"""004 admin role and email identity - close what open signup would open

Two holes that are only harmless while nobody can create an account, and become
live the moment self-service signup ships.

**Nothing distinguishes an administrator.** `require_auth` checks that a token
decodes and no more, and `api/admin.py` uses it on all three endpoints —
including `POST /formulas/{id}/promote`, which sets `knowledge_status` to
`approved` and permits `execution_policy='authoritative'`. That is the gate the
architecture doc (§7.2) puts human legal and actuarial review behind, and
`_format_calculation` reads exactly that field to decide whether a figure is
presented as an estimate. So one promotion changes how figures are framed for
every member, and with open signup it becomes a self-service operation for
anyone with an email address. `is_admin` is what `require_admin` reads.

**Email is the account identity and is case-sensitive.** `employees.email` is
`TEXT UNIQUE`, so `Foo@example.com` and `foo@example.com` are two rows, and
login's exact-match lookup finds whichever it happens to find. Under a pilot of
two hand-seeded addresses that never comes up; under open signup it is both a
support burden and a way to shadow someone else's address. The application
lowercases on the way in, but the functional index is the thing that actually
holds.

Revision ID: 004_admin_role
Revises: 003_enforce_rls
Create Date: 2026-09-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_admin_role"
down_revision: str | None = "003_enforce_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Defaults to false, and signup can never set it — the request schema
    # forbids unknown fields and the row is built field by field rather than
    # splatted from the payload.
    op.add_column(
        "employees",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Existing addresses are already lowercase, so this cannot fail on the
    # pilot data. If it ever does, that is two accounts differing only by case
    # and someone has to decide which is real — better as a failed migration
    # than as a silent collision.
    op.execute(
        "CREATE UNIQUE INDEX uq_employees_email_lower ON employees (lower(email))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_employees_email_lower")
    op.drop_column("employees", "is_admin")
