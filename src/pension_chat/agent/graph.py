"""LangGraph assembly and the streaming entry point (§9.4).

The graph runs everything deterministic and everything that must complete before
a single word of the answer is safe to send. Answer *generation* then streams
outside the graph, so tokens reach the browser as the model produces them.

Flow::

    triage
      ├─(out of scope / greeting)─────────────────> decline ──> END
      └─> [plan_request ∥ fetch_employee_record] ──> retrieve_policy ──> resolve_recipes
                                                            │
                                              (no calculation needed)
                                                            │
                                          resolve_inputs ──> validate_recipes
                                                            │
                                          execute_calculations ──> prepare_answer ──> END

Ordering is a correctness constraint, not a style choice. ``retrieve_policy``
needs the employee record to know which Article to filter to; ``resolve_recipes``
needs the retrieved text to derive from; ``resolve_inputs`` needs the recipe to
know which inputs exist. The first implementation fanned these out concurrently
from ``plan_request`` and every one of those dependencies was violated.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.graph import END, StateGraph

from pension_chat.agent import guardrails
from pension_chat.agent.llm import begin_usage
from pension_chat.agent.llm import stream as stream_llm
from pension_chat.agent.nodes import (
    execute_calculations,
    fetch_employee_record,
    plan_request,
    prepare_answer,
    resolve_inputs,
    resolve_recipes,
    retrieve_policy,
    triage,
    validate_recipes,
)
from pension_chat.agent.state import PensionAgentState, initial_state
from pension_chat.config import get_settings

logger = logging.getLogger("pension_chat.agent.graph")

# Stage labels sent to the client as the graph advances. Keyed by node name so
# adding a node without a label is visible rather than silent.
STAGE_LABELS = {
    "triage": "reading your question",
    "plan_request": "planning",
    "fetch_employee_record": "loading your record",
    "retrieve_policy": "searching the Pension Code",
    "resolve_recipes": "working out the applicable calculation",
    "resolve_inputs": "checking the required inputs",
    "validate_recipes": "validating the calculation",
    "execute_calculations": "calculating",
    "prepare_answer": "writing your answer",
}

OUT_OF_SCOPE_MESSAGE = (
    "I answer questions about the Illinois Pension Code (40 ILCS 5/) — things "
    "like which retirement provision applies to you, what a section says, or "
    "what your estimated benefit would be. Ask me one of those and I'll look it "
    "up in the statute."
)

GREETING_MESSAGE = (
    "Hello. I can answer questions about the Illinois Pension Code — which "
    "retirement provisions apply to you, what a particular Section says, or an "
    "estimate of your benefit. What would you like to know?"
)


def _needs_calculation(state: PensionAgentState) -> str:
    """Skip the calculation branch entirely for a question that has no maths."""
    if state.get("requires_calculation") and state.get("formula_types_needed"):
        return "resolve_inputs"
    return "prepare_answer"


def _in_scope(state: PensionAgentState) -> str | list[str]:
    """Fan out to both independent nodes, or short-circuit to the decline path.

    Returning a list dispatches to several nodes at once; LangGraph runs them
    concurrently and joins them at the edge that lists both as sources.
    """
    if not state.get("in_scope", True):
        return "decline"
    return ["plan_request", "fetch_employee_record"]


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


def build_graph():
    """Assemble and compile the StateGraph."""
    workflow = StateGraph(PensionAgentState)

    workflow.add_node("triage", triage)
    workflow.add_node("plan_request", plan_request)
    workflow.add_node("decline", decline)
    workflow.add_node("fetch_employee_record", fetch_employee_record)
    workflow.add_node("retrieve_policy", retrieve_policy)
    workflow.add_node("resolve_recipes", resolve_recipes)
    workflow.add_node("resolve_inputs", resolve_inputs)
    workflow.add_node("validate_recipes", validate_recipes)
    workflow.add_node("execute_calculations", execute_calculations)
    workflow.add_node("prepare_answer", prepare_answer)

    workflow.set_entry_point("triage")
    workflow.add_conditional_edges(
        "triage",
        _in_scope,
        ["plan_request", "fetch_employee_record", "decline"],
    )
    workflow.add_edge("decline", END)

    # Planning and the record fetch are independent — planning reads only the
    # question, and the record read needs no plan. Both are ~2s against remote
    # services, so running them concurrently takes one of them off the critical
    # path entirely. Retrieval waits for both: it needs the queries from one and
    # the member's Article number from the other.
    workflow.add_edge(["plan_request", "fetch_employee_record"], "retrieve_policy")
    workflow.add_edge("retrieve_policy", "resolve_recipes")
    workflow.add_conditional_edges(
        "resolve_recipes",
        _needs_calculation,
        {"resolve_inputs": "resolve_inputs", "prepare_answer": "prepare_answer"},
    )
    workflow.add_edge("resolve_inputs", "validate_recipes")
    workflow.add_edge("validate_recipes", "execute_calculations")
    workflow.add_edge("execute_calculations", "prepare_answer")
    workflow.add_edge("prepare_answer", END)

    return workflow.compile()


agent_graph = build_graph()


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

    state: dict[str, Any] = initial_state(question, session_id, employee_id, history)

    try:
        async for update in agent_graph.astream(state, stream_mode="updates"):
            for node_name, node_output in update.items():
                if isinstance(node_output, dict):
                    # `timings` is an accumulator: every node contributes one
                    # key. A plain dict update would replace the whole map with
                    # the last node's single entry.
                    merged_timings = {
                        **state.get("timings", {}),
                        **(node_output.get("timings") or {}),
                    }
                    state.update(node_output)
                    state["timings"] = merged_timings
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
    try:
        async for delta in stream_llm(
            node="generate_answer",
            messages=messages,
            reasoning_effort=settings.reasoning_effort_answer,
        ):
            if first_token_at is None:
                first_token_at = time.perf_counter() - started
            chunks.append(delta)
            yield {"type": "token", "text": delta}
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

    answer = "".join(chunks)
    state["final_answer"] = answer

    report = _verify(state, answer, settings)
    state["guardrail_passed"] = report.passed
    state["guardrail_report"] = report.to_dict()

    if not report.passed:
        # The unverified text has already been streamed, so tell the client to
        # replace it rather than leaving an unsupported figure on screen.
        replacement = guardrails.fallback_answer(report)
        state["final_answer"] = replacement
        logger.warning(
            "Guardrail rejected answer for session %s: %s",
            session_id,
            report.hard_failures,
        )
        yield {"type": "replace", "text": replacement, "reason": report.to_dict()}

    state["token_usage"] = usage.to_dict()
    state["timings"] = {
        **state.get("timings", {}),
        "time_to_first_token": first_token_at or 0.0,
        "total": time.perf_counter() - started,
    }
    yield {"type": "done", "state": state}


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
    return guardrails.verify_answer(
        answer,
        retrieved_sections=state.get("retrieved_sections") or [],
        calculation_result=state.get("calculation_result"),
        facts=state.get("member_facts") or {},
        user_text=conversation or state.get("question", ""),
        check_numbers=settings.guardrail_numeric_provenance,
    )
