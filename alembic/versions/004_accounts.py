"""004 accounts - one sign-in, many profiles

A profile is an `employees` row. Until now the row was also the sign-in: email,
password and admin flag lived on it, the JWT named it, and one person could be
exactly one member -- trying a second member situation meant a second account.

Additive, so a downgrade restores today exactly:
- `accounts` holds the sign-in. Every employee who can sign in today becomes an
  account over its own row (`primary_employee_id`), linked back through
  `employees.account_id`.
- `employees` gains `account_id`, `persona_key` and a member-facing `label`,
  and `email` becomes optional: a second profile has no address of its own.
- The old sign-in columns on `employees` stay, unused, until a cleanup migration
  once this has run in anger.

Row-level security gains an account identity, `app.current_account_id`, set by
`get_db_session(account_id=...)`. Three policies use it; messages, salary
history and checkpoints follow through the subselects 003 already gave them.
Every employee-keyed policy is unchanged, which is what keeps the record fetch,
the checkpointer and the evals working as they are.

Revision ID: 004_accounts
Revises: 003_enforce_rls
Create Date: 2026-09-11
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_accounts"
down_revision: str | None = "003_enforce_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# As in 003: set by `get_admin_session()`, transaction-local, and by nothing else.
ADMIN = "current_setting('app.admin', true) = 'on'"
_ACCOUNT = "NULLIF(current_setting('app.current_account_id', true), '')::uuid"

POLICIES = [
    ("accounts", "accounts_self_access", f"id = {_ACCOUNT}"),
    ("employees", "employees_account_access", f"account_id = {_ACCOUNT}"),
    (
        "sessions",
        "sessions_account_access",
        f"employee_id IN (SELECT id FROM employees WHERE account_id = {_ACCOUNT})",
    ),
]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE accounts (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email text NOT NULL,
            hashed_password text,
            display_name text,
            is_admin boolean NOT NULL DEFAULT false,
            primary_employee_id uuid REFERENCES employees(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE UNIQUE INDEX accounts_email_lower_key ON accounts (lower(email))")

    op.execute("ALTER TABLE employees ADD COLUMN account_id uuid REFERENCES accounts(id)")
    op.execute("ALTER TABLE employees ADD COLUMN persona_key text")
    op.execute("ALTER TABLE employees ADD COLUMN label text")
    op.execute("ALTER TABLE employees ALTER COLUMN email DROP NOT NULL")
    op.execute("CREATE INDEX employees_account_id_idx ON employees (account_id)")

    # The app role has had no BYPASSRLS since 003, so without this the copy
    # below would see no employees at all. Transaction-local, like every use.
    op.execute("SELECT set_config('app.admin', 'on', true)")
    op.execute("""
        INSERT INTO accounts (email, hashed_password, is_admin, primary_employee_id)
        SELECT email, hashed_password, is_admin, id
        FROM employees
        WHERE hashed_password IS NOT NULL
    """)
    op.execute("""
        UPDATE employees e SET account_id = a.id
        FROM accounts a
        WHERE a.primary_employee_id = e.id
    """)

    op.execute("ALTER TABLE accounts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE accounts FORCE ROW LEVEL SECURITY")
    for table, policy, predicate in POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"""
            CREATE POLICY {policy} ON {table}
                USING ({predicate} OR {ADMIN})
                WITH CHECK ({predicate} OR {ADMIN})
        """)


def downgrade() -> None:
    for table, policy, _ in POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")

    op.execute("SELECT set_config('app.admin', 'on', true)")
    # Hand the sign-in back to the row it belongs to. An account made after 004
    # created its first profile without an address, so it takes the account's.
    op.execute("""
        UPDATE employees e
        SET email = COALESCE(e.email, a.email),
            hashed_password = a.hashed_password,
            is_admin = a.is_admin
        FROM accounts a
        WHERE a.primary_employee_id = e.id
    """)
    # A further profile never had an address, and the column needs one again.
    op.execute("UPDATE employees SET email = id::text || '@profile.invalid' WHERE email IS NULL")
    op.execute("ALTER TABLE employees ALTER COLUMN email SET NOT NULL")

    op.execute("DROP INDEX IF EXISTS employees_account_id_idx")
    op.execute("ALTER TABLE employees DROP COLUMN label")
    op.execute("ALTER TABLE employees DROP COLUMN persona_key")
    op.execute("ALTER TABLE employees DROP COLUMN account_id")
    op.execute("DROP TABLE accounts")
