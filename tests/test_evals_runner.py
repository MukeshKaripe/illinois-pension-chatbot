from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pension_chat.agent import instrument
from pension_chat.evals.cases import Case, Turn
from pension_chat.evals.runner import RunOptions, run_conversation, run_suite


def _done(answer_sections=("1-160",)) -> dict:
    return {"type": "done", "card": None, "scenarios": [], "state": {
        "retrieved_sections": [{"section": s} for s in answer_sections], "guardrail_passed": True,
        "guardrail_report": {"passed": True, "verified_citations": list(answer_sections)},
        "in_scope": True, "timings": {}, "token_usage": {}}}


def _agent(script: dict[str, list[str]], seen: list | None = None):
    """script: question -> the citations its answer verifies (empty list = a failing answer)."""
    async def agent(question, session_id, employee_id=None, history=None, thread_owner=None):
        if seen is not None:
            seen.append((session_id, employee_id, question))
        async with instrument.timed("model", question):
            await asyncio.sleep(0.001)
        yield {"type": "token", "text": f"answer to {question}"}
        yield _done(tuple(script[question]))
    return agent


def _case(cid: str, qs: list[str], **kw) -> Case:
    turns = [Turn(q=q, goal="g", reference="r", checks={"cites_any": ["1-160"]}) for q in qs]
    return Case(id=cid, family="S1", areas=["retrieval"], turns=turns, path=Path("x.yaml"), **kw)


OPTS = RunOptions(judge=False)


@pytest.mark.asyncio
async def test_turns_run_in_order_on_one_thread() -> None:
    seen: list = []
    result = await run_conversation(_case("A", ["q1", "q2"]), fixtures={}, probe={},
                                    options=OPTS, agent=_agent({"q1": ["1-160"], "q2": ["1-160"]}, seen))
    assert [s[2] for s in seen] == ["q1", "q2"]
    assert len({s[0] for s in seen}) == 1
    assert [t.status for t in result.turns] == ["pass", "pass"] and result.passed


@pytest.mark.asyncio
async def test_turns_after_a_failure_are_marked_not_scored() -> None:
    result = await run_conversation(_case("A", ["q1", "q2", "q3"]), fixtures={}, probe={},
                                    options=OPTS, agent=_agent({"q1": ["1-160"], "q2": [], "q3": ["1-160"]}))
    assert [t.status for t in result.turns] == ["pass", "fail", "after_failure"]


@pytest.mark.asyncio
async def test_a_pending_case_runs_but_is_marked_pending() -> None:
    result = await run_conversation(_case("A", ["q1"], needs=["find_employee"]), fixtures={}, probe={},
                                    options=OPTS, agent=_agent({"q1": ["1-160"]}))
    assert result.turns[0].status == "pending"


@pytest.mark.asyncio
async def test_concurrent_conversations_do_not_see_each_others_calls() -> None:
    results = await run_suite([_case("A", ["qa"]), _case("B", ["qb"])], fixtures={}, probe={},
                              options=RunOptions(judge=False, concurrency=2),
                              agent=_agent({"qa": ["1-160"], "qb": ["1-160"]}))
    labels = {r.case.id: [c["label"] for c in r.turns[0].trace.calls] for r in results}
    assert labels == {"A": ["qa"], "B": ["qb"]}


@pytest.mark.asyncio
async def test_a_failed_conversation_is_rerun_once_and_marked_flaky_if_it_then_passes() -> None:
    answers = iter([[], ["1-160"]])

    async def agent(question, session_id, employee_id=None, history=None, thread_owner=None):
        yield {"type": "token", "text": "x"}
        yield _done(tuple(next(answers)))

    [result] = await run_suite([_case("A", ["q1"])], fixtures={}, probe={}, options=OPTS, agent=agent)
    assert result.passed and result.flaky


@pytest.mark.asyncio
async def test_a_rate_limited_turn_is_retried_not_failed() -> None:
    attempts = {"n": 0}

    async def agent(question, session_id, employee_id=None, history=None, thread_owner=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            yield {"type": "error", "message": "m", "detail": "RateLimitError: Error code: 429"}
            return
        yield {"type": "token", "text": "x"}
        yield _done()

    result = await run_conversation(_case("A", ["q1"]), fixtures={}, probe={},
                                    options=RunOptions(judge=False, rate_limit_backoff=0.0), agent=agent)
    assert result.turns[0].status == "pass" and attempts["n"] == 2
