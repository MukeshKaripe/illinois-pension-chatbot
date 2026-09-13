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
from pension_chat.agent.retrieval import retrieve
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

TOOL_NAMES = tuple(tool["function"]["name"] for tool in RETRIEVAL_TOOLS)


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


_HANDLERS = {
    "search_statute": _search,
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
