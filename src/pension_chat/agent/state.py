"""Agent state schema (§9.2).

The blackboard that flows through all LangGraph nodes.

The ``Annotated`` reducers matter only for keys written by nodes that run
concurrently. After the graph was reordered so that recipe resolution follows
retrieval (it needs the statute text), the only genuine fan-in is
``retrieve_policy`` alongside ``fetch_employee_record``, and those write
disjoint keys — but the reducers are kept on the shared accumulators so adding
a concurrent branch later does not silently drop half its output.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage


def merge_dicts(a: dict | None, b: dict | None) -> dict:
    """Merge two dictionaries, updating a with b."""
    a = a or {}
    b = b or {}
    return {**a, **b}


def merge_lists(a: list | None, b: list | None) -> list:
    """Merge two lists, appending b to a."""
    a = a or []
    b = b or []
    return a + b


class PensionAgentState(TypedDict, total=False):
    """The central state for the LangGraph agent."""

    # ── Core inputs ───────────────────────────────────────────────
    session_id: str
    employee_id: str | None
    question: str
    messages: Annotated[list[BaseMessage], operator.add]

    # ── Gate (deterministic, before any model call) ───────────────
    in_scope: bool
    out_of_scope_reason: str
    # Result of scanning the question for "apply my formula, skip the Code"
    override_signal: dict

    # ── Plan (from plan_request) ──────────────────────────────────
    requires_calculation: bool
    formula_types_needed: list[str]
    search_queries: list[str]
    # Sections the user named outright — pinned into retrieval
    explicit_sections: list[str]
    explicit_articles: list[str]
    # Scenario variants to evaluate, e.g. [{"label": "at age 60", "retirement_age": 60}]
    scenarios: list[dict]

    # ── Execution State ───────────────────────────────────────────
    employee_record: dict | None
    salary_history: list[dict]
    # Deterministic facts computed from the record (age, service, FAS, ...)
    member_facts: Annotated[dict[str, Any], merge_dicts]

    retrieved_sections: list[dict]
    # The question named an Article that is not the member's own, so it is
    # asking what the law provides rather than what this member will be paid
    answers_other_article: bool

    # formula_type -> {spec, source, status, policy, source_sections, ...}
    recipes: Annotated[dict[str, dict], merge_dicts]
    # Human-readable note about where each recipe came from (registry vs derived)
    recipe_provenance: list[dict]
    # A recipe the agent could not derive from the retrieved text
    derivation_failures: list[dict]

    # Inputs resolved for execution, and the ones that could not be resolved
    resolved_inputs: Annotated[dict[str, Any], merge_dicts]
    missing_inputs: list[dict]
    # Inputs where the member stated a value their record contradicts
    input_overrides: list[dict]

    validation_errors: list[dict]
    validation_warnings: list[dict]
    risk_tier: str | None
    can_execute: bool
    # Why execution was blocked, when it was
    execution_blocked: str | None

    calculation_result: dict | None
    # Comparisons the member asked for that could not be resolved to a
    # retirement age — reported in the answer rather than dropped
    unresolved_scenarios: list[dict]

    # ── Final Output ──────────────────────────────────────────────
    # Assembled by prepare_answer; the streaming layer sends these to the model.
    answer_messages: list[dict]
    final_answer: str | None
    citations: list[dict]
    tool_calls: list[dict]
    guardrail_passed: bool
    guardrail_report: dict

    # ── Observability ─────────────────────────────────────────────
    token_usage: dict
    timings: Annotated[dict[str, float], merge_dicts]


def initial_state(
    question: str,
    session_id: str,
    employee_id: str | None = None,
    history: list[BaseMessage] | None = None,
) -> dict[str, Any]:
    """Build a complete initial state.

    Every key the graph reads is defaulted here. A node that reads a key this
    function forgot gets a ``KeyError`` mid-run, so new state keys must be added
    in both places — hence one constructor rather than a dict literal at each
    call site.
    """
    from langchain_core.messages import HumanMessage

    return {
        "session_id": session_id,
        "employee_id": employee_id,
        "question": question,
        "messages": list(history or []) + [HumanMessage(content=question)],
        "in_scope": True,
        "out_of_scope_reason": "",
        "override_signal": {},
        "requires_calculation": False,
        "formula_types_needed": [],
        "search_queries": [],
        "explicit_sections": [],
        "explicit_articles": [],
        "scenarios": [],
        "employee_record": None,
        "salary_history": [],
        "member_facts": {},
        "retrieved_sections": [],
        "answers_other_article": False,
        "recipes": {},
        "recipe_provenance": [],
        "derivation_failures": [],
        "resolved_inputs": {},
        "missing_inputs": [],
        "input_overrides": [],
        "validation_errors": [],
        "validation_warnings": [],
        "risk_tier": None,
        "can_execute": False,
        "execution_blocked": None,
        "calculation_result": None,
        "unresolved_scenarios": [],
        "answer_messages": [],
        "final_answer": None,
        "citations": [],
        "tool_calls": [],
        "guardrail_passed": True,
        "guardrail_report": {},
        "token_usage": {},
        "timings": {},
    }
