"""LangGraph assembly and the streaming entry point (§9.4).

The graph runs everything deterministic and everything that must complete before
a single word of the answer is safe to send. Answer *generation* then streams
outside the graph, so tokens reach the browser as the model produces them.

Flow::

    triage
      ├─(out of scope / greeting)─────────────────> decline ──> END
      └─> [understand_turn ∥ fetch_employee_record] ─> assess_coverage ─> retrieve_policy
                                                                              │
                                                                       resolve_recipes
                                                            │
                                              (no calculation needed)
                                                            │
                                          resolve_inputs ──> validate_recipes
                                                            │
                                          execute_calculations ──> prepare_answer ──> END

Ordering is a correctness constraint, not a style choice. ``assess_coverage``
needs the record in order to have something to disagree with, and
``retrieve_policy`` needs its verdict to know which Article to filter to; ``resolve_recipes``
needs the retrieved text to derive from; ``resolve_inputs`` needs the recipe to
know which inputs exist. The first implementation fanned these out concurrently
from the planning node and every one of those dependencies was violated.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, StateGraph

from pension_chat.agent import guardrails
from pension_chat.agent.blocks import MarkerSubstituter
from pension_chat.agent.capture import record_failure
from pension_chat.agent.card import calculation_card
from pension_chat.agent.checkpointer import (
    SqlAlchemyCheckpointSaver,
    thread_config,
    thread_has_state,
)
from pension_chat.agent.facts import aliases_of
from pension_chat.agent.llm import begin_usage
from pension_chat.agent.llm import stream as stream_llm
from pension_chat.agent.loop import compose, investigate
from pension_chat.agent.memory import remember
from pension_chat.agent.nodes import (
    _available_facts,
    _record_facts_usable,
    _stand_ins_allowed,
    _stated_in,
    fetch_employee_record,
    triage,
)
from pension_chat.agent.state import CLEAR, PensionAgentState, initial_state, turn_input
from pension_chat.agent.worksheet import WorkSheet
from pension_chat.config import get_settings

logger = logging.getLogger("pension_chat.agent.graph")

# Stage labels sent to the client as the graph advances. Keyed by node name so
# adding a node without a label is visible rather than silent.
STAGE_LABELS = {
    "triage": "reading your question",
    "understand_turn": "reading your question",
    "fetch_employee_record": "loading your record",
    "retrieve_policy": "searching the Pension Code",
    "resolve_recipes": "working out the applicable calculation",
    "resolve_inputs": "checking the required inputs",
    "validate_recipes": "validating the calculation",
    "execute_calculations": "calculating",
    "prepare_answer": "writing your answer",
    "investigate": "reading the Pension Code",
    "compose": "writing your answer",
}

OUT_OF_SCOPE_MESSAGE = (
    "That one is outside what I can help with. I answer questions about your "
    "pension under the Illinois Pension Code — which retirement provision "
    "applies to you, what a particular Section says, or roughly what your "
    "pension would come to."
)

GREETING_MESSAGE = (
    "Hello. I can help with your pension under the Illinois Pension Code — "
    "which retirement provisions apply to you, what a particular Section says, "
    "or roughly what your pension would come to. What would you like to know?"
)


async def decline(state: PensionAgentState) -> dict:
    """Answer an out-of-scope or conversational message without a model call.

    A greeting does not need retrieval and a request for a poem does not need
    the Pension Code; both are answered from a template, which costs nothing and
    cannot hallucinate.
    """
    reason = state.get("out_of_scope_reason", "")
    answer = GREETING_MESSAGE if reason == "greeting" else OUT_OF_SCOPE_MESSAGE
    return {
        "final_answer": answer,
        "answer_messages": [],
        "citations": [],
        "guardrail_passed": True,
    }


def build_loop_graph(checkpointer: Any | None = None):
    """The agent loop: triage in front, investigation, then the answer.

    Three nodes where the pipeline has eleven, and the two that survive are the
    two that were never the problem. `triage` still turns a greeting or a deny
    away for nothing; `guardrails.verify` still runs behind the stream, outside
    this graph, unchanged.

    Between them there is one mind. `investigate` searches, reads what came
    back and decides what to do next; `compose` continues that same
    conversation into the answer rather than handing it to a model that was not
    there.
    """
    workflow = StateGraph(PensionAgentState)

    workflow.add_node("triage", triage)
    workflow.add_node("decline", decline)
    workflow.add_node("fetch_employee_record", fetch_employee_record)
    workflow.add_node("investigate", cast(Any, investigate))
    workflow.add_node("compose", cast(Any, compose))

    workflow.set_entry_point("triage")
    workflow.add_conditional_edges(
        "triage",
        lambda state: "decline" if not state.get("in_scope", True) else "fetch_employee_record",
        ["fetch_employee_record", "decline"],
    )
    workflow.add_edge("decline", END)
    # The record first, so the loop's context block has the member in it from
    # its first iteration rather than discovering them partway through.
    workflow.add_edge("fetch_employee_record", "investigate")
    workflow.add_edge("investigate", "compose")
    workflow.add_edge("compose", END)

    return workflow.compile(checkpointer=checkpointer)


loop_graph = build_loop_graph(SqlAlchemyCheckpointSaver())


def active_graph():
    """The agent that answers a turn.

    One graph since the cutover on 2026-09-14. The indirection stays because
    every caller already goes through it and because a second agent, if one is
    ever built to be measured against this one, needs somewhere to be switched
    in -- which is what this function was for the first time.
    """
    return loop_graph


async def run_agent(
    question: str,
    session_id: str,
    employee_id: str | None = None,
    history: list[BaseMessage] | None = None,
) -> dict[str, Any]:
    """Run the full turn and return the completed state, answer included.

    Non-streaming. Used by tests and the evaluation harness; the chat endpoint
    uses :func:`run_agent_stream` so the answer reaches the browser as it is
    written.
    """
    result: dict[str, Any] = {}
    answer_parts: list[str] = []
    async for event in run_agent_stream(question, session_id, employee_id, history):
        if event["type"] == "token":
            answer_parts.append(event["text"])
        elif event["type"] == "done":
            result = event["state"]
    if result and not result.get("final_answer"):
        result["final_answer"] = "".join(answer_parts)
    return result


async def run_agent_stream(
    question: str,
    session_id: str,
    employee_id: str | None = None,
    history: list[BaseMessage] | None = None,
    thread_owner: dict[str, str | None] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run a turn, yielding progress and answer tokens as they become available.

    Yields dicts with a ``type`` of:
        ``status``  — a stage label for the UI
        ``token``   — a chunk of answer text
        ``done``    — final state, citations, guardrail report, token usage
        ``error``   — the turn failed; ``message`` is safe to show

    Guardrail verification runs *after* the stream completes. Buffering the
    whole answer to verify it first would put the model's full generation time
    onto time-to-first-token, so instead tokens stream immediately and the
    ``done`` event carries the verdict; the caller replaces the message when
    verification fails hard.
    """
    settings = get_settings()
    usage = begin_usage()
    started = time.perf_counter()

    # Two identities that look alike and are not the same. `employee_id` is
    # whose record to read; `thread_owner` is who owns this conversation's
    # stored state. In the chat API they coincide, but the evaluation harness
    # reads a member's record on threads that are not sessions at all, and
    # naming an owner there would scope the checkpoints to sessions that do not
    # contain the thread -- every write silently discarded.
    config = thread_config(session_id, **(thread_owner or {}))

    # `history` seeds a thread that has none. Once a thread is checkpointed it
    # holds its own messages, and re-supplying them would append a second copy
    # of the conversation to itself. The lookup that decides this is one indexed
    # row and replaces the larger transcript read it makes unnecessary.
    if history and not await thread_has_state(config):
        turn: dict[str, Any] = initial_state(question, session_id, employee_id, history)
    else:
        turn = turn_input(question, session_id, employee_id)

    state: dict[str, Any] = {}

    try:
        # "values" carries the whole blackboard, including what earlier turns
        # left on it; "updates" says which node just finished, for the stage
        # labels. Taking both avoids a second read to recover the full state.
        async for mode, payload in active_graph().astream(
            turn,
            config,
            stream_mode=["values", "updates"],
            # One checkpoint write at the end of the turn rather than one per
            # superstep. Seven extra round trips to Azure Postgres would land
            # squarely on time-to-first-token.
            durability="exit",
        ):
            if mode == "values":
                merged_timings = {
                    **state.get("timings", {}),
                    **(payload.get("timings") or {}),
                }
                state = dict(payload)
                state["timings"] = merged_timings
                continue
            for node_name in payload:
                label = STAGE_LABELS.get(node_name)
                if label:
                    yield {"type": "status", "stage": label, "node": node_name}
    except Exception as exc:
        logger.exception("Agent graph failed")
        yield {
            "type": "error",
            "message": (
                "Something went wrong while looking that up. Please try again — "
                "if it keeps happening, contact your retirement fund directly."
            ),
            "detail": str(exc),
        }
        return

    # A short-circuit path (out of scope, greeting) already has its answer.
    if state.get("final_answer"):
        answer = state["final_answer"]
        yield {"type": "token", "text": answer}
        await _record_turn(config, state)
        state["token_usage"] = usage.to_dict()
        state["timings"] = {**state.get("timings", {}), "total": time.perf_counter() - started}
        yield {"type": "done", "state": state}
        return

    messages = state.get("answer_messages") or []
    if not messages:
        yield {
            "type": "error",
            "message": "I could not assemble an answer for that question.",
        }
        return

    yield {"type": "status", "stage": "answering", "node": "generate_answer"}

    chunks: list[str] = []
    first_token_at: float | None = None
    substituter = MarkerSubstituter(state.get("answer_blocks") or {})
    try:
        async for delta in stream_llm(
            node="generate_answer",
            messages=messages,
            reasoning_effort=settings.reasoning_effort_answer,
        ):
            if first_token_at is None:
                first_token_at = time.perf_counter() - started
            text = substituter.feed(delta)
            if text:
                chunks.append(text)
                yield {"type": "token", "text": text}
    except Exception as exc:
        logger.exception("Answer generation failed")
        yield {
            "type": "error",
            "message": (
                "I found the relevant sections but could not finish writing the "
                "answer. Please try again."
            ),
            "detail": str(exc),
        }
        return

    if tail := substituter.flush():
        chunks.append(tail)
        yield {"type": "token", "text": tail}

    # A block whose marker never arrived is appended rather than lost. The
    # member must never be short a table because the model forgot to ask for it.
    for block in substituter.unused():
        addition = f"\n\n{block}"
        chunks.append(addition)
        yield {"type": "token", "text": addition}

    answer = "".join(chunks)
    state["final_answer"] = answer

    report = _verify(state, answer, settings)
    state["guardrail_passed"] = report.passed
    state["guardrail_report"] = report.to_dict()

    if not report.passed and settings.answer_repair_enabled:
        # One attempt, never a loop. The unverified text has already been
        # streamed, so whatever comes out of this replaces it.
        problems = "\n".join(
            f"- {failure['code']}: {failure['message']}"
            for failure in report.hard_failures
        )
        repair_messages = list(messages) + [
            {"role": "assistant", "content": answer},
            {
                "role": "system",
                "content": f"{guardrails.REPAIR_SYSTEM}\n\nProblems found:\n{problems}",
            },
        ]
        try:
            repaired = "".join(
                [
                    delta
                    async for delta in stream_llm(
                        node="repair_answer",
                        messages=repair_messages,
                        reasoning_effort=settings.reasoning_effort_answer,
                    )
                ]
            )
        except Exception:
            logger.exception("Answer repair failed")
            repaired = ""

        if repaired:
            repaired = _substitute(state.get("answer_blocks") or {}, repaired)
            retry = _verify(state, repaired, settings)
            if retry.passed:
                answer = repaired
                report = retry
                state["final_answer"] = answer
                state["guardrail_passed"] = True
                state["guardrail_report"] = report.to_dict()
                yield {"type": "replace", "text": answer, "reason": {"repaired": True}}

    if not report.passed:
        # The unverified text has already been streamed, so tell the client to
        # replace it rather than leaving an unsupported figure on screen.
        typed = cast(PensionAgentState, state)
        replacement = guardrails.fallback_answer(
            report,
            sheet=WorkSheet.from_state(
                state,
                record_usable=_record_facts_usable(typed),
                as_stand_in=_stand_ins_allowed(typed),
            ),
        )
        state["final_answer"] = replacement
        logger.warning(
            "Guardrail rejected answer for session %s: %s",
            session_id,
            report.hard_failures,
        )
        # The evidence, written down while it exists. It used to survive only as
        # long as a log line, and the two defects Phase 3c found both needed it
        # recovered by hand afterwards. `record_failure` never raises.
        record_failure(state, report, session_id)
        yield {"type": "replace", "text": replacement, "reason": report.to_dict()}

    recorded = await _record_turn(config, state)
    scenarios = active_scenarios(
        {**(state.get("scenario_set") or {}), **(recorded.get("scenario_set") or {})}
    )

    state["token_usage"] = usage.to_dict()
    state["timings"] = {
        **state.get("timings", {}),
        "time_to_first_token": first_token_at or 0.0,
        "total": time.perf_counter() - started,
    }
    # The card's figures come from the interpreter, but it sits beside an answer;
    # when verification replaced that answer its figures are unverified too, and
    # the card goes with them.
    card = None
    if report.passed:
        # The verifier's pass is the label. Nothing enters the memory on the
        # strength of the model's own opinion, and a replaced answer teaches
        # the system nothing -- that one goes to `capture` instead.
        await remember(state, report)
        card = calculation_card(
            state,
            WorkSheet.from_state(
                state,
                record_usable=_record_facts_usable(cast(PensionAgentState, state)),
                as_stand_in=_stand_ins_allowed(cast(PensionAgentState, state)),
            ),
        )
    yield {"type": "done", "state": state, "card": card, "scenarios": scenarios}


def turn_record(state: dict) -> dict[str, Any] | None:
    """What this turn adds to the thread's durable memory.

    Pure: given the finished state, return the update, or ``None`` when there is
    nothing worth remembering.

    The answer is generated outside the graph, so the graph never sees it. Left
    at that, a thread would accumulate only the member's questions and the
    assistant would lose track of its own half of the conversation.

    The working set is written here for the same reason: the provision this
    conversation settled on and the figures it last produced are what a
    follow-up refers back to. The figures are kept as a named baseline, which is
    not the same thing as leaving `calculation_result` lying around — the
    baseline is something a later turn may vary from and cite as its own, while
    a stale `calculation_result` would silently widen what the next turn's
    verification accepts.
    """
    answer = state.get("final_answer")
    if not answer:
        return None

    turn = state.get("turn_index")
    update: dict[str, Any] = {"messages": [AIMessage(content=answer)]}

    # Sections this turn cited, once verification has accepted them. A later
    # turn asking "why did you rule that one out?" retrieves on its own words,
    # which need not surface the provision it is asking about.
    if state.get("guardrail_passed", True):
        cited = {
            str(citation["section"]): turn
            for citation in (state.get("citations") or [])
            if citation.get("section")
        }
        if cited:
            update["cited_sections"] = cited

    results = state.get("calculation_result") or {}
    for formula_type, entry in results.items():
        base = entry.get("base") or {}
        if not base.get("ok"):
            continue
        update["selected_provision"] = {
            "formula_type": formula_type,
            "section": entry.get("section"),
            "subsection": entry.get("subsection"),
            "formula_id": entry.get("formula_id"),
            "selection_basis": entry.get("selection_basis"),
        }
        inputs_used = base.get("inputs_used") or {}
        update["baseline"] = {
            "turn": turn,
            "formula_type": formula_type,
            "section": entry.get("section"),
            "annual_amount": base.get("annual_amount"),
            "monthly_amount": base.get("monthly_amount"),
            "inputs_used": inputs_used,
            # The dependency set, named. A figure that does not say what it was
            # built from cannot be invalidated when the member corrects one of
            # those values, and "actually it's more like 23 years" then reaches
            # a turn that has no reason to recalculate anything.
            "rests_on": list(inputs_used),
            # The rates the figure was built from, so a later turn explaining
            # what changed can quote "2.40% a year" without it tracing to
            # nothing. They came from the statute with a source section
            # attached; carrying the amounts but not the rates that produced
            # them left the follow-up unable to say why the number moved.
            "statutory_values_used": base.get("statutory_values_used") or {},
        }

        # Results accumulate across turns under the label the member used, so
        # "put all four in one table" has all four to put there. The baseline
        # above is only ever the latest turn, which is why asking for the set
        # produced a table the guardrail then had to withdraw: the age-59 figure
        # was three turns old and traced to nothing this turn had computed.
        #
        # When a turn evaluates scenarios those are the results worth keeping;
        # the unscenarioed base is the "as things stand today" case.
        scenarios = entry.get("scenarios") or []
        recorded = {
            str(scenario.get("label")): {
                "annual_amount": scenario.get("annual_amount"),
                "monthly_amount": scenario.get("monthly_amount"),
                "service_years": (scenario.get("assumptions") or {}).get(
                    "service_years"
                ),
                "section": entry.get("section"),
                "turn": state.get("turn_index"),
                # A variant is the same recipe over the same inputs at a
                # different retirement date, so it rests on the same values the
                # base figure did and goes stale with them.
                "rests_on": list(inputs_used),
            }
            for scenario in scenarios
            if scenario.get("label") and scenario.get("annual_amount")
        } or {
            NOW_LABEL: {
                "annual_amount": base.get("annual_amount"),
                "monthly_amount": base.get("monthly_amount"),
                "service_years": inputs_used.get("creditable_service_years"),
                "section": entry.get("section"),
                "turn": state.get("turn_index"),
                "rests_on": list(inputs_used),
            }
        }
        # A label reused with a different figure means the member corrected
        # something. Overwriting silently leaves "which parts of the original
        # changed?" with nothing to compare against, and the model then quotes
        # a figure that traces to nothing and has the answer withdrawn. The
        # superseded result is kept under its own name instead.
        previous = state.get("scenario_set") or {}
        superseded = {
            f"{label}{_SUPERSEDED}{was['turn']})": was
            for label, was in previous.items()
            if label in recorded
            and str(was.get("annual_amount")) != str(recorded[label]["annual_amount"])
        }
        update["scenario_set"] = {**superseded, **recorded}
        break

    if state.get("resolved_inputs"):
        question = state.get("question") or ""
        # Provenance already settled this turn is not re-derived here. A value
        # the system *assumed* -- `resolve_inputs` puts those into
        # `resolved_inputs` alongside the record's -- would otherwise be
        # relabelled `record` on its way into durable state, and the next turn
        # would treat something the system invented as something the member's
        # file says. Everything downstream reads that label: `assumed_facts()`
        # empties, the rung drops from "calculated, with assumptions" to
        # "calculated", the assumptions block renders as nothing, the
        # disclosure guardrail stops firing and the offer to correct the value
        # disappears. A disclosed assumption becomes an undisclosed fabrication
        # in one turn boundary.
        #
        # `assumptions` is a merged key, so a name omitted here keeps the entry
        # the sheet already wrote for it, basis and all.
        established = state.get("assumptions") or {}
        provenance: dict[str, Any] = {}
        for name, value in (state.get("resolved_inputs") or {}).items():
            prior = established.get(name)
            if (
                isinstance(prior, dict)
                and prior.get("source") not in (None, "", "record")
                and str(prior.get("value")) == str(value)
            ):
                continue
            provenance[name] = {
                "value": value,
                # What the member typed outranks the record, and the answer may
                # need to say which was which several turns later.
                "source": "stated" if _stated_in(value, question) else "record",
                "turn": turn,
            }
        if provenance:
            update["assumptions"] = provenance

    return update


async def _record_turn(config: dict, state: dict) -> dict[str, Any]:
    """Persist what `turn_record` decided this turn is worth remembering.

    Returns what was recorded -- nothing if the write failed -- because the
    `done` event reports the conversation's scenarios, and this turn's are only
    in the checkpoint, not in `state`.
    """
    update = turn_record(state)
    if not update:
        return {}
    try:
        await active_graph().aupdate_state(config, update)
    except Exception:
        # The member has their answer; losing the thread's memory of it is worth
        # a warning, not a failed turn.
        logger.exception("Could not record turn state for session %s", config)
        return {}
    return update


#: The label `turn_record` files an unscenarioed figure under. A figure, not a
#: scenario the member built, so the chip leaves it out.
NOW_LABEL = "if you retire now"
#: What `turn_record` appends to a label whose figure the member corrected.
_SUPERSEDED = " (before the correction on turn "


def active_scenarios(scenario_set: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The what-ifs a member has built up, for the chip above the composer (§7).

    Not the "retire now" figure every calculation files -- a figure, not a
    scenario -- and not the superseded copies kept so a correction can be
    compared: those are history, not something in play.
    """
    return [
        {
            "label": label,
            "annual_amount": entry.get("annual_amount"),
            "monthly_amount": entry.get("monthly_amount"),
        }
        for label, entry in scenario_set.items()
        if label != NOW_LABEL and _SUPERSEDED not in label and isinstance(entry, Mapping)
    ]


async def scenarios_of(
    session_id: str, *, employee_id: str | None = None, session_token: str | None = None
) -> list[dict[str, Any]]:
    """The chip's scenarios for one conversation, read from its checkpoint."""
    config = thread_config(session_id, employee_id=employee_id, session_token=session_token)
    snapshot = await active_graph().aget_state(config)
    return active_scenarios((snapshot.values or {}).get("scenario_set") or {})


async def forget_scenarios(
    session_id: str,
    *,
    label: str | None = None,
    employee_id: str | None = None,
    session_token: str | None = None,
) -> list[dict[str, Any]]:
    """Forget one scenario (`label`), or every one the chip shows.

    `scenario_set` merges, so nothing can be removed by writing to it -- it is
    emptied with CLEAR and what stays is written back. The "retire now" figure
    always stays: it is what "put them all in one table" builds on.
    """
    config = thread_config(session_id, employee_id=employee_id, session_token=session_token)
    snapshot = await active_graph().aget_state(config)
    current = (snapshot.values or {}).get("scenario_set") or {}
    if not current:
        return []
    if label is None:
        keep = {name: entry for name, entry in current.items() if name == NOW_LABEL}
    else:
        # The superseded copies of a forgotten scenario go with it.
        keep = {
            name: entry
            for name, entry in current.items()
            if name != label and not name.startswith(f"{label}{_SUPERSEDED}")
        }
    await active_graph().aupdate_state(config, {"scenario_set": CLEAR})
    if keep:
        await active_graph().aupdate_state(config, {"scenario_set": keep})
    return active_scenarios(keep)




def _substitute(blocks: Mapping[str, str], text: str) -> str:
    """Apply the computed blocks to a complete answer rather than a stream.

    The repair path joins its deltas before verifying, and it is handed the same
    system prompt as the original generation -- markers and all -- so it emits
    them again. Without this the member's repaired answer showed a raw
    ``<<<BASIS>>>`` and lost its legal-basis footer entirely: 12 of 119 turns on
    the eval suite, every one of them a repair.

    Substitution has to happen *before* verification, not after, because the
    blocks carry the citations `verify_citations` reads.
    """
    substituter = MarkerSubstituter(blocks)
    out = substituter.feed(text) + substituter.flush()
    for block in substituter.unused():
        out += "\n\n" + block
    return out


def _inputs_behind(computed: dict[str, Any]) -> set[str]:
    """Every input name the figures in this answer were actually computed from.

    Read off each result's own ``inputs_used``, base and scenarios alike, so the
    set is what the interpreter consumed rather than what happened to be on the
    sheet when it ran.
    """
    used: set[str] = set()
    for entry in computed.values():
        if not isinstance(entry, Mapping):
            continue
        blocks: list[Any] = [entry.get("base"), *(entry.get("scenarios") or [])]
        for block in blocks:
            if isinstance(block, Mapping):
                used |= {str(name) for name in (block.get("inputs_used") or {})}
    return used


def _pairwise_differences(scenarios: dict) -> list[str]:
    """Every gap between two carried figures, annual and monthly."""
    gaps: list[str] = []
    for field in ("annual_amount", "monthly_amount"):
        amounts = []
        for result in scenarios.values():
            try:
                amounts.append(Decimal(str(result.get(field))))
            except (InvalidOperation, TypeError):
                continue
        for i, first in enumerate(amounts):
            for second in amounts[i + 1 :]:
                gaps.append(str(abs(first - second)))
    return gaps


def _verify(state: dict, answer: str, settings) -> guardrails.GuardrailReport:
    """Run post-generation verification, honouring the config switches."""
    if not settings.guardrail_enabled:
        return guardrails.GuardrailReport()

    # Figures the member supplied earlier in the conversation are as legitimate
    # a source as ones in the latest message — "recalculate with my contribution
    # accumulation changed to $205,000" is answered by referring back to the
    # $185,000 they gave a turn ago, and verifying against this turn's text
    # alone rejected the answer for quoting it.
    # Only the member's own turns count. Including previous assistant messages
    # would let a figure this system stated once become its own justification
    # for stating it again.
    conversation = "\n".join(
        str(message.content)
        for message in state.get("messages") or []
        if getattr(message, "type", None) == "human"
    )
    # Figures this conversation worked out on an earlier turn are traceable
    # too, but only as the structured baseline -- the interpreter's own output,
    # carried forward as state. That is a different thing from the sentences of
    # a previous answer, which stay excluded: the baseline cannot contain a
    # figure the model invented, because the model never wrote it.
    computed = dict(state.get("calculation_result") or {})
    turn = state.get("turn_index")
    if (baseline := state.get("baseline")) and baseline.get("turn") != turn:
        computed["_carried_baseline"] = baseline
    if carried := {
        label: result
        for label, result in (state.get("scenario_set") or {}).items()
        if result.get("turn") != turn
    }:
        computed["_carried_scenarios"] = carried
        # "Compare all of them and explain why the figure moves" is answered
        # with the gaps between the figures, and a gap between two engine
        # results is as traceable as the results themselves -- the same
        # reasoning that already allows a monthly figure derived from an annual
        # one. Without this the comparison turn had its answer withdrawn for
        # doing arithmetic the previous turns had made unavoidable.
        computed["_carried_differences"] = _pairwise_differences(carried)

    sheet = WorkSheet.from_state(
        state,
        record_usable=_record_facts_usable(cast(PensionAgentState, state)),
        as_stand_in=_stand_ins_allowed(cast(PensionAgentState, state)),
    )
    # Only the assumptions *this* answer's figures were computed from. The rule
    # being enforced is "a figure resting on a value nobody supplied must say
    # so", and an assumption the figure never touched is not something the
    # answer owes the member a sentence about. The sheet is durable, so without
    # this the retirement date assumed on turn 1 was still owed disclosure on
    # turn 3 -- where the question was about a survivor annuity, the answer had
    # no reason to mention a retirement date, and a correct answer was withdrawn
    # for not disclosing an assumption it had not used.
    used = _inputs_behind(computed)
    assumed = {
        name: fact.basis
        for name, fact in sheet.assumed_facts().items()
        # No readable `inputs_used` means we cannot tell what the figure rests
        # on, so every assumption is policed. Erring towards a spurious repair
        # is the right side to err on: the other side is an undisclosed
        # assumption, which is the thing this check exists to make impossible.
        if not used or aliases_of(name) & used
    }

    return guardrails.verify_answer(
        answer,
        retrieved_sections=state.get("retrieved_sections") or [],
        calculation_result=computed or None,
        # Withheld when the conversation is reading an Article this member's
        # record is not filed under. Their figures are perfectly traceable --
        # that is the problem. Tracing to the member's record is not enough when
        # the record belongs to a different fund, so for these turns the record
        # stops being an allowed source and a figure from it is caught rather
        # than blessed.
        facts=_available_facts(state),
        user_text=conversation or state.get("question", ""),
        check_numbers=settings.guardrail_numeric_provenance,
        established_sections=(state.get("cited_sections") or {}),
        coverage=state.get("coverage"),
        assumed_facts=assumed,
    )
