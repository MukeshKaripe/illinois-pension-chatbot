"""001 initial schema - full base schema plus RLS policies

Creates pension_articles, employees, employee_salary_history, sessions,
session_messages, formula_registry tables with all constraints, indexes,
and RLS policies as defined in §7.1 of the architecture document.

Revision ID: 001_initial
Revises: None
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── pension_articles ──────────────────────────────────────────
    op.create_table(
        "pension_articles",
        sa.Column("article_number", sa.SmallInteger(), primary_key=True),
        sa.Column("system_name", sa.Text(), nullable=False),
        sa.Column("chapter", sa.Text(), nullable=False, server_default="40"),
        sa.Column("act", sa.Text(), nullable=False, server_default="5"),
        sa.Column(
            "ingestion_status",
            sa.Text(),
            nullable=False,
            server_default="not_ingested",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "ingestion_status IN ('not_ingested', 'validating', 'ingested')",
            name="ck_pension_articles_ingestion_status",
        ),
    )

    # ── employees ─────────────────────────────────────────────────
    op.create_table(
        "employees",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), unique=True, nullable=False),
        sa.Column("hashed_password", sa.Text(), nullable=True),
        sa.Column(
            "article_number",
            sa.SmallInteger(),
            sa.ForeignKey("pension_articles.article_number"),
            nullable=False,
        ),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("hire_date", sa.Date(), nullable=False),
        sa.Column(
            "employment_status",
            sa.Text(),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "total_service_years",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("current_annual_salary", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("tier IN (1, 2)", name="ck_employees_tier"),
        sa.CheckConstraint(
            "employment_status IN ('active', 'separated', 'retired')",
            name="ck_employees_employment_status",
        ),
    )

    # ── employee_salary_history ───────────────────────────────────
    op.create_table(
        "employee_salary_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "employee_id",
            UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("annual_salary", sa.Numeric(12, 2), nullable=False),
        sa.UniqueConstraint(
            "employee_id", "effective_date", name="uq_salary_history_emp_date"
        ),
    )

    # ── sessions ──────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "employee_id",
            UUID(as_uuid=True),
            sa.ForeignKey("employees.id"),
            nullable=True,
        ),
        sa.Column(
            "session_token",
            sa.Text(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.CheckConstraint(
            "status IN ('active', 'ended')", name="ck_sessions_status"
        ),
    )

    # ── session_messages ──────────────────────────────────────────
    op.create_table(
        "session_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", JSONB(), nullable=True),
        sa.Column("tool_calls", JSONB(), nullable=True),
        sa.Column("guardrail_passed", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_session_messages_role"
        ),
    )
    op.create_index(
        "idx_session_messages_session",
        "session_messages",
        ["session_id", "created_at"],
    )

    # ── formula_registry ──────────────────────────────────────────
    op.create_table(
        "formula_registry",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            sa.Text(),
            nullable=False,
            server_default="il_pension_code_40_ilcs_5",
        ),
        sa.Column(
            "article_number",
            sa.SmallInteger(),
            sa.ForeignKey("pension_articles.article_number"),
            nullable=False,
        ),
        sa.Column("formula_id", sa.Text(), unique=True, nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("formula_name", sa.Text(), nullable=False),
        sa.Column("formula_type", sa.Text(), nullable=False),
        sa.Column("tier", sa.SmallInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required_inputs", JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("optional_inputs", JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("output_schema", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("applicability", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("dependencies", JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("rule_specification", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_sections", JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("evidence_fingerprint", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "execution_type",
            sa.Text(),
            nullable=False,
            server_default="declarative",
        ),
        sa.Column("execution_reference", sa.Text(), nullable=True),
        sa.Column("implementation_hash", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_start_date", sa.Date(), nullable=False),
        sa.Column("effective_end_date", sa.Date(), nullable=True),
        sa.Column("annual_parameter_set", sa.Text(), nullable=True),
        sa.Column(
            "knowledge_status",
            sa.Text(),
            nullable=False,
            server_default="discovered",
        ),
        sa.Column("risk_tier", sa.Text(), nullable=False, server_default="high"),
        sa.Column(
            "execution_policy",
            sa.Text(),
            nullable=False,
            server_default="no_execute",
        ),
        sa.Column("derivation_method", sa.Text(), nullable=True),
        sa.Column("completeness_check", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_from_message_id",
            sa.BigInteger(),
            sa.ForeignKey("session_messages.id"),
            nullable=True,
        ),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("test_reference", sa.Text(), nullable=True),
        sa.Column("source_public_act", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "execution_type IN ('declarative', 'custom')",
            name="ck_formula_registry_execution_type",
        ),
        sa.CheckConstraint(
            "knowledge_status IN ('discovered', 'agent_derived', 'validated', 'approved', 'stale', 'retired')",
            name="ck_formula_registry_knowledge_status",
        ),
        sa.CheckConstraint(
            "risk_tier IN ('low', 'medium', 'high')",
            name="ck_formula_registry_risk_tier",
        ),
        sa.CheckConstraint(
            "execution_policy IN ('no_execute', 'estimate', 'authoritative')",
            name="ck_formula_registry_execution_policy",
        ),
        sa.UniqueConstraint(
            "article_number", "formula_id", "version", name="uq_formula_article_version"
        ),
    )
    op.create_index(
        "idx_formula_lookup",
        "formula_registry",
        ["article_number", "tier", "formula_type", "effective_start_date"],
    )
    op.create_index(
        "idx_formula_status",
        "formula_registry",
        ["knowledge_status", "execution_policy"],
    )

    # ── Row-Level Security Policies ───────────────────────────────
    # These are executed as raw SQL since Alembic/SQLAlchemy doesn't
    # have native RLS support.

    # Employee RLS: users can only see their own record
    op.execute("ALTER TABLE employees ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE employees FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY employee_self_access ON employees
            USING (id = current_setting('app.current_employee_id', true)::uuid)
    """)

    # Session RLS: scoped by employee_id OR session_token
    op.execute("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sessions FORCE ROW LEVEL SECURITY")
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

    # Session messages RLS: delegates to session boundary
    op.execute("ALTER TABLE session_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE session_messages FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY session_messages_self_access ON session_messages
            USING (session_id IN (SELECT id FROM sessions))
    """)

    # ── Link function for first-login employee matching ───────────
    # Used by the auth layer to link a new user to their pre-seeded employee record
    op.execute("""
        CREATE OR REPLACE FUNCTION link_employee_by_email(
            p_email TEXT,
            p_employee_id UUID
        ) RETURNS UUID
        LANGUAGE plpgsql
        SECURITY DEFINER
        AS $$
        DECLARE
            v_id UUID;
        BEGIN
            UPDATE employees
            SET id = id  -- no-op update to confirm the row exists
            WHERE email = p_email
              AND id = p_employee_id
            RETURNING id INTO v_id;
            RETURN v_id;
        END;
        $$
    """)


def downgrade() -> None:
    # Drop RLS policies first
    op.execute("DROP POLICY IF EXISTS session_messages_self_access ON session_messages")
    op.execute("DROP POLICY IF EXISTS session_self_access ON sessions")
    op.execute("DROP POLICY IF EXISTS employee_self_access ON employees")
    op.execute("DROP FUNCTION IF EXISTS link_employee_by_email")

    # Drop tables in reverse dependency order
    op.drop_table("formula_registry")
    op.drop_table("session_messages")
    op.drop_table("sessions")
    op.drop_table("employee_salary_history")
    op.drop_table("employees")
    op.drop_table("pension_articles")
