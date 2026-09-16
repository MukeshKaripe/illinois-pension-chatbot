"""What a failure costs, decided by what kind of failure it is.

Today every hard failure costs the same thing: the whole answer, replaced with
"I have taken that answer back -- sorry." That is right for a figure nobody can
trace and wrong for a sentence that named the wrong section beside four that
named the right one.
"""

from __future__ import annotations

from pension_chat.agent.guardrails import (
    CONSEQUENCE,
    EXCISE,
    REPAIR,
    RETRACT,
    GuardrailReport,
)


def test_an_untraceable_figure_still_retracts_everything():
    """The one failure that harms a member."""
    assert CONSEQUENCE["UNVERIFIED_FIGURE"] == RETRACT


def test_a_bad_citation_costs_its_own_sentence():
    assert CONSEQUENCE["UNVERIFIED_CITATION"] == EXCISE
    assert CONSEQUENCE["UNSUPPORTED_COVERAGE"] == EXCISE


def test_a_missing_disclosure_is_regenerated_not_cut():
    """Nothing in the answer is wrong -- a sentence is missing. Cutting text
    cannot add one, and cannot make a flat statement conditional."""
    assert CONSEQUENCE["ASSUMPTION_NOT_DISCLOSED"] == REPAIR
    assert CONSEQUENCE["UNMARKED_HYPOTHETICAL"] == REPAIR


def test_an_unrecognised_code_retracts():
    """A check added without a consequence must fail safe. The alternative is
    a new failure silently becoming excisable."""
    report = GuardrailReport()
    report.fail("SOMETHING_NEW", "...")
    assert report.consequence == RETRACT


def test_a_clean_report_costs_nothing():
    assert GuardrailReport().consequence == ""


def test_the_worst_failure_decides():
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "...", spans=["a."])
    report.fail("UNVERIFIED_FIGURE", "...")
    assert report.consequence == RETRACT


def test_repair_outranks_excision():
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "...", spans=["a."])
    report.fail("ASSUMPTION_NOT_DISCLOSED", "...")
    assert report.consequence == REPAIR


def test_an_excisable_failure_with_no_span_cannot_be_excised():
    """Knowing a sentence is wrong is not knowing which sentence."""
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "...")
    assert report.consequence == RETRACT


def test_every_code_the_guardrail_can_raise_has_a_consequence():
    """A bug class, not a behaviour: a check added without an entry falls to
    the RETRACT default silently, which is safe but is not a decision anybody
    made. This fails until the table is updated, so the decision gets made."""
    import re
    from pathlib import Path

    source = Path("src/pension_chat/agent/guardrails.py").read_text(encoding="utf-8")
    raised = set(re.findall(r'\.fail\(\s*"([A-Z_]+)"', source))
    assert raised, "no failure codes found -- the pattern has drifted"
    assert raised <= set(CONSEQUENCE), f"no consequence for {raised - set(CONSEQUENCE)}"
