"""The starting inventory the spec sets, and that it all loads."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from pension_chat.evals.cases import FIRST_CLASS, load_cases
from pension_chat.evals.fixtures import load_fixtures

EVALS = Path(__file__).parents[1] / "evals"
STATUTES = Path(__file__).parents[1] / "data" / "statutes"


@pytest.fixture(scope="module")
def cases():
    fixtures = load_fixtures(EVALS / "fixtures" / "employees", date.today())
    return load_cases(EVALS / "cases", set(fixtures))


def test_inventory_matches_the_spec(cases) -> None:
    per_family = Counter(c.family for c in cases)
    assert per_family["S1"] >= 8 and per_family["S4"] >= 6 and per_family["S4b"] >= 6
    assert per_family["S11"] >= 5 and per_family["S6"] >= 3 and per_family["S12"] >= 8
    for fam in ("S2", "S3", "S5", "S7", "S10"):
        assert per_family[fam] >= 1, fam
    for fam in ("S8", "S9"):
        assert per_family[fam] >= 2, fam


def test_smoke_tier_has_every_critical_and_each_first_class_family(cases) -> None:
    smoke = [c for c in cases if "smoke" in c.tiers]
    assert all("smoke" in c.tiers for c in cases if c.critical)
    assert {c.family for c in smoke} >= set(FIRST_CLASS)
    assert 10 <= len(smoke) <= 18


def test_the_tier2_case_is_critical_and_checks_the_evidence(cases) -> None:
    [case] = [c for c in cases if c.id == "S1-003"]
    assert case.critical
    assert case.turns[0].checks["evidence_has"] == ["1-160(c)"]


@pytest.mark.skipif(not STATUTES.exists(), reason="data/ is not in the repo")
def test_every_section_a_reference_names_exists_in_the_corpus(cases) -> None:
    corpus = "".join(p.read_text(encoding="utf-8", errors="replace") for p in STATUTES.glob("article_*.txt"))
    missing = []
    for c in cases:
        for t in c.turns:
            for section in re.findall(r"§\s?(\d+-\d+(?:\.\d+)?[a-z]?)", t.reference):
                if f"(40 ILCS 5/{section})" not in corpus:
                    missing.append(f"{c.id}: §{section}")
    assert not missing, missing


def test_the_member_first_harness_is_gone() -> None:
    root = Path(__file__).parents[1]
    assert not (root / "scripts" / "eval_agent.py").exists()
    assert [p.name for p in (root / "evals").glob("*.yaml")] == ["rubric.yaml"], "old suite files in evals/"
    assert not (root / "evals" / "_captured").exists()
