"""Shared async clients and model-call helpers.

Two things matter here for latency:

1. **Client reuse.** ``AsyncAzureOpenAI`` owns an HTTP connection pool. Building
   one per node call (as the first implementation did) pays a fresh TLS
   handshake on every hop — several seconds across a five-node graph. These are
   module-level singletons created lazily.
2. **Reasoning effort.** The chat deployment is a reasoning model. Effort
   ``none`` costs ~1.2s round-trip and 0 reasoning tokens; ``high`` costs ~1.7s
   plus reasoning tokens. Nodes pick the cheapest effort that does their job.

The deployment also rejects ``temperature`` and ``max_tokens`` (see
``pension_chat.config``), so callers must use ``max_completion_tokens`` and
leave temperature at its default.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient as AsyncSearchClient
from openai import APIConnectionError, APITimeoutError, AsyncAzureOpenAI, RateLimitError

from pension_chat.config import get_settings

logger = logging.getLogger("pension_chat.agent.llm")

_llm_client: AsyncAzureOpenAI | None = None
_search_client: AsyncSearchClient | None = None
_lock = asyncio.Lock()


def get_llm() -> AsyncAzureOpenAI:
    """Return the process-wide Azure OpenAI client, creating it on first use."""
    global _llm_client
    if _llm_client is None:
        settings = get_settings()
        _llm_client = AsyncAzureOpenAI(
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_endpoint=settings.azure_openai_endpoint,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,  # we retry explicitly below so we can bound total time
        )
    return _llm_client


def get_async_search_client() -> AsyncSearchClient:
    """Return the process-wide async Azure AI Search client.

    The sync ``SearchClient`` used elsewhere blocks the event loop; retrieval on
    the request path must use this one.
    """
    global _search_client
    if _search_client is None:
        settings = get_settings()
        _search_client = AsyncSearchClient(
            endpoint=settings.azure_search_endpoint,
            index_name=settings.azure_search_index_name,
            credential=AzureKeyCredential(settings.azure_search_api_key),
        )
    return _search_client


async def close_clients() -> None:
    """Release pooled connections (called from the FastAPI lifespan shutdown)."""
    global _llm_client, _search_client
    if _search_client is not None:
        await _search_client.close()
        _search_client = None
    if _llm_client is not None:
        await _llm_client.close()
        _llm_client = None


# ── Token accounting ─────────────────────────────────────────────

@dataclass
class TokenUsage:
    """Per-request token totals, accumulated across every node in one turn."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0
    by_node: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, node: str, usage: Any) -> None:
        if usage is None:
            return
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", 0) or 0 if details else 0
        p_details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(p_details, "cached_tokens", 0) or 0 if p_details else 0

        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.reasoning_tokens += reasoning
        self.cached_tokens += cached
        self.calls += 1

        slot = self.by_node.setdefault(
            node, {"prompt": 0, "completion": 0, "reasoning": 0, "cached": 0, "calls": 0}
        )
        slot["prompt"] += prompt
        slot["completion"] += completion
        slot["reasoning"] += reasoning
        slot["cached"] += cached
        slot["calls"] += 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
            "by_node": self.by_node,
        }


_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)

# Token accounting is per-request but every node calls the model independently.
# A context variable threads one accumulator through the whole turn without
# putting a mutable object into the graph state (LangGraph would drop a key the
# state schema does not declare, and declaring it would leak accounting into the
# domain model).
_current_usage: contextvars.ContextVar[TokenUsage | None] = contextvars.ContextVar(
    "pension_chat_token_usage", default=None
)


def begin_usage() -> TokenUsage:
    """Start a fresh token accumulator for this request and make it current."""
    usage = TokenUsage()
    _current_usage.set(usage)
    return usage


def current_usage() -> TokenUsage | None:
    """The accumulator for the request in flight, if one was started."""
    return _current_usage.get()


def _resolve_usage(explicit: TokenUsage | None) -> TokenUsage | None:
    return explicit if explicit is not None else _current_usage.get()


@dataclass(frozen=True)
class ToolCall:
    """One tool the model asked for, with its arguments already parsed."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolTurn:
    """What one model call came back with: tools to run, or an answer.

    ``raw_message`` is the assistant turn that *made* the calls, as a plain
    dict ready to append. The API rejects a ``role: "tool"`` result whose call
    is not already in the history, so the loop cannot skip it.
    """

    calls: tuple[ToolCall, ...]
    content: str
    raw_message: dict[str, Any]

    @property
    def done(self) -> bool:
        return not self.calls


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """A tool call's arguments, or an empty dict if the model mangled them.

    Raising would end a member's turn over a recoverable mistake: the model has
    still said which tool it wants, and the tool can report what it needed.
    """
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def call_with_tools(
    *,
    node: str,
    messages: list[dict],
    tools: list[dict],
    usage: TokenUsage | None = None,
    reasoning_effort: str = "none",
    attempts: int = 2,
    timeout: float | None = None,
) -> ToolTurn:
    """One completion that may ask for tools.

    Retries transport failures the way `complete` does, and for the same
    reason: a 400 from a malformed request is a bug, not something a second
    attempt fixes.
    """
    settings = get_settings()
    client = get_llm()
    if timeout is not None:
        client = client.with_options(timeout=timeout)
    kwargs: dict[str, Any] = {
        "model": settings.azure_openai_chat_deployment,
        "messages": messages,
        "reasoning_effort": reasoning_effort,
    }
    # An empty tool list with `tool_choice` set is a 400, not a no-op.
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    last_error: Exception | None = None
    for attempt in range(attempts):
        started = time.perf_counter()
        try:
            response = await client.chat.completions.create(**kwargs)
            accumulator = _resolve_usage(usage)
            if accumulator is not None:
                accumulator.record(node, response.usage)
            message = response.choices[0].message
            raw_calls = list(getattr(message, "tool_calls", None) or [])
            calls = tuple(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=_parse_arguments(call.function.arguments),
                )
                for call in raw_calls
            )
            raw_message: dict[str, Any] = {"role": "assistant"}
            if message.content:
                raw_message["content"] = message.content
            if raw_calls:
                raw_message["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in raw_calls
                ]
            logger.debug(
                "%s asked for %d tool(s) in %.2fs", node, len(calls), time.perf_counter() - started
            )
            return ToolTurn(
                calls=calls, content=message.content or "", raw_message=raw_message
            )
        except _RETRYABLE as exc:
            last_error = exc
            logger.warning("%s transport error (attempt %d): %s", node, attempt + 1, exc)
            await asyncio.sleep(0.4 * (attempt + 1))

    raise last_error if last_error else RuntimeError(f"{node} failed with no error")


async def complete(
    *,
    node: str,
    messages: list[dict],
    usage: TokenUsage | None = None,
    reasoning_effort: str = "none",
    json_mode: bool = False,
    max_completion_tokens: int | None = None,
    attempts: int = 2,
    timeout: float | None = None,
) -> str:
    """Run one non-streaming completion and return its text.

    Retries only transport-level failures; a 400 from a malformed request is a
    bug and is raised immediately rather than burned on a retry.

    ``timeout`` overrides the client default for this call. High reasoning
    effort over a large context can legitimately run past the default, and
    retrying such a call on timeout doubles an already-long stall — those
    callers pass a longer timeout with ``attempts=1``.
    """
    settings = get_settings()
    client = get_llm()
    if timeout is not None:
        client = client.with_options(timeout=timeout)
    kwargs: dict[str, Any] = {
        "model": settings.azure_openai_chat_deployment,
        "messages": messages,
        "reasoning_effort": reasoning_effort,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_completion_tokens:
        kwargs["max_completion_tokens"] = max_completion_tokens

    last_error: Exception | None = None
    for attempt in range(attempts):
        started = time.perf_counter()
        try:
            response = await client.chat.completions.create(**kwargs)
            accumulator = _resolve_usage(usage)
            if accumulator is not None:
                accumulator.record(node, response.usage)
            logger.debug("%s completed in %.2fs", node, time.perf_counter() - started)
            return response.choices[0].message.content or ""
        except _RETRYABLE as exc:
            last_error = exc
            logger.warning("%s transport error (attempt %d): %s", node, attempt + 1, exc)
            await asyncio.sleep(0.4 * (attempt + 1))

    raise last_error if last_error else RuntimeError(f"{node}: completion failed")


async def stream(
    *,
    node: str,
    messages: list[dict],
    usage: TokenUsage | None = None,
    reasoning_effort: str = "none",
    max_completion_tokens: int | None = None,
) -> AsyncIterator[str]:
    """Stream a completion, yielding text deltas as they arrive.

    Streaming is not retried: once the first delta has been handed to the
    caller it has already been sent to the browser, so a silent restart would
    duplicate text.
    """
    settings = get_settings()
    client = get_llm()
    kwargs: dict[str, Any] = {
        "model": settings.azure_openai_chat_deployment,
        "messages": messages,
        "reasoning_effort": reasoning_effort,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if max_completion_tokens:
        kwargs["max_completion_tokens"] = max_completion_tokens

    response = await client.chat.completions.create(**kwargs)
    async for chunk in response:
        accumulator = _resolve_usage(usage)
        if getattr(chunk, "usage", None) and accumulator is not None:
            accumulator.record(node, chunk.usage)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
