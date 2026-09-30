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
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from pension_chat.agent import instrument
from pension_chat.evals.cases import Case
from pension_chat.evals.checks import CheckResult, run_checks
from pension_chat.evals.fixtures import FixtureProvider
from pension_chat.evals.judge import Verdict, judge_turn
from pension_chat.evals.timing import Probe, estimate_in_region, stage_breakdown
from pension_chat.evals.trace import TurnTrace, build_trace
from pension_chat.records import use_provider


@dataclass
class RunOptions:
    concurrency: int = 8
    judge: bool = True
    judge_gates: bool = False
    latency_gate: bool = False
    rubric_path: Path = Path("evals/rubric.yaml")
    cache_dir: Path = Path("evals/.judge_cache")
    rate_limit_retries: int = 2
    rate_limit_backoff: float = 10.0


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

    @property
    def passed(self) -> bool:
        return all(t.status in ("pass", "pending") for t in self.turns)


def _rate_limited(trace: TurnTrace) -> bool:
    return bool(trace.error) and ("429" in trace.error or "RateLimit" in trace.error)


async def _one_turn(agent, question, thread, employee, case_id, index) -> TurnTrace:
    inst = instrument.begin()
    started = time.perf_counter()
    events: list[tuple[float, dict[str, Any]]] = []
    async for event in agent(question, thread, employee):
        events.append((time.perf_counter() - started, event))
    return build_trace(case_id=case_id, turn_index=index, question=question, employee=employee,
                       events=events, instrument=inst, started=started)


async def run_conversation(case: Case, *, fixtures: dict, probe: Probe, options: RunOptions,
                           agent=None) -> ConversationResult:
    from pension_chat.agent.graph import build_loop_graph, run_agent_stream, use_graph

    agent = agent or run_agent_stream
    thread = f"eval-{case.id}-{uuid.uuid4().hex[:8]}"
    result = ConversationResult(case)
    history: list[tuple[str, str]] = []
    failed_before = False
    with use_graph(build_loop_graph(InMemorySaver())), use_provider(FixtureProvider(fixtures)):
        for index, turn in enumerate(case.turns):
            employee = turn.employee or case.employee
            for attempt in range(options.rate_limit_retries + 1):
                trace = await _one_turn(agent, turn.q, thread, employee, case.id, index)
                if not _rate_limited(trace) or attempt == options.rate_limit_retries:
                    break
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
                if options.judge and verdict is None:
                    status = "unjudged"
                elif options.judge_gates and verdict is not None and not verdict.passes(turn.acceptable):
                    status = "fail"
                else:
                    status = "pass"
            failed_before = failed_before or status in ("fail", "error", "unjudged")
            result.turns.append(TurnResult(case.id, index, trace, checks, verdict, status,
                                           estimate_in_region(trace, probe) if probe else None,
                                           stage_breakdown(trace)))
            history.append((turn.q, trace.final_answer))
    return result


async def run_suite(cases: list[Case], *, fixtures: dict, probe: Probe, options: RunOptions,
                    agent=None) -> list[ConversationResult]:
    gate = asyncio.Semaphore(options.concurrency)

    async def guarded(case: Case) -> ConversationResult:
        async with gate:
            return await run_conversation(case, fixtures=fixtures, probe=probe, options=options, agent=agent)

    results = list(await asyncio.gather(*(guarded(c) for c in cases)))
    for i, first in enumerate(results):
        if not first.passed:
            second = await guarded(first.case)
            if second.passed:
                second.flaky = True
                results[i] = second
    return results
