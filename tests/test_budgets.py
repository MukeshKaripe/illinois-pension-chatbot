"""The model-call budget: counting, classifying, and the per-turn record."""
from __future__ import annotations

from types import SimpleNamespace

from pension_chat.agent.budgets import (
    BUDGETS,
    MODEL_NODES,
    ModelCall,
    classify_turn,
    over_budget,
    repeated_sections,
    sequential_model_calls,
    turn_budget_record,
)


def test_calls_one_after_another_each_count() -> None:
    calls = [ModelCall("investigate", 0.0, 1.4), ModelCall("generate_answer", 1.5, 2.9)]
    assert sequential_model_calls(calls, first_token_at=None) == 2


def test_overlapping_calls_count_once() -> None:
    # Two people's computations issued together are one wait, not two.
    calls = [ModelCall("investigate", 0.0, 1.4), ModelCall("derive_recipe", 1.5, 3.0),
             ModelCall("derive_recipe", 1.6, 3.2), ModelCall("generate_answer", 3.3, 4.6)]
    assert sequential_model_calls(calls, first_token_at=None) == 3


def test_calls_after_the_first_answer_word_do_not_count() -> None:
    calls = [ModelCall("investigate", 0.0, 1.4), ModelCall("generate_answer", 1.5, 9.0),
             ModelCall("repair_answer", 9.5, 11.0)]
    assert sequential_model_calls(calls, first_token_at=2.9) == 2


def test_turn_types_come_from_what_the_turn_did() -> None:
    assert classify_turn(tools=["search_statute"], model_nodes=["investigate", "generate_answer"],
                         people=0, has_figure=False, asked=False) == "statute"
    assert classify_turn(tools=["search_statute", "compute_benefit"], model_nodes=[], people=1,
                         has_figure=True, asked=False) == "figure"
    assert classify_turn(tools=["compute_benefit"], model_nodes=[], people=0,
                         has_figure=False, asked=True) == "asks"
    assert classify_turn(tools=["compute_benefit"], model_nodes=[], people=2,
                         has_figure=True, asked=False) == "comparison"
    assert classify_turn(tools=["check_coverage"], model_nodes=["assess_coverage"], people=0,
                         has_figure=False, asked=False) == "self_described"
    assert classify_turn(tools=["compute_benefit"], model_nodes=["derive_recipe"], people=0,
                         has_figure=True, asked=False) == "derivation"


def test_over_budget_names_the_count_and_any_stray_node() -> None:
    assert over_budget("figure", 2, ["investigate", "generate_answer"]) == []
    problems = over_budget("figure", 3, ["investigate", "resolve_inputs", "generate_answer"])
    assert "3 sequential model calls, budget 2" in problems[0]
    assert "resolve_inputs" in problems[1]


def test_every_budgeted_node_is_registered() -> None:
    for turn_type, (_, nodes) in BUDGETS.items():
        assert nodes <= set(MODEL_NODES), turn_type


def test_repeated_section_text_is_measured() -> None:
    body = "x" * 400
    messages = [
        {"role": "system", "content": f"### 40 ILCS 5/8-138 — Minimum annuities\n{body}"},
        {"role": "tool", "content": f"### 40 ILCS 5/8-138 — Minimum annuities\n{body}\n"
                                    f"### 40 ILCS 5/1-160 — Tier 2\n{body}"},
    ]
    repeated = repeated_sections(messages)
    assert list(repeated) == ["8-138"] and repeated["8-138"] > 90


def test_the_turn_record_reads_state_and_calls() -> None:
    calls = [SimpleNamespace(kind="embedding", label="query", start=100.0, end=100.5),
             SimpleNamespace(kind="model", label="investigate", start=100.6, end=102.0),
             SimpleNamespace(kind="model", label="generate_answer", start=102.1, end=104.0)]
    state = {"tool_calls_spent": {"search_statute": 1}, "people": {}, "calculation_result": {},
             "missing_inputs": [], "answer_messages": []}
    record = turn_budget_record(state, calls, started=100.0, first_token_at=3.5)
    assert record == {"turn_type": "statute", "sequential_model_calls": 2,
                      "model_nodes": ["investigate", "generate_answer"], "over_budget": [],
                      "repeated_sections": {}}
