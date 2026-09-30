from __future__ import annotations

from pension_chat.agent.instrument import Call, Instrument
from pension_chat.evals.trace import TurnTrace, build_trace


def _done(**state) -> dict:
    base = {
        "retrieved_sections": [{"section": "1-160"}, {"section": "8-129"}],
        "guardrail_passed": True,
        "guardrail_report": {"passed": True, "hard_failures": [], "warnings": [],
                             "verified_citations": ["1-160"], "unverified_citations": [],
                             "unverified_numbers": []},
        "input_sources": {}, "assumptions": {}, "calculation_result": None,
        "employee_record": None, "in_scope": True,
        "loop_messages": [
            {"role": "assistant",
             "tool_calls": [{"function": {"name": "outline", "arguments": "{\"article\": \"8\"}"}}]},
        ],
        "tool_calls_spent": {"search_statute": 1, "outline": 1},
        "timings": {"investigate": 8.7}, "token_usage": {"calls": 2},
    }
    base.update(state)
    return {"type": "done", "state": base, "card": None, "scenarios": []}


def test_builds_from_events_and_instrument() -> None:
    inst = Instrument(calls=[Call("search", "q", 100.5, 101.0)],
                      notes=[(100.2, "rate_limited", "0.40")],
                      rendered={"1-160": ["(a)", "(c)"]})
    events = [
        (1.0, {"type": "progress", "text": "searching"}),
        (3.2, {"type": "token", "text": "Age 67 "}),
        (3.5, {"type": "token", "text": "with 10 years."}),
        (4.0, _done()),
    ]
    t = build_trace(case_id="S1-003", turn_index=0, question="q", employee=None,
                    events=events, instrument=inst, started=100.0)
    assert t.ttft == 3.2
    assert t.ttft_rate_limit_wait == 0.4
    assert t.final_answer == "Age 67 with 10 years." == t.streamed_answer
    assert t.retrieved == ["1-160", "8-129"]
    assert t.rendered == {"1-160": ["(a)", "(c)"]}
    assert t.verified_citations == ["1-160"]
    assert [x["name"] for x in t.tools] == ["search_statute", "outline"]
    assert t.calls == [{"kind": "search", "label": "q", "start": 0.5, "end": 1.0}]


def test_a_replacement_is_the_final_answer_and_its_codes_are_kept() -> None:
    events = [
        (2.0, {"type": "token", "text": "Section 8-129 says"}),
        (2.5, {"type": "replace", "text": "Hedge.", "reason": {"hard_failures": [{"code": "UNVERIFIED_CITATION"}]}}),
        (3.0, _done(guardrail_passed=False)),
    ]
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=events, instrument=Instrument(), started=0.0)
    assert t.final_answer == "Hedge."
    assert t.streamed_answer == "Section 8-129 says"
    assert "UNVERIFIED_CITATION" in t.failure_codes


def test_an_error_event_is_recorded() -> None:
    events = [(1.0, {"type": "error", "message": "m", "detail": "RateLimitError: 429"})]
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=events, instrument=Instrument(), started=0.0)
    assert t.error == "RateLimitError: 429" and t.ttft is None


def test_round_trips_through_json() -> None:
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=[(1.0, _done())], instrument=Instrument(), started=0.0)
    assert TurnTrace.from_dict(t.to_dict()) == t


def test_a_recipe_that_ran_and_failed_is_not_a_calculation() -> None:
    # execute_calculations files every recipe it ran, a failure included.
    failed = {"annuity": {"base": {"ok": False, "error": "not eligible before 60"}}}
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=[(1.0, _done(calculation_result=failed))], instrument=Instrument(), started=0.0)
    assert t.calculation is False
    worked = {"annuity": {"base": {"ok": True, "annual_amount": 30000}}}
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=[(1.0, _done(calculation_result=worked))], instrument=Instrument(), started=0.0)
    assert t.calculation is True


def test_the_trace_says_whether_a_formula_was_reused_or_derived() -> None:
    provenance = [{"formula_type": "retirement_annuity", "source": "registry", "reused": True},
                  {"formula_type": "survivor_annuity", "source": "derived"}]
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=[(1.0, _done(recipe_provenance=provenance))], instrument=Instrument(), started=0.0)
    assert t.formula_sources == {"retirement_annuity": "registry", "survivor_annuity": "derived"}


def test_the_trace_records_why_no_figure_was_produced() -> None:
    state = {
        "derivation_failures": [{"formula_type": "retirement_annuity", "reason": "Section 1-162 may govern"}],
        "missing_inputs": [{"name": "final_average_salary_8_year", "description": "Tier 2 average"}],
        "validation_errors": [{"code": "QUOTE_NOT_IN_SOURCE", "message": "m"}],
        "execution_blocked": "validation_failed",
    }
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=[(1.0, _done(**state))], instrument=Instrument(), started=0.0)
    assert t.derivation_failures == ["retirement_annuity: Section 1-162 may govern"]
    assert t.missing_inputs == ["final_average_salary_8_year"]
    assert t.validation_codes == ["QUOTE_NOT_IN_SOURCE"]
    assert t.execution_blocked == "validation_failed"
