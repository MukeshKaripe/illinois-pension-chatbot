# Backend image: FastAPI + LangGraph + the calculation engine, one process.
#
# Two stages so the built virtualenv crosses into a runtime image that carries
# no build toolchain. `uv sync --frozen` installs from uv.lock, so an image
# built today and one built next month hold the same dependency graph -- a
# latency measurement taken against a silently different resolution is not a
# measurement of anything.
FROM ghcr.io/astral-sh/uv:0.9-python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# source does, so a code edit rebuilds seconds rather than minutes.
# README.md comes along because it is not documentation here: pyproject declares
# it as the package readme, and hatchling refuses to build the project without
# the file it was told to read.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

# Not root. The process needs no privilege it cannot get as an ordinary user,
# and a container that runs as root is one escape away from being root.
RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --from=build --chown=app:app /app/src /app/src
COPY --from=build --chown=app:app /app/alembic /app/alembic
COPY --from=build --chown=app:app /app/alembic.ini /app/alembic.ini
COPY --from=build --chown=app:app /app/scripts /app/scripts

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

# `--proxy-headers` because Container Apps terminates TLS at its ingress: the
# app sees plain HTTP and would otherwise build redirect URLs on the wrong
# scheme. One worker -- the process holds warmed, in-memory state (the corpus
# graph, the section cache, the HTTP pools), and a second worker would pay the
# whole warm-up again and hold a second copy of it. Scaling is more replicas.
CMD ["uvicorn", "pension_chat.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
