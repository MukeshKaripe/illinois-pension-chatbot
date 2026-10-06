"""Load and validate eval cases. A case is one conversation; each turn says
what the user wants (for the judge) and which code checks apply (the vetoes).

Everything is validated before a single turn runs: an unknown check, family,
area, capability, behaviour or fixture is a typo that would otherwise pass
silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pension_chat.evals import capabilities

FAMILIES = ("S1", "S2", "S3", "S4", "S4b", "S5", "S6", "S7", "S8", "S9", "S10", "S11", "S12", "S13", "L1")
FIRST_CLASS = ("S1", "S4", "S4b", "S11", "S13", "L1")
AREAS = frozenset({
    "retrieval", "citations", "calculation", "clarification", "assumptions",
    "boundary", "continuity", "recommendation", "records", "voice", "people",
})
BEHAVIOURS = frozenset({"answer", "ask_critical", "branch_on_critical", "ask_other", "refuse"})
TTFT_CLASSES = {"simple": 3.0, "heavy": 7.0}

#: check name -> the type its value must have
CHECKS: dict[str, type | tuple[type, ...]] = {
    "model_calls_max": int,
    "cites_any": list, "cites_all": list, "cites_none_outside": list, "evidence_has": list,
    "must_state": list, "must_not_state": list,
    "calculation": bool, "no_calculation": bool, "figures_from": list, "worked_from_article": str,
    "critical_inputs": list, "max_assumed": int, "assumptions_disclosed": bool,
    "no_reask": list, "max_questions": int,
    "refuses": bool, "answers_from_article": str,
    "verification_passed": bool, "not_replaced": bool,
    "ttft_class": str, "about_employee": str, "expected_figure": dict, "expected_figures": list,
}


class CaseError(ValueError):
    pass


@dataclass
class Turn:
    q: str
    goal: str
    reference: str
    acceptable: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    employee: str | None = None


@dataclass
class Case:
    id: str
    family: str
    areas: list[str]
    turns: list[Turn]
    path: Path
    critical: bool = False
    needs: list[str] = field(default_factory=list)
    employee: str | None = None
    tiers: list[str] = field(default_factory=lambda: ["full"])
    # The conversation's people (fixture keys), as if the user had already chosen them.
    people: list[str] = field(default_factory=list)

    @property
    def pending(self) -> bool:
        return not set(self.needs) <= capabilities.AVAILABLE


def _fail(path: Path, message: str) -> None:
    raise CaseError(f"{path}: {message}")


def _turn(path: Path, raw: dict[str, Any], fixture_keys: set[str]) -> Turn:
    for key in ("q", "goal", "reference"):
        if not str(raw.get(key) or "").strip():
            _fail(path, f"a turn has no {key}")
    checks = dict(raw.get("checks") or {})
    for name, value in checks.items():
        if name not in CHECKS:
            _fail(path, f"unknown check '{name}'")
        if not isinstance(value, CHECKS[name]):
            _fail(path, f"check '{name}' needs a {CHECKS[name]}")
    if "ttft_class" in checks and checks["ttft_class"] not in TTFT_CLASSES:
        _fail(path, f"ttft_class must be one of {sorted(TTFT_CLASSES)}")
    acceptable = list(raw.get("acceptable") or [])
    for b in acceptable:
        if b not in BEHAVIOURS:
            _fail(path, f"unknown behaviour '{b}'")
    employee = raw.get("employee")
    if employee and employee not in fixture_keys:
        _fail(path, f"unknown fixture '{employee}'")
    return Turn(str(raw["q"]), str(raw["goal"]), str(raw["reference"]), acceptable, checks, employee)


def load_cases(root: Path, fixture_keys: set[str]) -> list[Case]:
    cases: list[Case] = []
    seen: dict[str, Path] = {}
    for path in sorted(root.rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        case_id = str(raw.get("id") or "")
        if not case_id:
            _fail(path, "no id")
        if case_id in seen:
            _fail(path, f"duplicate id '{case_id}' (also in {seen[case_id]})")
        seen[case_id] = path
        family = raw.get("family")
        if family not in FAMILIES:
            _fail(path, f"unknown family '{family}'")
        areas = list(raw.get("areas") or [])
        for area in areas:
            if area not in AREAS:
                _fail(path, f"unknown area '{area}'")
        needs = list(raw.get("needs") or [])
        for need in needs:
            if need not in capabilities.KNOWN:
                _fail(path, f"unknown capability '{need}'")
        employee = raw.get("employee")
        if employee and employee not in fixture_keys:
            _fail(path, f"unknown fixture '{employee}'")
        people = list(raw.get("people") or [])
        for key in people:
            if key not in fixture_keys:
                _fail(path, f"unknown fixture '{key}' in people")
        if len(people) > 4:
            _fail(path, "at most 4 people in a conversation")
        turns = [_turn(path, t, fixture_keys) for t in raw.get("turns") or []]
        if not turns:
            _fail(path, "no turns")
        cases.append(Case(
            id=case_id, family=str(family), areas=areas, turns=turns, path=path,
            critical=bool(raw.get("critical")), needs=needs, employee=employee,
            tiers=list(raw.get("tiers") or ["full"]), people=people,
        ))
    return cases
