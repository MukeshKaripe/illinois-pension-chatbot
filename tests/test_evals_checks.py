from __future__ import annotations

import json
from pathlib import Path

import pytest

from pension_chat.evals.cases import Turn
from pension_chat.evals.checks import run_checks
from pension_chat.evals.trace import TurnTrace

BEFORE = TurnTrace.from_dict(json.loads(
    (Path(__file__).parent / "fixtures/eval_traces/tier2_before_fix.json").read_text()))


def _turn(**checks) -> Turn:
    return Turn(q="q", goal="g", reference="r", checks=checks)


def _one(turn: Turn, trace: TurnTrace, **kw):
    [result] = run_checks(turn, trace, **kw)
    return result


def _trace(**kw) -> TurnTrace:
    return TurnTrace(case_id="c", turn_index=0, question="q", employee=None, **kw)


def test_the_tier2_failure_is_caught_by_evidence_has() -> None:
    r = _one(_turn(evidence_has=["1-160(c)"]), BEFORE)
    assert not r.passed and "1-160(c)" in r.detail


def test_evidence_has_passes_once_c_reaches_the_model() -> None:
    after = TurnTrace.from_dict({**BEFORE.to_dict(), "rendered": {"1-160": ["(a)", "(b)", "(c)"]}})
    assert _one(_turn(evidence_has=["1-160(c)"]), after).passed
    assert _one(_turn(evidence_has=["1-160"]), after).passed


@pytest.mark.parametrize("check, value, verified, ok", [
    ("cites_any", ["1-160"], ["1-162"], False),
    ("cites_any", ["1-160", "8-129"], ["8-129"], True),
    ("cites_all", ["1-160", "8-129"], ["1-160"], False),
    ("cites_none_outside", ["8"], ["8-129", "1-160"], True),       # 1 and 20 are cross-cutting
    ("cites_none_outside", ["8"], ["8-129", "3-111"], False),
])
def test_citations(check, value, verified, ok) -> None:
    assert _one(_turn(**{check: value}), _trace(verified_citations=verified)).passed is ok


@pytest.mark.parametrize("answer, facts, ok", [
    ("Age 67 with at least 10 years.", ["67", "10 years"], True),
    ("You need $1,234.50 a month.", ["$1234.50"], True),
    ("Age 65 with 10 years.", ["67"], False),
    ("AGE  67   and  ten", ["age 67"], True),
])
def test_must_state_reads_the_final_answer_normalised(answer, facts, ok) -> None:
    assert _one(_turn(must_state=facts), _trace(final_answer=answer)).passed is ok


def test_must_not_state() -> None:
    assert not _one(_turn(must_not_state=["1-162"]), BEFORE).passed


def test_figures_from() -> None:
    ok = _trace(input_sources={"salary": "stated", "age": "stated", "fas": "derived"})
    assert _one(_turn(figures_from=["user", "engine"]), ok).passed
    bad = _trace(input_sources={"salary": "record"})
    assert not _one(_turn(figures_from=["user"]), bad).passed
    untraced = _trace(unverified_numbers=["$9,999"])
    assert not _one(_turn(figures_from=["engine"]), untraced).passed


def test_critical_inputs_are_never_assumed() -> None:
    t = _trace(input_sources={"hire_date": "assumed"})
    r = _one(_turn(critical_inputs=["hire_date"]), t)
    assert not r.passed and "hire_date" in r.detail
    assert _one(_turn(critical_inputs=["hire_date"]), _trace(input_sources={"hire_date": "stated"})).passed


def test_max_assumed_and_disclosure() -> None:
    three = _trace(input_sources={"a": "assumed", "b": "assumed", "c": "assumed"})
    assert not _one(_turn(max_assumed=2), three).passed
    hidden = _trace(failure_codes=["ASSUMPTION_NOT_DISCLOSED"])
    assert not _one(_turn(assumptions_disclosed=True), hidden).passed


def test_no_reask_flags_a_question_about_something_given() -> None:
    t = _trace(final_answer="Thanks. How old is the employee? I'll then compute it.")
    assert not _one(_turn(no_reask=["age"]), t).passed
    fine = _trace(final_answer="At 56 the employee is not yet eligible. When did they first join?")
    assert _one(_turn(no_reask=["age"]), fine).passed


def test_max_questions_ignores_quoted_text() -> None:
    t = _trace(final_answer='You asked "can she retire?" When did she join? Which date?')
    assert not _one(_turn(max_questions=1), t).passed
    assert _one(_turn(max_questions=2), t).passed


def test_refuses_and_calculation_and_verification() -> None:
    assert _one(_turn(refuses=True), _trace(in_scope=False)).passed
    assert not _one(_turn(calculation=True), _trace()).passed
    assert _one(_turn(no_calculation=True), _trace()).passed
    assert not _one(_turn(not_replaced=True), BEFORE).passed
    assert _one(_turn(verification_passed=True), BEFORE).passed


def test_worked_from_and_answers_from_article() -> None:
    t = _trace(card={"section": "8-138"}, verified_citations=["8-138", "1-160"])
    assert _one(_turn(worked_from_article="8"), t).passed
    assert _one(_turn(answers_from_article="8"), t).passed
    assert not _one(_turn(answers_from_article="8"), _trace(verified_citations=["11-134"])).passed


def test_about_employee() -> None:
    assert _one(_turn(about_employee="tier2_standard"), _trace(subject="tier2_standard")).passed


def test_ttft_class_is_reported_but_not_gating_until_the_latency_gate() -> None:
    slow = _trace(ttft=9.0)
    r = _one(_turn(ttft_class="simple"), slow)
    assert not r.passed and not r.gating
    assert _one(_turn(ttft_class="simple"), slow, latency_gate=True).gating


def test_ttft_excludes_rate_limit_waits() -> None:
    t = _trace(ttft=3.8, ttft_rate_limit_wait=1.2)
    assert _one(_turn(ttft_class="simple"), t).passed


def test_an_errored_turn_fails_every_check() -> None:
    t = _trace(error="boom")
    assert not _one(_turn(cites_any=["1-160"]), t).passed


def test_participation_date_covers_the_engines_hire_date() -> None:
    # The engine has no "participation_date"; when someone first became a member
    # reaches it as hire_date, and assuming it silently is the policy's worst case.
    t = _trace(input_sources={"hire_date": "assumed"})
    assert not _one(_turn(critical_inputs=["participation_date"]), t).passed
