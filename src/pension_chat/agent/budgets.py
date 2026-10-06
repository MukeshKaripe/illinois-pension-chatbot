"""The model-call budget: which model calls a turn may make before its first answer word.

On gpt-6-luna each call costs about 1.4 s server-side whatever its size
(measured 2026-10-05), so latency is set by how many calls run one after
another. Every call on today's hot path arrived with a feature; this module is
where a new one has to be declared (spec §3 P1/P2), and where a turn's record
of what it actually did is built.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: Every node that may call the model. A call site naming anything else fails
#: tests/test_budget_registry.py.
MODEL_NODES: dict[str, str] = {
    "investigate": "the decision call: read, search, compute, or stop",
    "generate_answer": "writes the answer; its first token ends the clock",
    "repair_answer": "rewrites an answer verification rejected; always after the first token",
    "assess_coverage": "coverage, only when the member described themselves (exception: self_described)",
    "derive_recipe": "derives a formula no stored one covers (exception: derivation)",
    "resolve_inputs": "the separate extraction call; off unless settings.extract_with_separate_call",
    "eval_judge": "the eval harness's judge; never part of a turn",
}

#: turn type -> (most sequential model calls before the first answer word, nodes allowed)
BUDGETS: dict[str, tuple[int, frozenset[str]]] = {
    "statute": (2, frozenset({"investigate", "generate_answer"})),
    "figure": (2, frozenset({"investigate", "generate_answer"})),
    "asks": (2, frozenset({"investigate", "generate_answer"})),
    "comparison": (2, frozenset({"investigate", "generate_answer"})),
    "self_described": (3, frozenset({"investigate", "assess_coverage", "generate_answer"})),
    "derivation": (3, frozenset({"investigate", "derive_recipe", "generate_answer"})),
}


@dataclass(frozen=True)
class ModelCall:
    node: str
    start: float
    end: float


def sequential_model_calls(calls: Iterable[ModelCall], first_token_at: float | None) -> int:
    """How many model calls ran one after another before the first answer word.

    Calls that overlap in time count once: two people's computations issued
    together are one wait.
    """
    spans = sorted(
        (c.start, c.end) for c in calls if first_token_at is None or c.start < first_token_at
    )
    waves, reach = 0, float("-inf")
    for start, end in spans:
        if start >= reach:
            waves += 1
            reach = end
        else:
            reach = max(reach, end)
    return waves


def classify_turn(*, tools: Iterable[str], model_nodes: Iterable[str], people: int,
                  has_figure: bool, asked: bool) -> str:
    """The turn type, read from what the turn did -- never declared by hand."""
    used, nodes = set(tools), set(model_nodes)
    if "derive_recipe" in nodes:
        return "derivation"
    if "assess_coverage" in nodes or "check_coverage" in used:
        return "self_described"
    if people > 1:
        return "comparison"
    if has_figure:
        return "figure"
    if "compute_benefit" in used:
        return "asks" if asked else "figure"
    return "statute"


def over_budget(turn_type: str, sequential: int, model_nodes: Iterable[str]) -> list[str]:
    limit, allowed = BUDGETS[turn_type]
    problems: list[str] = []
    if sequential > limit:
        problems.append(f"{sequential} sequential model calls, budget {limit}")
    stray = sorted(set(model_nodes) - allowed - {"repair_answer"})
    if stray:
        problems.append(f"nodes outside the {turn_type} budget: {', '.join(stray)}")
    return problems


_HEADING = re.compile(r"^### 40 ILCS 5/(\S+)", re.MULTILINE)


def repeated_sections(messages: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Sections whose text appears more than once in a prompt -> estimated repeated tokens."""
    seen: set[str] = set()
    repeated: dict[str, int] = {}
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        heads = list(_HEADING.finditer(content))
        for i, head in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(content)
            section = head.group(1)
            if section in seen:
                repeated[section] = repeated.get(section, 0) + (end - head.start()) // 4
            seen.add(section)
    return repeated


def _has_figure(state: Mapping[str, Any]) -> bool:
    for entry in (state.get("calculation_result") or {}).values():
        entry = entry or {}
        if (entry.get("base") or {}).get("ok") or any(
            s.get("annual_amount") for s in entry.get("scenarios") or []
        ):
            return True
    return False


def turn_budget_record(state: Mapping[str, Any], calls: Iterable[Any], started: float,
                       first_token_at: float | None) -> dict[str, Any]:
    """One turn's budget record: its type, its sequential model calls, and any breach.

    ``calls`` are `instrument.Call`s (absolute perf_counter times); ``first_token_at``
    is relative to ``started``, as `run_agent_stream` keeps it.
    """
    first_abs = None if first_token_at is None else started + first_token_at
    model = [ModelCall(str(c.label), float(c.start), float(c.end)) for c in calls
             if c.kind == "model" and getattr(c, "ok", True)
             and (first_abs is None or c.start < first_abs)]
    nodes = [m.node for m in sorted(model, key=lambda m: m.start)]
    turn_type = classify_turn(
        tools=list((state.get("tool_calls_spent") or {}).keys()),
        model_nodes=nodes,
        people=len(state.get("people") or {}),
        has_figure=_has_figure(state),
        asked=bool(state.get("missing_inputs")),
    )
    sequential = sequential_model_calls(model, None)
    return {
        "turn_type": turn_type,
        "sequential_model_calls": sequential,
        "model_nodes": nodes,
        "over_budget": over_budget(turn_type, sequential, nodes),
        "repeated_sections": repeated_sections(state.get("answer_messages") or []),
    }
