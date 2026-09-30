"""Everything a turn did, in one record the checks and the judge read.

Checks and the judge read only this, never the live agent, so a saved trace
can be re-checked or re-judged without running a model call again.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from pension_chat.agent.instrument import Instrument


@dataclass
class TurnTrace:
    case_id: str
    turn_index: int
    question: str
    employee: str | None
    streamed_answer: str = ""
    final_answer: str = ""
    replacements: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    in_scope: bool = True
    tools: list[dict[str, Any]] = field(default_factory=list)
    retrieved: list[str] = field(default_factory=list)
    rendered: dict[str, list[str]] = field(default_factory=dict)
    verified_citations: list[str] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    unverified_numbers: list[Any] = field(default_factory=list)
    guardrail_passed: bool | None = None
    failure_codes: list[str] = field(default_factory=list)
    input_sources: dict[str, str] = field(default_factory=dict)
    assumptions: dict[str, Any] = field(default_factory=dict)
    calculation: bool = False
    #: formula type -> "registry" (a saved formula reused) or "derived" (new)
    formula_sources: dict[str, str] = field(default_factory=dict)
    card: dict[str, Any] | None = None
    subject: str | None = None
    ttft: float | None = None
    ttft_rate_limit_wait: float = 0.0
    total: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    stages: dict[str, float] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(json.dumps(asdict(self), default=str))
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnTrace:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


def _codes(report: dict[str, Any] | None) -> list[str]:
    report = report or {}
    return [str(f.get("code")) for f in (report.get("hard_failures") or []) + (report.get("warnings") or [])
            if isinstance(f, dict) and f.get("code")]


def _tools(state: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    spent = dict(state.get("tool_calls_spent") or {})
    # The pre-search runs before the model, so it has no assistant message; take it from the spend.
    if spent.get("search_statute"):
        out.append({"name": "search_statute", "args": {"prefetch": True}})
    for message in state.get("loop_messages") or []:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"raw": fn.get("arguments")}
            out.append({"name": fn.get("name"), "args": args})
    return out


def build_trace(
    *, case_id: str, turn_index: int, question: str, employee: str | None,
    events: list[tuple[float, dict[str, Any]]], instrument: Instrument, started: float,
) -> TurnTrace:
    trace = TurnTrace(case_id=case_id, turn_index=turn_index, question=question, employee=employee)
    streamed: list[str] = []
    final: str | None = None
    for at, event in events:
        kind = event.get("type")
        if kind == "token":
            if trace.ttft is None:
                trace.ttft = at
            streamed.append(str(event.get("text") or ""))
        elif kind == "replace":
            final = str(event.get("text") or "")
            reason = event.get("reason") or {}
            trace.replacements.append({"reason": reason})
            trace.failure_codes += _codes(reason if isinstance(reason, dict) else {})
        elif kind == "error":
            trace.error = str(event.get("detail") or event.get("message") or "error")
        elif kind == "done":
            state = event.get("state") or {}
            report = state.get("guardrail_report") or {}
            trace.in_scope = bool(state.get("in_scope", True))
            trace.tools = _tools(state)
            trace.retrieved = [str(s.get("section")) for s in state.get("retrieved_sections") or [] if s.get("section")]
            trace.verified_citations = list(report.get("verified_citations") or [])
            trace.unverified_citations = list(report.get("unverified_citations") or [])
            trace.unverified_numbers = list(report.get("unverified_numbers") or [])
            trace.guardrail_passed = state.get("guardrail_passed")
            trace.failure_codes += _codes(report)
            trace.input_sources = dict(state.get("input_sources") or {})
            trace.assumptions = dict(state.get("assumptions") or {})
            # A figure, not a recipe that ran: every executed recipe is filed,
            # a failure included (the same test as loop._first_figure).
            trace.calculation = any(
                ((entry or {}).get("base") or {}).get("ok")
                for entry in (state.get("calculation_result") or {}).values()
            )
            trace.card = event.get("card")
            trace.formula_sources = {
                str(p.get("formula_type")): str(p.get("source"))
                for p in state.get("recipe_provenance") or [] if p.get("formula_type")
            }
            trace.subject = (state.get("employee_record") or {}).get("id")
            trace.stages = dict(state.get("timings") or {})
            trace.usage = dict(state.get("token_usage") or {})
            trace.total = at
    trace.streamed_answer = "".join(streamed)
    trace.final_answer = final if final is not None else trace.streamed_answer
    trace.rendered = {k: list(v) for k, v in instrument.rendered.items()}
    trace.calls = [{"kind": c.kind, "label": c.label, "start": round(c.start - started, 4),
                    "end": round(c.end - started, 4)} for c in instrument.calls]
    trace.notes = [{"at": round(t - started, 4), "kind": k, "detail": d} for t, k, d in instrument.notes]
    trace.ttft_rate_limit_wait = sum(
        float(n["detail"] or 0) for n in trace.notes
        if n["kind"] == "rate_limited" and (trace.ttft is None or n["at"] <= trace.ttft)
    )
    trace.failure_codes = list(dict.fromkeys(trace.failure_codes))
    return trace
