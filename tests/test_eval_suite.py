"""Tests for the benchmark's own definition files.

An expectation with a misspelled key is worse than no expectation: the case
still passes, the report still says so, and nothing is being checked. The same
goes for a case whose id collides with another, or one that claims a category
the report will never group it under. None of that shows up when the suite is
green, which is exactly when it matters.

These run offline. They read the YAML and the checker; they do not run the agent.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
HARNESS = ROOT / "scripts" / "eval_agent.py"


def _harness():
    spec = importlib.util.spec_from_file_location("eval_agent", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_agent"] = module
    spec.loader.exec_module(module)
    return module


def _implemented_expectations() -> set[str]:
    """Every key `check()` actually reads, taken from the source.

    Read out of the AST rather than listed by hand, so the list cannot drift
    away from the function it describes.
    """
    tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
    check = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "check"
    )
    keys: set[str] = set()
    for node in ast.walk(check):
        # expect.get("name") and expect.get("name", default)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "expect"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            keys.add(node.args[0].value)
    return keys


def _cases() -> list[dict]:
    cases = []
    for path in sorted(EVALS.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in document.get("cases", []):
            case["source"] = path.name
            cases.append(case)
    return cases


ALL_CASES = _cases()


def test_the_suite_is_not_empty():
    assert len(ALL_CASES) > 30


def test_case_ids_are_unique():
    ids = [str(c["id"]) for c in ALL_CASES]
    duplicates = {i for i in ids if ids.count(i) > 1}

    assert not duplicates, f"duplicate case ids: {sorted(duplicates)}"


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: str(c["id"]))
def test_every_expectation_is_one_the_checker_reads(case):
    implemented = _implemented_expectations()
    for turn in case["turns"]:
        unknown = set(turn.get("expect") or {}) - implemented
        assert not unknown, (
            f"{case['id']} expects {sorted(unknown)}, which check() never reads — "
            "the case would pass whatever the agent did"
        )


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: str(c["id"]))
def test_every_case_has_the_shape_the_runner_expects(case):
    assert case["turns"], f"{case['id']} has no turns"
    for turn in case["turns"]:
        assert turn.get("q", "").strip(), f"{case['id']} has a turn with no question"
    assert case.get("status", "active") in {"active", "expected_fail", "blocked"}
    assert case.get("employee", "default") == "anon" or isinstance(
        case.get("employee", "default"), str
    )


@pytest.mark.parametrize(
    "case",
    [c for c in ALL_CASES if c.get("status") == "expected_fail"],
    ids=lambda c: str(c["id"]),
)
def test_a_known_gap_says_why(case):
    """Recording a gap without the reason turns it into a silent exclusion."""
    assert case.get("gap", "").strip(), f"{case['id']} is expected_fail with no reason"


@pytest.mark.parametrize(
    "case",
    [c for c in ALL_CASES if c.get("status") == "blocked"],
    ids=lambda c: str(c["id"]),
)
def test_a_blocked_case_says_what_it_is_waiting_for(case):
    assert case.get("blocked_by", "").strip(), f"{case['id']} is blocked with no reason"


def test_the_critical_cases_are_the_ones_worth_failing_a_run_over():
    """Critical means "an overall percentage must not bury this".

    Kept explicit so that marking something critical stays a decision. The
    shared property is that a confident wrong answer does real harm: a figure
    from the wrong Article, an unsupported formula the member supplied, a
    calculation for someone who does not qualify.
    """
    critical = {str(c["id"]) for c in ALL_CASES if c.get("critical")}

    assert critical == {
        "11", "14", "16", "S3",           # from the original regression suite
        "G01", "G02", "G04",              # right Article, right provision
        "G13",                            # a hypothetical must not overwrite the record
        "G21", "G22", "G23",              # unsupported formula, bigger number, wrong Article
        "G30",                            # the full arc
        # Coverage. Everything else rests on which Article governs, so a
        # confident wrong answer here is wrong all the way down — and none of it
        # shows up as a bad citation or an untraceable figure, which is why the
        # 43-case suite stayed green while a member was being told the wrong
        # fund computed their pension.
        "G31",                            # a description pointing away from the record
        "G32",                            # and the answer to it sticking
        "G35",                            # not interrogating someone whose file agrees
        # Suppositions. Answering "if I were a park employee" in the plain
        # indicative tells a member they are in a fund they only asked about.
        "G38", "G39", "G41",              # adopted, held across turns, released
    }
