"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    jwt_secret_key: str = "change-this-to-a-random-secret-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440  # 24 hours

    # ── App ───────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:8000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings object.

    Cached: this is called from every node on every request, and an uncached
    call re-reads and re-parses `.env` from disk each time.
    """
    return Settings()  # type: ignore[call-arg]
