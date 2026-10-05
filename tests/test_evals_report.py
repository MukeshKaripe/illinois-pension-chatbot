from __future__ import annotations

from pathlib import Path

from pension_chat.evals.cases import Case, Turn
from pension_chat.evals.checks import CheckResult
from pension_chat.evals.report import diff, exit_code, render_markdown, summarise
from pension_chat.evals.runner import ConversationResult, TurnResult
from pension_chat.evals.trace import TurnTrace


def _conv(cid, family, statuses, critical=False, ttft=2.0, flaky=False) -> ConversationResult:
    case = Case(id=cid, family=family, areas=["retrieval"], path=Path("x"),
                turns=[Turn("q", "g", "r", checks={"ttft_class": "simple"}) for _ in statuses], critical=critical)
    fallback = [{"at": 0, "kind": "semantic_fallback", "detail": ""}]
    turns = [TurnResult(cid, i, TurnTrace(case_id=cid, turn_index=i, question="q", employee=None, ttft=ttft,
                                          guardrail_passed=True, notes=fallback),
                        [CheckResult("x", s == "pass")], None, s, ttft - 1.0, {"model": 1.0})
             for i, s in enumerate(statuses)]
    return ConversationResult(case, turns, flaky=flaky)


RESULTS = [
    _conv("S1-001", "S1", ["pass", "pass"], critical=True),
    _conv("S4-001", "S4", ["pass", "fail", "after_failure"]),
    _conv("S8-001", "S8", ["pending"]),
]


def test_family_rates_count_scored_turns_only() -> None:
    r = summarise(RESULTS, judge_calibrated=False, probe={})
    assert r["families"]["S1"] == {"passed": 2, "scored": 2, "first_class": True}
    assert r["families"]["S4"] == {"passed": 1, "scored": 2, "first_class": True}   # after_failure not scored
    assert r["families"]["S8"]["scored"] == 0
    assert r["semantic_fallbacks"] == 6


def test_a_critical_failure_fails_the_run() -> None:
    bad = [_conv("S1-001", "S1", ["fail"], critical=True)]
    r = summarise(bad, judge_calibrated=False, probe={})
    assert r["critical_failures"] == ["S1-001#0"]
    assert exit_code(r, diff(r, None)) == 1


def test_diff_against_baseline() -> None:
    base = summarise([_conv("S4-001", "S4", ["pass", "pass", "pass"])], judge_calibrated=False, probe={})
    now = summarise(RESULTS, judge_calibrated=False, probe={})
    changes = diff(now, base)
    assert changes["new_failures"] == ["S4-001#1"]
    assert exit_code(now, changes) == 1


def test_markdown_leads_with_changes() -> None:
    now = summarise(RESULTS, judge_calibrated=False, probe={})
    text = render_markdown(now, diff(now, None))
    assert text.index("Changes since baseline") < text.index("Families")
    assert "judge not calibrated" in text.lower()
    assert "estimated" in text.lower()


def test_the_report_counts_formulas_derived_and_reused() -> None:
    conv = _conv("S4-001", "S4", ["pass", "pass"])
    conv.turns[0].trace.formula_sources = {"retirement_annuity": "derived"}
    conv.turns[1].trace.formula_sources = {"retirement_annuity": "registry"}
    r = summarise([conv], judge_calibrated=False, probe={})
    assert r["formulas"] == {"derived": 1, "registry": 1}
    assert "Formulas: 1 derived, 1 reused" in render_markdown(r, diff(r, None))


def test_latency_is_reported_per_turn_type_with_p95():
    from pension_chat.evals.cases import Case, Turn
    from pension_chat.evals.report import summarise
    from pension_chat.evals.runner import ConversationResult, TurnResult
    from pension_chat.evals.trace import TurnTrace

    case = Case(id="L1-001", family="L1", areas=["retrieval"],
                turns=[Turn(q="q", goal="g", reference="r", checks={})], path=None)
    turns = []
    for i, t in enumerate((2.0, 2.5, 3.0, 6.0)):
        trace = TurnTrace(case_id="L1-001", turn_index=0, question="q", employee=None,
                          ttft=t, turn_type="statute", usage={"by_node": {"investigate": {"prompt": 10000}}})
        turns.append(TurnResult("L1-001", 0, trace, [], None, "pass", t - 1.0, {}))
    report = summarise([ConversationResult(case, [tr]) for tr in turns], judge_calibrated=False, probe={})
    slot = report["latency_by_turn_type"]["statute"]
    assert slot["measured"]["n"] == 4 and slot["measured"]["p95"] == 6.0
    assert report["prompt_tokens"]["investigate"] == 10000


def test_accepting_a_narrow_run_keeps_the_rest_of_the_baseline():
    from pension_chat.evals.report import accepted

    old = {"turns": {"S4-001#0": {"status": "pass"}, "L1-001#0": {"status": "fail"}},
           "latency": {"heavy": {}}, "families": {"S4": {}},
           "latency_by_turn_type": {"figure": {"estimated": {"p50": 4.0}}}}
    report = {"turns": {"L1-001#0": {"status": "pass"}}, "latency": {"simple": {}},
              "families": {"L1": {}}, "latency_by_turn_type": {"statute": {"estimated": {"p50": 2.0}}},
              "prompt_tokens": {"investigate": 9000}, "probe": {}}
    merged = accepted(report, old)
    assert merged["turns"] == {"S4-001#0": {"status": "pass"}, "L1-001#0": {"status": "pass"}}
    assert set(merged["families"]) == {"S4", "L1"}
    assert merged["latency_by_turn_type"]["statute"]["estimated"]["p50"] == 2.0
    assert merged["latency_by_turn_type"]["figure"]["estimated"]["p50"] == 4.0
    assert merged["prompt_tokens"] == {"investigate": 9000}
