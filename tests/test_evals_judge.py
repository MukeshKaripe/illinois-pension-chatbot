from __future__ import annotations

import json
from pathlib import Path

import pytest

from pension_chat.evals.cases import Turn
from pension_chat.evals.judge import Verdict, judge_turn, rubric_version
from pension_chat.evals.trace import TurnTrace

RUBRIC = Path(__file__).parents[1] / "evals" / "rubric.yaml"
TURN = Turn(q="Tier 2 age?", goal="The age and section.", reference="67 with 10 years, §1-160(c).")
TRACE = TurnTrace(case_id="S1-003", turn_index=0, question="Tier 2 age?", employee=None,
                  final_answer="Age 67 with 10 years under Section 1-160(c).")
GOOD = {"goal": "yes", "relevant": True, "within_boundary": True, "behaviour": "answer",
        "voice": 4, "hedging": 5, "clarity": 5, "quotes": {"goal": "Age 67 with 10 years"}, "reason": "ok"}


def _fake(responses: list[str]):
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    return complete, calls


@pytest.mark.asyncio
async def test_a_verdict_is_parsed_and_cached(tmp_path: Path) -> None:
    complete, calls = _fake([json.dumps(GOOD)])
    v = await judge_turn(TURN, TRACE, [], rubric_path=RUBRIC, cache_dir=tmp_path, complete=complete)
    assert v == Verdict(**GOOD) and v.passes([])
    again = await judge_turn(TURN, TRACE, [], rubric_path=RUBRIC, cache_dir=tmp_path, complete=complete)
    assert again == v and len(calls) == 1          # served from the cache


@pytest.mark.asyncio
async def test_the_prompt_carries_goal_reference_and_answer(tmp_path: Path) -> None:
    complete, calls = _fake([json.dumps(GOOD)])
    await judge_turn(TURN, TRACE, [("earlier q", "earlier a")],
                     rubric_path=RUBRIC, cache_dir=tmp_path, complete=complete)
    prompt = json.dumps(calls[0]["messages"])
    for piece in ("The age and section.", "67 with 10 years", "Age 67 with 10 years under", "earlier q"):
        assert piece in prompt
    assert calls[0]["json_mode"] is True


@pytest.mark.asyncio
async def test_malformed_twice_is_unjudged(tmp_path: Path) -> None:
    complete, calls = _fake(["not json", "{\"goal\": \"maybe\"}"])
    assert await judge_turn(TURN, TRACE, [], rubric_path=RUBRIC, cache_dir=tmp_path, complete=complete) is None
    assert len(calls) == 2


def test_passes_needs_goal_yes_and_an_acceptable_behaviour() -> None:
    assert not Verdict(**{**GOOD, "goal": "partly"}).passes([])
    assert not Verdict(**{**GOOD, "relevant": False}).passes([])
    assert not Verdict(**{**GOOD, "behaviour": "answer"}).passes(["ask_critical", "branch_on_critical"])
    assert Verdict(**{**GOOD, "behaviour": "branch_on_critical"}).passes(["ask_critical", "branch_on_critical"])


def test_editing_the_rubric_changes_its_version(tmp_path: Path) -> None:
    p = tmp_path / "r.yaml"
    p.write_text("a: 1")
    first = rubric_version(p)
    p.write_text("a: 2")
    assert rubric_version(p) != first
