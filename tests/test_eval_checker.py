"""The benchmark's checker, tested the way the agent's own layers are.

`test_eval_suite.py` proves an expectation key is *read*. It cannot prove the
key means what the case author thought, and a key that reads the wrong field
passes every case silently -- which is the same failure as a misspelled key,
with better camouflage.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "eval_agent.py"


def _harness():
    spec = importlib.util.spec_from_file_location("eval_agent", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_agent"] = module
    spec.loader.exec_module(module)
    return module


def result(answer: str, **overrides) -> dict:
    """A turn result with every field `check()` reads, defaulted to harmless."""
    base = {
        "question": "q",
        "answer": answer,
        "error": None,
        "guardrail_passed": True,
        "guardrail_report": {},
        "requires_calculation": False,
        "sections": [],
        "articles": [],
        "recipe_provenance": [],
        "derivation_failures": [],
        "validation_errors": [],
        "missing_inputs": [],
        "member_facts": {},
        "coverage": {},
        "hypothetical_frame": {},
        "self_reference": {},
        "clarification": {},
        "clarifications_asked": 0,
        "candidate_articles": [],
        "can_execute": True,
        "execution_blocked": None,
        "calculation": {},
        "selected_provision": {},
        "scenario_set": {},
        "tokens": {"calls": 1},
        "timings": {},
        "in_scope": True,
        "stale_figures": [],
        "answer_rung": None,
    }
    base.update(overrides)
    return base


def test_mentions_passes_when_any_phrase_appears():
    harness = _harness()
    turn = result("I assumed your salary stays where it is.")
    turn["expect"] = {"mentions": ["assum", "held flat"]}
    assert harness.check(turn, []) == []


def test_mentions_fails_when_no_phrase_appears():
    harness = _harness()
    turn = result("Your pension is about $32,000 a year.")
    turn["expect"] = {"mentions": ["assum", "held flat"]}
    problems = harness.check(turn, [])
    assert problems, "an answer disclosing nothing must not pass a disclosure check"
    assert "assum" in problems[0]


def test_mentions_ignores_case():
    harness = _harness()
    turn = result("ASSUMING you stop working today...")
    turn["expect"] = {"mentions": ["assuming"]}
    assert harness.check(turn, []) == []
