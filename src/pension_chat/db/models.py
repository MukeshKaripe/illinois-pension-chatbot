"""SQLAlchemy ORM models matching the §7.1 schema exactly.

All tables, constraints, indexes, and RLS policies from the architecture
document are defined here. Alembic migrations derive from these models.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class PensionArticle(Base):
    """Reference table: one row per Article (~20 eventually)."""

    __tablename__ = "pension_articles"

    article_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    system_name: Mapped[str] = mapped_column(Text, nullable=False)
    chapter: Mapped[str] = mapped_column(Text, nullable=False, server_default="40")
    act: Mapped[str] = mapped_column(Text, nullable=False, server_default="5")
    ingestion_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="not_ingested",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "ingestion_status IN ('not_ingested', 'validating', 'ingested')",
            name="ck_pension_articles_ingestion_status",
        ),
    )

    # Relationships
    employees: Mapped[list[Employee]] = relationship(back_populates="article")
    formulas: Mapped[list[FormulaRegistry]] = relationship(back_populates="article")


class Employee(Base):
    """Employees table — synthetic for pilot, identical schema for real data later."""

    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Optional since 005: sign-in lives on `Account`, and a profile beyond an
    # account's first has no address of its own. Kept for the pilot's seeded
    # members, whom `/register` finds by it.
    email: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    # Superseded by `Account.hashed_password` (005); kept for the downgrade.
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    article_number: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("pension_articles.article_number"),
        nullable=False,
    )
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    employment_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="active",
    )
    total_service_years: Mapped[float] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        server_default="0",
    )
    current_annual_salary: Mapped[float] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    # Facts only some funds have (`funds.Fund.extra_fields`), keyed by field
    # name -- whether a state employee is covered by Social Security, say. JSON
    # rather than a column each, because they arrive one at a time, as a recipe
    # for that fund needs them (migration 006).
    extra_facts: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    # Superseded by `Account.is_admin` (005), which `require_admin` reads. Kept
    # so a downgrade can hand the flag back; dropped by the cleanup migration.
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    # The account this profile belongs to (005). NULL for a seeded pilot member
    # nobody has claimed yet.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id"),
        nullable=True,
        index=True,
    )
    # The preset this profile started from, and its member-facing name.
    persona_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint("tier IN (1, 2)", name="ck_employees_tier"),
        CheckConstraint(
            "employment_status IN ('active', 'separated', 'retired')",
            name="ck_employees_employment_status",
        ),
    )

    # Relationships
    article: Mapped[PensionArticle] = relationship(back_populates="employees")
    salary_history: Mapped[list[EmployeeSalaryHistory]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[Session]] = relationship(back_populates="employee")


class Account(Base):
    """A sign-in: one person, any number of profiles (005).

    A profile is an `Employee` row; this is who may use it. `is_admin` lives
    here, read per request by `require_admin` rather than carried as a token
    claim: a claim inside a 24-hour token cannot be revoked, and the admin
    endpoints are cold enough that the round trip costs nothing.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Unique on lower(email), by index (005).
    email: Mapped[str] = mapped_column(Text, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    # The profile a new chat uses unless another is chosen. `use_alter`: the
    # two tables reference each other, so one constraint has to come second.
    primary_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class EmployeeSalaryHistory(Base):
    """Salary history for final-average-salary calculations."""

    __tablename__ = "employee_salary_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    annual_salary: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    __table_args__ = (
        UniqueConstraint("employee_id", "effective_date", name="uq_salary_history_emp_date"),
    )

    # Relationships
    employee: Mapped[Employee] = relationship(back_populates="salary_history")


class Session(Base):
    """Chat sessions. employee_id is NULL for unauthenticated sessions."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id"),
        nullable=True,
    )
    session_token: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("gen_random_uuid()::text"),
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="active",
    )
    # The conversation's people (migration 009): most recent first, at most 4,
    # each keyed by an internal MEAB ID never shown. Not the principal.
    subjects: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'ended')",
            name="ck_sessions_status",
        ),
        CheckConstraint(
            "jsonb_typeof(subjects) = 'array' AND jsonb_array_length(subjects) <= 4",
            name="ck_sessions_subjects_at_most_four",
        ),
    )

    # Relationships
    employee: Mapped[Employee | None] = relationship(back_populates="sessions")
    messages: Mapped[list[SessionMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class SessionMessage(Base):
    """Turn-by-turn messages: conversation state AND the DB-level audit trail."""

    __tablename__ = "session_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    guardrail_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_session_messages_role",
        ),
        Index("idx_session_messages_session", "session_id", "created_at"),
    )

    # Relationships
    session: Mapped[Session] = relationship(back_populates="messages")


class AnswerEvidence(Base):
    """Which sections verifiably answered a kind of question before.

    Not a cache and not a belief. A row says only that an answer resting on
    these sections passed verification, and it is offered back as enrichment on
    a search result -- never as a filter, so a section that once got lucky
    cannot entrench itself by suppressing the search that would find a better
    one.

    Written only on a guardrail pass: the verifier's judgement is the label, so
    nothing enters on the strength of the model's own opinion.

    `embedding` is JSONB rather than a pgvector column on purpose. The pilot's
    row count is small enough that cosine in Python over an Article-filtered
    slice is unmeasurably fast, and it keeps the schema free of an extension
    the deployment does not otherwise need. Swapping it later is a migration,
    not a redesign.
    """

    __tablename__ = "answer_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    concept: Mapped[str] = mapped_column(Text, nullable=False)
    # Where the concept phrase came from: the member's question with their own
    # particulars stripped, a coverage determination, or (later) a canonical
    # phrase the model emitted. Recorded so that adding one needs no migration.
    concept_source: Mapped[str] = mapped_column(Text, nullable=False, server_default="question")
    embedding: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    article: Mapped[str | None] = mapped_column(Text, nullable=True)
    tier: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sections: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_answer_evidence_article_tier", "article", "tier"),
    )


class FormulaRegistry(Base):
    """Calculation Knowledge Registry: stores reusable agent-derived recipes
    and promoted/approved capabilities.

    Full lifecycle: discovered → agent_derived → validated → approved → stale/retired
    """

    __tablename__ = "formula_registry"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="il_pension_code_40_ilcs_5",
    )
    article_number: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("pension_articles.article_number"),
        nullable=False,
    )
    formula_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    section: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula_name: Mapped[str] = mapped_column(Text, nullable=False)
    formula_type: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    optional_inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    output_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    applicability: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    dependencies: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    rule_specification: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    source_sections: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    evidence_fingerprint: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    execution_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="declarative",
    )
    execution_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    implementation_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    effective_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    annual_parameter_set: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="discovered",
    )
    risk_tier: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="high",
    )
    execution_policy: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="no_execute",
    )
    derivation_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    completeness_check: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_from_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("session_messages.id"),
        nullable=True,
    )
    reviewed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    test_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_public_act: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What this recipe is actually carrying. Not "what earned approval" -- the
    # registry is trusted by default and the admin's power is to correct and
    # retire, not to admit -- but what a reviewer should read first, because an
    # error in a recipe that has quietly produced four hundred figures costs
    # more than an error in one that has produced none. `contested_count` comes
    # from `input_overrides`, which already detects a member disputing a value a
    # figure rested on and until now only mentioned it in prose.
    reuse_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_reused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    contested_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # A recipe that replaced this one, set when `rederive` supersedes it.
    superseded_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "execution_type IN ('declarative', 'custom')",
            name="ck_formula_registry_execution_type",
        ),
        CheckConstraint(
            "knowledge_status IN ('discovered', 'agent_derived', 'validated', 'approved', 'stale', 'retired')",
            name="ck_formula_registry_knowledge_status",
        ),
        CheckConstraint(
            "risk_tier IN ('low', 'medium', 'high')",
            name="ck_formula_registry_risk_tier",
        ),
        CheckConstraint(
            "execution_policy IN ('no_execute', 'estimate', 'authoritative')",
            name="ck_formula_registry_execution_policy",
        ),
        UniqueConstraint("article_number", "formula_id", "version", name="uq_formula_article_version"),
        Index(
            "idx_formula_lookup",
            "article_number", "tier", "formula_type", "effective_start_date",
        ),
        Index("idx_formula_status", "knowledge_status", "execution_policy"),
    )

    # Relationships
    article: Mapped[PensionArticle] = relationship(back_populates="formulas")


class AgentCheckpoint(Base):
    """One saved snapshot of the agent graph, keyed by conversation thread.

    The thread is the chat session, so a follow-up turn resumes from what the
    previous turn established instead of re-deriving it from the prose of the
    answer. Written once per turn (``durability="exit"``), not once per node.

    The payload is opaque here on purpose: LangGraph owns its own serialisation
    format, and this table stores what its serialiser produced. What it contains
    is not opaque, though — member facts reach it — which is why the migration
    puts the same row-level security on this table as on the messages.
    """

    __tablename__ = "agent_checkpoints"

    # LangGraph's thread id, holding the session UUID as text. Kept as text
    # because the checkpointer contract says so, and because the eval harness
    # and tests use thread ids that are not session rows.
    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        Text, primary_key=True, server_default=""
    )
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    checkpoint_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_agent_checkpoints_thread", "thread_id", "checkpoint_ns"),
    )


class AgentCheckpointWrite(Base):
    """Pending channel writes belonging to a checkpoint.

    LangGraph records what each task wrote before the next checkpoint is
    committed, so an interrupted run can be resumed without replaying the work
    that already finished.
    """

    __tablename__ = "agent_checkpoint_writes"

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        Text, primary_key=True, server_default=""
    )
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    task_path: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
