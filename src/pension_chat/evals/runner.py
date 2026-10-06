"""Run conversations through the real agent, concurrently, one thread each.

Each conversation gets its own in-memory graph, its own fixture provider, and a
fresh instrument per turn -- all context variables set inside its own task, so
eight conversations running at once never see each other's state. Turns within
a conversation run in order, because turn two resumes from turn one.

A rate-limited turn is retried after a backoff, not failed: a 429 is capacity,
not quality. A conversation that fails is re-run once; failed-then-passed is
reported as flaky rather than as a regression.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from pension_chat.agent import instrument
from pension_chat.evals.cases import Case
from pension_chat.evals.checks import CheckResult, run_checks
from pension_chat.evals.fixtures import FixtureProvider, NamedFixtureProvider
from pension_chat.evals.judge import Verdict, judge_turn
from pension_chat.evals.timing import Probe, estimate_in_region, stage_breakdown
from pension_chat.evals.trace import TurnTrace, build_trace
from pension_chat.records import use_provider

logger = logging.getLogger(__name__)


@dataclass
class RunOptions:
    concurrency: int = 2
    judge: bool = True
    judge_gates: bool = False
    latency_gate: bool = False
    rubric_path: Path = Path("evals/rubric.yaml")
    cache_dir: Path = Path("evals/.judge_cache")
    rate_limit_retries: int = 2
    rate_limit_backoff: float = 10.0
    #: Judge every turn, whatever the code checks said -- for calibration,
    #: where each turn a person labels needs a verdict to compare. Statuses
    #: are unchanged; only verdicts are added.
    judge_all: bool = False
    #: Print one flushed line per finished conversation (the CLI turns it on).
    progress: bool = False
    #: Timing-only re-runs of every case, so p95 means something; not judged,
    #: not checked for pass/fail.
    repeat: int = 1


@dataclass
class TurnResult:
    case_id: str
    turn_index: int
    trace: TurnTrace
    checks: list[CheckResult]
    verdict: Verdict | None
    status: str
    estimate: float | None
    stages: dict[str, float]


@dataclass
class ConversationResult:
    case: Case
    turns: list[TurnResult] = field(default_factory=list)
    flaky: bool = False
    #: Timing-only re-runs (RunOptions.repeat); read for latency, never for pass/fail.
    repeats: list[ConversationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(t.status in ("pass", "pending") for t in self.turns)


def _rate_limited(trace: TurnTrace) -> bool:
    """A 429 the turn died of, or one a node gave up on and the agent swallowed
    (derivation catches its own failures and carries on without a figure)."""
    error = trace.error or ""
    exhausted = any(n.get("kind") == "rate_limit_exhausted" for n in trace.notes)
    return exhausted or "429" in error or "RateLimit" in error


def _snapshot(saver: InMemorySaver) -> tuple[Any, Any, Any]:
    return copy.deepcopy((saver.storage, saver.writes, saver.blobs))


def _restore(saver: InMemorySaver, snapshot: tuple[Any, Any, Any]) -> None:
    storage, writes, blobs = copy.deepcopy(snapshot)
    saver.storage.clear()
    saver.storage.update(storage)
    saver.writes.clear()
    saver.writes.update(writes)
    saver.blobs.clear()
    saver.blobs.update(blobs)


def roster_for(case: Any, fixtures: dict[str, Any]) -> list[dict[str, Any]]:
    """A case's people, as if the user had already chosen them (spec §8)."""
    from pension_chat.evals.fixtures import _split

    roster = []
    for key in getattr(case, "people", None) or []:
        record = fixtures[key][0]
        first, last = _split(record.display_name) or (None, None)
        roster.append({"ref": key, "display_name": record.display_name, "first_name": first,
                       "last_name": last, "department": record.department, "employer": record.employer,
                       "birth_year": None})
    return roster


async def _one_turn(agent: Any, question: str, thread: str, employee: str | None,
                    case_id: str, index: int, roster: list[dict[str, Any]] | None = None) -> TurnTrace:
    inst = instrument.begin()
    started = time.perf_counter()
    events: list[tuple[float, dict[str, Any]]] = []
    async for event in agent(question, thread, employee, **({"subjects": roster} if roster else {})):
        events.append((time.perf_counter() - started, event))
    return build_trace(case_id=case_id, turn_index=index, question=question, employee=employee,
                       events=events, instrument=inst, started=started)


async def run_conversation(case: Case, *, fixtures: dict[str, Any], probe: Probe, options: RunOptions,
                           agent: Any = None) -> ConversationResult:
    from pension_chat.agent.graph import build_loop_graph, run_agent_stream, use_graph

    agent = agent or run_agent_stream
    thread = f"eval-{case.id}-{uuid.uuid4().hex[:8]}"
    result = ConversationResult(case)
    history: list[tuple[str, str]] = []
    failed_before = False
    saver = InMemorySaver()
    roster = roster_for(case, fixtures)
    provider = NamedFixtureProvider(fixtures) if roster else FixtureProvider(fixtures)
    with use_graph(build_loop_graph(saver)), use_provider(provider):
        for index, turn in enumerate(case.turns):
            employee = turn.employee or case.employee
            for attempt in range(options.rate_limit_retries + 1):
                # A turn that ends in a 429 has already checkpointed its question
                # (durability="exit" persists on error too); retrying on top of
                # that would put the question in the thread twice.
                before = _snapshot(saver)
                trace = await _one_turn(agent, turn.q, thread, employee, case.id, index, roster)
                if not _rate_limited(trace) or attempt == options.rate_limit_retries:
                    break
                _restore(saver, before)
                await asyncio.sleep(options.rate_limit_backoff * (attempt + 1))
            checks = run_checks(turn, trace, latency_gate=options.latency_gate)
            gating_failed = any(not c.passed and c.gating for c in checks)
            verdict: Verdict | None = None
            if case.pending:
                status = "pending"
            elif failed_before:
                status = "after_failure"
            elif trace.error:
                status = "error"
            elif gating_failed:
                status = "fail"
            else:
                if options.judge:
                    verdict = await judge_turn(turn, trace, history, rubric_path=options.rubric_path,
                                               cache_dir=options.cache_dir)
                if options.judge and verdict is None and options.judge_gates:
                    # Unjudged fails only a turn the judge is allowed to fail; an
                    # uncalibrated judge being throttled is capacity, not quality.
                    status = "unjudged"
                elif options.judge_gates and verdict is not None and not verdict.passes(turn.acceptable):
                    status = "fail"
                else:
                    status = "pass"
            if options.judge_all and verdict is None and not trace.error:
                verdict = await judge_turn(turn, trace, history, rubric_path=options.rubric_path,
                                           cache_dir=options.cache_dir)
            failed_before = failed_before or status in ("fail", "error", "unjudged")
            result.turns.append(TurnResult(case.id, index, trace, checks, verdict, status,
                                           estimate_in_region(trace, probe) if probe else None,
                                           stage_breakdown(trace)))
            history.append((turn.q, trace.final_answer))
    return result


async def run_suite(cases: list[Case], *, fixtures: dict[str, Any], probe: Probe, options: RunOptions,
                    agent: Any = None) -> list[ConversationResult]:
    gate = asyncio.Semaphore(options.concurrency)
    finished = 0
    started = time.perf_counter()

    def report(result: ConversationResult) -> None:
        nonlocal finished
        finished += 1
        if not options.progress:
            return
        statuses = " ".join(t.status for t in result.turns)
        verdict = "pass" if result.passed else "fail"
        print(f"[{finished}/{len(cases)}] {result.case.id} {verdict} ({statuses}) "
              f"{time.perf_counter() - started:.0f}s", flush=True)

    async def guarded(case: Case) -> ConversationResult:
        result = await _guarded(case)
        report(result)
        return result

    async def _guarded(case: Case) -> ConversationResult:
        async with gate:
            try:
                return await run_conversation(case, fixtures=fixtures, probe=probe, options=options,
                                              agent=agent)
            except Exception as exc:
                # One conversation failing in a way the agent did not handle is
                # that conversation's error, never the whole run's.
                trace = TurnTrace(case_id=case.id, turn_index=0, question=case.turns[0].q,
                                  employee=case.employee, error=f"{type(exc).__name__}: {exc}")
                return ConversationResult(case, [TurnResult(case.id, 0, trace, [], None, "error", None, {})])

    results = list(await asyncio.gather(*(guarded(c) for c in cases)))
    for i, first in enumerate(results):
        if not first.passed:
            second = await _guarded(first.case)
            if options.progress:
                print(f"[re-run] {first.case.id} {'pass (flaky)' if second.passed else 'fail again'}",
                      flush=True)
            if second.passed:
                second.flaky = True
                results[i] = second
    if options.repeat > 1:
        quiet = dataclasses.replace(options, judge=False, judge_all=False, progress=False)
        for result in results:
            for _ in range(options.repeat - 1):
                async with gate:
                    try:
                        result.repeats.append(await run_conversation(
                            result.case, fixtures=fixtures, probe=probe, options=quiet, agent=agent))
                    except Exception:
                        logger.exception("Timing repeat failed for %s", result.case.id)
    return results
