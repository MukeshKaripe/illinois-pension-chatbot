from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.evals.cases import CaseError, load_cases

GOOD = """
id: S1-900
family: S1
areas: [retrieval]
critical: true
turns:
  - q: "What is the Tier 2 retirement age?"
    goal: "The age and the section."
    reference: "Age 67 with 10 years (§1-160(c))."
    checks:
      cites_any: ["1-160"]
      ttft_class: simple
"""


def _write(tmp: Path, text: str, name: str = "c.yaml") -> Path:
    (tmp / "S1").mkdir(exist_ok=True)
    (tmp / "S1" / name).write_text(text, encoding="utf-8")
    return tmp


def test_loads_a_case(tmp_path: Path) -> None:
    [case] = load_cases(_write(tmp_path, GOOD), fixture_keys=set())
    assert case.id == "S1-900" and case.critical and not case.pending
    assert case.turns[0].checks["cites_any"] == ["1-160"]


@pytest.mark.parametrize(
    "mutation, message",
    [
        (("cites_any:", "cite_any:"), "unknown check 'cite_any'"),
        (("family: S1", "family: S99"), "unknown family"),
        (("areas: [retrieval]", "areas: [retreival]"), "unknown area"),
        (("goal: \"The age and the section.\"", "goal: \"\""), "goal"),
        (("ttft_class: simple", "ttft_class: fast"), "ttft_class"),
    ],
)
def test_refuses_a_malformed_case(tmp_path: Path, mutation, message) -> None:
    with pytest.raises(CaseError, match=message):
        load_cases(_write(tmp_path, GOOD.replace(*mutation)), fixture_keys=set())


def test_refuses_an_unknown_fixture(tmp_path: Path) -> None:
    text = GOOD.replace("critical: true", "critical: true\nemployee: nobody")
    with pytest.raises(CaseError, match="fixture 'nobody'"):
        load_cases(_write(tmp_path, text), fixture_keys={"tier2_standard"})


def test_refuses_an_unknown_capability(tmp_path: Path) -> None:
    text = GOOD.replace("critical: true", "critical: true\nneeds: [telepathy]")
    with pytest.raises(CaseError, match="capability 'telepathy'"):
        load_cases(_write(tmp_path, text), fixture_keys=set())


def test_a_case_needing_an_unbuilt_capability_is_pending(tmp_path: Path) -> None:
    text = GOOD.replace("critical: true", "critical: true\nneeds: [find_employee]")
    [case] = load_cases(_write(tmp_path, text), fixture_keys=set())
    assert case.pending


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, GOOD, "a.yaml")
    _write(tmp_path, GOOD, "b.yaml")
    with pytest.raises(CaseError, match="duplicate id 'S1-900'"):
        load_cases(tmp_path, fixture_keys=set())


def test_an_unacceptable_behaviour_name_is_refused(tmp_path: Path) -> None:
    text = GOOD.replace("    checks:", "    acceptable: [shrug]\n    checks:")
    with pytest.raises(CaseError, match="behaviour 'shrug'"):
        load_cases(_write(tmp_path, text), fixture_keys=set())


def test_the_latency_suite_covers_every_turn_type_twice():
    from pathlib import Path

    from pension_chat.evals.cases import load_cases
    fixtures_dir = Path("evals/fixtures/employees")
    cases = [c for c in load_cases(Path("evals/cases"), {p.stem for p in fixtures_dir.glob("*.yaml")})
             if c.family == "L1"]
    assert len(cases) >= 12
    assert all("model_calls_max" in t.checks for c in cases for t in c.turns)
