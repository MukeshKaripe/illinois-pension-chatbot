"""Tests for parsing the turn-understanding result.

The node's job is to replace roughly 200 lines of regex that decided what a
message was. What is tested here is the part that has to survive a model
returning something unexpected: a missing key, a wrong type, or no JSON at
all must never take a member's question down with it.
"""

from __future__ import annotations

import json

import pytest

from pension_chat.agent.understanding import (
    fallback_understanding,
    parse_understanding,
)


def test_a_complete_result_parses() -> None:
    raw = json.dumps({
        "in_scope": True,
        "intent": "recalculate with corrected service",
        "benefit_types": ["retirement_annuity"],
        "needs_figure": True,
        "recompute": True,
        "revised_facts": {"service_years": 23},
        "scenarios": [{"label": "at 60", "retirement_age": 60}],
        "unresolved": [{"what": "date of birth", "why": "sets the reduction"}],
        "answerable_part": "",
        "answers_open_question": False,
        "search_queries": ["minimum annuity 20 years of service"],
    })
    result = parse_understanding(raw)
    assert result.in_scope is True
    assert result.recompute is True
    assert result.revised_facts == {"service_years": 23}
    assert result.scenarios[0]["retirement_age"] == 60
    assert result.search_queries == ["minimum annuity 20 years of service"]


def test_missing_keys_take_safe_defaults() -> None:
    result = parse_understanding('{"in_scope": true}')
    assert result.in_scope is True
    assert result.benefit_types == ["retirement_annuity"]
    assert result.needs_figure is False
    assert result.revised_facts == {}
    assert result.scenarios == []


def test_wrong_types_do_not_crash_the_turn() -> None:
    result = parse_understanding(
        '{"in_scope": "yes", "benefit_types": "retirement_annuity", '
        '"revised_facts": ["service_years"], "scenarios": {"label": "x"}}'
    )
    assert result.in_scope is True
    assert result.benefit_types == ["retirement_annuity"]
    assert result.revised_facts == {}
    assert result.scenarios == []


def test_unparseable_output_falls_open_not_closed() -> None:
    """A member wrongly refused has no recourse; a member wrongly admitted
    still meets the verifier and the calculation gate."""
    result = parse_understanding("the model said something else entirely")
    assert result.in_scope is True
    assert result.needs_figure is False
    assert result.benefit_types == ["retirement_annuity"]


def test_an_explicit_refusal_is_honoured() -> None:
    result = parse_understanding('{"in_scope": false, "out_of_scope_reason": "poem"}')
    assert result.in_scope is False
    assert result.out_of_scope_reason == "poem"


def test_the_fallback_is_permissive() -> None:
    result = fallback_understanding()
    assert result.in_scope is True
    assert result.needs_figure is False


def test_unrecognized_string_in_scope_falls_open() -> None:
    """Garbage string in_scope value must not produce a refusal."""
    result = parse_understanding('{"in_scope": "garbage"}')
    assert result.in_scope is True


def test_explicit_string_falsy_values_are_honoured() -> None:
    """Clearly-expressed intent: "false" and "no" strings mean refusal."""
    result_false = parse_understanding('{"in_scope": "false"}')
    assert result_false.in_scope is False

    result_no = parse_understanding('{"in_scope": "no"}')
    assert result_no.in_scope is False


def test_unrecognized_types_in_scope_fall_open() -> None:
    """Non-string, non-bool types must not produce a refusal."""
    result_zero = parse_understanding('{"in_scope": 0}')
    assert result_zero.in_scope is True

    result_empty_list = parse_understanding('{"in_scope": []}')
    assert result_empty_list.in_scope is True


async def test_the_node_falls_open_when_the_model_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pension_chat.agent import understanding

    async def boom(**kwargs: object) -> str:
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(understanding, "complete", boom)
    update = await understanding.understand_turn(
        {"question": "how much pension will i get", "turn_index": 1, "messages": []}
    )
    assert update["in_scope"] is True
    assert update["requires_calculation"] is False


async def test_the_node_maps_its_result_onto_the_state_keys_the_graph_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pension_chat.agent import understanding

    async def canned(**kwargs: object) -> str:
        return (
            '{"in_scope": true, "needs_figure": true, "recompute": true, '
            '"benefit_types": ["retirement_annuity"], '
            '"revised_facts": {"service_years": 23}, '
            '"search_queries": ["minimum annuity"]}'
        )

    monkeypatch.setattr(understanding, "complete", canned)
    update = await understanding.understand_turn(
        {
            "question": "actually it's more like 23 years",
            "turn_index": 2,
            "messages": [],
            "member_facts": {"service_years": 21.5},
            "assumptions": {},
            "baseline": {
                "result": {"annual_amount": "32126.78"},
                "provision": {"section": "11-134"},
                "rests_on": ["service_years"],
                "turn": 1,
            },
        }
    )
    assert update["requires_calculation"] is True
    assert update["formula_types_needed"] == ["retirement_annuity"]
    assert update["search_queries"] == ["minimum annuity"]
    assert update["stale_figures"] == ["now"]
