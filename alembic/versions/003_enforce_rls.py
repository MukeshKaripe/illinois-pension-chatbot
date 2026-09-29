"""003 enforce RLS - make the row-level policies actually do something

The policies from 001 were never in force. The application connects as a role
holding BYPASSRLS, which outranks even FORCE ROW LEVEL SECURITY, so every policy
was skipped: a query with no identity set returned every employee and every
session message. Nothing had noticed, because `get_db_session()` — the helper
that sets the identity — had no callers, and would have raised on the first one.

Three things change here.

**An explicit admin escape.** Dropping BYPASSRLS from the role would otherwise
break `get_admin_session()`, which legitimately needs to see everything: the
admin API, the health check, and the login path, which looks an employee up by
email *before* there is an identity to scope to. So the escape moves from a role
grant, which is invisible in the code and applies to every statement, into a
transaction-local setting the caller has to ask for by name.

**Salary history gets a policy.** It had none. It holds ten years of a member's
salary, and it is the one table whose contents the answers quote most.

**Empty settings stop being a cast error.** `current_setting(..., true)::uuid`
raises on an empty string, which is what a caller that sets the variable and
leaves it blank would produce. NULLIF makes that read as "no identity" instead
of failing the query.

Revision ID: 003_enforce_rls
Revises: 002_agent_checkpoints
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_enforce_rls"
down_revision: Union[str, None] = "002_agent_checkpoints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Set by `get_admin_session()`, transaction-local, and by nothing else.
ADMIN = "current_setting('app.admin', true) = 'on'"

_EMPLOYEE = (
    "id = NULLIF(current_setting('app.current_employee_id', true), '')::uuid"
)
_SESSION = (
    "("
    "  (employee_id IS NOT NULL AND employee_id = "
    "     NULLIF(current_setting('app.current_employee_id', true), '')::uuid)"
    "  OR (employee_id IS NULL AND session_token = "
    "     NULLIF(current_setting('app.current_session_token', true), ''))"
    ")"
)

POLICIES = [
    ("employees", "employee_self_access", _EMPLOYEE),
    ("sessions", "session_self_access", _SESSION),
    (
        "session_messages",
        "session_messages_self_access",
        "session_id IN (SELECT id FROM sessions)",
    ),
    (
        "employee_salary_history",
        "salary_history_self_access",
        "employee_id IN (SELECT id FROM employees)",
    ),
    (
        "agent_checkpoints",
        "agent_checkpoints_self_access",
        "thread_id IN (SELECT id::text FROM sessions)",
    ),
    (
        "agent_checkpoint_writes",
        "agent_checkpoint_writes_self_access",
        "thread_id IN (SELECT id::text FROM sessions)",
    ),
]


def upgrade() -> None:
    op.execute("ALTER TABLE employee_salary_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE employee_salary_history FORCE ROW LEVEL SECURITY")

    for table, policy, predicate in POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"""
            CREATE POLICY {policy} ON {table}
                USING ({predicate} OR {ADMIN})
                WITH CHECK ({predicate} OR {ADMIN})
        """)

    # The grant that made all of the above decoration.
    #
    # Guarded: PostgreSQL 16+ refuses ALTER ROLE on your own role, and the app
    # role on the client's server (PostgreSQL 18) is created without BYPASSRLS,
    # so there is nothing to drop. A role that *does* bypass and cannot drop it
    # fails the migration loudly — otherwise every policy above is decoration.
    op.execute("""
        DO $$
        BEGIN
            IF (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) THEN
                BEGIN
                    ALTER ROLE CURRENT_USER NOBYPASSRLS;
                EXCEPTION WHEN insufficient_privilege THEN
                    RAISE EXCEPTION
                        'role % has BYPASSRLS and cannot drop it itself (PostgreSQL 16+); '
                        'run migrations and the app as a role created NOBYPASSRLS', current_user;
                END;
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("ALTER ROLE CURRENT_USER BYPASSRLS")

    for table, policy, _ in POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")

    op.execute("ALTER TABLE employee_salary_history DISABLE ROW LEVEL SECURITY")

    # Restore the 001/002 policies unchanged.
    op.execute("""
        CREATE POLICY employee_self_access ON employees
            USING (id = current_setting('app.current_employee_id', true)::uuid)
    """)
    op.execute("""
        CREATE POLICY session_self_access ON sessions
            USING (
                (employee_id IS NOT NULL
                 AND employee_id = current_setting('app.current_employee_id', true)::uuid)
                OR
                (employee_id IS NULL
                 AND session_token = current_setting('app.current_session_token', true))
            )
    """)
    op.execute("""
        CREATE POLICY session_messages_self_access ON session_messages
            USING (session_id IN (SELECT id FROM sessions))
    """)
    for table in ("agent_checkpoints", "agent_checkpoint_writes"):
        op.execute(f"""
            CREATE POLICY {table}_self_access ON {table}
                USING (thread_id IN (SELECT id::text FROM sessions))
                WITH CHECK (thread_id IN (SELECT id::text FROM sessions))
        """)
