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


def test_conditional_wants_the_answer_to_stay_in_the_subjunctive():
    """Answering a supposition in the plain indicative tells a member they are
    in a fund they only asked about."""
    harness = _harness()

    hedged = result("If you were covered by Article 4, your annuity would be 50% of salary.")
    hedged["expect"] = {"conditional": True}
    assert harness.check(hedged, []) == []

    flat = result("Your annuity is 50% of salary attached to rank.")
    flat["expect"] = {"conditional": True}
    assert harness.check(flat, []), "a flat indicative must fail a conditional check"


def test_projects_wants_a_variant_that_moved_the_service_forward():
    """A future-dated question is answered by a variant, not by the base.

    The base is always "as of today" and stays that way -- it is a legitimate
    row in the table. So an expectation about a projection has to read the
    variant, and asserting an exact projected service is a date-dependent
    assertion that rots: two more years from a 56-year-old is 23.2 today and
    something else next month.
    """
    harness = _harness()

    moved = result("...", calculation={
        "retirement_annuity": {
            "base": {"ok": True, "inputs_used": {"service_years": "21.5"}},
            "scenarios": [{"label": "at 58", "assumptions": {"service_years": "23.2"}}],
        }
    })
    moved["expect"] = {"projects": True}
    assert harness.check(moved, []) == []

    flat = result("...", calculation={
        "retirement_annuity": {
            "base": {"ok": True, "inputs_used": {"service_years": "21.5"}},
            "scenarios": [],
        }
    })
    flat["expect"] = {"projects": True}
    assert harness.check(flat, []), "no variant means nothing was projected"


# ── answers_from_article ──────────────────────────────────────────


def _cited(*sections, provision=None, article="5"):
    turn = result(
        "An answer.",
        guardrail_report={"verified_citations": list(sections)},
        recipe_provenance=[{"section": provision}] if provision else [],
    )
    turn["expect"] = {"answers_from_article": article}
    return turn


def test_answers_from_article_ignores_what_was_merely_looked_at():
    """An agent that decides what to read as it reads looks at the neighbouring
    police fund precisely in order to rule it out. Looking is not the harm."""
    check = _harness().check
    turn = _cited("5-132", provision="5-132")
    turn["articles"] = ["1", "3", "5", "8"]
    assert check(turn, []) == []


def test_answers_from_article_catches_another_funds_citation():
    check = _harness().check
    problems = check(_cited("5-132", "11-134"), [])
    assert problems and "11" in problems[0]


def test_answers_from_article_catches_a_borrowed_formula():
    """The failure the case exists for: one fund's formula answering another
    fund's question, with a real citation on it."""
    check = _harness().check
    problems = check(_cited("5-132", provision="11-134"), [])
    assert problems and "11-134".split("-")[0] in problems[0]


def test_answers_from_article_permits_the_cross_cutting_articles():
    """Section 1-160 is Tier 2 and Article 20 is the Reciprocal Act. Nobody is a
    member of either, and both govern the members of every fund -- so citing one
    is not citing another fund's law."""
    check = _harness().check
    assert check(_cited("5-132", "1-160", provision="5-132"), []) == []
