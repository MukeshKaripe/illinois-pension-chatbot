"""The investigation: one mind, deciding what it needs and when it has enough.

The pipeline wrote its search queries in `understand_turn`, before any statute
had been seen, and could not revise them once results came back. That is the
root of "I don't have enough information": one query, no second look. Here the
model reads what it got and decides what to do next, and the conversation it
built is the same one the answer is written from.

Bounded by resource, not by iteration count. A fixed iteration cap is itself a
semantic constraint -- "compare my options at 58, 60 and 62 with the reduction
and the survivor election" legitimately needs more thinking than "what is
Article 11?" -- so the budgets below cap the expensive things and leave the
thinking alone. Spending one **withdraws that tool and says why**; it does not
end the turn, because a loop out of searches can still read a section it
already knows about and answer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pension_chat.agent import coverage as coverage_mod
from pension_chat.agent.blocks import render_assumptions, render_thresholds
from pension_chat.agent.llm import call_with_tools
from pension_chat.agent.nodes import (
    ANSWER_SYSTEM,
    _available_facts,
    _coverage_unresolved,
    _governing_article,
    _record_coverage,
)
from pension_chat.agent.presentation import select_rung
from pension_chat.agent.registry import recipes_for
from pension_chat.agent.retrieval import (
    CROSS_CUTTING_ARTICLES,
    extract_article_references,
)
from pension_chat.agent.tools import (
    CALCULATION_TOOLS,
    COVERAGE_TOOLS,
    RETRIEVAL_TOOLS,
    run_tool,
)
from pension_chat.agent.worksheet import WorkSheet
from pension_chat.config import get_settings
from pension_chat.funds import FUNDS

logger = logging.getLogger("pension_chat.agent.loop")

#: What the loop may spend. `total` and `seconds` end the turn; a per-tool
#: budget only withdraws that tool.
BUDGETS: dict[str, int] = {
    "total": 20,
    "seconds": 90,
    "search_statute": 6,
    "get_section": 10,
    "outline": 4,
    "related_sections": 6,
    "compute_benefit": 4,
    "check_coverage": 3,
}

#: Everything the loop may reach for. Retrieval, calculation and coverage
#: together, because a member asking "how much would I get" is asking one
#: question -- and "if I were a Chicago police officer, what would I get?" is
#: still one question, with all three in it.
LOOP_TOOLS = RETRIEVAL_TOOLS + CALCULATION_TOOLS + COVERAGE_TOOLS

#: How many cross-referenced sections to read before answering. Bounded so one
#: section naming a dozen others cannot flood the answer's context.
MAX_COMPLETION_FETCHES = 3

INVESTIGATE_SYSTEM = """You are researching one member's question about the Illinois
Pension Code (40 ILCS 5/). You are not writing the answer yet -- you are finding
out what the Code says, and you will write the answer yourself once you have.

Work like a lawyer reading a statute, not like a search box.

- The Code has its own vocabulary. A member says "retire early without losing
  too much"; the Code says "annuity reduction" and "withdrawal prior to age 60".
  If a search comes back weak, read an Article's outline to find the words it
  actually uses, then search again.
- A question about what an Article *is* is answered by its outline and its
  opening sections, not by whichever section a search happened to rank first.
- A provision usually leans on others. Follow the sections it names. Ask what
  names *it* -- an exception that changes the answer normally lives elsewhere.
- Which Article governs is a determination, not an assumption. When the member
  describes the **job or employer** that would decide which fund they are in --
  or supposes a different one -- call check_coverage with their own words. Their
  record is a prior, not an oracle: it can disagree with them, and a description
  you place in an Article yourself is a guess with a citation on it. Wait for
  its verdict before you search an Article.
  This is only about which fund. A member correcting a number about themselves
  -- their service, their salary, their age -- has told you nothing about their
  employer, and answering that with check_coverage spends the turn and leaves
  the figure they corrected standing.
- A figure is never yours to work out. Call compute_benefit: it finds the
  controlling provision, resolves the inputs from the record and from what the
  member said, and computes. If it reports a missing input, ask the member for
  that one thing -- not for a list of things, and never for something their
  record already holds.
- Stop when you can answer, not when you are tired. Stop as soon as you can.
- Not every message is a question about the Code. If it is an aside, small talk
  or plainly somebody else's job, do not research it: stop immediately and say
  in one or two sentences that it is outside what you can help with. A long
  refusal is worse than a short one.

When you have enough, stop calling tools and say briefly what you found. Do not
write the member's answer at that point; you will be asked for it next, with the
rules for how it should read."""


def _context(state: dict[str, Any]) -> str:
    """What is known about this member, re-rendered every iteration.

    Not left to be rediscovered from the history: a loop twenty calls deep
    should not be scrolling back for the member's age, and a correction made
    mid-conversation has to take effect immediately rather than compete with a
    stale message further up.
    """
    # `_available_facts`, not `member_facts`: the same rule the calculation
    # engine, the applicability predicates and the verifier all read. Under an
    # Article the member's record is not filed under, their salary and service
    # are administered by another fund -- so they are not merely unhelpful here,
    # they are the wrong basis, and a model handed them reaches for them. That
    # is the bug the first hypothetical answer shipped with: the Article 12
    # formula set out correctly and then run on an Article 11 salary. The
    # pipeline starved this prompt through `prepare_answer`; the loop was
    # rendering `member_facts` raw.
    facts = _available_facts(state)
    lines = []
    if facts:
        known = ", ".join(
            f"{name}={value}"
            for name, value in sorted(facts.items())
            # `employment_status` in the facts describes the member at the
            # annuity start -- separated, because the figure posits withdrawal.
            # This line says what is *on file*, so it reads the record's value.
            if name in {
                "age", "service_years", "tier", "article_number",
                "employment_status_on_record",
            }
        )
        lines.append(f"What is on file for this member: {known or 'nothing usable'}")
    elif state.get("member_facts"):
        # There is a record; this turn may not use it. Saying so is the
        # difference between "we know nothing about you" and "your own figures
        # belong to another fund", and only the second is true.
        lines.append(
            "This member has a record, but it is filed under an Article this "
            "turn is not reading, so their own salary and service are not "
            "available to this answer. Ask for any value you need."
        )
    else:
        lines.append("No member record is available; answer from the Code alone.")
    coverage = state.get("coverage") or {}
    if coverage.get("article"):
        # Whose claim this is, said correctly. Under a supposition the file says
        # one Article and the member supposed another, and calling the supposed
        # one "their record" tells the model the opposite of what happened.
        on_file = coverage.get("record_article")
        if on_file and str(on_file) != str(coverage["article"]):
            lines.append(
                f"Their record places them in Article {on_file}. This turn is "
                f"reading Article {coverage['article']} "
                f"(basis: {coverage.get('basis', 'record')})."
            )
        else:
            lines.append(
                f"Their record places them in Article {coverage['article']} "
                f"(basis: {coverage.get('basis', 'record')})."
            )
    # What has already been said to this member, with what it rests on. The
    # pipeline never had to tell a model this -- `_needs_calculation` routes on
    # `stale_figures` before any model sees the turn -- but the loop's model is
    # the one deciding, and it cannot notice a contradiction it cannot see. R03
    # spent its whole second turn on check_coverage, reading "it's more like 23
    # years" as a statement about who the member is, because nothing in front of
    # it said a figure was standing.
    for label, figure in sorted(_standing_figures(state).items()):
        rests = ", ".join(figure.get("rests_on") or [])
        lines.append(
            f"A figure already given to this member ({label}): "
            f"{figure.get('annual_amount')} a year under Section "
            f"{figure.get('section')}, resting on {rests or 'unrecorded inputs'}. "
            "If this message contradicts one of those values, the member is "
            "correcting you: compute it again and say what changed."
        )

    frame = state.get("hypothetical_frame")
    if frame:
        # A follow-up exploring a premise carries none of its words: "and at 60?"
        # is still a question about being a park employee. The frame holds by
        # default and is left explicitly, so it has to be visible every turn --
        # otherwise the supposition quietly reverts to the record the turn after
        # it was set up.
        where = (
            f"which is Article {frame['article']}"
            if frame.get("article")
            else "which was never placed in an Article"
        )
        lines.append(
            f"This conversation is inside a premise the member supposed: "
            f"{frame.get('premise')!r}, {where}. It holds until they step out of "
            "it, and everything resting on it stays conditional."
        )
    return "\n".join(lines)


def _standing_figures(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Figures earlier turns gave this member, from the durable keys that hold them.

    `graph.turn_record` writes `baseline` and `scenario_set`; `WorkSheet` reads
    the same two. Read here rather than rebuilt, so a figure the member was
    actually shown is the one they can be said to be correcting.
    """
    standing: dict[str, dict[str, Any]] = {}
    baseline = state.get("baseline")
    if isinstance(baseline, dict) and baseline.get("annual_amount"):
        standing["now"] = baseline
    for label, entry in (state.get("scenario_set") or {}).items():
        if isinstance(entry, dict) and entry.get("annual_amount"):
            standing[str(label)] = entry
    return standing


def _available(spent: dict[str, int]) -> list[dict[str, Any]]:
    return [
        tool
        for tool in LOOP_TOOLS
        if spent.get(tool["function"]["name"], 0) < BUDGETS.get(tool["function"]["name"], 0)
    ]


def _merge_sections(existing: list[dict], incoming: list[dict]) -> list[dict]:
    seen = {s.get("section") for s in existing}
    return existing + [s for s in incoming if s.get("section") not in seen]


async def _read_sections(
    targets: list[str], working: dict[str, Any]
) -> list[tuple[str, dict] | None]:
    """Read several known sections in one wait, in the order they were asked for.

    Every caller below is past the point of choosing: the section ids are
    settled, and none of the texts decides whether another is worth fetching.
    Read one after another they cost a round trip each to Azure Search -- about
    a quarter of a second apiece, on the member's time-to-first-token, for
    nothing that could not have happened at once.

    A target that fails comes back as ``None`` rather than ending the turn. Each
    of these fetches is a section being *completed* for the model, not the
    evidence the answer stands on; losing one costs a paragraph's grounding,
    and raising costs the member their answer. What is left unread is already
    reported by `unread_references`.
    """
    if not targets:
        return []
    results = await asyncio.gather(
        *(run_tool("get_section", {"section": target}, working) for target in targets),
        return_exceptions=True,
    )
    read: list[tuple[str, dict] | None] = []
    for target, outcome in zip(targets, results):
        if isinstance(outcome, BaseException):
            logger.warning("Could not read Section %s for the answer: %s", target, outcome)
            read.append(None)
        else:
            read.append(outcome)
    return read


def _reference_weights(sections: list[dict]) -> dict[str, int]:
    """How many of the gathered sections lean on each one they name.

    The same weighting Phase 1 gave `_follow_references`, for the same reason: a
    provision two sections turn on is likelier to be load-bearing than one
    mentioned once in passing.
    """
    read = {s.get("section") for s in sections}
    weights: dict[str, int] = {}
    for section in sections:
        for reference in section.get("referenced_sections") or []:
            target = str(reference).split("(")[0].strip()
            if target and target not in read:
                weights[target] = weights.get(target, 0) + 1
    return weights


def _unread_references(sections: list[dict]) -> list[str]:
    """Cross-references the gathered evidence names but never opened.

    Computed rather than judged. A model asked whether it has seen enough tends
    to say yes; "you relied on 11-134, which computes on service as defined in
    11-216, and you never read 11-216" is a checkable statement about what is
    missing.
    """
    read = {s.get("section") for s in sections}
    named: list[str] = []
    for section in sections:
        for reference in section.get("referenced_sections") or []:
            target = str(reference).split("(")[0].strip()
            if target and target not in read and target not in named:
                named.append(target)
    return named


def _confined_to_premise(
    sections: list[dict], state: dict[str, Any]
) -> tuple[list[dict], list[str]]:
    """Drop other funds' law once a premise has named the fund in question.

    Asked what a Chicago police officer would get, the loop read Article 3 --
    police *outside* Chicago -- in the course of ruling it out, and then cited
    it. Reading it was right. Citing it is the harm the coverage layer exists
    for: one fund's law answering another fund's question, with a real citation
    on it.

    The pipeline cannot make this mistake, because its retrieval filter *is* the
    determination -- a section from another fund can only arrive by the
    determination being wrong. A loop that decides what to read as it reads has
    no filter, so the determination is applied to what it gathered instead:
    under a premise naming an Article, the evidence is that Article and the
    Articles that govern every member whatever fund they are in. The rest was
    investigation, not evidence, and this is enforced rather than asked for --
    `guardrails` verifies citations against exactly this list.

    Only under a premise. A question comparing two funds rests on both, and
    nothing has committed the answer to either.
    """
    frame = state.get("hypothetical_frame") or {}
    article = str(frame.get("article") or "")
    if not article:
        return sections, []
    allowed = {article, *CROSS_CUTTING_ARTICLES}
    kept, aside = [], []
    for section in sections:
        (kept if str(section.get("article") or article) in allowed else aside).append(section)
    if aside:
        logger.info(
            "Premise names Article %s; set aside %d section(s) of other funds' law",
            article,
            len(aside),
        )
    return kept, [str(s.get("section")) for s in aside if s.get("section")]


def _bind_open_question(state: dict[str, Any]) -> dict[str, Any]:
    """Resolve a coverage question the member has just answered.

    Deterministic, and deliberately not the model's to do. `bind_reply` reads an
    Article number, a section number, or one option's own distinguishing words;
    handing that to a model puts the member's own answer at the mercy of a
    re-reading, and the conversation this layer was built from is one where an
    answer given in the statute's literal words was not heard twice running.

    Runs before the investigation for the same reason `assess_coverage` runs it
    at the top of a turn: a member who has just been asked something and
    answered it must not be asked again.
    """
    question = state.get("question") or ""
    open_question = state.get("open_question")
    turn = int(state.get("turn_index") or 0)
    if not coverage_mod.question_is_live(open_question, turn):
        return {}
    bound = coverage_mod.bind_reply(question, open_question)
    if not bound:
        return {}

    employee = state.get("employee_record")
    settled = {
        **bound,
        "record_article": str(employee["article_number"]) if employee else None,
        "candidates": (open_question or {}).get("options") or [],
        "turn": turn,
        "described_as": (open_question or {}).get("described_as"),
    }
    logger.info(
        "Coverage settled by the member: article=%s (%s)",
        settled.get("article"),
        bound.get("confirmed_by"),
    )
    return {
        "coverage": settled,
        "open_question": None,
        "clarification": {},
        "candidate_articles": [settled["article"]] if settled.get("article") else [],
    }


def _search_filter(state: dict[str, Any]) -> list[str]:
    """Which Articles the fallback search reads, and why that is not the whole Code.

    `retrieve_policy` is article-filtered and that filter *is* the
    determination: a section from another fund can only arrive by the
    determination being wrong. The fallback below exists because running a
    search is the one structural thing the loop can skip -- and it was added
    without the filter, so a turn that only browsed got a slice of the entire
    Code dropped into its citable set. G30 then named Article 11 correctly and
    volunteered Section 7-109 to say it could not establish anything from it;
    F02 cited Section 4-109 to an Article 2 member the same way. A gap reported
    with another fund's section number on it is still another fund's law in an
    answer about this member.

    The order is the pipeline's, for the pipeline's reasons. A fund Article the
    member *named* moves the filter -- "what does Article 4 provide?" is a
    question about the law, not about them. A premise moves it too, and saves a
    call: `_confined_to_premise` sets everything else aside afterwards anyway.
    Then the settled verdict, then the record. Cross-cutting Articles are added
    rather than substituted, because 1 and 20 govern this member alongside their
    own fund. Nothing at all means an anonymous member with no fund, where
    narrowing would invent the determination instead of applying it.
    """
    named = [
        a
        for a in extract_article_references(state.get("question") or "")
        if a not in CROSS_CUTTING_ARTICLES
    ]
    frame = state.get("hypothetical_frame") or {}
    coverage = state.get("coverage") or {}
    employee = state.get("employee_record")
    article = (
        (named[0] if named else None)
        or frame.get("article")
        or coverage.get("article")
        or (str(employee["article_number"]) if employee else None)
    )
    if not article:
        return []
    # A fund that takes its benefits from another is searched in both. Section
    # 10-103 gives Forest Preserve District employees "the same annuities,
    # refunds and benefits ... as are made and allowed for employees of the
    # county", so an Article 10 figure worked under Section 9-134 is right and
    # filtering to 10 alone would hide the law it comes from. Read out of
    # `funds.py`, where the establishing section is declared -- never inferred
    # from a cross-reference, because an Article whose text merely mentions
    # another is not incorporating it.
    borrowed: list[str] = []
    try:
        fund = FUNDS.get(int(article))
    except (TypeError, ValueError):
        fund = None
    if fund is not None and fund.benefits_from:
        borrowed = [str(fund.benefits_from)]
    return [str(article), *borrowed, *CROSS_CUTTING_ARTICLES]


async def _engine_provisions(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Which provisions the engine would use for this member, without computing.

    G17, and it fails in the way that matters most to a member: turn 1 named
    "Section 11-130" as their retirement provision and turn 3 computed the
    figure from **Section 11-134**. The agent named one provision and used
    another.

    `resolve_recipes` is the authority for both, and the loop could only reach
    it through `compute_benefit` -- which is exactly what "do not calculate
    anything yet" forbids. So the registry is consulted directly: one query, no
    model call, the same ranking `lookup_recipe` uses, so the provision named
    here is the one a later figure rests on rather than a second opinion.

    Three gates, and each is the same rule stated somewhere else in the system.

    **Not when a figure was already worked.** `_worked_from` is the sharper
    statement then -- it names where this turn's number actually came from --
    and two blocks naming provisions is how they come to disagree.

    **Not while the Article is an open question.** The rule that starves the
    figure starves this: until it is settled, every answer about "your
    provision" is potentially the wrong fund's.

    **Under a premise, the premise's Article.** `_governing_article` already
    resolves that, and reading Article 12's own provision under an Article 12
    supposition is the law the question asked about. Nothing is starved by the
    supposition here, because a provision is text rather than the member's data.
    """
    if any(
        (entry or {}).get("base", {}).get("ok")
        for entry in (state.get("calculation_result") or {}).values()
    ):
        return []
    if _coverage_unresolved(state):
        return []
    coverage = state.get("coverage") or {}
    if not coverage.get("article") and not state.get("employee_record"):
        return []
    article = _governing_article(state)  # type: ignore[arg-type]
    tier = (state.get("member_facts") or {}).get("tier")
    try:
        return await recipes_for(int(article), int(tier) if tier is not None else None)
    except Exception:
        # A registry that cannot be read costs the turn a sentence, not the
        # answer. Every other path to the provision is still open.
        logger.exception("Could not resolve the engine's provision for Article %s", article)
        return []


def _leave_hypothetical(state: dict[str, Any]) -> dict[str, Any]:
    """Release a supposition the member has stepped out of.

    G41, and it is CRITICAL. `check_coverage` creates a `hypothetical_frame`
    and, until this, nothing anywhere cleared one -- so "Now go back to my
    actual record. What is my pension really?" was answered as a park employee
    again, resolving 12-133 against an Article 11 member and reporting every
    record-derived input missing, because the frame it never left says those
    facts belong to another fund.

    Deterministic, and the same code the pipeline runs: `assess_coverage`
    releases the frame on an edge before retrieval, so the loop's only gap was
    that it has no such edge. It joins the two steps `investigate` already runs
    before any model sees the turn, for the reason both are there -- a member
    who has said which conversation they are in must not have to say it twice.

    Two ways out, both the pipeline's. Saying so (`leaves_hypothetical`), or
    asserting who they actually are: an assertion is a new determination, not a
    follow-up inside the premise, and leaving the frame up would confine the
    new one's evidence to the old one's Article.
    """
    frame = state.get("hypothetical_frame")
    if not frame:
        return {}
    question = state.get("question") or ""
    reference = coverage_mod.classify_self_reference(question)
    leaving = coverage_mod.leaves_hypothetical(question) or (
        reference is not None and reference.act == coverage_mod.ASSERTED
    )
    if not leaving:
        return {}

    employee = state.get("employee_record")
    record_article = str(employee["article_number"]) if employee else None
    logger.info("Member stepped out of the premise %r", frame.get("premise"))
    return {
        "hypothetical_frame": None,
        "coverage": _record_coverage(record_article, int(state.get("turn_index") or 0)),
        "candidate_articles": [record_article] if record_article else [],
    }


async def investigate(state: dict[str, Any]) -> dict[str, Any]:
    """Search, read, reconsider, and stop when there is enough.

    Returns the state update the pipeline's own nodes would have written --
    `retrieved_sections` above all -- plus the message history, which `compose`
    continues rather than replacing.
    """
    started = time.perf_counter()
    settings = get_settings()

    # Before anything else, and before any model sees the turn: did they answer
    # what we asked last time?
    settled = _bind_open_question(state)
    state = {**state, **settled} if settled else state

    # And have they stepped out of a supposition? Checked after the binding,
    # because answering an open question is the narrower act and settles the
    # same key.
    if left := _leave_hypothetical(state):
        settled = {**settled, **left}
        state = {**state, **left}

    # And if nothing has established coverage yet, the record establishes it.
    #
    # `assess_coverage` does this on its fast exit, so the pipeline always has a
    # verdict. The loop only had one when the model called check_coverage, which
    # it does only when the member describes themselves -- and the ordinary
    # turn describes nobody. All eighteen fund_smoke cases failed on the same
    # line, `coverage article None`, against the pipeline's thirteen passes.
    #
    # It mostly worked by luck: `_governing_article` falls back to the record,
    # so recipes resolved. What it broke was everything that *reads* coverage --
    # the answer's own account of which fund it speaks for, and `memory`, which
    # refuses to remember an answer it cannot place in an Article.
    if not (state.get("coverage") or {}).get("basis"):
        employee = state.get("employee_record")
        settled = {
            **settled,
            "coverage": _record_coverage(
                str(employee["article_number"]) if employee else None,
                int(state.get("turn_index") or 0),
            ),
        }
        state = {**state, **settled}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": INVESTIGATE_SYSTEM},
        {"role": "system", "content": _context(state)},
        {"role": "user", "content": state["question"]},
    ]
    sections: list[dict[str, Any]] = list(state.get("retrieved_sections") or [])
    # What a tool sees: the turn's state plus everything found so far. Handing
    # each tool the *original* state meant compute_benefit derived its recipe
    # from an empty corpus while the sections it needed sat in this loop.
    working: dict[str, Any] = {**state}
    spent: dict[str, int] = {}
    withdrawn: set[str] = set()
    exhausted = False
    pushed_back = False

    while True:
        if sum(spent.values()) >= BUDGETS["total"]:
            logger.info("Loop spent its total budget of %d calls", BUDGETS["total"])
            exhausted = True
            break
        if time.perf_counter() - started > BUDGETS["seconds"]:
            logger.info("Loop ran past %ds", BUDGETS["seconds"])
            exhausted = True
            break

        available = _available(spent)
        # A tool that has just gone is explained rather than vanishing, so the
        # model stops trying it and knows what it has left.
        for tool in LOOP_TOOLS:
            name = tool["function"]["name"]
            if tool not in available and name not in withdrawn:
                withdrawn.add(name)
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"You have no more {name} calls left. Work with what you "
                            "have, or use another tool."
                        ),
                    }
                )

        turn = await call_with_tools(
            node="investigate",
            messages=messages,
            tools=available,
            reasoning_effort=settings.reasoning_effort_understand,
        )
        if turn.done:
            # A model that has read nothing has nothing to be right from. Asked
            # "what is Article 11?" with a member on file, this loop once
            # answered from memory that Article 11 is the Municipal Employees'
            # fund -- it is the Laborers' fund -- and the guardrail passed it,
            # because an answer with no citations has nothing to verify and no
            # figures to trace.
            #
            # One push back, then it is let go: arguing spends the budget on a
            # fight, and `compose` can say what it consulted instead.
            if not sections and not pushed_back:
                pushed_back = True
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "You have not read any statute text yet. An outline "
                            "gives you headings, not what the sections say, and you "
                            "may not cite or describe a provision you have not read. "
                            "Do not answer from memory -- what you recall about an "
                            "Article may be another Article's. Read something first: "
                            "get_section for a heading you have seen, search_statute "
                            "for a question about a rule."
                        ),
                    }
                )
                continue
            messages.append(turn.raw_message)
            break

        messages.append(turn.raw_message)
        for call in turn.calls:
            spent[call.name] = spent.get(call.name, 0) + 1
            result, update = await run_tool(call.name, call.arguments, working)
            if update.get("retrieved_sections"):
                sections = _merge_sections(sections, update["retrieved_sections"])
            working.update(update)
            working["retrieved_sections"] = sections
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )

    # ── A turn that only browsed has not looked ──────────────────
    #
    # `retrieve_policy` always runs a search. That is the one thing the pipeline
    # does structurally that the loop can skip entirely, and five of ten
    # full-suite failures were "did not cite 11-134" on turns whose whole spend
    # was `{outline: 1, get_section: N}`.
    #
    # The loop reads an Article's outline, fetches the sections whose *headings*
    # sound relevant, and answers. Section 11-134 is called "Amount of annuity";
    # 11-130 is "Annuities - Present employees and future entrants", which reads
    # like the better match and is not. And there is no way out of that
    # cul-de-sac by following references: 11-127 through 11-131 cite **nothing**,
    # so the completion step below has no edge to walk.
    #
    # So the search stops being optional. The member's own question is the
    # query, which is the worst wording available -- but a weak search that
    # surfaces the operative section beats a confident browse that never sees
    # it, and whatever comes back joins the citable set rather than replacing
    # what the model chose.
    if not exhausted and not spent.get("search_statute"):
        result, update = await run_tool(
            "search_statute",
            {
                "queries": [state["question"]],
                "articles": _search_filter(working),
                "why": "nothing was searched this turn",
            },
            working,
        )
        if update.get("retrieved_sections"):
            sections = _merge_sections(sections, update["retrieved_sections"])
            working["retrieved_sections"] = sections
        messages.append(
            {
                "role": "system",
                "content": (
                    "You answered without searching, so the Code was searched for "
                    "you on the member's own words. An outline gives headings, and "
                    "the section that governs is often not the one whose heading "
                    "reads best:" + chr(10) + chr(10) + result
                ),
            }
        )

    # ── Complete the evidence before the answer is written ───────
    #
    # The step the loop dropped and the pipeline always had: `resolve_recipes`
    # pulls a recipe's cross-referenced sections in explicitly, because an
    # answer that names Section 11-216 without its text in front of it is
    # citing from memory and verification rejects it outright. The loop gathered
    # evidence that leaned on sections it never read, cited them, and had each
    # answer thrown away. Two prompt-level attempts did not stop it, because it
    # was never a prompt problem.
    #
    # Skipped when the budget is gone: this is worth a few calls, not an
    # overrun, and `unread_references` reports what is left either way.
    # The registry lookup below needs nothing any of these fetches return -- it
    # reads coverage, the record and the tier -- so it is started here and
    # collected after them rather than waiting its turn behind them.
    provisions_task = asyncio.ensure_future(_engine_provisions(working))

    if not exhausted:
        weights = _reference_weights(sections)
        wanted = sorted(weights, key=lambda s: (-weights[s], s))[:MAX_COMPLETION_FETCHES]
        # Read in one wait rather than three. Each target is already chosen and
        # none of them decides whether another is worth reading, so the round
        # trips were serial for no reason. What has to stay ordered is which
        # sections are chosen -- `wanted` settles that above -- and the order
        # they are put in front of the model, which the zip below preserves.
        for target, fetched in zip(wanted, await _read_sections(wanted, working)):
            if fetched is None:
                continue
            result, update = fetched
            if update.get("retrieved_sections"):
                sections = _merge_sections(sections, update["retrieved_sections"])
                working["retrieved_sections"] = sections
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"The text you gathered leans on Section {target}, so it "
                            f"has been read for you:" + chr(10) + chr(10) + f"{result}"
                        ),
                    }
                )

    # ── The provision this conversation already settled on ───────
    #
    # G30's thirteenth turn, and `retrieve_policy` names it in its own comment:
    # "Re-run the applicability analysis without changing my facts" carries no
    # section, no benefit and no age, so retrieval has nothing to lock onto and
    # a long conversation drifts away from its own subject the moment a turn is
    # phrased without statutory vocabulary. The pipeline pins the settled
    # section into the query; the loop had nothing equivalent.
    #
    # Being told a figure is standing is not enough. `_context` says which
    # section it rests on, and a section the turn has not *read* is one
    # verification will not let it cite -- so the answer talks around the
    # provision the whole conversation is about, and whether the case passes
    # comes down to whether the model restates it from memory.
    #
    # Not gated on the budget, unlike the completion step above, and the
    # difference is the point: completion makes up to three *speculative*
    # fetches for cross-references that might matter, and dropping those when
    # the budget is gone is right. This is one *known* fetch for the provision
    # the answer has to rest on, and a turn that spent its whole budget is the
    # one most likely to have drifted away from its own subject.
    #
    # Reading it is necessary and not sufficient, which the full suite settled:
    # G30 failed with Section 11-134 sitting in its own retrieved sections,
    # cited 11-110, 11-125 and 11-221 instead, and the pin had run. What was
    # missing is on the other side -- `_settled_provision` puts it in front of
    # the model writing the answer, not only the one gathering evidence.
    settled_section = str((state.get("selected_provision") or {}).get("section") or "")
    if settled_section and any(str(s.get("section")) == settled_section for s in sections):
        settled_section = ""

    engine_provisions = await provisions_task
    engine_targets = [
        provision
        for provision in engine_provisions
        if str(provision.get("section") or "")
        and not any(str(x.get("section")) == str(provision["section"]) for x in sections)
    ]

    # The turn's last known fetches, made together. Both are named sections
    # rather than candidates, and neither's text changes whether the other is
    # worth reading, so the old order bought two waits instead of one.
    tail_targets = ([settled_section] if settled_section else []) + [
        str(p["section"]) for p in engine_targets
    ]
    tail = dict(zip(tail_targets, await _read_sections(tail_targets, working)))

    if settled_section and (read := tail.get(settled_section)) is not None:
        result, update = read
        if update.get("retrieved_sections"):
            sections = _merge_sections(sections, update["retrieved_sections"])
            working["retrieved_sections"] = sections
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"This conversation already settled on Section "
                        f"{settled_section}, so it has been read for you. Being "
                        "challenged is not a reason to change the answer: if the "
                        "determination was right it should survive being "
                        "questioned, and if it was wrong say which provision "
                        "replaces it and why." + chr(10) + chr(10) + result
                    ),
                }
            )

    for provision in engine_targets:
        target = str(provision["section"])
        read = tail.get(target)
        if read is None:
            continue
        result, update = read
        if update.get("retrieved_sections"):
            sections = _merge_sections(sections, update["retrieved_sections"])
            working["retrieved_sections"] = sections
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"The calculation engine resolves Section {target} for "
                        f"this member's {provision.get('formula_type')}, so it has "
                        "been read for you. If they asked which provision applies, "
                        "this is the answer -- not the section whose heading reads "
                        "best." + chr(10) + chr(10) + result
                    ),
                }
            )
    working["engine_provisions"] = engine_provisions

    # ── The provision the engine would use, read before it is named ──
    #
    # G17. "Which retirement provision applies to me? Do not calculate anything
    # yet." The loop browsed Article 11's outline, picked by heading, and named
    # 11-130 -- then computed from 11-134 two turns later, so "the provision you
    # selected earlier" was not the one it used. The registry knew all along.
    #
    # Read here and named in `compose`, because G30 settled that one without the
    # other is half a fix: available to cite is not named, and named without
    # having been read is a citation verification refuses.

    sections, set_aside = _confined_to_premise(sections, working)

    produced = {
        key: value
        for key, value in working.items()
        if key not in state or state.get(key) != value
    }
    return {
        **settled,
        **produced,
        "retrieved_sections": sections,
        # Named rather than silently gone. The model has them in its own
        # history either way, so a dropped section it cannot see dropped is a
        # citation it will make and have withdrawn.
        "set_aside_sections": set_aside,
        "loop_messages": messages,
        "loop_exhausted": exhausted,
        "unread_references": _unread_references(sections),
        "tool_calls_spent": spent,
        "consulted_nothing": not sections,
        "timings": {"investigate": time.perf_counter() - started},
    }


COMPOSE_INSTRUCTION = """Now write the member's answer, from what you just found.

You are the same assistant who did that research. Everything you read is above;
work from it and from nothing else. Write the answer itself -- no preamble about
having searched, and no account of how you went about it."""


def _assumed(state: dict[str, Any]) -> str:
    """What the answer must disclose, or "" when there is nothing to disclose.

    Only beside a figure. The rule `guardrails` enforces is "a figure resting on
    a value nobody supplied must say so"; with no figure nothing rests on
    anything, and the disclosure reads as a hedge about an answer that never
    needed one.
    """
    if not any(
        (entry or {}).get("base", {}).get("ok")
        for entry in (state.get("calculation_result") or {}).values()
    ):
        return ""
    return render_assumptions(WorkSheet.from_state(state))


def _borrowed_benefits(state: dict[str, Any]) -> str:
    """What to say when this member's fund takes its benefits from another.

    Declared in `funds.py` with the section that establishes it, never inferred
    from a cross-reference.
    """
    article = (state.get("coverage") or {}).get("article")
    try:
        fund = FUNDS.get(int(article))
    except (TypeError, ValueError):
        return ""
    if fund is None or not fund.benefits_from:
        return ""
    return (
        f"This member is in Article {fund.article}, and Article {fund.article} "
        f"takes its benefits from Article {fund.benefits_from}: Section "
        f"{fund.benefits_from_section} allows them the same annuities and "
        f"benefits as Article {fund.benefits_from} members. So a figure worked "
        f"under Article {fund.benefits_from} is theirs, not another fund's -- "
        f"but say why, and cite Section {fund.benefits_from_section} for it. An "
        "Article 9 citation with no bridge reads like somebody else's law."
    )


def _worked_from(state: dict[str, Any]) -> str:
    """The provision a figure standing in this turn was actually worked from.

    L13, and the "did not cite 11-134" signature behind five failures of the
    first full-suite run. Section 11-134 is called "Amount of annuity"; 11-130
    is "Annuities -- Present employees and future entrants", which reads like
    the better match and is not. The loop read both, computed from the
    registry's 11-134 recipe, and wrote prose naming 11-130 as the governing
    provision -- so the figure and the section beside it came from different
    places, which is the one thing a cited figure may never do.

    `prepare_answer` never had this problem: it renders the provision into the
    answer out of `recipe_provenance`. The loop has the same key and was
    showing the model nothing from it.

    Only beside a figure, for the reason `_assumed` is: with nothing computed,
    nothing rests on a provision, and naming one sends the answer to cite a
    section it has no use for.
    """
    if not any(
        (entry or {}).get("base", {}).get("ok")
        for entry in (state.get("calculation_result") or {}).values()
    ):
        return ""
    sections = [
        str(p["section"])
        for p in (state.get("recipe_provenance") or [])
        if p.get("section")
    ]
    if not sections:
        return ""
    named = ", ".join(f"Section {s}" for s in dict.fromkeys(sections))
    return (
        f"The figure you have was worked from {named} -- the deterministic "
        "engine resolved that provision and computed from its text. An answer "
        "that gives the figure must name that provision as the one it rests on "
        "and cite it. Naming a different section beside the same number says "
        "the figure came from somewhere it did not, and a section whose heading "
        "reads better is the usual way that happens."
    )


def _cliff_ahead(state: dict[str, Any]) -> str:
    """A boundary the member is walking towards, and what reaching it is worth.

    L11, and the Phase 3c note named it in advance: "`shape_for`,
    `_comparison_rows` and the THRESHOLDS slot are all in that category and none
    of them runs on the loop path." This is that slot.

    `execute_calculations` prices the boundary with the same interpreter that
    produced the answer's own figure -- which is why the guardrail admits the
    number -- and `prepare_answer` renders it. `compose` rendered nothing, so a
    member of 56 carrying an 11% reduction that vanishes at 60 was never told
    the date, and the sentence saying so is what a good advisor offers
    unprompted.

    Whether there *is* a cliff worth mentioning is `find_thresholds`' decision,
    not this one: a boundary already behind the member is real, is in the
    recipe's parameters, and saying anything about it is noise dressed as
    service. This renders what it was handed and nothing else.
    """
    for entry in (state.get("calculation_result") or {}).values():
        if rendered := render_thresholds((entry or {}).get("thresholds")):
            return rendered
    return ""


def _settled_provision(state: dict[str, Any]) -> str:
    """The provision this conversation has been about, when nothing new was worked.

    G30 turn 13, and the first attempt at it was half a fix. The investigation
    reads the settled section — Section 11-134 was in that turn's own retrieved
    sections — and the answer still came back citing 11-110, 11-125 and 11-221.
    **Being available to cite is not being named.** The pin sits in the middle of
    a long tool history; `compose`'s blocks are what the model reads immediately
    before it writes, which is the difference between this and `_assumed`.

    Two conditions, both load-bearing. **No figure this turn**, because
    `_worked_from` is the sharper statement when there is one — this turn's
    number came from this section — and two blocks naming provisions is how they
    end up disagreeing. And **the text was actually read this turn**, because a
    provision named but not read is one verification will not let the answer
    cite, so pointing the model at it would manufacture the rejection.

    A fact, not a stance: which provision the conversation has been about is
    read off `selected_provision`; whether this turn is still about that benefit
    stays the model's call, and a member who has moved to survivor benefits
    should be answered about those.
    """
    if _worked_from(state):
        return ""
    section = str((state.get("selected_provision") or {}).get("section") or "")
    if not section:
        return ""
    read = {str(s.get("section")) for s in (state.get("retrieved_sections") or [])}
    if section not in read:
        return ""
    return (
        f"This conversation has been about Section {section}: it is the provision "
        "the determination and any figure already given to this member rest on, "
        "and its text is in front of you. If this turn is still about that "
        "benefit, name it and cite it. Being challenged is not a reason to drop "
        "it -- a determination that was right should survive being questioned, "
        "and if it was wrong, say which provision replaces it and why. If the "
        "member has moved to a different benefit, answer about that one instead."
    )


def _engine_provision_note(state: dict[str, Any]) -> str:
    """Name the provision the engine resolves, and why that one.

    G17 turn 1 asks which provision applies; turn 2 asks why that one over the
    alternatives. Both answers are in the registry -- `selection_basis` and
    `alternatives_considered`, recorded by the derivation and read back on every
    reuse -- and the loop had never looked, so it answered turn 2 by explaining
    why *Article 11* rather than why *this section*.

    Silent when a figure was worked: `_worked_from` is the sharper statement
    then, and two blocks naming provisions is how they come to disagree.
    """
    if _worked_from(state):
        return ""
    provisions = state.get("engine_provisions") or []
    read = {str(s.get("section")) for s in (state.get("retrieved_sections") or [])}
    lines: list[str] = []
    for provision in provisions:
        section = str(provision.get("section") or "")
        if section not in read:
            # Named but unread is a citation verification refuses.
            continue
        benefit = str(provision.get("formula_type") or "benefit").replace("_", " ")
        sub = provision.get("subsection")
        where = f"Section {section}{sub or ''}"
        line = f"- For a {benefit}, the engine would work it from {where}"
        basis = str(provision.get("selection_basis") or "")
        alternatives = [
            str(a.get("section"))
            for a in provision.get("alternatives_considered") or []
            if a.get("section") and str(a.get("section")) != section
        ]
        if alternatives:
            line += (
                f", chosen over {', '.join(dict.fromkeys(alternatives))}"
                + (f" on a {basis.replace('_', ' ')} basis" if basis else "")
            )
        elif basis:
            line += f" ({basis.replace('_', ' ')})"
        lines.append(line + ".")
    if not lines:
        return ""
    return (
        "The deterministic engine has already resolved which provision governs "
        "this member, and its text is in front of you:" + chr(10)
        + chr(10).join(lines) + chr(10)
        + "If the member asked which provision applies, or why that one rather "
        "than its neighbours, this is the answer and you may cite it. It is the "
        "same provision a figure would rest on, so do not name a different one "
        "here and then compute from this later."
    )


async def compose(state: dict[str, Any]) -> dict[str, Any]:
    """Turn the investigation into the answer, in the same conversation.

    Deliberately not a fresh call. Handing a model that never saw the statute a
    pile of text and asking it to narrate is exactly the split this phase
    exists to remove: the model that read the section is the model that writes
    the sentence about it.

    What is appended is the *style* half of the pipeline's answer prompt --
    voice, citation discipline, the closing line. Not its stance blocks: those
    told a generation model what position to take because it could not reason
    to one, and this model has just spent the whole investigation reasoning.
    """
    messages = list(state.get("loop_messages") or [])
    messages.append({"role": "system", "content": ANSWER_SYSTEM})

    unread = state.get("unread_references") or []
    if unread:
        # Computed, not judged. Naming the provisions the evidence leans on but
        # never opened is checkable; "do you feel you have enough?" is not.
        messages.append(
            {
                "role": "system",
                "content": (
                    "The text you gathered relies on sections you could not read: "
                    f"{', '.join(unread)}. Do not state a conclusion that turns on "
                    "them. Say plainly what you could not establish."
                ),
            }
        )

    if state.get("consulted_nothing"):
        # The loop already pushed back once and was overruled. Deterministic
        # backstop: an answer resting on nothing may not speak for the statute.
        messages.append(
            {
                "role": "system",
                "content": (
                    "You did not read any of the Pension Code for this answer. Do "
                    "not state what the Code provides or name any Article's fund "
                    "from memory. Say what you can without it, and say plainly that "
                    "you could not consult the statute."
                ),
            }
        )

    readable = [s["section"] for s in (state.get("retrieved_sections") or []) if s.get("section")]
    if readable:
        # A list is harder to misread than a rule. The loop saw 11-124 in an
        # outline and cited it without reading it; ANSWER_SYSTEM's "cite only
        # what is in front of you" was already there, and the guardrail already
        # caught the breach, but naming the set costs nothing.
        messages.append(
            {
                "role": "system",
                "content": (
                    "You read these sections, and may cite only these: "
                    + ", ".join(readable)
                    + ". A section you saw named in an outline or inside another "
                    "section's text is not one you have read."
                ),
            }
        )

    if asking := (state.get("clarification") or {}):
        # A question that does not quote the test it turns on is an assertion
        # wearing a question mark. The loop asked and then wrote about how
        # Article 11 calculates a pension, citing five Article 11 sections and
        # neither coverage test -- and a clarifying question that names a
        # section it never read is how the first one ever asked got itself
        # withdrawn.
        tests = [
            f"Article {o.get('article')}"
            + (f" (Section {o['section']}: {o.get('test')})" if o.get("section") else
               " (where their record places them; no coverage test was retrieved for it)")
            for o in (asking.get("options") or [])
            if o.get("article")
        ]
        messages.append(
            {
                "role": "system",
                "content": (
                    "Which Article covers this member is NOT settled, and that is "
                    "what this answer is about. They described themselves as "
                    f"{asking.get('described_as')!r}, which could put them in:"
                    + chr(10) + chr(10) + chr(10).join(f"- {line}" for line in tests)
                    + chr(10) + chr(10)
                    + "Put the question to them, and quote the test it turns on "
                    "from the section itself. What settles it: "
                    f"{asking.get('settled_by') or 'which employer pays them'}. "
                    "Give no figure -- the Article is precisely what is in doubt, "
                    "and a pension worked out from the wrong one is worse than a "
                    "turn's delay."
                ),
            }
        )

    if bridge := _borrowed_benefits(state):
        # A Forest Preserve figure is computed under Section 9-134, and that is
        # correct -- but an answer citing Article 9 to an Article 10 member
        # without saying why has handed them another fund's law with no bridge,
        # which reads exactly like the borrowed-formula error it is not.
        messages.append({"role": "system", "content": bridge})

    if frame := (state.get("hypothetical_frame") or {}):
        # The guardrail catches an unconditional answer under a premise, and
        # catching means the member sees a fallback rather than an answer.
        # `_context` tells the investigation the premise is live; until this,
        # `compose` said nothing, and the answer came back in the plain
        # indicative -- which tells a member they are in a fund they only asked
        # about.
        messages.append(
            {
                "role": "system",
                "content": (
                    "This answer is about something the member ASKED YOU TO "
                    f"SUPPOSE: {frame.get('premise')!r}. They have not told you "
                    "they are this, so nothing here is established about them. "
                    "Open by naming the supposition and keep every sentence "
                    "resting on it conditional -- 'if you were', 'you would'. An "
                    "answer written in the plain indicative will be withdrawn "
                    "before the member sees it. Do not ask them to confirm the "
                    "premise: they stipulated it."
                ),
            }
        )

    if aside := (state.get("set_aside_sections") or []):
        frame = state.get("hypothetical_frame") or {}
        messages.append(
            {
                "role": "system",
                "content": (
                    "You read these while working out which fund was in question, "
                    f"and they are another fund's law: {', '.join(aside)}. The "
                    f"answer rests on Article {frame.get('article')} alone. Do not "
                    "cite them -- they are not evidence for this answer, and an "
                    "answer that cites them will be withdrawn before the member "
                    "sees it."
                ),
            }
        )

    if provision := _worked_from(state):
        messages.append({"role": "system", "content": provision})

    if settled := _settled_provision(state):
        messages.append({"role": "system", "content": settled})

    if engine := _engine_provision_note(state):
        messages.append({"role": "system", "content": engine})

    if cliff := _cliff_ahead(state):
        messages.append(
            {
                "role": "system",
                "content": (
                    "This member is walking towards a boundary in the Code, and "
                    "the figures below were worked by the same engine as their "
                    "own estimate, so you may quote them. State it plainly beside "
                    "the answer -- do not offer to look into it, and do not close "
                    "with a question:" + chr(10) + chr(10) + cliff
                ),
            }
        )

    if disclosure := _assumed(state):
        # `apply_assumptions` fills a missing value deep inside `resolve_inputs`,
        # several tool calls below this conversation, so the model has no way of
        # knowing an assumption was made -- and `guardrails` throws the answer
        # away when it does not say so. `prepare_answer` renders this block into
        # the answer; here the model writes it, which is the point of the phase,
        # so it is handed the content and told it is not optional.
        messages.append(
            {
                "role": "system",
                "content": (
                    "The figure you are about to quote rests on values nobody "
                    "supplied. You must say so, in your own words, beside the "
                    "figure:" + chr(10) + chr(10) + disclosure + chr(10) + chr(10)
                    + "An estimate whose assumptions are not stated will be "
                    "withdrawn before the member sees it."
                ),
            }
        )

    messages.append({"role": "system", "content": COMPOSE_INSTRUCTION})

    # The same shape `prepare_answer` publishes. The guardrail checks the
    # answer's own citations against what was retrieved, but the `done` event
    # and the evidence drawer read *this* list -- so without it the loop's
    # answer cited two sections and the member could open neither.
    citations = [
        {
            "id": s["section"],
            "section": s["section"],
            "title": s.get("title", ""),
            "citation": s.get("citation") or f"40 ILCS 5/{s['section']}",
        }
        for s in (state.get("retrieved_sections") or [])
    ]
    return {
        "answer_messages": messages,
        "answer_blocks": {},
        "citations": citations,
        # `select_rung` lives inside `prepare_answer`, which the loop does not
        # run, so every loop answer reported rung 0 -- including answers whose
        # content was right and whose assumption was properly disclosed. The
        # ladder is a claim about what the evidence supports, and it is as true
        # of an answer the loop wrote as of one the pipeline assembled.
        "answer_rung": select_rung(WorkSheet.from_state(state), dict(state)),
    }
