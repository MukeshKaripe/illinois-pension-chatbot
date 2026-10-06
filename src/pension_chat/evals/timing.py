"""Time to first answer token: measured here, and estimated for in-region.

Every remote call on the path to the first answer token carries this
machine's round trip (about 245 ms from India to Central US on 2026-09-29) where
an in-region caller would pay about 2 ms. The probe measures that round trip
per endpoint, cold (DNS+TCP+TLS) and warm; the estimate takes the difference
off each call before the first token, counting calls that ran in parallel
once. An estimate is labelled as one, and the latency gate is provisional,
until a run inside the client's region calibrates it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pension_chat.evals.trace import TurnTrace

KIND_ENDPOINT = {"model": "openai", "embedding": "openai", "search": "search",
                 "postgres": "postgres", "postgres_connect": "postgres", "pbms": "pbms"}
Probe = dict[str, dict[str, float]]


class ProbeError(RuntimeError):
    pass


async def probe(settings: Any) -> Probe:
    import asyncpg  # type: ignore[import-untyped]
    import httpx

    out: Probe = {}
    for endpoint, url in (("openai", settings.azure_openai_endpoint), ("search", settings.azure_search_endpoint)):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                t = time.perf_counter()
                await client.get(url)
                cold = time.perf_counter() - t
                t = time.perf_counter()
                await client.get(url)
                out[endpoint] = {"cold": cold, "warm": time.perf_counter() - t}
        except Exception as exc:
            raise ProbeError(f"{endpoint} unreachable at {url}: {exc}") from exc
    try:
        t = time.perf_counter()
        conn = await asyncpg.connect(host=settings.pghost, port=settings.pgport, user=settings.pguser,
                                     password=settings.pgpassword.get_secret_value(),
                                     database=settings.pgdatabase, ssl="require")
        cold = time.perf_counter() - t
        t = time.perf_counter()
        await conn.fetchval("select 1")
        out["postgres"] = {"cold": cold, "warm": time.perf_counter() - t}
        await conn.close()
    except Exception as exc:
        raise ProbeError(f"postgres unreachable at {settings.pghost}: {exc}") from exc
    if getattr(settings, "pbms_sql_server", None):
        # The eval agent reads fixtures, never PBMS, so an unreachable PBMS
        # (off the office network) leaves its round trip unmeasured rather
        # than stopping the run; an estimate with no pbms probe adds nothing for it.
        host, _, port = str(settings.pbms_sql_server).partition(",")
        try:
            timings = []
            for _ in range(2):
                t = time.perf_counter()
                _, writer = await asyncio.wait_for(asyncio.open_connection(host.strip(), int(port or 1433)), 10)
                timings.append(time.perf_counter() - t)
                writer.close()
            out["pbms"] = {"cold": timings[0], "warm": timings[1]}
        except Exception as exc:
            logging.getLogger(__name__).warning("pbms unreachable at %s, not probed: %s", host, exc)
    return out


def _before_first_token(trace: TurnTrace) -> list[dict[str, Any]]:
    if trace.ttft is None:
        return []
    return sorted((c for c in trace.calls if c["start"] < trace.ttft), key=lambda c: c["start"])


def _overhead(call: dict[str, Any], probe: Probe, rtt: float) -> float:
    measured = probe.get(KIND_ENDPOINT.get(call["kind"], ""), {})
    if call["kind"].endswith("_connect"):
        return max(0.0, float(call["end"] - call["start"]) - 3 * rtt)
    return max(0.0, float(measured.get("warm", rtt)) - rtt)


def estimate_in_region(trace: TurnTrace, probe: Probe, in_region_rtt: float = 0.002) -> float | None:
    if trace.ttft is None:
        return None
    saved, group_end, group_max = 0.0, None, 0.0
    for call in _before_first_token(trace):
        cost = _overhead(call, probe, in_region_rtt)
        if group_end is not None and call["start"] < group_end:
            group_end, group_max = max(group_end, call["end"]), max(group_max, cost)
            continue
        saved += group_max
        group_end, group_max = call["end"], cost
    saved += group_max
    return max(0.0, trace.ttft - trace.ttft_rate_limit_wait - saved)


def _union(intervals: list[tuple[float, float]]) -> float:
    total, end = 0.0, None
    for start, stop in sorted(intervals):
        if end is None or start > end:
            total += stop - start
            end = stop
        elif stop > end:
            total += stop - end
            end = stop
    return total


def stage_breakdown(trace: TurnTrace) -> dict[str, float]:
    if trace.ttft is None:
        return {}
    calls = _before_first_token(trace)
    ttft = trace.ttft

    def clip(c: dict[str, Any]) -> tuple[float, float]:
        return (float(c["start"]), min(float(c["end"]), ttft))

    stages: dict[str, float] = {}
    for kind in ("model", "embedding", "search", "postgres"):
        stages[kind] = _union([clip(c) for c in calls if c["kind"].startswith(kind)])
    stages["unattributed"] = max(0.0, ttft - _union([clip(c) for c in calls]))
    return stages
