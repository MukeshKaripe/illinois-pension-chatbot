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

# `None` means clear, anything else merges. Without that distinction a
# checkpointed conversation could not start a turn fresh: passing `{}` for a
# merged key merges nothing and leaves the previous turn's value in place, so
# last turn's facts and figures would silently become this turn's.
CLEAR = None


def merge_dicts(a: dict | None, b: dict | None) -> dict:
    """Merge ``b`` into ``a``; ``b is CLEAR`` empties the key."""
    if b is CLEAR:
        return {}
    return {**(a or {}), **b}


def merge_lists(a: list | None, b: list | None) -> list:
    """Append ``b`` to ``a``; ``b is CLEAR`` empties the key."""
    if b is CLEAR:
        return []
    return (a or []) + b


class PensionAgentState(TypedDict, total=False):
    """The central state for the LangGraph agent."""

    # ── The agent loop (agent_mode == "loop") ─────────────────────
    # Scratch for one turn: the investigation's own message history, which
    # `compose` continues into the answer, and what it could not reach.
    loop_messages: list
    loop_exhausted: bool
    unread_references: list
    #: Other funds' law the loop read while settling which fund was in
    #: question. Named to the answer rather than silently dropped: the model
    #: has them in its own history either way, so a section it cannot see
    #: dropped is a citation it will make and have withdrawn.
    set_aside_sections: list
    tool_calls_spent: dict

    # ── Durable: carried across turns by the checkpointer ─────────
    # Everything else on this blackboard is scratch for one turn and is cleared
    # by `turn_input()`. What survives is what the conversation has actually
    # established, so a follow-up can refer to it instead of re-reading it out
    # of the prose of an earlier answer.
    session_id: str
    employee_id: str | None
    messages: Annotated[list[BaseMessage], operator.add]
    turn_index: Annotated[int, operator.add]
    # The provision this conversation settled on: section, subsection,
    # formula_type, formula_id, selection_basis.
    selected_provision: dict | None
    # The last figures worked out, with the inputs they were worked out from,
    # so "change only my salary" has something to change. `rests_on` names those
    # inputs as a dependency set, which is what lets a member's correction mark
    # the figure stale instead of being noticed only if they used the right words.
    baseline: dict | None
    # name -> {value, source, basis, turn, asked}, read and written through
    # `agent/worksheet.py` rather than directly.
    #
    # `source` is the point, and it has five values, not three:
    #   record        - from the member's file
    #   stated        - the member said it
    #   hypothetical  - a premise they asked us to suppose
    #   assumed       - we filled it in; `basis` says why, and the answer must
    #                   disclose it or verification fails
    #   withheld      - on file, but not usable under the premise this turn is
    #                   exploring, so no figure may rest on it
    # `record` and `withheld` are never written here: the file is re-read every
    # turn, and `withheld` is a per-turn view of a record fact rather than a
    # fact, so persisting it would overlay the record with an unusable copy of
    # itself for the rest of the conversation.
    # `asked` records that the question has already been put to the member, so a
    # later turn does not put it again.
    assumptions: Annotated[dict[str, Any], merge_dicts]
    # Scenarios the member has built up by name, across turns.
    scenario_set: Annotated[dict[str, Any], merge_dicts]
    # Sections this conversation has cited and had verified, section -> turn.
    cited_sections: Annotated[dict[str, Any], merge_dicts]
    # Which Article covers this member and on what evidence:
    # {article, basis, record_article, candidates, conflict, described_as}.
    # `basis` is the single thing downstream code reads, so a supposed premise
    # reaches retrieval, the answer prompt and the guardrail through the same
    # channel as a real one -- the difference lives in one enum value rather
    # than in a parallel code path.
    coverage: dict | None
    # A premise the member asked us to suppose, and how long it has been held.
    # "If I were a park employee" is not a hedged "I am": it needs no
    # confirmation, it moves retrieval, and it has to survive the follow-ups
    # that explore it until the member steps back out.
    hypothetical_frame: dict | None
    # What was put to the member last turn, so their reply can be bound to it.
    # Without this a question is worse than useless -- the member answers, the
    # answer lands nowhere, and the next turn asks again.
    open_question: dict | None
    # How many turns this conversation has spent asking. Budgeted, because an
    # assistant that keeps asking is its own kind of not listening.
    clarifications_asked: Annotated[int, operator.add]

    # ── Per-turn ──────────────────────────────────────────────────
    question: str

    # ── Gate (deterministic, before any model call) ───────────────
    in_scope: bool
    out_of_scope_reason: str
    # Result of scanning the question for "apply my formula, skip the Code"
    override_signal: dict

    # ── Plan (from understand_turn) ───────────────────────────────
    requires_calculation: bool
    formula_types_needed: list[str]
    search_queries: list[str]
    # Sections the user named outright — pinned into retrieval
    explicit_sections: list[str]
    explicit_articles: list[str]
    # Scenario variants to evaluate, e.g. [{"label": "at age 60", "retirement_age": 60}]
    scenarios: list[dict]
    # What the member's message actually wants, one short phrase. Set only by
    # `understand_turn`; the regex path has no equivalent.
    intent: str
    # Things the turn raised that could not be settled: [{"what", "why"}].
    unresolved: list[dict[str, Any]]
    # When part of the message is outside the Pension Code, the part that is in it.
    answerable_part: str
    # Figures an earlier turn worked out whose inputs this turn contradicted.
    # Non-empty means the calculation branch runs whatever words were used.
    stale_figures: list[str]

    # ── Execution State ───────────────────────────────────────────
    employee_record: dict | None
    salary_history: list[dict]
    # The record and salary history as loaded on the conversation's first turn,
    # {record, salary_history}. Durable: later turns rebuild their facts from it,
    # so editing a profile changes new chats and not one already under way.
    pinned_record: dict | None
    # Deterministic facts computed from the record (age, service, FAS, ...)
    member_facts: Annotated[dict[str, Any], merge_dicts]

    # What the member said about who they are this turn, and which act it was:
    # {text, act: asserted|stipulated|doubted, kind: coverage|status}
    self_reference: dict | None
    # Articles this turn should read from, when coverage put them in play.
    # Distinct from `explicit_articles`, which means "the user named it".
    candidate_articles: list[str]
    # The coverage sections a candidate rests on, pinned into this turn's
    # retrieval so the answer can quote the test rather than assert it. They
    # come from a separate search, so without pinning them the answer cites
    # text that was never retrieved and verification withdraws it -- which is
    # exactly what happened to the first clarifying question ever asked.
    coverage_sections: list[str]
    # Set when this turn asks rather than answers.
    clarification: dict | None

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
    # Where each resolved input came from -- record, stated, derived, assumed --
    # decided by `resolve_inputs` as it resolves them. The calculation card
    # shows it, so it must be known rather than reconstructed.
    input_sources: dict[str, str]
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
    # The rung and shape prepare_answer selected, and the slots it rendered
    # itself (see presentation.py, blocks.py) -- per-turn, like calculation_result.
    answer_blocks: dict[str, str]
    answer_rung: int
    answer_shape: str
    tool_calls: list[dict]
    guardrail_passed: bool
    guardrail_report: dict

    # ── Observability ─────────────────────────────────────────────
    token_usage: dict
    timings: Annotated[dict[str, float], merge_dicts]


# Keys the checkpointer carries from one turn to the next. Everything else is
# scratch and is cleared by `turn_input()`.
#
# `calculation_result` is deliberately absent, and that is the load-bearing
# omission. Verification builds the set of figures an answer may state from this
# turn's calculation, the member's record, the retrieved statute and what the
# member typed -- never from what the assistant said before, "so a figure this
# system stated once does not become its own justification for stating it
# again". Leaving a result in place across turns would smuggle exactly that back
# in through the front door.
DURABLE_KEYS: frozenset[str] = frozenset({
    "session_id",
    "employee_id",
    "messages",
    "turn_index",
    "selected_provision",
    "baseline",
    "assumptions",
    "scenario_set",
    "cited_sections",
    # Coverage is durable for the same reason a chosen provision is: the member
    # answered a question about who they are, and an answer that has to be
    # re-established every turn was never really taken. That was the fourth turn
    # of the conversation this work came from -- they restated their coverage in
    # the statute's own words and got the same wrong figure back.
    "coverage",
    "hypothetical_frame",
    "open_question",
    "clarifications_asked",
    # The record this conversation started with (spec §6): an answer resting on
    # facts that change under it mid-conversation contradicts the one before it.
    "pinned_record",
})

# Merged keys need `CLEAR` rather than an empty value: `{}` merges to nothing
# and leaves the previous turn's contents in place.
_MERGED_PER_TURN: frozenset[str] = frozenset({
    "member_facts", "recipes", "resolved_inputs", "timings",
})

PER_TURN_KEYS: frozenset[str] = frozenset(
    PensionAgentState.__annotations__
) - DURABLE_KEYS


def turn_input(
    question: str,
    session_id: str,
    employee_id: str | None = None,
) -> dict[str, Any]:
    """The update that starts a new turn on an existing thread.

    Not a whole state: the checkpointer already holds one. This resets every
    per-turn key and adds the new message, so what the previous turn worked out
    survives and what it merely happened to be doing does not.
    """
    from langchain_core.messages import HumanMessage

    fresh = {key: _per_turn_default(key) for key in PER_TURN_KEYS}
    fresh.update(
        session_id=session_id,
        employee_id=employee_id,
        question=question,
        messages=[HumanMessage(content=question)],
        turn_index=1,
    )
    return fresh


def _per_turn_default(key: str) -> Any:
    if key in _MERGED_PER_TURN:
        return CLEAR
    return _BLANK_STATE[key]


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
        "turn_index": 0,
        "selected_provision": None,
        "baseline": None,
        "assumptions": {},
        "scenario_set": {},
        "cited_sections": {},
        "coverage": None,
        "hypothetical_frame": None,
        "open_question": None,
        "clarifications_asked": 0,
        "self_reference": None,
        "candidate_articles": [],
        "coverage_sections": [],
        "clarification": None,
        "in_scope": True,
        "out_of_scope_reason": "",
        "loop_messages": [],
        "loop_exhausted": False,
        "unread_references": [],
        "set_aside_sections": [],
        "tool_calls_spent": {},
        "override_signal": {},
        "requires_calculation": False,
        "formula_types_needed": [],
        "search_queries": [],
        "explicit_sections": [],
        "explicit_articles": [],
        "scenarios": [],
        "intent": "",
        "unresolved": [],
        "answerable_part": "",
        "stale_figures": [],
        "employee_record": None,
        "pinned_record": None,
        "salary_history": [],
        "member_facts": {},
        "retrieved_sections": [],
        "answers_other_article": False,
        "recipes": {},
        "recipe_provenance": [],
        "derivation_failures": [],
        "resolved_inputs": {},
        "input_sources": {},
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
        "answer_blocks": {},
        "answer_rung": 0,
        "answer_shape": "",
        "tool_calls": [],
        "guardrail_passed": True,
        "guardrail_report": {},
        "token_usage": {},
        "timings": {},
    }


# The per-turn defaults, named once. `turn_input()` reads them, so a key added
# to `initial_state` without a default fails loudly rather than arriving as a
# missing key mid-run.
_BLANK_STATE: dict[str, Any] = initial_state("", "")
