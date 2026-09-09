"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shipped as the default so local development and tests can construct
# Settings() freely. It is also a sentence in English, which is a poor
# HS256 signing key -- see `_refuse_a_guessable_secret`.
PLACEHOLDER_JWT_SECRET = "change-this-to-a-random-secret-in-production"
MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Central settings object — reads from .env automatically."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Azure OpenAI ──────────────────────────────────────────────
    azure_openai_api_key: str
    azure_openai_endpoint: str
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_chat_deployment: str = "gpt-5.6-luna-1"
    azure_openai_embedding_deployment: str = "text-embedding-3-large"

    # Model routing — Terra/Sol deployment names (disabled until deployed)
    azure_openai_terra_deployment: str | None = None
    azure_openai_sol_deployment: str | None = None

    # ── Azure AI Search ───────────────────────────────────────────
    azure_search_api_key: str
    azure_search_endpoint: str
    azure_search_index_name: str = "pension-code-dev"

    # ── Database (Postgres) ───────────────────────────────────────
    pghost: str
    pguser: str
    pgport: int = 5432
    pgdatabase: str = "pension_chat_db"
    pgpassword: str

    @property
    def database_url(self) -> str:
        """Async Postgres connection string for SQLAlchemy."""
        from urllib.parse import quote_plus
        pwd = self.pgpassword.strip('"').strip("'")
        pwd = quote_plus(pwd)
        return (
            f"postgresql+asyncpg://{self.pguser}:{pwd}"
            f"@{self.pghost}:{self.pgport}/{self.pgdatabase}"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync Postgres connection string for Alembic."""
        from urllib.parse import quote_plus
        pwd = self.pgpassword.strip('"').strip("'")
        pwd = quote_plus(pwd)
        return (
            f"postgresql://{self.pguser}:{pwd}"
            f"@{self.pghost}:{self.pgport}/{self.pgdatabase}"
        )

    # ── Agent tuning ──────────────────────────────────────────────
    # The chat deployment is a reasoning model: it rejects `temperature` and
    # `max_tokens`, and accepts reasoning_effort in {none, low, medium, high}.
    # `none` costs ~1.2s/call with zero reasoning tokens; each step up adds
    # latency and billed reasoning tokens, so nodes ask for the cheapest
    # effort that does their job.
    llm_timeout_seconds: float = 40.0
    reasoning_effort_plan: str = "none"
    # Derivation is the one step where reasoning effort buys correctness.
    # Measured on the Article 11 retirement-annuity recipe: "low" emits a tree
    # that fails to execute, "medium" derives 11-134(a) correctly in ~26s, and
    # "high" reaches the same answer in ~81s. Medium it is — and the result is
    # persisted to formula_registry so the cost is paid once per formula type.
    reasoning_effort_derive: str = "medium"
    reasoning_effort_answer: str = "none"      # answer is grounded, not derived
    reasoning_effort_extract: str = "none"

    # Retrieval
    retrieval_vector_k: int = 40               # HNSW candidates before reranking
    retrieval_top_sections: int = 6            # distinct sections handed to the model
    retrieval_context_token_budget: int = 7000
    retrieval_per_query_top: int = 8
    # Recipe derivation reads whole provisions — a mid-truncated section makes
    # the model (correctly) refuse to derive, so it gets a much larger budget
    # than the answer-writing step, which only needs the passages it cites.
    derivation_context_token_budget: int = 10000
    derivation_top_sections: int = 6
    derivation_timeout_seconds: float = 110.0

    # Conversation history carried into prompts (user+assistant turns)
    history_turns: int = 4

    # Skip the planner round trip when the question names exactly one benefit
    # and the timing scenarios can be read off it deterministically. Once the
    # database sits in the same region as the code, that call is the single
    # largest item left on time-to-first-token.
    deterministic_planning: bool = True

    # Coverage assessment: match how a member describes their job against the
    # Code's own coverage sections instead of trusting the Article column on
    # their record. Costs one search and one model call, and only on turns where
    # the member actually said something about who they are.
    coverage_assessment: bool = True
    # How many turns a conversation may spend asking before it must proceed on
    # the record and say what it assumed. An assistant that keeps asking is its
    # own kind of not listening.
    clarification_budget: int = 3

    # Calculation knowledge registry
    persist_derived_recipes: bool = True
    # Recipes the agent derives itself are estimates, never authoritative.
    derived_recipe_execution_policy: str = "estimate"

    # Guardrails
    guardrail_enabled: bool = True
    # Reject answers whose numeric claims cannot be traced to a calculation
    # result or to retrieved statute text.
    guardrail_numeric_provenance: bool = True

    # ── Database pooling ──────────────────────────────────────────
    db_echo: bool = False
    db_pool_pre_ping: bool = False
    db_pool_recycle_seconds: int = 900

    # ── JWT Auth ──────────────────────────────────────────────────
    # The default is kept so that tests and local development can construct
    # Settings() without ceremony. What must not happen is shipping it: see
    # `_refuse_a_guessable_secret` below, which is the half that has teeth.
    jwt_secret_key: str = PLACEHOLDER_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440  # 24 hours

    # ── App ───────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:8000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @model_validator(mode="after")
    def _refuse_a_guessable_secret(self) -> Settings:
        """Outside development, refuse to start on the shipped signing key.

        Tokens carry the employee id that row-level security is keyed on and,
        once there is an admin role, whether the holder may promote a
        calculation recipe to authoritative. A signing key anybody can read out
        of this file forges both. Failing at boot is the only failure mode here
        that is loud enough to notice.
        """
        if self.app_env in {"development", "test"}:
            return self
        if self.jwt_secret_key == PLACEHOLDER_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET_KEY is still the placeholder from config.py. "
                "Set it to a random value of at least "
                f"{MIN_JWT_SECRET_LENGTH} characters before running outside "
                "development."
            )
        if len(self.jwt_secret_key) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY is {len(self.jwt_secret_key)} characters; "
                f"the minimum is {MIN_JWT_SECRET_LENGTH}."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings object.

    Cached: this is called from every node on every request, and an uncached
    call re-reads and re-parses `.env` from disk each time.
    """
    return Settings()  # type: ignore[call-arg]
