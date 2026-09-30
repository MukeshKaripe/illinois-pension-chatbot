from __future__ import annotations

from pathlib import Path

from pension_chat.evals.cases import AREAS, Case, Turn
from pension_chat.evals.select import AREA_PATHS, areas_for, select


def _case(cid, family, areas, critical=False, tiers=("full",)) -> Case:
    return Case(id=cid, family=family, areas=list(areas), path=Path(f"evals/cases/{family}/{cid}.yaml"),
                turns=[Turn("q", "g", "r")], critical=critical, tiers=list(tiers))


CASES = [
    _case("S1-001", "S1", ["retrieval"], critical=True, tiers=("smoke", "full")),
    _case("S4-001", "S4", ["calculation"]),
    _case("S11-001", "S11", ["continuity"], tiers=("smoke", "full")),
]


def test_every_area_has_paths() -> None:
    assert set(AREA_PATHS) == AREAS


def test_a_retrieval_change_selects_retrieval_cases_and_criticals() -> None:
    picked = select(CASES, changed=["src/pension_chat/agent/retrieval.py"])
    assert [c.id for c in picked] == ["S1-001"]


def test_a_calculation_change_keeps_the_criticals() -> None:
    picked = select(CASES, changed=["src/pension_chat/calculation/dsl_interpreter.py"])
    assert {c.id for c in picked} == {"S1-001", "S4-001"}


def test_a_core_change_selects_everything() -> None:
    assert areas_for(["src/pension_chat/agent/loop.py"]) == set(AREAS)


def test_a_changed_case_file_selects_itself() -> None:
    picked = select(CASES, changed=["evals/cases/S4/S4-001.yaml"])
    assert "S4-001" in {c.id for c in picked}


def test_an_unrelated_change_selects_only_criticals() -> None:
    assert [c.id for c in select(CASES, changed=["frontend/src/app/page.tsx"])] == ["S1-001"]


def test_tier_family_and_id_filters() -> None:
    assert {c.id for c in select(CASES, tier="smoke")} == {"S1-001", "S11-001"}
    assert [c.id for c in select(CASES, families=["S4"])] == ["S4-001"]
    assert [c.id for c in select(CASES, ids=["S11-001"])] == ["S11-001"]


def test_nodes_holds_prompts_and_input_resolution_so_it_is_core() -> None:
    assert areas_for(["src/pension_chat/agent/nodes.py"]) == set(AREAS)
