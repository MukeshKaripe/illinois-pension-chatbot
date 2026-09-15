"""Time-to-first-token benchmark, runnable from anywhere the backend can run.

Written to be run from *two* places and compared: a developer's machine and a
container sitting in the same region as the database and the search index. That
comparison is the only way to separate what the model costs from what the
network costs, and the two were indistinguishable from the outside -- a turn
that takes 20 seconds looks the same whether it spent them thinking or waiting.

Fixed questions, fresh thread per turn, warm process. Each of those matters:

* **Fixed questions**, because time-to-first-token varies more between questions
  than between most changes to the code. Comparing a median over the same five
  is a comparison; comparing whatever was asked is an anecdote.
* **A fresh thread per turn**, so no turn is answered out of another's
  checkpoint. A conversation's second turn skips the record fetch and often the
  search, which flatters a run that happens to contain more of them.
* **A warm process**, because the corpus scan and the connection pools are
  paid once at start-up by `main.py::_warm_connections` and never again. Timing
  them into the first answer measures a cost no member pays after a deploy.

Reports the median rather than the mean: one 70-second outlier -- the model
deployment produces them -- moves a mean of fifteen by four seconds and says
nothing about the turn a member is waiting on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

#: The five are deliberately a spread across the shapes of turn the agent has,
#: not five phrasings of one: a calculation, a named section, a definition, a
#: threshold question, and one about how service is counted.
QUESTIONS = [
    "what would my pension be if I retired now?",
    "what does section 11-134 say?",
    "how is my final average salary worked out?",
    "when can i retire without a reduction?",
    "how does my service time get counted?",
]


async def _egress_ip() -> str:
    """The public address this process leaves from, as the world sees it.

    Reported because a Container Apps Consumption environment has no documented
    outbound address: its `staticIp` is the *ingress* one, and egress goes
    through a shared SNAT pool. The only honest way to know which address a
    database firewall has to admit is to ask from inside the container.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get("https://api.ipify.org")
            return response.text.strip()
    except Exception as exc:  # noqa: BLE001 - a diagnostic, never the point
        return f"unknown ({exc})"


async def _database_unreachable() -> str | None:
    """A short, explicit connection attempt, or a sentence saying what failed."""
    from sqlalchemy import text

    from pension_chat.db.session import get_admin_session

    try:
        async with asyncio.timeout(15):
            async with get_admin_session() as db:
                await db.execute(text("select 1"))
    except TimeoutError:
        return (
            "Postgres did not answer within 15s. A dropped connection rather "
            "than a refused one is what a firewall looks like from here -- "
            "check that this container's egress address is allowed."
        )
    except Exception as exc:  # noqa: BLE001
        return f"Postgres refused the connection: {exc}"
    return None


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default=os.environ.get("BENCH_LABEL", "local"))
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument(
        "--employee",
        default=os.environ.get("BENCH_EMPLOYEE", "article-11-standard"),
        help="A persona key, or a raw employee id.",
    )
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    from pension_chat import personas
    from pension_chat.agent import llm
    from pension_chat.agent.graph import run_agent_stream
    from pension_chat.config import get_settings

    persona = personas.by_key(args.employee)
    employee_id = str(persona.id) if persona else args.employee

    settings = get_settings()
    print(
        f"[{args.label}] chat={settings.azure_openai_chat_deployment} "
        f"search={settings.azure_search_endpoint} pg={settings.pghost}",
        flush=True,
    )
    print(f"[{args.label}] egress ip = {await _egress_ip()}", flush=True)

    # Fail on the first turn rather than the fifteenth.
    #
    # Postgres refuses an address its firewall does not know by *dropping* the
    # packet, so the client does not learn it was refused -- it waits. Run from
    # a container whose egress address was not on the list, every one of the
    # fifteen turns spent its full 60s timeout inside the checkpointer and the
    # run reported nothing but "Something went wrong" fifteen times over.
    # Reaching the database is a precondition of measuring anything here, so it
    # is checked once, out loud, before the clock starts.
    if problem := await _database_unreachable():
        print(f"[{args.label}] FATAL: {problem}", flush=True)
        return 2

    # The same warm-up the API performs in its lifespan, so the first measured
    # turn is not the one paying for the corpus scan.
    from pension_chat.main import _warm_connections

    warm_started = time.perf_counter()
    await _warm_connections()
    print(f"[{args.label}] warm-up {time.perf_counter() - warm_started:.2f}s", flush=True)

    rows: list[dict] = []
    for rep in range(args.reps):
        for question in QUESTIONS:
            started = time.perf_counter()
            ttft: float | None = None
            state: dict = {}
            error = None
            async for event in run_agent_stream(
                question, f"bench-{uuid.uuid4()}", employee_id, []
            ):
                if event["type"] == "token" and ttft is None:
                    ttft = time.perf_counter() - started
                elif event["type"] == "done":
                    state = event["state"]
                elif event["type"] == "error":
                    error = event["message"]
            timings = state.get("timings") or {}
            rows.append(
                {
                    "label": args.label,
                    "question": question,
                    "rep": rep,
                    "ttft": ttft,
                    "total": time.perf_counter() - started,
                    "investigate": timings.get("investigate"),
                    "fetch_employee_record": timings.get("fetch_employee_record"),
                    # What is left once the investigation and the record fetch
                    # are accounted for: `compose` plus opening the answer
                    # stream. Reported separately because it is the one phase
                    # no amount of colocation touches.
                    "gap": (ttft or 0.0)
                    - (timings.get("investigate") or 0.0)
                    - (timings.get("fetch_employee_record") or 0.0),
                    "error": error,
                }
            )
            print(
                f"[{args.label}] rep{rep} ttft={ttft if ttft else -1:6.2f}s "
                f"inv={timings.get('investigate') or 0:6.2f}s  {question[:44]}",
                flush=True,
            )

    print(f"\n=== {args.label} ===", flush=True)
    for key in ("ttft", "investigate", "gap"):
        values = [r[key] for r in rows if r[key] is not None]
        if not values:
            continue
        print(
            f"{key:14s} p50={statistics.median(values):6.2f}s "
            f"p25={_percentile(values, 0.25):6.2f}s "
            f"p75={_percentile(values, 0.75):6.2f}s "
            f"max={max(values):6.2f}s  n={len(values)}",
            flush=True,
        )

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(rows, indent=1))
        print(f"report written to {args.json_path}", flush=True)
    else:
        # Container logs are the only channel out of a one-off job, so the rows
        # are printed rather than written to a filesystem nobody will read.
        print("ROWS_JSON " + json.dumps(rows), flush=True)

    await llm.close_clients()
    return 0 if all(r["ttft"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
