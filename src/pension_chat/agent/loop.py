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

import logging
import time
from typing import Any

from pension_chat.agent import coverage as coverage_mod
from pension_chat.agent.blocks import render_assumptions
from pension_chat.agent.llm import call_with_tools
from pension_chat.agent.nodes import ANSWER_SYSTEM
from pension_chat.agent.presentation import select_rung
from pension_chat.agent.retrieval import CROSS_CUTTING_ARTICLES
from pension_chat.agent.tools import (
    CALCULATION_TOOLS,
    COVERAGE_TOOLS,
    PROVISION_TOOLS,
    RETRIEVAL_TOOLS,
    run_tool,
)
from pension_chat.agent.worksheet import WorkSheet
from pension_chat.config import get_settings

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
    "find_provision": 3,
}

#: Everything the loop may reach for. Retrieval, calculation and coverage
#: together, because a member asking "how much would I get" is asking one
#: question -- and "if I were a Chicago police officer, what would I get?" is
#: still one question, with all three in it.
LOOP_TOOLS = RETRIEVAL_TOOLS + CALCULATION_TOOLS + COVERAGE_TOOLS + PROVISION_TOOLS

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
- Which provision governs is not a heading you pick. An outline tells you what
  sections are called; find_provision tells you the one the engine would
  actually use, and why that one rather than its neighbours. Use it whenever
  the member asks which rule applies, why you chose it, or what their options
  are -- and especially when they have said not to calculate yet, because it
  computes nothing. What you name from it is what a later figure will rest on.
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
    facts = state.get("member_facts") or {}
    lines = []
    if facts:
        known = ", ".join(
            f"{name}={value}"
            for name, value in sorted(facts.items())
            if name in {"age", "service_years", "tier", "article_number", "employment_status"}
        )
        lines.append(f"What is on file for this member: {known or 'nothing usable'}")
    else:
        lines.append("No member record is available; answer from the Code alone.")
    coverage = state.get("coverage") or {}
    if coverage.get("article"):
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
    if not exhausted:
        weights = _reference_weights(sections)
        wanted = sorted(weights, key=lambda s: (-weights[s], s))[:MAX_COMPLETION_FETCHES]
        for target in wanted:
            result, update = await run_tool("get_section", {"section": target}, working)
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
