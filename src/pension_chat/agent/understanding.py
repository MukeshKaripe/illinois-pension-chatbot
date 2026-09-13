"""What this turn is, decided by the model rather than by a word list.

Scope, whether a figure is wanted, which benefit is in play, which values the
member has just revised and which scenarios they want compared were all read
off regular expressions over the current message. Every gap in those patterns
was a silent refusal: "I'm thinking about retiring" was turned away because
"retiring" does not contain the substring "retire", and "my wife is worried
about what happens to her" because the vocabulary held "spouse" and "widow" but
not "wife". Measured against the live model this reads all eight of those cases
correctly in 2.27s median.

Parsing is defensive on purpose and fails **open**. The safety story is the
post-generation verifier and the deterministic calculation gate; neither is
weakened by letting a question through, and a member wrongly refused has no
recourse at all.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pension_chat.agent.llm import complete
from pension_chat.agent.scenarios import (
    corroborated,
    extract_scenarios,
    merge_scenarios,
)
from pension_chat.agent.worksheet import WorkSheet
from pension_chat.config import get_settings

logger = logging.getLogger("pension_chat.agent.understanding")

BENEFIT_TYPES = (
    "retirement_annuity",
    "survivor_annuity",
    "disability_benefit",
    "refund_of_contributions",
    "service_credit",
)

UNDERSTAND_SYSTEM = """You read one turn of a conversation between a member of a
Chicago retirement fund and a pension assistant, and decide how the turn should
be routed. You do not answer the question.

You are given what the assistant already knows about this member, what has
already been worked out in this conversation, and the member's new message.

Return JSON only:
{
 "in_scope": true|false,
 "out_of_scope_reason": "<one clause, only when in_scope is false>",
 "intent": "<one short phrase describing what the member actually wants>",
 "benefit_types": ["retirement_annuity"|"survivor_annuity"|"disability_benefit"|
   "refund_of_contributions"|"service_credit"],
 "needs_figure": true|false,
 "recompute": true|false,
 "revised_facts": {"<fact name>": <value>},
 "scenarios": [{"label": "...", "retirement_age": <int|null>,
   "years_from_now": <int|null>, "basis": "..."}],
 "unresolved": [{"what": "...", "why": "..."}],
 "answerable_part": "<if part of the message is outside the Pension Code, the part that is inside it>",
 "answers_open_question": true|false,
 "search_queries": ["<the statute-worded queries to retrieve on>"]
}

Rules:
- in_scope is true for anything about this member's pension, retirement,
  service, salary basis, survivors, disability, refunds, or the Pension Code,
  and for a bare continuation of an earlier turn. It is false only for a request
  that is plainly somebody else's job.
- needs_figure is true whenever the member wants a number, including a
  comparison of several retirement dates. It is also true when they ask how
  their *own* benefit is worked out: "how is my pension calculated" wants the
  method and the figure, not a choice between them. Only a question about the
  law in the abstract, with no "my" in it, wants no figure.
- recompute is true when the member corrects, revises or supplies a value that
  a figure already worked out in this conversation depended on.
- revised_facts carries only values the member actually stated, under the fact
  names used in the context you were given.
- A scenario names either an absolute retirement age ("at 58", "when I turn
  60") or an offset from today ("two more years", "if I keep working another
  decade"). Put an absolute age in retirement_age and an offset in
  years_from_now, and never convert one into the other -- you have not been
  told the member's current age, and the offset is resolved against it for you
  later. "If I work two more years" is a scenario; so is "what if I stay on a
  while longer", if you can put a number on it.
- You decide which benefit is in play. You never decide which Article covers the
  member; that is settled elsewhere and you must not report it."""


@dataclass
class TurnUnderstanding:
    """What one message is asking for."""

    in_scope: bool = True
    out_of_scope_reason: str = ""
    intent: str = ""
    benefit_types: list[str] = field(default_factory=lambda: ["retirement_annuity"])
    needs_figure: bool = False
    recompute: bool = False
    revised_facts: dict[str, Any] = field(default_factory=dict)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    answerable_part: str = ""
    answers_open_question: bool = False
    search_queries: list[str] = field(default_factory=list)


def fallback_understanding() -> TurnUnderstanding:
    """What to believe when the model could not be read. Deliberately permissive."""
    return TurnUnderstanding(in_scope=True, needs_figure=False)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        return default
    return default


def _as_str_list(value: Any, allowed: tuple[str, ...] | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item) for item in value if isinstance(item, (str, int, float))]
    if allowed is not None:
        items = [item for item in items if item in allowed]
    return items


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def parse_understanding(raw: str) -> TurnUnderstanding:
    """Turn the model's JSON into a result, surviving anything it actually returns."""
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        logger.warning("Turn understanding was not JSON; falling open")
        return fallback_understanding()
    if not isinstance(data, dict):
        return fallback_understanding()

    benefit_types = _as_str_list(data.get("benefit_types"), BENEFIT_TYPES)
    revised = data.get("revised_facts")

    return TurnUnderstanding(
        in_scope=_as_bool(data.get("in_scope"), True),
        out_of_scope_reason=str(data.get("out_of_scope_reason") or ""),
        intent=str(data.get("intent") or ""),
        benefit_types=benefit_types or ["retirement_annuity"],
        needs_figure=_as_bool(data.get("needs_figure"), False),
        recompute=_as_bool(data.get("recompute"), False),
        revised_facts=dict(revised) if isinstance(revised, dict) else {},
        scenarios=_as_dict_list(data.get("scenarios")),
        unresolved=_as_dict_list(data.get("unresolved")),
        answerable_part=str(data.get("answerable_part") or ""),
        answers_open_question=_as_bool(data.get("answers_open_question"), False),
        search_queries=_as_str_list(data.get("search_queries")),
    )


def _context(sheet: WorkSheet, state: dict[str, Any]) -> str:
    """What the model needs to know about this member and this conversation."""
    known = ", ".join(
        f"{name}={value}" for name, value in sorted(sheet.usable_values().items())
    )
    figures = "; ".join(
        f"{label}: {figure.result} from {list(figure.rests_on)} "
        f"under {figure.provision.get('section')}"
        for label, figure in sheet.figures.items()
    )
    open_items = "; ".join(item.what for item in sheet.open_items)

    lines = [f"What is known about this member: {known or 'nothing yet'}"]
    if figures:
        lines.append(f"Worked out earlier in this conversation: {figures}")
    if open_items:
        lines.append(f"Put to the member last turn: {open_items}")
    return "\n".join(lines)


async def understand_turn(state: dict[str, Any]) -> dict[str, Any]:
    """Decide what this turn is, and what an earlier turn's figures owe it.

    Runs concurrently with the record fetch. That is safe because the sheet is
    durable: from the second turn onwards the member's facts are already in the
    checkpoint, and on the first turn there is nothing yet to contradict.
    """
    started = time.perf_counter()
    settings = get_settings()
    sheet = WorkSheet.from_state(state)

    history = "\n".join(
        f"{getattr(message, 'type', '?')}: {message.content}"
        for message in (state.get("messages") or [])[-settings.history_turns * 2 :]
    )

    try:
        raw = await complete(
            node="understand_turn",
            messages=[
                {"role": "system", "content": UNDERSTAND_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"{_context(sheet, state)}\n\n"
                        f"Conversation so far:\n{history}\n\n"
                        f"Member's new message: {state['question']}"
                    ),
                },
            ],
            reasoning_effort=settings.reasoning_effort_understand,
            json_mode=True,
        )
        result = parse_understanding(raw)
    except Exception as exc:
        logger.warning("Turn understanding failed (%s); falling open", exc)
        result = fallback_understanding()

    stale = sorted(sheet.apply_revisions(result.revised_facts, state.get("turn_index") or 0))

    # The model's scenarios are unioned with the ones a regex can resolve and
    # filtered by whether this message names them at all. Neither half is
    # redundant. The model cannot resolve "wait 2 more years" -- this node runs
    # concurrently with the record fetch, so it does not know the member's age --
    # and it is shown the conversation so far, which is how scenarios from three
    # turns ago came back attached to a question that never asked for them.
    scenarios = merge_scenarios(
        extract_scenarios(state["question"]),
        [s for s in result.scenarios if corroborated(s, state["question"])],
    )

    # Naming a retirement age mid-conversation asks for the figure again, however
    # the sentence is phrased. "Call this Scenario A: retire at 57" is an
    # instruction rather than a question, and read as one it produced no figure
    # and no comparison -- two turns into a conversation about exactly that.
    # This only ever *adds* a calculation, so a misread costs a figure nobody
    # asked for rather than a refusal.
    mid_conversation = len(state.get("messages") or []) > 1
    continuing = mid_conversation and bool(scenarios)
    wants_figure = result.needs_figure or bool(stale) or continuing

    # A continuation is a continuation *of something*: requiring the calculation
    # without naming what to calculate would skip it anyway, so fall back to the
    # benefit this conversation already settled on.
    settled = (state.get("selected_provision") or {}).get("formula_type")
    benefits = result.benefit_types if result.needs_figure or stale else []
    if not benefits and continuing and settled:
        benefits = [str(settled)]

    return {
        "in_scope": result.in_scope,
        "out_of_scope_reason": result.out_of_scope_reason,
        "intent": result.intent,
        "requires_calculation": wants_figure,
        "formula_types_needed": benefits,
        "search_queries": result.search_queries,
        "scenarios": scenarios,
        "unresolved": result.unresolved,
        "answerable_part": result.answerable_part,
        "stale_figures": stale,
        **sheet.to_state_update(),
        "timings": {"understand_turn": time.perf_counter() - started},
    }
