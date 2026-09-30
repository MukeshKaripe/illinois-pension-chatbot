"""Eval support for people in context: named fixtures, a case's roster, per-person figures."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from pension_chat.evals.cases import Turn
from pension_chat.evals.checks import run_checks
from pension_chat.evals.fixtures import NamedFixtureProvider, load_fixtures
from pension_chat.evals.runner import roster_for
from pension_chat.evals.trace import TurnTrace
from pension_chat.records import NameSearch

FIXTURES = load_fixtures(Path("evals/fixtures/employees"), date(2026, 9, 30))


def test_a_pbms_shaped_fixture_carries_its_source_and_gaps() -> None:
    agent = FIXTURES["pbms_tier_disagreement"][0].to_agent_record([])
    assert agent["record_source"] == "PBMS" and "indicates Tier 3" in agent["missing"]["tier"]


async def test_named_fixtures_can_be_found_by_name() -> None:
    provider = NamedFixtureProvider(FIXTURES)
    assert isinstance(provider, NameSearch)
    first, last = FIXTURES["people_a"][0].display_name.split(" ", 1)
    assert provider.names_in(f"What would {first} {last}'s pension be?") == [(first, last)]
    assert [m.key for m in await provider.find_by_name(first, last)] == ["people_a"]


def test_a_cases_roster_is_built_from_its_fixtures() -> None:
    class _Case:
        people = ["people_a", "people_b"]

    roster = roster_for(_Case(), FIXTURES)
    assert [r["ref"] for r in roster] == ["people_a", "people_b"]
    assert roster[0]["display_name"] == FIXTURES["people_a"][0].display_name
    assert roster[0]["first_name"] and roster[0]["last_name"]


def test_expected_figures_are_checked_per_person() -> None:
    trace = TurnTrace(case_id="x", turn_index=0, question="q", employee=None)
    trace.figures = [{"formula": "a", "when": "now", "annual": "19770.89", "person": "RO_BK TA_NE"},
                     {"formula": "b", "when": "now", "annual": "25200.00", "person": "LI_MO GA_RD"}]
    good = Turn(q="q", goal="g", reference="r", checks={"expected_figures": [
        {"person": "RO_BK TA_NE", "annual": 19770.89}, {"person": "LI_MO GA_RD", "annual": 25200.00}]})
    swapped = Turn(q="q", goal="g", reference="r", checks={"expected_figures": [
        {"person": "RO_BK TA_NE", "annual": 25200.00}, {"person": "LI_MO GA_RD", "annual": 19770.89}]})
    assert all(c.passed for c in run_checks(good, trace))
    assert not any(c.passed for c in run_checks(swapped, trace))
