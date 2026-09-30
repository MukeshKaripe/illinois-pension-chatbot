"""FastAPI application entry point.

Configures CORS, routers, and lifespan events.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pension_chat.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

# The SDK loggers are silenced independently of our own level.
#
# At DEBUG, `azure.core`'s HTTP policy logs every request and response header
# and httpx logs every URL. Run in a container that ships stdout to Log
# Analytics, that is what a deployment's logs become: thousands of lines of
# headers per turn, an ingestion bill for them, and -- once a real member is
# asking about a real pension -- request metadata retained somewhere nobody
# decided to retain it. Raising our own level to DEBUG to debug our own code
# should not turn the transport into a firehose, so these are pinned at
# WARNING rather than left to inherit.
for _noisy in (
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.identity",
    "httpx",
    "httpcore",
    "openai",
    "urllib3",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("pension_chat")


async def _warm_connections() -> None:
    """Open a connection to each backing service before the first request.

    Every one of these is remote (Azure OpenAI, Azure AI Search, Azure
    Postgres), and the first call to each pays DNS, TCP and TLS setup. Measured
    on this deployment that is the difference between a 13s first answer and a
    6s one — a cost that would otherwise land on whichever member happens to ask
    first after a deploy. Failures are logged and ignored: a warm-up that cannot
    reach a service must not stop the app from starting.
    """
    import asyncio

    from sqlalchemy import text as sa_text

    from pension_chat.agent.corpus import load_corpus
    from pension_chat.agent.llm import get_async_search_client, get_llm
    from pension_chat.db.session import get_admin_session

    async def warm_db():
        async with get_admin_session() as db:
            await db.execute(sa_text("SELECT 1"))

    async def warm_search():
        client = get_async_search_client()
        results = await client.search(search_text="*", top=1, select=["id"])
        async for _ in results:
            break

    async def warm_llm():
        await get_llm().embeddings.create(
            model=settings.azure_openai_embedding_deployment, input="warmup"
        )

    async def warm_corpus():
        await load_corpus()

    for name, task in (
        ("database", warm_db),
        ("search", warm_search),
        ("model", warm_llm),
        # Last: it scans the index, so the search connection above is already open.
        ("corpus", warm_corpus),
    ):
        try:
            await asyncio.wait_for(task(), timeout=20)
            logger.info("Warmed %s connection", name)
        except Exception as exc:
            logger.warning("Could not warm %s connection: %s", name, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    from pension_chat.pbms import build_record_provider
    from pension_chat.records import set_default_provider

    logger.info("Starting Pension Chat API (env=%s)", settings.app_env)
    # RECORD_SOURCE=pbms: every turn reads employee records from PBMS, live and
    # read-only. Built before warming so a misconfiguration fails at start.
    record_provider = build_record_provider(settings)
    set_default_provider(record_provider)
    logger.info("Employee records from %s", settings.record_source)
    await _warm_connections()
    yield
    from pension_chat.agent.llm import close_clients
    from pension_chat.agent.registry import drain_background_writes

    # Bookkeeping writes were moved off the member's critical path, which means
    # some may still be in flight when the process is asked to stop. Closing the
    # connection pool under them would lose the count they were carrying.
    await drain_background_writes()
    await close_clients()
    set_default_provider(None)
    if record_provider is not None:
        await record_provider.close()
    logger.info("Shutting down Pension Chat API")


app = FastAPI(
    title="Pension Chat API",
    description="Illinois Pension Code AI Chatbot — API Backend",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ─────────────────────────────────────────────
from pension_chat.api.admin import router as admin_router  # noqa: E402
from pension_chat.api.auth import router as auth_router  # noqa: E402
from pension_chat.api.health import router as health_router  # noqa: E402
from pension_chat.api.profiles import router as profiles_router  # noqa: E402
from pension_chat.api.sections import router as sections_router  # noqa: E402
from pension_chat.api.sessions import router as sessions_router  # noqa: E402

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(sections_router)
app.include_router(profiles_router)
app.include_router(admin_router)
