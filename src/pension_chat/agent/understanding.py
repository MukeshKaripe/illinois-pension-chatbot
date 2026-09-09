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
from dataclasses import dataclass, field
from typing import Any

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
 "scenarios": [{"label": "...", "retirement_age": <int|null>, "basis": "..."}],
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
  comparison of several retirement dates.
- recompute is true when the member corrects, revises or supplies a value that
  a figure already worked out in this conversation depended on.
- revised_facts carries only values the member actually stated, under the fact
  names used in the context you were given.
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
