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

import asyncio
import logging
import re
from typing import Any

from pension_chat.agent import corpus, gaps, memory
from pension_chat.agent import coverage as coverage_mod
from pension_chat.agent.nodes import (
    BENEFIT_TYPES,
    execute_calculations,
    resolve_inputs,
    resolve_recipes,
    validate_recipes,
)
from pension_chat.agent.retrieval import retrieve, retrieve_coverage
from pension_chat.agent.worksheet import SOURCE_STAND_IN, SOURCE_STATED, WorkSheet
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
                    "stand_in_facts": {
                        "type": "boolean",
                        "description": (
                            "Set this ONLY when the member is supposing a different "
                            "job and asked for their own figures to be used under it "
                            "-- 'if I were a Chicago police officer with my salary "
                            "and service'. Their record is filed under another "
                            "Article, so by default none of it is used and every "
                            "input comes back missing. This borrows their salary and "
                            "service as clearly-labelled stand-ins. It does nothing "
                            "outside a supposition, and you must say in the answer "
                            "that the figures are their own values under another "
                            "fund's formula."
                        ),
                    },
                },
                "required": ["benefit"],
            },
        },
    },
]

#: **Withdrawn from the loop on 2026-09-14, and kept here as the record of why.**
#:
#: Added the same day to fix G17 -- one case, where the loop read an outline,
#: picked a provision by its heading, and then computed from a different one.
#: It did fix that. The full suite, run for the first time on the loop
#: afterwards, found it had caused or contributed to eight failures including
#: five CRITICAL ones, and it was called on eight of the nine.
#:
#: Two harms, and neither yielded to prose. It hands the model a section number
#: **with no text**, and the model cites it -- UNVERIFIED_CITATION on 12-131,
#: from a turn that called nothing else. The result already said "this names the
#: provision, it does not put its text in front of you"; that is a warning, not
#: a mechanism. And it *satisfies*: answering "which provision" feels like the
#: hard part, so turns stopped there without computing. A hand-off sentence
#: ("if the member asked what they would GET, call compute_benefit now") did not
#: stop that either.
#:
#: The lesson is about the shape, not the wording: a tool that can be called
#: **instead of reading** will be. If this comes back it belongs inside
#: `search_statute`'s result -- provision and text together, where naming one
#: cannot be an alternative to having read it.
_WITHDRAWN_PROVISION_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "find_provision",
            "description": (
                "Which provision of the Code governs a benefit for this member, "
                "and why that one rather than the ones beside it -- without "
                "computing anything. Use it when the member asks which rule "
                "applies, why you chose it, or what their options are, and "
                "especially when they have said not to calculate yet. An "
                "Article's outline gives you headings; this gives you the "
                "provision the engine would actually use, so what you name here "
                "is what a later figure will rest on."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "benefit": {
                        "type": "string",
                        "enum": list(BENEFIT_TYPES),
                        "description": "Which benefit to find the provision for.",
                    }
                },
                "required": ["benefit"],
            },
        },
    },
]

COVERAGE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_coverage",
            "description": (
                "Work out which Article of the Code covers someone described a "
                "particular way, by matching the description against the Code's own "
                "coverage tests. Call this whenever the member says what they do for "
                "a living, or supposes a different job -- their record is a prior, "
                "not an oracle, and a description you place yourself is a guess. "
                "It also tells you whether the answer may state their coverage as a "
                "fact, or must attribute it to their file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            "How they described the job, in their own words -- "
                            "'a park employee', 'a Chicago police officer'. Do not "
                            "translate it into the statute's vocabulary; that is what "
                            "this does."
                        ),
                    },
                    "act": {
                        "type": "string",
                        "enum": [
                            coverage_mod.ASSERTED,
                            coverage_mod.STIPULATED,
                            coverage_mod.DOUBTED,
                        ],
                        "description": (
                            "What they did with the description. 'asserted': they say "
                            "it is so ('I am a park employee'). 'stipulated': they are "
                            "supposing it ('if I were a park employee'). 'doubted': "
                            "they are unsure or asking you ('I think I'm a park "
                            "employee', 'am I a park employee?'). These are different "
                            "acts, not degrees of confidence."
                        ),
                    },
                },
                "required": ["description", "act"],
            },
        },
    },
]

TOOL_NAMES = tuple(
    tool["function"]["name"]
    for tool in RETRIEVAL_TOOLS + CALCULATION_TOOLS + COVERAGE_TOOLS
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

    # Enrichment, never a filter. The search below runs its own query and
    # returns what it found; the hint is a line of text beside the results. A
    # section that once got lucky therefore cannot entrench itself by
    # suppressing the search that would have found a better one, which is the
    # failure mode every retrieval cache has.
    #
    # Started *with* the search rather than after it, and that independence is
    # exactly why it can be: a hint that cannot narrow the search has nothing to
    # learn from its results. Awaited second, it cost a Postgres round trip plus
    # an embedding on the member's critical path -- 0.7s to 4.8s of a 20s
    # time-to-first-token, for a line the answer does not depend on.
    coverage = state.get("coverage") or {}
    found, hint = await asyncio.gather(
        retrieve(
            queries,
            articles=articles,
            top_sections=settings.retrieval_top_sections,
            char_budget=settings.retrieval_context_token_budget * 4,
        ),
        memory.recall(
            str(state.get("question") or " ".join(queries)),
            article=str(coverage.get("article") or "") or None,
            tier=(state.get("member_facts") or {}).get("tier"),
        ),
        # The hint is optional and the sections are not, so a hint that raises
        # must not take the search down with it. `recall` swallows its own
        # failures today; concurrency is not the place to rely on that.
        return_exceptions=True,
    )
    if isinstance(found, BaseException):
        raise found
    if isinstance(hint, BaseException):
        logger.warning("Could not recall anything beside the search: %s", hint)
        hint = []

    rows = [s.to_dict() for s in found]
    body = _render(rows, settings.retrieval_context_token_budget * 4)
    remembered = (
        "Sections that previously answered questions like this, and whose "
        f"answers were verified: {', '.join(hint)}. Read one if it looks "
        "relevant; it is a hint, not a finding."
        if hint
        else ""
    )

    return (
        "\n\n".join(p for p in (_quality(rows), remembered, body) if p).strip(),
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


#: Why no provision could be resolved, when the answer is "you have not read
#: anything yet". Both calculation tools fall back to *deriving* a recipe from
#: the statute retrieved so far, and the model tends to reach for them early --
#: so derivation runs against an empty corpus and returns nothing, leaving the
#: turn with a provision named from an outline and no figure.
#:
#: Reported rather than required. A recipe already in the registry needs no
#: retrieval at all, and refusing to compute until something had been searched
#: would break every reuse -- which is most turns, and the case the registry
#: exists for. The precondition belongs to derivation, not to computing.
_NOTHING_READ = (
    "You have not read any statute text yet, so there was nothing to derive a "
    "calculation from and none was already on file. Use search_statute first "
    "-- for this member's own Article -- and then ask again."
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
    origins: dict[str, str] | None = None,
    stale: list[str] | None = None,
    revised: dict[str, Any] | None = None,
    benefit: str = "this benefit",
    read_anything: bool = True,
    verdict: str = "",
) -> str:
    """What to tell the model, whichever of the three things happened.

    ``verdict`` is `nodes.gap_verdict`'s reading of what this turn's gaps
    allow, counted in code from the rank each input resolved at. It decides
    which of two very different things a gap means: a routing field nobody can
    supply is a question, and a value nobody can supply is still a provision
    worth working through on the Code's own figures. Both used to come out as
    "Could not compute: missing X. Ask the member."
    """
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
            borrowed = [
                name
                for name, origin in (origins or {}).items()
                if origin == SOURCE_STAND_IN and name in used
            ]
            if borrowed:
                # The figure is real and comparable; whose values it rests on is
                # the part an answer can quietly drop, and dropping it turns
                # "their numbers under another fund's formula" into "their
                # pension".
                lines.append(
                    "  standing in from their own record, at their request: "
                    + ", ".join(sorted(borrowed))
                    + ". Say so in the answer -- these are their values under "
                    "another fund's formula, not their entitlement."
                )
            for variant in entry.get("scenarios") or []:
                lines.append(
                    f"  {variant.get('label')}: {variant.get('annual_amount')} a year"
                )
    if missing:
        governing, value = gaps.split(missing)
        asks = "; ".join(str(m.get("ask_for") or m.get("name")) for m in governing)
        wants = "; ".join(str(m.get("ask_for") or m.get("name")) for m in value)
        if verdict == "ask" and governing:
            lines.append(
                f"Cannot pick the provision: {asks} decides which rule applies, "
                "not what it computes. Either ask the member for it, or -- where "
                "there are only two possibilities -- compute both and compare "
                "them, saying which applies in which case. Do not pick one."
            )
            if value:
                lines.append(f"  also missing: {wants}.")
        elif verdict == "worked_example":
            lines.append(
                f"Not enough to compute this member's own figure: missing {wants}. "
                "Do NOT stop at saying so. Set out how the provision computes the "
                "benefit, work it through on the figures the Code itself states, "
                "and then say exactly what you would need to make it theirs."
            )
        else:
            lines.append(
                "Could not compute: missing "
                + "; ".join(str(m.get("ask_for") or m.get("name")) for m in missing)
                + ". Ask the member for what is actually needed, and nothing else."
            )
    if blocked:
        detail = "; ".join(str(e.get("detail") or e) for e in (errors or []))
        lines.append(f"Blocked: {blocked}. {detail}".strip())
    if stale:
        # Without this the recomputation reads as a fresh question, and the
        # member who corrected themselves is never told what their correction
        # moved.
        lines.append(
            "This CORRECTS an earlier figure. The member revised "
            + ", ".join(f"{k} to {v}" for k, v in sorted((revised or {}).items()))
            + f", which supersedes the figure you gave as {', '.join(stale)}. Say "
            "what changed and what it moved the number to -- do not answer as "
            "though this were a new question."
        )
    if not lines:
        # F02 and F18: the chain ran, produced nothing, and named no missing
        # input and no block, so the turn ended with neither a figure nor a
        # reason. "No provision could be resolved" is a reason, and it is one
        # the member is owed rather than one to swallow.
        if not read_anything:
            return _NOTHING_READ
        return (
            f"No figure for {benefit}. No provision could be resolved for this "
            "member, so there was nothing to compute -- say that you could not "
            "establish which rule governs, and do not name one from an outline."
        )
    return chr(10).join(lines)


def _revised(update: dict[str, Any]) -> dict[str, Any]:
    """The values this computation took from the member rather than their file.

    Derived from what was actually used, not asked of the model. A correction
    the model forgets to declare is a correction that silently does not happen,
    and `input_sources` already records exactly this as each input resolves.
    """
    origins = update.get("input_sources") or {}
    resolved = update.get("resolved_inputs") or {}
    return {
        name: value
        for name, value in resolved.items()
        if origins.get(name) == SOURCE_STATED
    }


def _supersede(
    state: dict[str, Any], update: dict[str, Any], scenario: list[dict[str, Any]]
) -> list[str]:
    """Mark the figures a value the member just corrected was built on.

    R03, and it is CRITICAL. "Actually it's more like 23 years, I forgot the
    first two" reaches the engine on its own -- `resolve_inputs` reads it out of
    the conversation and recomputes -- and nothing said that the figure from two
    messages ago had been replaced, so the answer read as though a fresh
    question had been asked.

    The pipeline does this in `understand_turn`, which the loop does not run.
    `WorkSheet.apply_revisions` is the whole mechanism and needed nothing new:
    it records the corrected value, expands aliases (the member says
    "service_years"; the recipe consumed "creditable_service_years"), and
    returns the labels of every figure that rested on it. A value restated
    unchanged moves nothing, and a value that came from the record was not
    corrected by anybody.
    """
    stale: set[str] = set()
    turn = int(state.get("turn_index") or 0)

    revised = _revised(update)
    if revised:
        sheet = WorkSheet.from_state({**state, **update})
        if sheet.figures:
            stale |= sheet.apply_revisions(revised, turn)

    stale |= _scenario_moved(state, scenario, turn)
    return sorted(stale)


def _scenario_kind(label: str) -> str:
    """Which sort of scenario a label names, or "" for neither."""
    if label.startswith("at age "):
        return "age"
    if label.endswith(" more years"):
        return "offset"
    return ""


def _scenario_moved(
    state: dict[str, Any], scenario: list[dict[str, Any]], turn: int
) -> set[str]:
    """Figures whose scenario the member has just moved.

    L04. The loop recomputed at three years and answered correctly, and never
    mentioned the two-year figure it had given one message earlier -- so a
    correction read as a fresh question. "Actually make that three years" moves
    the scenario; the figure built on the old one is no longer theirs.

    Three conditions, and each rules out something that looks the same:

    - **Same kind.** "And what about at 60?" *adds* a comparison to "in two
      years"; only a scenario of the same kind replaces one.
    - **Different value.** Asking the same question again is not correcting
      the answer.
    - **An earlier turn.** R04 asks for retire-now, 58 and 60 side by side,
      which is three calls on one turn. A comparison is not three successive
      corrections.
    """
    if not scenario:
        return set()
    label = str(scenario[0].get("label") or "")
    kind = _scenario_kind(label)
    if not kind:
        return set()

    moved: set[str] = set()
    for standing, figure in (state.get("scenario_set") or {}).items():
        if not isinstance(figure, dict):
            continue
        if int(figure.get("turn") or 0) >= turn:
            continue
        if _scenario_kind(str(standing)) == kind and str(standing) != label:
            moved.add(str(standing))
    return moved


#: The member offering their own file to another fund's formula, in their own
#: words. Not a reading of what they meant -- `_invited_stand_ins` checks that
#: the words are there, and the model still decides whether they were an
#: invitation. What it names is deliberately narrow: a value the *record*
#: supplies. "how would my pension be calculated" names the benefit, not an
#: input, and is the sentence G38 borrowed a whole salary history on.
_INVITES_STAND_INS = re.compile(
    r"\b(?:my|our|(?:the\s+)?same)\s+(?:own\s+)?(?:current\s+|actual\s+|real\s+)?"
    r"(?:salary|salaries|wage|wages|pay|earnings|compensation|"
    r"service|seniority|years|numbers|figures|record|credits?|contributions?)\b",
    re.IGNORECASE,
)


def _invited_stand_ins(state: dict[str, Any]) -> bool:
    """Did the member offer their own salary and service to a supposed job?

    G38 and G39, both CRITICAL. The schema tells the model to set
    `stand_in_facts` "ONLY when the member ... asked for their own figures to be
    used under it", and asked how a park employee's pension is calculated it set
    the flag anyway and quoted the member's own Article 11 annuity under Article
    12's formula. A paragraph of schema prose is a warning, not a mechanism --
    the same lesson `find_provision` was withdrawn for on the same day.

    So the flag is corroborated rather than trusted, exactly as
    `scenarios.corroborated` corroborates a scenario the planner carried
    forward: the model reads the invitation, and this checks the member
    actually made it. A regex may accuse, supply data, or check provenance; it
    may not decide what the member meant, and deciding is still the model's.

    Two places the words may be, and both are the member's own. The message in
    front of us, and the premise they set up -- which holds across the turns
    that explore it, so an invitation given when the supposition was built is
    still theirs on the turn after. Without the second, exploring a premise
    properly set up costs them the figure on every turn but the first.
    """
    words = str(state.get("question") or "")
    frame = state.get("hypothetical_frame") or {}
    premise = str(frame.get("premise") or "")
    return bool(_INVITES_STAND_INS.search(words) or _INVITES_STAND_INS.search(premise))


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
        # Per call, not durable. `_stand_ins_allowed` already refuses it outside
        # a supposition, and the model re-reads the member's invitation on every
        # computation rather than inheriting one from three turns ago -- and
        # `_invited_stand_ins` checks the invitation is really in the member's
        # words before any of their record reaches another fund's formula.
        "stand_in_facts": bool(arguments.get("stand_in_facts"))
        and _invited_stand_ins(state),
    }
    update: dict[str, Any] = {}
    for node in (resolve_recipes, resolve_inputs, validate_recipes, execute_calculations):
        produced = await node(slice_)  # type: ignore[arg-type]
        slice_.update(produced)
        update.update(produced)

    stale = _supersede(state, update, slice_["scenarios"])
    update["stale_figures"] = stale

    return (
        _describe(
            update.get("calculation_result") or {},
            update.get("missing_inputs") or [],
            update.get("execution_blocked"),
            update.get("validation_errors") or [],
            update.get("resolved_inputs") or {},
            update.get("input_sources") or {},
            stale,
            _revised(update),
            benefit,
            bool(state.get("retrieved_sections")),
            str(update.get("gap_verdict") or ""),
        ),
        update,
    )


#: The acts a description can be performed with. Kept as a tuple read by both
#: the schema's enum and the handler's check, so an act the model is offered
#: always has a branch in `adjudicate` -- one that did not would be read
#: silently as an assertion, which is the worst of the three to be wrong about.
_ACTS = (coverage_mod.ASSERTED, coverage_mod.STIPULATED, coverage_mod.DOUBTED)

#: As `nodes._MAX_COVERAGE_SECTIONS`: every section a verdict rests on is made
#: readable, capped so a broad description cannot flood the answer's context.
_MAX_COVERAGE_SECTIONS = 6


def _candidate_line(candidate: dict[str, Any]) -> str:
    test = str(candidate.get("test") or "").strip()
    line = f"  Article {candidate.get('article')} -- Section {candidate.get('section')}"
    return f"{line}: {test}" if test else line


def _coverage_text(verdict: dict[str, Any], description: str, evidence: str) -> str:
    """The verdict, put to the model as the determination it is.

    Each branch says two things: which Article to read from, and what the answer
    is allowed to claim about it. The second is what `guardrails._verify_coverage`
    polices -- an answer may state coverage as a fact only on a confirmed or
    statutory basis, and must attribute it to the file otherwise.
    """
    basis = verdict["basis"]
    article = verdict.get("article")
    record = verdict.get("record_article")
    candidates = verdict.get("candidates") or []
    pointed = ", ".join(dict.fromkeys(str(c.get("article")) for c in candidates))
    lines: list[str] = []

    if basis == coverage_mod.BASIS_HYPOTHETICAL:
        if article:
            elsewhere = (
                f"Article {record}, which is the member's own"
                if record
                else "the member's own Article"
            )
            lines.append(
                f"Premise adopted: someone described as {description!r} is covered by "
                f"Article {article}. Read Article {article} from here, not {elsewhere}."
            )
        else:
            lines.append(
                f"Premise adopted, but no coverage test in the Code matched "
                f"{description!r}, so it cannot be placed in an Article. Do not answer "
                "it out of the member's own Article -- say which fund you could not "
                "identify."
            )
        lines.append(
            "This is a supposition. Nothing about the member has changed, so every "
            "sentence resting on it stays conditional."
        )
    elif basis == coverage_mod.BASIS_STATUTORY:
        agrees = (
            ", which is where their record places them too" if record else ""
        )
        lines.append(
            f"The Code's own coverage test places {description!r} in Article "
            f"{article}{agrees}. You may state this as a fact about them."
        )
    elif basis == coverage_mod.BASIS_UNRESOLVED:
        lines.append(
            "There is no record to fall back on, and the description "
            + (
                f"matches more than one Article ({pointed})."
                if pointed
                else "matched no coverage test in the Code."
            )
        )
        lines.append(
            "Ask which employer pays them before saying which fund governs, and "
            "answer what you can from the Code meanwhile."
        )
    elif verdict.get("conflict"):
        if verdict.get("why") == "doubted":
            lines.append(
                "They are not sure themselves, which outranks every other kind of "
                f"uncertainty here. Their record says Article {record}"
                + (
                    f"; the Code's coverage tests for {description!r} point at "
                    f"Article {pointed}."
                    if pointed
                    else "."
                )
            )
        else:
            lines.append(
                f"Their own words point at Article {pointed}; their record says Article "
                f"{record}. The record holds until they settle it, so attribute Article "
                f"{record} to their file rather than stating it as law."
            )
        lines.append(
            "Ask them the one thing that separates the two -- usually which employer "
            "pays them -- and answer what you can meanwhile."
        )
    elif verdict.get("why") == "unprompted":
        # The description came from the question's subject matter, not from the
        # member -- so there is nobody to be in conflict with. Say which Article
        # the law belongs to, and keep it separate from who they are.
        lines.append(
            f"Nothing the member said describes them, so this is a question "
            f"about the law rather than about them. {description!r} is placed by "
            f"the Code in Article {pointed or 'no Article that was retrieved'}; "
            f"their own file says Article {record}. Answer what that law provides "
            "and do not ask them to settle a disagreement they did not raise."
        )
    elif verdict.get("why") == "budget_spent":
        lines.append(
            "You have already asked this member as many questions as you may. "
            f"Answer on their record, Article {record}, and say in one line which "
            "reading you went with and that they can correct it. Do not ask again."
        )
    else:
        lines.append(
            f"{description!r} matched no coverage test in the Code. Falling back to "
            f"their record, Article {record}: attribute that to their file rather than "
            "saying their job puts them there."
        )

    if candidates:
        lines.append("Grounded in:")
        lines.extend(_candidate_line(c) for c in candidates)
    if evidence:
        lines.append("")
        lines.append(evidence)
    return chr(10).join(lines)


async def _check_coverage(
    arguments: dict[str, Any], state: dict[str, Any]
) -> tuple[str, dict]:
    description = str(arguments.get("description") or "").strip()
    if not description:
        return (
            "check_coverage needs a `description` -- the member's own words for the job.",
            {},
        )
    act = str(arguments.get("act") or "").strip()
    if act not in _ACTS:
        return f"There is no act called {act!r}. Use one of: " + ", ".join(_ACTS) + ".", {}

    employee = state.get("employee_record")
    record_article = str(employee["article_number"]) if employee else None
    turn = state.get("turn_index") or 0

    # ── Already answered? ────────────────────────────────────────
    #
    # A confirmation the member gave outranks a fresh assessment of the same
    # words. `assess_coverage` cannot reach this problem -- it binds the reply
    # and returns, never running the assessment again -- but here the model
    # chooses when to call, and calling twice re-adjudicated from nothing and
    # put the conflict back one turn after the member had settled it. Their own
    # answer is not something to re-derive.
    #
    # Narrow on purpose: only an assertion, and only while the description is
    # still about the settled question. Supposing a different job is a new
    # question and gets a new determination.
    settled = state.get("coverage") or {}
    if (
        act == coverage_mod.ASSERTED
        and settled.get("basis") == coverage_mod.BASIS_CONFIRMED
        and settled.get("article")
    ):
        return (
            f"Already settled: the member confirmed Article {settled['article']} "
            f"({settled.get('confirmed_by') or 'they told you'}). Do not ask again "
            "and do not re-open it. You may state this as a fact about them.",
            {"coverage": settled, "candidate_articles": [str(settled["article"])]},
        )

    # The member's own Article goes in alongside whatever their description
    # ranked: the assessment is being asked to weigh a description against a
    # record, and cannot do that having read only one side.
    sections = await retrieve_coverage(description, also_covering=record_article)
    report = await coverage_mod.assess(description, sections)
    reference = coverage_mod.SelfReference(text=description, act=act)
    verdict = coverage_mod.adjudicate(reference, report, record_article, turn=turn)

    # Every section the verdict rests on becomes readable, because a verdict the
    # answer may not cite is a verdict the answer cannot explain -- and citing an
    # unread section is what verification exists to refuse.
    by_id = {s.section: s for s in sections}
    grounding: list[dict[str, Any]] = []
    for candidate in verdict.get("candidates") or []:
        row = by_id.get(str(candidate.get("section") or ""))
        if row is not None and all(g["section"] != row.section for g in grounding):
            grounding.append(row.to_dict())
    grounding = grounding[:_MAX_COVERAGE_SECTIONS]

    update: dict[str, Any] = {
        "coverage": verdict,
        "self_reference": reference.to_dict(),
        "coverage_sections": [g["section"] for g in grounding],
        "candidate_articles": [verdict["article"]] if verdict.get("article") else [],
    }
    if grounding:
        update["retrieved_sections"] = grounding
    if verdict["basis"] == coverage_mod.BASIS_HYPOTHETICAL:
        # Adopted whole, and durable: "and what if I had 30 years?" is still
        # inside the premise. The frame is what keeps later turns under it and
        # what starves the record-derived facts a supposition may not borrow.
        update["hypothetical_frame"] = {
            "premise": description,
            "article": verdict.get("article"),
            "entered_turn": turn,
            "last_used_turn": turn,
        }

    # ── A verdict with consequences ──────────────────────────────
    #
    # Returning the verdict as text was not enough: the loop read "their words
    # point at Article 12, ask them which employer pays them", computed a figure
    # from Article 11 anyway, and answered. `assess_coverage` does not persuade
    # the pipeline of anything -- it writes the keys that make the turn ask, and
    # `check_coverage` writes the same ones.
    settings = get_settings()

    # ── A question is put to the member, so the member must have spoken ──
    #
    # Case 16 and G30's twelfth turn, both CRITICAL. "An employee is covered by
    # Article 12 ... what retirement provisions should the system consider?" is
    # a question about the law, and "re-run the applicability analysis without
    # changing my facts" is a question about the analysis. Neither describes the
    # member; the loop called this tool on both, the verdict disagreed with
    # their file, and the whole answer came back "Which employer pays you?" --
    # once as the entire answer to case 16, and once twelve turns into a
    # conversation whose coverage had been settled throughout.
    #
    # `assess_coverage` cannot do this: it fast-exits when
    # `classify_self_reference` finds no self-description, and neither question
    # has one. Narrow on purpose -- the model may still call the tool, because
    # noticing a self-description a regex misses is the loop's own advantage and
    # the reason coverage is a tool here rather than a stage. What needs the
    # member to have spoken is the one act that speaks back to them: turning a
    # disagreement with their file into a question put to them. Which Article
    # the law belongs to, and the sections the verdict rests on, are facts about
    # the Code and stand either way.
    spoke = coverage_mod.classify_self_reference(str(state.get("question") or "")) is not None
    if verdict.get("conflict") and not spoke:
        logger.info(
            "Coverage conflict on a turn where nobody described themselves; "
            "not asking (described_as=%r)",
            description,
        )
        verdict = {**verdict, "conflict": False, "why": "unprompted"}
        update["coverage"] = verdict

    if verdict.get("conflict"):
        asked = int(state.get("clarifications_asked") or 0)
        if asked >= settings.clarification_budget:
            # Proceed on the record and say so. Asking a fourth time is its own
            # failure, and one the member has no way out of.
            verdict = {**verdict, "conflict": False, "why": "budget_spent"}
            update["coverage"] = verdict
        else:
            asking = coverage_mod.open_question_for(verdict)
            update["clarification"] = asking
            update["open_question"] = asking
            update["clarifications_asked"] = 1
            # Every candidate and the record's own Article, so the question can
            # quote the coverage test it turns on instead of asserting it.
            update["candidate_articles"] = [
                a
                for a in dict.fromkeys(
                    str(o.get("article")) for o in asking.get("options") or []
                )
                if a and a != "None"
            ]

    evidence = _render(grounding, settings.retrieval_context_token_budget * 4)
    return _coverage_text(verdict, description, evidence), update


async def _find_provision(arguments: dict[str, Any], state: dict) -> tuple[str, dict]:
    """Resolve the controlling provision and stop there.

    G17. "Which retirement provision applies to me? Do not calculate anything
    yet." The loop had no way to ask that: it read Article 11's outline, chose
    four sections by their headings, and named 11-130 -- then computed from
    11-134 two turns later, so "the provision you selected earlier" was not the
    one it used. The pipeline never has this problem because `resolve_recipes`
    answers exactly this question; the loop could only reach it by also
    computing, which the member had just asked it not to do.

    So this is `compute_benefit` stopped after its first node. What it names is
    what a later figure will rest on, because it is resolved by the same code
    that will produce it.
    """
    benefit = str(arguments.get("benefit") or "")
    if benefit not in BENEFIT_TYPES:
        return (
            f"There is no benefit called {benefit!r}. Available: "
            + ", ".join(BENEFIT_TYPES)
            + ".",
            {},
        )

    slice_: dict[str, Any] = {
        **state,
        "requires_calculation": True,
        "formula_types_needed": [benefit],
        "scenarios": [],
    }
    update = await resolve_recipes(slice_)  # type: ignore[arg-type]
    recipe = (update.get("recipes") or {}).get(benefit)
    if not recipe:
        if not (state.get("retrieved_sections") or []):
            return _NOTHING_READ, update
        return (
            f"No provision for {benefit} could be resolved for this member. Say "
            "what you could not establish rather than naming one from an outline.",
            update,
        )

    lines = [
        f"{benefit} is governed by Section {recipe.get('section')}"
        + (f"({recipe['controlling_subsection']})" if recipe.get("controlling_subsection") else "")
        + "."
    ]
    if basis := recipe.get("selection_basis"):
        lines.append(f"  chosen because: {basis}")
    if applicability := recipe.get("applicability"):
        lines.append(f"  it reaches: {applicability}")
    for alternative in (recipe.get("alternatives_considered") or [])[:4]:
        lines.append(
            f"  not Section {alternative.get('section')}: "
            f"{alternative.get('why_not') or 'set aside'}"
        )
    lines.append(
        "Nothing was computed. Read the section before describing it -- this "
        "names the provision, it does not put its text in front of you. And if "
        "the member asked what they would GET rather than which rule applies, "
        "call compute_benefit now: a provision is not a figure, and answering "
        "with one where the other was wanted leaves them with neither."
    )
    return chr(10).join(lines), update


_HANDLERS = {
    "search_statute": _search,
    "check_coverage": _check_coverage,
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
