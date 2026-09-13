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

from pension_chat.agent.llm import call_with_tools
from pension_chat.agent.nodes import ANSWER_SYSTEM
from pension_chat.agent.tools import RETRIEVAL_TOOLS, run_tool
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
}

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
- Stop when you can answer, not when you are tired. Stop as soon as you can.

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
    return "\n".join(lines)


def _available(spent: dict[str, int]) -> list[dict[str, Any]]:
    return [
        tool
        for tool in RETRIEVAL_TOOLS
        if spent.get(tool["function"]["name"], 0) < BUDGETS.get(tool["function"]["name"], 0)
    ]


def _merge_sections(existing: list[dict], incoming: list[dict]) -> list[dict]:
    seen = {s.get("section") for s in existing}
    return existing + [s for s in incoming if s.get("section") not in seen]


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


async def investigate(state: dict[str, Any]) -> dict[str, Any]:
    """Search, read, reconsider, and stop when there is enough.

    Returns the state update the pipeline's own nodes would have written --
    `retrieved_sections` above all -- plus the message history, which `compose`
    continues rather than replacing.
    """
    started = time.perf_counter()
    settings = get_settings()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": INVESTIGATE_SYSTEM},
        {"role": "system", "content": _context(state)},
        {"role": "user", "content": state["question"]},
    ]
    sections: list[dict[str, Any]] = list(state.get("retrieved_sections") or [])
    spent: dict[str, int] = {}
    withdrawn: set[str] = set()
    exhausted = False

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
        for tool in RETRIEVAL_TOOLS:
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
            messages.append(turn.raw_message)
            break

        messages.append(turn.raw_message)
        for call in turn.calls:
            spent[call.name] = spent.get(call.name, 0) + 1
            result, update = await run_tool(call.name, call.arguments, state)
            if update.get("retrieved_sections"):
                sections = _merge_sections(sections, update["retrieved_sections"])
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )

    return {
        "retrieved_sections": sections,
        "loop_messages": messages,
        "loop_exhausted": exhausted,
        "unread_references": _unread_references(sections),
        "tool_calls_spent": spent,
        "timings": {"investigate": time.perf_counter() - started},
    }


COMPOSE_INSTRUCTION = """Now write the member's answer, from what you just found.

You are the same assistant who did that research. Everything you read is above;
work from it and from nothing else. Write the answer itself -- no preamble about
having searched, and no account of how you went about it."""


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
    return {"answer_messages": messages, "answer_blocks": {}, "citations": citations}
