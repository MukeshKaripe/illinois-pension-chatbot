"""What the agent can do, as tools it chooses rather than stages it passes through.

Each of these wraps code the pipeline already runs. The difference is who
decides when: `retrieve_policy` ran on every turn with queries written before
any statute had been seen, and could not act on what came back. Here the model
reads a result and decides what to do next.

Two contracts matter more than the schemas.

**A search reports how well it did.** The known weakness of a loop that grades
itself is a model deciding "good enough" and answering. Naming the best score in
the result makes a weak search visible rather than something the model has to
infer from text it is already inclined to accept.

**Every tool answers, even when it fails.** An unknown section, an empty
outline, a missing argument -- each comes back as text saying so. A tool that
raises ends the member's turn over something the model could have recovered
from by trying a different one.
"""

from __future__ import annotations

import logging
from typing import Any

from pension_chat.agent import corpus
from pension_chat.agent.nodes import (
    execute_calculations,
    resolve_inputs,
    resolve_recipes,
    validate_recipes,
)
from pension_chat.agent.retrieval import retrieve
from pension_chat.agent.understanding import BENEFIT_TYPES
from pension_chat.config import get_settings

logger = logging.getLogger("pension_chat.agent.tools")

#: Sections rendered under the heading `ANSWER_SYSTEM`'s citation rule names:
#: "Cite ONLY a section whose own text appears in front of you under a
#: '### 40 ILCS 5/...' heading." The loop's evidence has to look the way the
#: answer prompt says evidence looks, or every citation is refused.
_HEADING = "### 40 ILCS 5/{section} — {title}"

RETRIEVAL_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_statute",
            "description": (
                "Search the Illinois Pension Code for sections relevant to a question. "
                "Returns whole sections with their text, and says how well each matched. "
                "Prefer the Code's own vocabulary over the member's: it says 'annuity "
                "reduction' where a member says 'losing too much'. If the results are "
                "weak, look at an Article's outline for the words it actually uses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more search phrases, worded as the statute would word them.",
                    },
                    "articles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Restrict to these Article numbers, e.g. ['11']. Omit to "
                            "search the whole Code."
                        ),
                    },
                    "why": {
                        "type": "string",
                        "description": "What you are trying to establish. One short phrase.",
                    },
                },
                "required": ["queries", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_section",
            "description": (
                "Read one section in full, by its number, from any Article. Use this to "
                "follow a cross-reference a section names, or to read a heading you "
                "found in an outline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "A section id such as '11-134' or '1-113.4a'.",
                    }
                },
                "required": ["section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "outline",
            "description": (
                "List every section heading in one Article, in the order the Code "
                "prints them. This is the Code's own vocabulary for its own concepts, "
                "and the way to answer 'what is Article 11?' or to find which sections "
                "cover survivors, disability or refunds."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "article": {
                        "type": "string",
                        "description": "An Article number, e.g. '11'.",
                    }
                },
                "required": ["article"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "related_sections",
            "description": (
                "Walk the Code's citation graph one hop. 'cites' gives what a section "
                "depends on; 'cited_by' gives what depends on it, which is where an "
                "exception that changes the answer usually lives."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "description": "A section id, e.g. '11-134'."},
                    "direction": {
                        "type": "string",
                        "enum": ["cites", "cited_by"],
                        "description": "'cites' for dependencies, 'cited_by' for dependents.",
                    },
                },
                "required": ["section", "direction"],
            },
        },
    },
]

CALCULATION_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "compute_benefit",
            "description": (
                "Work out a benefit figure for this member. Runs the deterministic "
                "calculation engine: it finds or derives the controlling provision, "
                "resolves the inputs from the member's record and from what they "
                "have told you, checks the provision reaches them, and computes. "
                "You never do the arithmetic yourself. If an input is missing it "
                "says which, so you can ask for that one thing rather than a list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "benefit": {
                        "type": "string",
                        "enum": list(BENEFIT_TYPES),
                        "description": "Which benefit to work out.",
                    },
                    "retirement_age": {
                        "type": "integer",
                        "description": (
                            "An age the member named ('at 58'). Omit for a figure as "
                            "of today."
                        ),
                    },
                    "years_from_now": {
                        "type": "integer",
                        "description": (
                            "An offset the member named ('two more years'). Never "
                            "convert it to an age yourself -- it is resolved against "
                            "their real age for you."
                        ),
                    },
                },
                "required": ["benefit"],
            },
        },
    },
]

TOOL_NAMES = tuple(
    tool["function"]["name"] for tool in RETRIEVAL_TOOLS + CALCULATION_TOOLS
)


def _render(sections: list[dict[str, Any]], budget: int) -> str:
    blocks: list[str] = []
    used = 0
    for section in sections:
        text = section.get("text") or ""
        if used + len(text) > budget:
            text = text[: max(0, budget - used)]
        if not text:
            break
        blocks.append(
            _HEADING.format(section=section["section"], title=section.get("title", ""))
            + f"\n{text}"
        )
        used += len(text)
    return "\n\n".join(blocks)


def _quality(sections: list[dict[str, Any]]) -> str:
    """How well the search did, said plainly rather than left to be inferred."""
    if not sections:
        return "No sections matched. Try the Code's own wording, or read an Article's outline."
    best = max((s.get("score") or 0) for s in sections)
    if best < 0.5:
        verdict = "weak -- these may not answer the question"
    elif best < 1.5:
        verdict = "moderate"
    else:
        verdict = "strong"
    return f"{len(sections)} section(s), best match {best:.2f} ({verdict})."


async def _search(arguments: dict[str, Any], state: dict) -> tuple[str, dict]:
    queries = [str(q) for q in (arguments.get("queries") or []) if str(q).strip()]
    if not queries:
        return "search_statute needs at least one query in `queries`.", {}
    articles = [str(a) for a in (arguments.get("articles") or [])] or None
    settings = get_settings()
    found = await retrieve(
        queries,
        articles=articles,
        top_sections=settings.retrieval_top_sections,
        char_budget=settings.retrieval_context_token_budget * 4,
    )
    rows = [s.to_dict() for s in found]
    body = _render(rows, settings.retrieval_context_token_budget * 4)
    return (
        f"{_quality(rows)}\n\n{body}".strip(),
        {"retrieved_sections": rows},
    )


async def _get_section(arguments: dict[str, Any], state: dict) -> tuple[str, dict]:
    section_id = corpus.normalize_section(str(arguments.get("section") or ""))
    if not section_id:
        return "get_section needs a section id such as '11-134'.", {}
    found = await retrieve([], must_include_sections=[section_id], follow_cross_references=0)
    rows = [s.to_dict() for s in found]
    if not rows:
        return (
            f"Section {section_id} is not in the indexed corpus. "
            "Check the number, or use outline to see what the Article contains.",
            {},
        )
    return _render(rows, get_settings().retrieval_context_token_budget * 4), {
        "retrieved_sections": rows
    }


async def _outline(arguments: dict[str, Any], state: dict) -> tuple[str, dict]:
    article = str(arguments.get("article") or "").strip()
    if not article:
        return "outline needs an article number, e.g. '11'.", {}
    nodes = await corpus.outline(article)
    if not nodes:
        return f"Article {article} has no indexed sections.", {}
    listing = "\n".join(f"  {n.section}  {n.title}" for n in nodes)
    return (
        f"Article {article} has {len(nodes)} sections. Their headings, in order:\n{listing}\n\n"
        "These are headings only. Use get_section to read one.",
        {},
    )


async def _related(arguments: dict[str, Any], state: dict) -> tuple[str, dict]:
    section_id = corpus.normalize_section(str(arguments.get("section") or ""))
    direction = str(arguments.get("direction") or "cites")
    if not section_id:
        return "related_sections needs a section id such as '11-134'.", {}
    try:
        related = await corpus.related_sections(section_id, direction)
    except ValueError as exc:
        return f"{exc}", {}
    if not related:
        which = "depends on nothing" if direction == "cites" else "is named by nothing"
        return f"Section {section_id} {which} else in the Code.", {}
    verb = "names" if direction == "cites" else "is named by"
    return (
        f"Section {section_id} {verb}: {', '.join(related)}.\n"
        "These are section numbers only. Use get_section to read one.",
        {},
    )


def _scenario(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """The scenario the member named, if any.

    An offset is carried unresolved on purpose: `_resolve_retirement_age` turns
    "two more years" into an age against the member's *real* age at execution
    time, and guessing it here would put a number on a fact this layer does not
    have.
    """
    age = arguments.get("retirement_age")
    offset = arguments.get("years_from_now")
    if age is not None:
        return [{"label": f"at age {int(age)}", "retirement_age": int(age)}]
    if offset is not None:
        return [{"label": f"{int(offset)} more years", "years_from_now": int(offset)}]
    return []


def _describe(
    result: dict[str, Any],
    missing: list[dict],
    blocked: Any,
    errors: list,
    resolved: dict[str, Any] | None = None,
) -> str:
    """What to tell the model, whichever of the three things happened."""
    lines: list[str] = []
    for formula_type, entry in (result or {}).items():
        base = (entry or {}).get("base") or {}
        if base.get("ok"):
            lines.append(
                f"{formula_type}: {base.get('annual_amount')} a year "
                f"({base.get('monthly_amount')} a month) under Section "
                f"{entry.get('section')}."
            )
            # `_present` fills inputs_used; fall back to what was resolved, so
            # the model always knows what the figure rests on and can say so.
            used = base.get("inputs_used") or resolved or {}
            if used:
                lines.append(
                    "  computed on: " + ", ".join(f"{k}={v}" for k, v in used.items())
                )
            for variant in entry.get("scenarios") or []:
                lines.append(
                    f"  {variant.get('label')}: {variant.get('annual_amount')} a year"
                )
    if missing:
        lines.append(
            "Could not compute: missing "
            + "; ".join(str(m.get("ask_for") or m.get("name")) for m in missing)
            + ". Ask the member for what is actually needed, and nothing else."
        )
    if blocked:
        detail = "; ".join(str(e.get("detail") or e) for e in (errors or []))
        lines.append(f"Blocked: {blocked}. {detail}".strip())
    return chr(10).join(lines) or "No figure was produced and no reason was given."


async def _compute(arguments: dict[str, Any], state: dict) -> tuple[str, dict]:
    benefit = str(arguments.get("benefit") or "")
    if benefit not in BENEFIT_TYPES:
        return (
            f"There is no benefit called {benefit!r}. Available: "
            + ", ".join(BENEFIT_TYPES)
            + ".",
            {},
        )

    # A slice of the turn's state, plus what the model asked for. The four nodes
    # run in this order because it is a correctness constraint: resolve_inputs
    # needs the recipe to know which inputs exist, and execute needs the
    # validator's verdict.
    slice_: dict[str, Any] = {
        **state,
        "requires_calculation": True,
        "formula_types_needed": [benefit],
        "scenarios": _scenario(arguments),
    }
    update: dict[str, Any] = {}
    for node in (resolve_recipes, resolve_inputs, validate_recipes, execute_calculations):
        produced = await node(slice_)  # type: ignore[arg-type]
        slice_.update(produced)
        update.update(produced)

    return (
        _describe(
            update.get("calculation_result") or {},
            update.get("missing_inputs") or [],
            update.get("execution_blocked"),
            update.get("validation_errors") or [],
            update.get("resolved_inputs") or {},
        ),
        update,
    )


_HANDLERS = {
    "search_statute": _search,
    "compute_benefit": _compute,
    "get_section": _get_section,
    "outline": _outline,
    "related_sections": _related,
}


async def run_tool(name: str, arguments: dict[str, Any], state: dict) -> tuple[str, dict]:
    """Run one tool, returning what to tell the model and what to write to state.

    Never raises. A tool that blows up mid-turn takes the member's answer with
    it, and every failure here is one the model can respond to by trying
    something else.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"There is no tool called {name!r}. Available: {', '.join(TOOL_NAMES)}.", {}
    try:
        return await handler(arguments or {}, state)
    except Exception as exc:  # noqa: BLE001 - reported to the model, not raised
        logger.exception("Tool %s failed", name)
        return f"{name} failed: {exc}. Try a different approach.", {}
