"""LangGraph agent nodes (§9.3).

The rule the whole design turns on: **the model plans, retrieves and writes
prose; it never performs authoritative arithmetic.** Every benefit figure comes
out of the deterministic DSL interpreter, from a recipe whose every constant
cites statute text the model was actually shown.

Node order matters and is not the order the first implementation used. Recipe
resolution has a hard dependency on retrieval (you cannot derive a rule from
text you have not fetched) and input resolution has a hard dependency on recipe
resolution (you cannot know which inputs are needed until the recipe declares
them). The original graph fanned all four out concurrently from ``plan_request``,
so ``retrieve_or_derive_recipe`` and ``extract_calculation_inputs`` both ran
against empty state.

Answer *generation* deliberately lives outside the graph, in ``graph.py`` — it
is the only step that streams, and keeping it out of a node lets tokens reach
the browser as they are produced rather than after the graph completes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from pension_chat.agent import coverage as coverage_mod
from pension_chat.agent import guardrails
from pension_chat.agent.derivation import derive_recipe
from pension_chat.agent.facts import build_member_facts
from pension_chat.agent.llm import complete
from pension_chat.agent.registry import (
    RegistryRecipe,
    lookup_recipe,
    persist_recipe,
    verify_freshness,
)
from pension_chat.agent.retrieval import (
    RetrievedSection,
    canonical_section_texts,
    extract_article_references,
    extract_section_references,
    retrieve,
    retrieve_coverage,
)
from pension_chat.agent.state import PensionAgentState
from pension_chat.calculation.applicability import (
    evaluate_predicates,
    facts_for_evaluation,
)
from pension_chat.calculation.dsl_interpreter import DSLExecutionError, DSLInterpreter
from pension_chat.calculation.risk_assessor import RiskAssessor
from pension_chat.calculation.validator import RecipeValidator
from pension_chat.config import get_settings
from pension_chat.db.models import Employee
from pension_chat.db.session import get_db_session

logger = logging.getLogger("pension_chat.agent.nodes")


def _history(state: PensionAgentState, turns: int) -> str:
    """Render the last N conversation turns as plain text."""
    lines = []
    for message in state.get("messages", [])[-(turns * 2 + 1) : -1]:
        role = "User" if message.type == "human" else "Assistant"
        content = str(message.content)
        lines.append(f"{role}: {content[:600]}")
    return "\n".join(lines)


# ── 1. Planning ───────────────────────────────────────────────────

_PLAN_SYSTEM = """You plan retrieval for an Illinois Pension Code assistant.

Decide three things about the user's message:

1. Does answering it require computing a benefit figure? Explaining a rule,
   defining a term, or saying which provision applies is NOT a calculation.
   Producing a dollar amount IS.
2. If it does, name the formula types needed, in snake_case
   (e.g. "retirement_annuity", "refund_of_contributions", "survivor_annuity").
3. Write 2-3 search queries against the statute index. Use the vocabulary the
   statute itself uses — "annuity", "withdrawal from service", "highest average
   annual salary", "years of service" — not the user's paraphrase. Name the
   section number when the user gave one.

Also extract any retirement-timing scenarios the user asks you to compare
("if I retire now, at 57, and at 60", "what if I wait two more years").

Scenarios take one of two forms, and you must not guess between them:
  {"label": "at age 60", "retirement_age": 60}     when the user names an age
  {"label": "in 2 years", "years_from_now": 2}     when the user names an offset
You are NOT given the member's age, so never convert an offset into an age —
emit "years_from_now" and it will be resolved against their record.

Respond with JSON only:
{
  "requires_calculation": true|false,
  "formula_types_needed": ["..."],
  "search_queries": ["...", "..."],
  "scenarios": [{"label": "...", "retirement_age": null, "years_from_now": null}]
}
Use an empty scenarios list when the user asks about a single point in time."""


# Words that mean the user wants a number, not an explanation. Used by the
# fast path below to decide whether the planner model call can be skipped.
# Word stems carry their own suffix wildcard. Written as a bare
# `\b(calculat|...)\b` the trailing boundary applied to whichever alternative
# matched, and "calculat" is followed by a word character in every real word it
# was meant to catch -- so `calculat`, `comput` and `estimat` matched nothing at
# all, for as long as they had existed. "calculate my estimated pension" planned
# no calculation; the model then did the arithmetic in prose from the facts in
# its prompt, and verification withdrew the answer. The eval suite never saw it
# because every case happened to trip a different alternative.
_CALCULATION_SIGNALS = re.compile(
    r"\b(?:"
    r"calculat\w*|recalculat\w*|comput\w*|estimat\w*|compar\w*|project\w*"
    r"|how much|what would|what will|amount|benefit|annuity"
    r"|my pension|per month|monthly|annual|scenario"
    r")\b",
    re.IGNORECASE,
)


# A personal quantity restated in the member's own words: "25 years of service",
# "my salary is $80,000". Anchored on the unit rather than the pronoun so that
# "I have a question about section 11-134" is not read as a stated fact.
_RESTATED_FACT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:years?|yrs?)\s+of\s+(?:service|creditable\s+service)\b"
    r"|\$\s?[\d,]{3,}",
    re.IGNORECASE,
)


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_COUNT = r"(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"

# Relative offsets: "wait 2 more years", "in three years", "5 years from now".
_OFFSET_PATTERNS = [
    re.compile(rf"\b(?:wait(?:ing)?|after|in)\s+(?:another\s+)?{_COUNT}\s+(?:more\s+)?years?\b", re.I),
    re.compile(rf"\b{_COUNT}\s+(?:more\s+)?years?\s+from\s+now\b", re.I),
]
# Absolute ages: "at age 60", "when I reach 62", "at 57, at 59 and at 60".
# A list of ages in one breath: "at ages 57, 59 and 60". Written as separate
# single-age patterns this matched nothing at all (the plural "ages" was not
# covered) and, once it was, matched only the first -- so a member nine turns
# into a comparison asking for three more got none of them.
_AGE_LIST = re.compile(
    r"\bages?\s+(\d{2}(?:\s*(?:,|and|or|/|through|to)\s*(?:ages?\s+)?\d{2})+)", re.I
)

_AGE_PATTERNS = [
    re.compile(r"\b(?:at|until|to|reach(?:ing|es)?|turn(?:ing|s)?)\s+ages?\s+(\d{2})\b", re.I),
    re.compile(r"\b(?:reach(?:ing|es)?|turn(?:ing|s)?)\s+(\d{2})\b", re.I),
    re.compile(r"\bat\s+(\d{2})\b", re.I),
]
_RETIRE_NOW = re.compile(r"\b(?:retire\s+now|right\s+now|retire\s+today|if\s+i\s+retire\s+now)\b", re.I)

# Ages outside this band are not retirement ages — the guard stops "at 21.5
# years of service" or a dollar figure being read as one.
_PLAUSIBLE_AGE = range(45, 76)

# The planner is shown the conversation so far, because a follow-up question is
# often unreadable without it. That context leaked: after a turn asking "what if
# I waited three more years", the NEXT question — about a claimed 50%-of-salary
# formula, with no timing request in it — still came back with a three-year
# comparison the member had not asked for again.
#
# A word-level "does this mention timing" gate does not fix it: "21.5 years of
# service" is not a request to compare retirement dates, and neither is "56
# years old". What distinguishes a live scenario from a remembered one is
# whether its target appears in the message in front of us, so that is what is
# checked. A planner scenario survives only if the current message names the age
# or the offset it is about.
_NOW_CUE = re.compile(r"\b(?:now|today|immediately|currently|right\s+away)\b", re.I)


def _corroborated(scenario: dict, question: str) -> bool:
    """True if the current message names the age or offset this scenario is for."""
    age = scenario.get("retirement_age")
    offset = scenario.get("years_from_now")

    if offset == 0 or (age is None and offset is None and _NOW_CUE.search(question)):
        return bool(_RETIRE_NOW.search(question) or _NOW_CUE.search(question))

    target = age if age is not None else offset
    if target is None:
        # Nothing concrete to corroborate. Kept only when the message is clearly
        # asking about timing at all, so it can be reported as unresolvable
        # rather than silently answered or silently dropped.
        return bool(_NOW_CUE.search(question)) or any(
            p.search(question) for p in _OFFSET_PATTERNS + _AGE_PATTERNS
        )

    word = next((w for w, n in _NUMBER_WORDS.items() if n == target), None)
    # The number must stand alone. Excluding a preceding "$" or digit keeps
    # "$72,000" from corroborating a retirement age of 72, and "(?!,\d)" rejects
    # thousands grouping without rejecting the comma in "at 57, at 59 and at 60".
    digits = re.compile(rf"(?<![\d.$]){target}(?![\d.]|,\d)")
    return bool(digits.search(question)) or (
        word is not None and re.search(rf"\b{word}\b", question, re.I) is not None
    )


def _as_count(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def extract_scenarios(question: str) -> list[dict]:
    """Find retirement-timing scenarios in the question, without a model call.

    The planner can also emit scenarios, but it runs concurrently with the
    record fetch and therefore does not know the member's age — so it cannot
    turn "wait 2 more years" into an absolute retirement age and returns null.
    Detecting the offset here, and resolving it against the real age at
    execution time, removes the model from a step it has no data for.
    """
    text = question or ""
    scenarios: list[dict] = []
    seen: set[tuple] = set()

    def add(entry: dict, key: tuple):
        if key not in seen:
            seen.add(key)
            scenarios.append(entry)

    if _RETIRE_NOW.search(text):
        add({"label": "retire now", "years_from_now": 0}, ("offset", 0))

    for pattern in _OFFSET_PATTERNS:
        for match in pattern.finditer(text):
            count = _as_count(match.group(1))
            if count is not None and 0 < count <= 40:
                label = "in 1 year" if count == 1 else f"in {count} years"
                add({"label": label, "years_from_now": count}, ("offset", count))

    for match in _AGE_LIST.finditer(text):
        for value in re.findall(r"\d{2}", match.group(1)):
            age = int(value)
            if age in _PLAUSIBLE_AGE:
                add({"label": f"at age {age}", "retirement_age": age}, ("age", age))

    for pattern in _AGE_PATTERNS:
        for match in pattern.finditer(text):
            age = int(match.group(1))
            if age in _PLAUSIBLE_AGE:
                add({"label": f"at age {age}", "retirement_age": age}, ("age", age))

    return scenarios


def _merge_scenarios(deterministic: list[dict], planned: list[dict]) -> list[dict]:
    """Union the regex-found scenarios with the planner's, preferring resolved ones."""
    merged: list[dict] = []
    seen: set[tuple] = set()

    for entry in list(deterministic) + list(planned):
        if not isinstance(entry, dict):
            continue
        age = entry.get("retirement_age")
        offset = entry.get("years_from_now")
        if age is None and offset is None:
            # An unresolvable planner scenario ("in 2 years", age null) is kept
            # only if nothing already covers it, so it can be reported rather
            # than silently dropped.
            key = ("unresolved", str(entry.get("label", "")).lower())
        else:
            key = ("age", age) if age is not None else ("offset", offset)
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)

    # A resolvable scenario supersedes an unresolvable one describing the same
    # thing, so drop the leftovers if anything concrete was found.
    concrete = [s for s in merged if s.get("retirement_age") is not None or s.get("years_from_now") is not None]
    return concrete if concrete else merged


async def triage(state: PensionAgentState) -> dict:
    """Deterministic gate before anything expensive runs.

    Scope classification and adversarial-instruction detection are pure string
    work. Doing them here means a greeting or an out-of-scope request costs zero
    model calls and zero database round trips, and it lets planning and the
    record fetch fan out in parallel behind it.
    """
    started = time.perf_counter()
    question = state["question"]
    # A bare continuation ("and what about at 60?") carries no pension
    # vocabulary of its own and is only in scope as a follow-up, so the check
    # needs to know whether anything precedes it. `messages` always holds at
    # least this turn's own question.
    scope = guardrails.classify_scope(
        question, has_history=len(state.get("messages") or []) > 1
    )

    return {
        "in_scope": scope.in_scope,
        "out_of_scope_reason": scope.reason,
        "override_signal": guardrails.detect_instruction_override(question).to_dict(),
        "explicit_sections": extract_section_references(question),
        "explicit_articles": extract_article_references(question),
        "timings": {"triage": time.perf_counter() - started},
    }


# Which benefit a question is about, read off the words the member used. The
# planner model can do this too, and does it better on an unusual question --
# but it costs a full round trip to the endpoint (~2.3s, the largest single item
# on the critical path once the database is in the same region as the code), and
# the overwhelming majority of questions here are about one benefit and say so
# plainly.
_BENEFIT_CUES: list[tuple[str, object]] = [
    (
        "survivor_annuity",
        re.compile(
            r"\b(?:widow(?:er)?|surviving spouse|survivors?|beneficiar|"
            r"if i die|when i die|after my death|death benefit)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "disability_benefit",
        re.compile(r"\b(?:disabilit\w*|disabled|injured on duty)\b", re.IGNORECASE),
    ),
    (
        "refund_of_contributions",
        re.compile(
            r"\b(?:refund\w*|my contributions back|withdraw my contributions)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "retirement_annuity",
        re.compile(
            r"\b(?:retire\w*|retirement|annuity|pension|superannuat\w*)\b",
            re.IGNORECASE,
        ),
    ),
]

# Written the way the statute writes it, which is what the planner was being
# asked for. Retrieval is hybrid, so the member's own phrasing still runs as a
# query alongside this one.
_STATUTE_QUERY = {
    "retirement_annuity": (
        "retirement annuity minimum annuity highest average annual salary "
        "years of service withdrawal from service age reduction"
    ),
    # The competing pathways, named so they are retrieved alongside the one
    # that usually wins. Without them a fresh derivation cannot say why the
    # provision it chose beats the annuity computed from accumulated
    # contributions -- and saying so is most of the value of the answer.
    "_retirement_alternatives": (
        "annuity from accumulated employee and city contributions in lieu of "
        "age and service annuity prior service annuity"
    ),
}


# Words that ask for a number, as opposed to naming a subject. The difference
# decides whether planning can be skipped: "benefit", "annuity" and "annual" are
# in the calculation-signal list because they usually accompany a request for a
# figure, but on their own they are just the topic. "Which salary definition
# applies to my benefit calculation" names a benefit and asks for no number at
# all -- read as a calculation, it searched with annuity vocabulary and never
# retrieved Section 11-116, the provision that actually defines salary.
_FIGURE_REQUEST = re.compile(
    r"\b(?:calculat\w*|recalculat\w*|comput\w*|estimat\w*|compar\w*|project\w*"
    r"|how much|what would|what will|work(?: it)? out|my pension|per month|monthly"
    r"|amount|scenario)\b",
    re.IGNORECASE,
)


def infer_formula_types(question: str) -> list[str]:
    """Benefit types named in the question, or an empty list if it is unclear.

    Empty means "ask the model": either nothing matched, or more than one
    benefit did and choosing between them is a judgement rather than a lookup.
    """
    matched = [name for name, cue in _BENEFIT_CUES if cue.search(question or "")]
    return matched if len(matched) == 1 else []


def _continuing_formula_types(state: PensionAgentState, continuing: bool) -> list[str]:
    """The benefit this conversation is already about, when a turn needs one."""
    if not continuing:
        return []
    settled = (state.get("selected_provision") or {}).get("formula_type")
    return [settled] if settled else []


async def plan_request(state: PensionAgentState) -> dict:
    """Classify intent and write statute-vocabulary search queries.

    Skips the model entirely for questions that ask what the law says rather
    than what a member will be paid. Planning exists to name the formula types
    and pick apart multi-scenario requests; a question with no calculation in it
    needs neither, and hybrid retrieval handles the raw question well. On this
    deployment one model round trip costs ~2.4s no matter how small the prompt,
    so skipping it is the single largest latency saving available in the lookup
    path — roughly a third of time-to-first-token.

    The trade is a false negative: a calculation question phrased without any of
    the signal words would be answered as a lookup. The signal list is
    deliberately broad for that reason, and the answer prompt refuses to state a
    benefit amount when no calculation result was supplied, so the failure mode
    is "explains the rule instead of computing", not "invents a number".
    """
    started = time.perf_counter()
    settings = get_settings()
    question = state["question"]

    # Restating a personal quantity mid-conversation is a request to work the
    # figure out again, and none of the signal words above appear in "Actually I
    # have 25 years of service, not 21.5. What changes?". That turn planned no
    # calculation, ran no recipe -- and the model, holding an earlier estimate in
    # the conversation, recomputed it in prose and had the answer withdrawn.
    #
    # Only mid-conversation: on a first turn the same sentence is someone
    # introducing themselves, and treating that as a calculation would put a
    # planner round trip in front of every applicability question.
    mid_conversation = len(state.get("messages") or []) > 1
    correcting = bool(_RESTATED_FACT.search(question)) and mid_conversation

    # "Now do the same at age 59." carries no calculation word at all -- it is
    # the previous question with one variable moved, and it produced no figure
    # for the same reason a correction did. What it does carry is a retirement
    # timing, which `extract_scenarios` already finds, so the follow-up is
    # recognised by what it names rather than by widening the keyword list.
    #
    # Mid-conversation only, for the same reason as a correction: on a first
    # turn "what happens at age 60" is asking what the law says, and planning a
    # calculation for it would put a round trip in front of every applicability
    # question.
    continuing_a_comparison = mid_conversation and bool(extract_scenarios(question))

    if (
        not _CALCULATION_SIGNALS.search(question)
        and not correcting
        and not continuing_a_comparison
    ):
        return {
            "requires_calculation": False,
            "formula_types_needed": [],
            # Any section the user named is pinned by retrieval; the question
            # text runs as a query so neighbouring provisions surface too.
            "search_queries": [question],
            "scenarios": extract_scenarios(question),
            "timings": {"plan_request": time.perf_counter() - started},
        }

    # A question that names exactly one benefit needs nothing the planner
    # supplies that cannot be read off it here: the benefit type, the statute
    # vocabulary to search with, and the retirement timings, which
    # `extract_scenarios` already finds without a model. The planner is kept for
    # everything else -- several benefits at once, or none named clearly.
    inferred = infer_formula_types(question) if settings.deterministic_planning else []
    if inferred and inferred[0] in _STATUTE_QUERY and _FIGURE_REQUEST.search(question):
        return {
            "requires_calculation": True,
            "formula_types_needed": inferred,
            "search_queries": [
                question,
                _STATUTE_QUERY[inferred[0]],
                *(
                    [_STATUTE_QUERY["_retirement_alternatives"]]
                    if inferred[0] == "retirement_annuity"
                    else []
                ),
            ],
            "scenarios": extract_scenarios(question),
            "timings": {"plan_request": time.perf_counter() - started},
        }

    try:
        raw = await complete(
            node="plan_request",
            messages=[
                {"role": "system", "content": _PLAN_SYSTEM},
                {
                    "role": "user",
                    "content": f"Conversation so far:\n{_history(state, 2)}\n\nMessage: {question}",
                },
            ],
            reasoning_effort=settings.reasoning_effort_plan,
            json_mode=True,
        )
        plan = json.loads(raw or "{}")
    except Exception as exc:
        # A planning failure must not sink the turn — fall back to searching the
        # question verbatim, which is what the retrieval layer handles worst but
        # still handles.
        logger.warning("Planning failed, falling back to literal query: %s", exc)
        plan = {}

    # The member's own words go in alongside the planner's rewrites, not
    # instead of them. Retrieval is hybrid, and the raw question is often the
    # better query: "which salary definition applies to my benefit calculation"
    # returns Section 11-116 — the provision that defines salary — at rank two,
    # while the statute-vocabulary rewrites of the same question did not return
    # it at all, and the answer said the Code did not provide a definition.
    queries = [question] + [
        q for q in (plan.get("search_queries") or []) if isinstance(q, str)
    ]

    return {
        # A member correcting one of their own numbers mid-conversation is
        # asking for the figure again, whether or not they phrased it as a
        # question. "My salary is actually $76,000, use that from here on" read
        # to the planner as a preference rather than a request, so nothing was
        # recomputed and the correction sat unused until the member happened to
        # ask again. That is what `correcting` was for; it was only being used
        # to decide whether to consult the planner, not to overrule it.
        # Two deterministic signals overrule the planner, for the same reason.
        # A member correcting one of their own numbers, or naming retirement
        # ages mid-conversation, is asking for the figure again whether or not
        # they phrased it as a question -- "show me the same at ages 57, 59 and
        # 60" produced no comparison at all, nine turns into a conversation
        # about exactly that. Both signals were only deciding whether to consult
        # the planner; neither was allowed to overrule it.
        "requires_calculation": bool(plan.get("requires_calculation"))
        or correcting
        or continuing_a_comparison,
        # Falling back to the provision this conversation already settled on:
        # a correction is a correction *to something*, and requiring the
        # calculation without naming what to calculate would skip it anyway.
        "formula_types_needed": [
            f for f in (plan.get("formula_types_needed") or []) if isinstance(f, str)
        ] or _continuing_formula_types(state, correcting or continuing_a_comparison),
        "search_queries": queries[:3],
        "scenarios": _merge_scenarios(
            extract_scenarios(question),
            [
                s
                for s in (plan.get("scenarios") or [])
                if isinstance(s, dict) and _corroborated(s, question)
            ],
        ),
        "timings": {"plan_request": time.perf_counter() - started},
    }


# ── 2a. Coverage ──────────────────────────────────────────────────


# How many coverage sections may be pinned into one turn's retrieval. Bounded
# because each is statute text the answer prompt then carries, but large
# enough to cover every candidate the question names.
_MAX_COVERAGE_SECTIONS = 6


def _record_coverage(record_article: str | None, turn: int) -> dict:
    """What we believe when the member has said nothing about themselves."""
    return {
        "article": record_article,
        "basis": (
            coverage_mod.BASIS_RECORD if record_article else coverage_mod.BASIS_UNRESOLVED
        ),
        "record_article": record_article,
        "candidates": [],
        "conflict": False,
        "turn": turn,
        "described_as": None,
    }


# Words that mean the same thing as an `employment_status` value on file. Only a
# disagreement matters here, so a description matching nothing agrees with
# everything rather than manufacturing a conflict out of unfamiliar wording.
_STATUS_SYNONYMS = {
    "active": ("still working", "working", "active", "employed"),
    "retired": ("retired", "retiring", "no longer working", "no longer employed"),
    "on_leave": ("leave", "leave of absence", "unpaid leave"),
    "disabled": ("disability", "out on disability", "disabled"),
}


def _status_agrees(described: str, on_file: str) -> bool:
    """Whether what the member said matches the status on their record."""
    described = (described or "").lower()
    on_file = (on_file or "").lower()
    known = _STATUS_SYNONYMS.get(on_file)
    if known and any(term in described for term in known):
        return True
    for status, terms in _STATUS_SYNONYMS.items():
        if any(term in described for term in terms):
            return status == on_file
    return True


def _coverage_elsewhere(state: PensionAgentState) -> bool:
    """Is this turn reading an Article the member's record is not filed under?

    True only for a coverage belief the member themselves created — a premise
    they supposed, or an Article they confirmed against their record. A `record`
    basis is never "elsewhere", because it *is* the record.
    """
    coverage = state.get("coverage") or {}
    article = coverage.get("article")
    on_file = coverage.get("record_article")
    if not article or not on_file or article == on_file:
        return False
    return coverage.get("basis") in {
        coverage_mod.BASIS_HYPOTHETICAL,
        coverage_mod.BASIS_CONFIRMED,
    }


async def assess_coverage(state: PensionAgentState) -> dict:
    """Decide which Article this turn reads from, and on what evidence.

    Sits between the record fetch and retrieval, because it needs the record in
    order to have something to disagree with, and retrieval needs its verdict.

    **The fast exit carries the latency budget.** A turn where the member said
    nothing about who they are, and did not answer an open question, returns from
    here without a search or a model call — which is nearly every turn, including
    almost all of the regression suite. The cost is paid only where it buys
    something.
    """
    started = time.perf_counter()
    if not state.get("in_scope", True):
        return {}

    settings = get_settings()
    question = state["question"]
    turn = state.get("turn_index") or 0
    employee = state.get("employee_record")
    record_article = str(employee["article_number"]) if employee else None

    reference = coverage_mod.classify_self_reference(question)
    frame = state.get("hypothetical_frame")
    established = state.get("coverage")

    # ── Did they answer what we asked last turn? ──────────────────
    # Checked before anything else. A member who has just been asked a question
    # and answered it must not be asked it again — and this is the exact turn
    # that failed in the conversation this work came from: they restated their
    # coverage in the statute's own words and got the same figure back.
    open_question = state.get("open_question")
    if coverage_mod.question_is_live(open_question, turn):
        bound = coverage_mod.bind_reply(question, open_question)
        if bound:
            settled = {
                **bound,
                "record_article": record_article,
                "candidates": open_question.get("options") or [],
                "turn": turn,
                "described_as": open_question.get("described_as"),
            }
            logger.info(
                "Coverage settled by the member: article=%s (%s)",
                settled.get("article"),
                bound.get("confirmed_by"),
            )
            return {
                "coverage": settled,
                "open_question": None,
                "candidate_articles": (
                    [settled["article"]] if settled.get("article") else []
                ),
                "timings": {"assess_coverage": time.perf_counter() - started},
            }

    # ── Are they stepping out of a supposition? ───────────────────
    leaving = bool(frame) and (
        coverage_mod.leaves_hypothetical(question)
        or (reference is not None and reference.act == coverage_mod.ASSERTED)
    )
    if leaving:
        frame = None

    # ── Still inside one? ─────────────────────────────────────────
    # A follow-up exploring a premise carries none of its words: "and at 60?" is
    # still a question about being a park employee. So the frame holds by
    # default and is left explicitly. Nothing here tries to detect that a
    # scenario has been "fully answered" — that is not computable, and guessing
    # it wrong strands the member mid-exploration. Every answer under a frame
    # says which frame it is under instead, which makes a stale one visible on
    # the turn it goes stale.
    if frame and reference is None:
        return {
            "coverage": {**(established or {}), "turn": turn},
            "hypothetical_frame": {**frame, "last_used_turn": turn},
            "candidate_articles": [frame["article"]] if frame.get("article") else [],
            "timings": {"assess_coverage": time.perf_counter() - started},
        }

    # ── The fast exit ─────────────────────────────────────────────
    if reference is None:
        update: dict[str, Any] = {}
        if leaving:
            update["hypothetical_frame"] = None
            update["coverage"] = _record_coverage(record_article, turn)
        elif not established:
            update["coverage"] = _record_coverage(record_article, turn)
        return update

    # ── A claim about employment status ───────────────────────────
    # Settled by looking at the row rather than by reading the statute, so it
    # never touches the corpus.
    if reference.kind == coverage_mod.STATUS:
        update = {"self_reference": reference.to_dict()}
        on_file = (employee or {}).get("employment_status")
        if (
            reference.act == coverage_mod.ASSERTED
            and on_file
            and not _status_agrees(reference.text, on_file)
        ):
            update["clarification"] = {
                "about": "employment_status",
                "described_as": reference.text,
                "on_file": on_file,
            }
        return update

    # ── A claim about coverage ────────────────────────────────────
    if not settings.coverage_assessment:
        return {"self_reference": reference.to_dict()}

    sections = await retrieve_coverage(reference.text)
    report = await coverage_mod.assess(reference.text, sections)
    verdict = coverage_mod.adjudicate(reference, report, record_article, turn=turn)

    update = {
        "self_reference": reference.to_dict(),
        "coverage": verdict,
        # Every section the verdict rests on goes into this turn's retrieval --
        # all of them, not the best few. The clarifying question is handed the
        # candidate list as JSON, section numbers included, so a candidate whose
        # text was not pinned is a section number the model can read and cite
        # without ever having seen the provision. Verification then withdraws
        # the question, and the member gets an apology instead of an answer.
        "coverage_sections": [
            c["section"] for c in verdict.get("candidates") or [] if c.get("section")
        ][:_MAX_COVERAGE_SECTIONS],
        "timings": {"assess_coverage": time.perf_counter() - started},
    }

    if verdict["basis"] == coverage_mod.BASIS_HYPOTHETICAL:
        # Adopted whole: no question, no budget spent. Retrieval moves to the
        # supposed Article. Whether a figure may be produced under it is decided
        # separately, by whether every input came from the member rather than
        # from a record filed under a different Article.
        article = verdict.get("article")
        update["hypothetical_frame"] = {
            "premise": reference.text,
            "article": article,
            "entered_turn": turn,
            "last_used_turn": turn,
        }
        update["candidate_articles"] = [article] if article else []
        return update

    if leaving:
        update["hypothetical_frame"] = None

    if not verdict.get("conflict"):
        return update

    # ── It is a question, unless the budget is spent ──────────────
    asked = state.get("clarifications_asked") or 0
    if asked >= settings.clarification_budget:
        # Proceed on the record and say so. Asking a fourth time is its own
        # failure, and one the member has no way out of.
        update["coverage"] = {**verdict, "conflict": False, "why": "budget_spent"}
        return update

    asking = coverage_mod.open_question_for(verdict)
    update["clarification"] = asking
    update["open_question"] = asking
    update["clarifications_asked"] = 1
    # No figure this turn. The Article is precisely what is in doubt, and a
    # pension worked out from the wrong one is worse than one turn's delay.
    update["requires_calculation"] = False
    # Every candidate and the record's own Article, so the question can quote
    # the coverage test it turns on instead of merely asserting it.
    update["candidate_articles"] = [
        a
        for a in dict.fromkeys(
            str(o.get("article")) for o in asking.get("options") or []
        )
        if a and a != "None"
    ]
    return update


# ── 2a. Retrieval ─────────────────────────────────────────────────


async def retrieve_policy(state: PensionAgentState) -> dict:
    """Retrieve and assemble the statute sections this question needs."""
    started = time.perf_counter()
    settings = get_settings()

    if not state.get("in_scope", True):
        return {"retrieved_sections": [], "answers_other_article": False}

    # Restrict to the member's own Article when we know it. Article 11 and 12
    # are deliberately confusable, so leaking Article 12 text into an Article 11
    # member's answer is the exact failure the pilot is scoped to catch. With no
    # record to go on, search the whole corpus.
    #
    # Naming an Article overrides that, rather than adding to it. An Article 11
    # member asking what Article 12 provides was told Article 12 "is not covered
    # by the statute text", which was untrue -- it is indexed, and the filter was
    # the only thing hiding it. Searching both Articles would not have helped
    # either: ranking puts Article 11 first even for a query that says "Article
    # 12". Whichever Article the member asked about is the one to read from.
    employee = state.get("employee_record")
    named = state.get("explicit_articles") or []
    # Coverage sits between the two. A member who named an Article outright has
    # said which law they want read; a member who described their job has said
    # something the Code itself can be asked about, and `assess_coverage` has
    # already asked it. Only when neither happened does the record's column
    # decide, which is where it belongs -- as the fallback rather than the
    # unfalsifiable default it used to be.
    candidates = state.get("candidate_articles") or []
    articles = (
        named
        or candidates
        or ([str(employee["article_number"])] if employee else None)
    )

    # A question about someone else's Article is a question about the law, not
    # about this member's benefit. Their salary and service belong to Article 11
    # and cannot be run through an Article 12 provision, so the figures stay out
    # and the answer explains what that Article provides.
    other_article = bool(
        named and employee and str(employee["article_number"]) not in named
    )

    # A calculation needs whole provisions; the derivation step refuses to work
    # from text marked truncated.
    calculating = state.get("requires_calculation")
    budget = (
        settings.derivation_context_token_budget
        if calculating
        else settings.retrieval_context_token_budget
    ) * 4
    top = (
        settings.derivation_top_sections
        if calculating
        else settings.retrieval_top_sections
    )

    # The coverage sections behind this turn's candidates are pinned in. They
    # were found by a separate, unfiltered search, so nothing else would bring
    # them back -- and an answer that names Section 12-101 without its text in
    # front of it is citing from memory, which verification rejects outright.
    pinned = list(
        dict.fromkeys(
            (state.get("explicit_sections") or []) + (state.get("coverage_sections") or [])
        )
    )

    sections = await retrieve(
        state.get("search_queries") or [state["question"]],
        articles=articles,
        must_include_sections=pinned,
        top_sections=top,
        char_budget=budget,
    )
    return {
        "retrieved_sections": [s.to_dict() for s in sections],
        "requires_calculation": False if other_article else calculating,
        "answers_other_article": other_article,
        "timings": {"retrieve_policy": time.perf_counter() - started},
    }


# ── 2b. Employee record ───────────────────────────────────────────


async def fetch_employee_record(state: PensionAgentState) -> dict:
    """Load the authenticated member's record and salary history.

    Salary history is not optional detail: Article 11's minimum-annuity formulas
    are written on "the highest average annual salary for any 4 consecutive
    years", which cannot be computed from a single current-salary column.
    """
    started = time.perf_counter()
    employee_id = state.get("employee_id")
    if not employee_id:
        return {"employee_record": None, "salary_history": []}

    try:
        # Scoped to this member: the policy on `employees` and, through it, on
        # `employee_salary_history` will return nothing for anyone else's id.
        async with get_db_session(employee_id=employee_id) as db:
            # One round trip, not two. The database is remote (Azure Postgres),
            # so each query costs ~1s of wall clock on the critical path; eager
            # loading the salary history in the same statement halves that.
            result = await db.execute(
                select(Employee)
                .options(selectinload(Employee.salary_history))
                .where(Employee.id == employee_id)
            )
            employee = result.scalar_one_or_none()
            if employee is None:
                return {"employee_record": None, "salary_history": []}

            history = [
                {
                    "effective_date": row.effective_date.isoformat(),
                    "annual_salary": float(row.annual_salary),
                }
                for row in sorted(
                    employee.salary_history, key=lambda r: r.effective_date
                )
            ]
    except Exception:
        logger.exception("Failed to load employee record %s", employee_id)
        return {"employee_record": None, "salary_history": []}

    record = {
        "id": str(employee.id),
        "article_number": employee.article_number,
        "tier": employee.tier,
        "birth_date": employee.birth_date.isoformat(),
        "hire_date": employee.hire_date.isoformat(),
        "employment_status": employee.employment_status,
        "total_service_years": float(employee.total_service_years),
        "current_annual_salary": float(employee.current_annual_salary),
    }

    facts = build_member_facts(
        record, [(date.fromisoformat(h["effective_date"]), Decimal(str(h["annual_salary"]))) for h in history]
    )

    return {
        "employee_record": record,
        "salary_history": history,
        "member_facts": facts,
        "timings": {"fetch_employee_record": time.perf_counter() - started},
    }


# ── 3. Recipe resolution ──────────────────────────────────────────

# How many extra sections may be pinned into context to back a recipe and the
# alternatives it set aside. Bounded because every one of them is statute text
# the answer prompt then carries.
_MAX_PINNED_SECTIONS = 6


def _available_facts(state: PensionAgentState) -> dict:
    """The member facts this turn is allowed to use.

    Empty when the conversation is reading an Article the member's record is not
    filed under. Their salary and service are administered by the fund named on
    that record, so under another Article they are not merely unhelpful -- they
    are the wrong basis, and every use of them is wrong in the same way: as a
    calculation input, as a fact an applicability predicate is tested against,
    as a figure the answer may quote, and as a source verification will accept.
    Single-sourced here so those four cannot drift apart.
    """
    if _coverage_elsewhere(state):
        return {}
    return dict(state.get("member_facts") or {})


def _governing_article(state: PensionAgentState) -> int:
    """The Article this turn is actually operating under.

    Coverage first, the record second. A conversation that has moved to another
    Article -- because the member confirmed their file is wrong, or asked to
    suppose a different job -- must look its recipes up under the Article it is
    reading, not under the one on the record. Otherwise an Article 12 provision
    is filed against an Article 11 member and handed to the next Article 11
    member who asks.
    """
    coverage = state.get("coverage") or {}
    article = coverage.get("article")
    if article is not None and str(article).isdigit():
        return int(article)
    employee = state.get("employee_record")
    if employee:
        return int(employee["article_number"])
    named = [a for a in (state.get("explicit_articles") or []) if a.isdigit()]
    return int(named[0]) if named else 11


def _article_of_sections(sections) -> int | None:
    """The Article a set of section ids belongs to, when they agree.

    Section ids carry their Article as a prefix ("11-134"), so this is the
    strongest available statement of where a derived recipe actually came from
    -- stronger than the member's record, which is what used to stamp it. At two
    Articles that mattered little. At twenty, a recipe derived partly from
    another Article's cross-referenced text would be filed under the wrong one
    and reused there.
    """
    articles = {
        str(section).split("-")[0]
        for section in (sections or [])
        if str(section).split("-")[0].isdigit()
    }
    return int(articles.pop()) if len(articles) == 1 else None


async def resolve_recipes(state: PensionAgentState) -> dict:
    """Reuse a registry recipe where one exists; derive from statute otherwise.

    This is the step the first implementation stubbed with a hardcoded
    ``salary x 0.5`` tree. Reuse is not merely a cache: a recipe promoted to
    ``approved`` by a human reviewer is better evidence than anything the agent
    derives on the fly, so the registry is consulted first on every turn.
    """
    started = time.perf_counter()
    settings = get_settings()

    formula_types = state.get("formula_types_needed") or []
    if not state.get("requires_calculation") or not formula_types:
        return {"recipes": {}, "timings": {"resolve_recipes": time.perf_counter() - started}}

    sections = [_section_from_dict(s) for s in state.get("retrieved_sections", [])]
    employee = state.get("employee_record")
    article = _governing_article(state)
    tier = employee["tier"] if employee else None
    facts = _available_facts(state)

    recipes: dict[str, dict] = {}
    provenance: list[dict] = []
    failures: list[dict] = []

    async def resolve_one(formula_type: str) -> None:
        row = await lookup_recipe(formula_type, article, tier)
        if row is not None:
            # Reuse only if the statute it was derived from still reads the same.
            # Fetch the recipe's own source sections rather than intersecting
            # with this turn's retrieval: whether the ranker happened to surface
            # 11-134 today says nothing about whether 11-134 has changed.
            relevant = await canonical_section_texts(row.source_sections or [])
            mismatches = await verify_freshness(row, relevant) if relevant else {}
            if not mismatches:
                recipe = RegistryRecipe(row).to_dict()
                # Freshness says the law has not moved under the recipe. It says
                # nothing about whether the recipe's provision reaches the member
                # asking today — and matching on article, formula type and tier
                # does not either. A recipe derived for a 56-year-old with 21.5
                # years of service would otherwise be handed unchanged to someone
                # aged 47 with 9, who cannot reach the provision at all.
                applicable = evaluate_predicates(
                    (recipe.get("spec") or {}).get("applicability_predicates"), facts
                )
                if applicable.blocks_execution:
                    logger.info(
                        "Registry recipe %s does not apply to this member (%s); deriving",
                        row.formula_id,
                        applicable.summary(),
                    )
                else:
                    recipe["applicability_check"] = applicable.to_dict()
                    recipes[formula_type] = recipe
                    provenance.append(
                        {
                            "formula_type": formula_type,
                            "source": "registry",
                            "formula_id": recipe["formula_id"],
                            "section": recipe.get("section"),
                            "subsection": recipe.get("controlling_subsection"),
                            "status": recipe["status"],
                            "applicability": recipe.get("applicability"),
                            "applicability_status": applicable.status,
                            "selection_basis": recipe.get("selection_basis"),
                            "alternatives_considered": (
                                recipe.get("alternatives_considered") or []
                            )[:5],
                            "reused": True,
                        }
                    )
                    return
            else:
                logger.info(
                    "Registry recipe %s is stale (%s); re-deriving",
                    row.formula_id,
                    mismatches,
                )

        derived = await derive_recipe(formula_type, sections, facts)
        if not derived.ok:
            failures.append(
                {"formula_type": formula_type, "reason": derived.cannot_derive or "unknown"}
            )
            return

        recipes[formula_type] = {
            "source": "derived",
            "formula_type": formula_type,
            "formula_name": derived.formula_name,
            "spec": derived.rule_specification,
            "section": derived.controlling_section,
            "source_sections": derived.source_sections,
            "status": "agent_derived",
            # Never authoritative on first derivation — promotion is a human step.
            "policy": settings.derived_recipe_execution_policy,
            "applicability": derived.applicability,
            "selection_basis": derived.selection_basis,
            "cross_references": derived.cross_references,
            "alternatives_considered": derived.alternatives_considered,
            "derivation_notes": derived.derivation_notes,
            "controlling_subsection": derived.controlling_subsection,
            "_derived": derived,
        }
        provenance.append(
            {
                "formula_type": formula_type,
                "source": "derived",
                "section": derived.controlling_section,
                "subsection": derived.controlling_subsection,
                "applicability": derived.applicability,
                "selection_basis": derived.selection_basis,
                # Which neighbouring provisions were weighed and set aside. This
                # is the answer to "why 11-134 and not 11-130", and it was being
                # computed and then thrown away.
                "alternatives_considered": derived.alternatives_considered[:5],
                "reused": False,
            }
        )

    # Formula types are independent; derive them concurrently rather than
    # paying the derivation latency once per type in series.
    await asyncio.gather(*(resolve_one(f) for f in formula_types[:3]))

    # A recipe's evidence must be in front of the answer, whatever the ranker
    # did this turn. A recipe reused from the registry was derived from sections
    # that today's queries may not surface — and if 11-134 is absent, strict
    # validation refuses to execute the recipe (its cited section is "not in the
    # corpus") and the guardrail rejects the answer for citing it. Pull the
    # missing evidence in explicitly.
    retrieved = list(state.get("retrieved_sections") or [])
    present = {s["section"] for s in retrieved}
    needed = [
        str(section).split("(")[0].strip()
        for recipe in recipes.values()
        for section in (recipe.get("source_sections") or [])
    ]

    # The provisions the derivation weighed and set aside are the answer to "why
    # this provision and not the neighbouring one" — the question a reader of
    # Article 11 has to ask, since 11-129, 11-130, 11-131 and 11-134 all reach a
    # member withdrawing after 55. Those alternatives were being named to the
    # answer without their text, so the answer cited provisions it had never
    # been shown and post-generation verification threw it away. Retrieve them.
    for recipe in recipes.values():
        for note in (recipe.get("alternatives_considered") or [])[:5]:
            if note.get("section"):
                needed.append(str(note["section"]).split("(")[0].strip())
        # A section the rule DEPENDS on for a defined term is not optional
        # context. Section 11-134 computes on service "as service is defined or
        # computed in Section 11-216", and an answer that has 11-134 but not
        # 11-216 has half the rule -- which is how one answer came to say the
        # cross-reference "cannot be analyzed further here".
        for reference in (recipe.get("cross_references") or [])[:4]:
            needed.append(str(reference).split("(")[0].strip())

    needed = [s for s in dict.fromkeys(needed) if s not in present][:_MAX_PINNED_SECTIONS]
    if needed:
        extra = await retrieve([], must_include_sections=needed, follow_cross_references=0)
        for section in extra:
            if section.section not in present:
                retrieved.append(section.to_dict())
                present.add(section.section)
        logger.info("Pulled recipe evidence into context: %s", [s.section for s in extra])

    return {
        "recipes": recipes,
        "recipe_provenance": provenance,
        "derivation_failures": failures,
        "retrieved_sections": retrieved,
        "timings": {"resolve_recipes": time.perf_counter() - started},
    }


# ── 4. Input resolution ───────────────────────────────────────────

# A recipe names the quantities its arithmetic consumes. Several of those are
# derivations, not things a member knows about themselves, and asking for them
# verbatim produces requests nobody can answer -- "the number of months or
# partial months your annuity would begin before age 60" is the system asking
# the member to do its own work. Each entry is what to ask for instead; the
# derivation still happens here once the underlying fact arrives.
_ASK_INSTEAD = {
    "months_before_age_60": "your date of birth",
    "months_before_60": "your date of birth",
    "full_months_under_age_60": "your date of birth",
    "age_at_annuity_start": "your date of birth",
    "age_at_withdrawal": "your date of birth",
    "withdrawal_date": "the date you plan to stop working",
    "creditable_service_years": "how many years of service you have with the fund",
    "service_years": "how many years of service you have with the fund",
    "highest_average_annual_salary_4yr": (
        "your salary for each of the last ten years, or your best four consecutive years"
    ),
    "final_average_salary": (
        "your salary for each of the last ten years, or your best four consecutive years"
    ),
}


_EXTRACT_SYSTEM = """You map values a user has stated onto a calculation's declared inputs.

You are given input specifications and the conversation. For each input, return
the value if — and only if — the user stated it or it follows directly from
something they stated. Never estimate, never assume a typical value, never
carry a number over from a different input because it seems close enough.

Return JSON only:
{"resolved": {"<input_name>": <value>}, "unresolved": ["<input_name>"]}

Money as a plain number (72000, not "$72,000"). Dates as YYYY-MM-DD.
Anything you cannot source from the conversation goes in "unresolved"."""


async def resolve_inputs(state: PensionAgentState) -> dict:
    """Assemble the values each recipe needs, and report what is missing.

    Three sources, in order of trust: facts computed from the member's record,
    then values the user stated in conversation, then nothing — an input that
    resolves from neither is reported as missing and blocks execution. The
    original implementation returned ``{}`` here and mocked ``age = 55``
    downstream, so user-supplied figures never reached the interpreter at all.
    """
    started = time.perf_counter()
    settings = get_settings()
    recipes = state.get("recipes") or {}
    if not recipes:
        return {"resolved_inputs": {}, "missing_inputs": []}

    declared: dict[str, dict] = {}
    for recipe in recipes.values():
        for spec in (recipe.get("spec") or {}).get("inputs", []):
            declared.setdefault(spec["name"], spec)

    # Empty when the conversation has moved to another Article. Starving the
    # record rather than refusing outright is what keeps the useful case
    # working: everything simply becomes a missing input, so a member who asks
    # "if I were a park employee, what would I get?" is told what Article 12
    # provides and what would be needed, while one who writes "if I were a park
    # employee with 25 years and $70,000" gets a figure -- because by then every
    # input is theirs and none of it was borrowed.
    facts = _available_facts(state)

    from_record = {name: facts[name] for name in declared if name in facts}
    resolved = dict(from_record)
    outstanding = [spec for name, spec in declared.items() if name not in resolved]

    # Extract whenever an input is still unresolved, and also whenever the user
    # put numbers in their message. A member exploring a hypothetical ("I am 60
    # with 25 years of service") states facts that contradict their record on
    # purpose; answering from the record and ignoring what they said produces a
    # confidently wrong answer to a question they did not ask.
    question_has_figures = bool(re.search(r"\d", state["question"]))
    if outstanding or question_has_figures:
        catalogue = [
            {
                "name": spec["name"],
                "type": spec.get("type"),
                "description": spec.get("description", ""),
            }
            for spec in (outstanding if not question_has_figures else declared.values())
        ]
        try:
            raw = await complete(
                node="resolve_inputs",
                messages=[
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Inputs needed:\n{json.dumps(catalogue, indent=1)}\n\n"
                            f"Conversation so far:\n{_history(state, settings.history_turns)}\n\n"
                            f"Latest message: {state['question']}"
                        ),
                    },
                ],
                    reasoning_effort=settings.reasoning_effort_extract,
                json_mode=True,
            )
            extracted = json.loads(raw or "{}").get("resolved") or {}
        except Exception as exc:
            logger.warning("Input extraction failed: %s", exc)
            extracted = {}

        for name, value in extracted.items():
            if name not in declared or value is None:
                continue
            if name in from_record and not _stated_in(value, state["question"]):
                # The extraction step may fill a gap, but it may not quietly
                # replace a value the facts layer computed from the record
                # unless the member actually wrote that value down. Left
                # unchecked the model "helpfully" derives things it should not:
                # it read "I am 56" and supplied months_before_age_60 = 48
                # (56 -> 60 is four years) where the record gives 44, because
                # the member is 56 years and nearly four months old.
                logger.info(
                    "Ignoring extracted %s=%r: not stated by the user and the "
                    "record gives %r",
                    name, value, from_record[name],
                )
                continue
            resolved[name] = value

    # Where the member stated something their record contradicts, the stated
    # value is used and the difference is surfaced in the answer rather than
    # silently resolved either way.
    overrides = [
        {"input": name, "from_record": from_record[name], "stated_by_user": resolved[name]}
        for name in from_record
        if name in resolved and str(resolved[name]) != str(from_record[name])
    ]

    missing = [
        {
            "name": spec["name"],
            "type": spec.get("type"),
            "description": spec.get("description", ""),
            # What to actually ask the member for. A recipe declares the inputs
            # its arithmetic needs, and some of those are quantities nobody
            # knows about themselves: asking someone for "the number of full
            # months your annuity begins before age 60" is asking them to do the
            # derivation. Ask for the fact it comes from instead.
            "ask_for": _ASK_INSTEAD.get(spec["name"], spec.get("description") or spec["name"]),
            "why_required": (
                f"The controlling provision computes the benefit from "
                f"{spec.get('description') or spec['name']}."
            ),
        }
        for name, spec in declared.items()
        if name not in resolved and spec.get("required", True)
    ]

    return {
        "resolved_inputs": resolved,
        "missing_inputs": missing,
        "input_overrides": overrides,
        "timings": {"resolve_inputs": time.perf_counter() - started},
    }


# ── 5. Validation ─────────────────────────────────────────────────


async def validate_recipes(state: PensionAgentState) -> dict:
    """Validate and risk-tier every recipe, and decide whether it may execute.

    Validation runs in strict mode: a recipe about to produce a number a member
    may act on must have every constant traceable to a retrieved section. In
    lenient mode an uncited rate is only a warning, which is how the stubbed
    ``salary x 0.5`` recipe passed validation and executed.
    """
    started = time.perf_counter()
    recipes = state.get("recipes") or {}
    if not recipes:
        return {
            "validation_errors": [],
            "validation_warnings": [],
            "risk_tier": None,
            "can_execute": False,
            "execution_blocked": None,
        }

    indexed = {s["section"] for s in state.get("retrieved_sections", [])}
    article_default = _governing_article(state)
    validator = RecipeValidator()
    assessor = RiskAssessor()

    # Predicates are checked against the member's record *and* anything they
    # stated themselves, so an anonymous user who supplies their age and service
    # gets the same applicability test a signed-in member does.
    checkable_facts = facts_for_evaluation(
        _available_facts(state), state.get("resolved_inputs")
    )

    errors: list[dict] = []
    warnings: list[dict] = []
    highest = "low"
    ranks = {"low": 1, "medium": 2, "high": 3}
    blocked: str | None = None
    can_execute = True

    for formula_type, recipe in recipes.items():
        spec = recipe.get("spec") or {}
        sources = recipe.get("source_sections") or []

        result = validator.validate(spec, sources, indexed, set(), strict=True)
        for error in result.errors:
            errors.append({"formula_type": formula_type, **error.to_dict()})
        for warning in result.warnings:
            warnings.append({"formula_type": formula_type, **warning.to_dict()})

        if not result.is_valid:
            can_execute = False
            blocked = "validation_failed"

        # The gate the whole applicability layer exists for: a recipe may not
        # produce a figure for a member the provision demonstrably does not
        # reach. A condition that simply could not be checked does not block —
        # an unverifiable fact is not a failed test — but it is recorded so the
        # answer can say the check was incomplete.
        applicable = evaluate_predicates(
            spec.get("applicability_predicates"), checkable_facts
        )
        recipe["applicability_check"] = applicable.to_dict()
        if applicable.blocks_execution:
            can_execute = False
            blocked = "applicability_not_satisfied"
            for failure in applicable.failed:
                errors.append(
                    {
                        "formula_type": formula_type,
                        "code": "APPLICABILITY_NOT_SATISFIED",
                        "severity": "error",
                        "message": (
                            f"{recipe.get('section') or 'The provision'} requires "
                            f"{failure['description']}, but this member has "
                            f"{failure.get('actual')}"
                        ),
                    }
                )
        elif applicable.unknown:
            warnings.append(
                {
                    "formula_type": formula_type,
                    "code": "APPLICABILITY_PARTIALLY_VERIFIED",
                    "severity": "warning",
                    "message": (
                        "Could not check every statutory condition: "
                        + "; ".join(
                            f"{u['description']} ({u.get('reason')})"
                            for u in applicable.unknown[:4]
                        )
                    ),
                }
            )

        assessment = assessor.assess(
            spec,
            sources,
            formula_type,
            _article_of(recipe.get("section"), default=article_default),
            has_cross_article_refs=len({str(s).split("-")[0] for s in sources}) > 1,
        )
        recipe["risk"] = assessment.to_dict()
        if ranks[assessment.risk_tier] > ranks[highest]:
            highest = assessment.risk_tier

        if recipe.get("policy") == "no_execute":
            can_execute = False
            blocked = "execution_policy_no_execute"

    if can_execute and state.get("missing_inputs"):
        can_execute = False
        blocked = "missing_inputs"

    return {
        "validation_errors": errors,
        "validation_warnings": warnings,
        "risk_tier": highest,
        "can_execute": can_execute,
        "execution_blocked": blocked,
        "recipes": recipes,
        "timings": {"validate_recipes": time.perf_counter() - started},
    }


# ── 6. Execution ──────────────────────────────────────────────────


def _money(value: Any) -> str:
    """Render a Decimal as a money string.

    The interpreter works in full Decimal precision, so a chain of percentage
    operations lands on something like ``31765.802112000``. Handing that to the
    answer step put raw internal precision in front of members
    ("$31,765.802112000"); rounding it here keeps the arithmetic exact right up
    to the point of presentation.
    """
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


def _quantity(value: Any) -> str:
    """Render a value that is not money: no forced second decimal place.

    ``_money`` quantizes everything to two places, which is right for a currency
    amount and wrong for everything else. Running the recipe's inputs through it
    put "21.50" where the member has 21.5 years of service, and since the answer
    is required to copy figures character for character, "21.50 years" is what
    came out. Trailing zeros are dropped so the figure reads the way a person
    would say it.
    """
    try:
        number = Decimal(str(value)).normalize()
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    # normalize() turns 44 into 4.4E+1; expand any exponent back out.
    if number == number.to_integral_value():
        return str(number.quantize(Decimal(1)))
    return f"{number:f}"


def _years(value: Any) -> str:
    """A length of service, to the tenth of a year a member would recognise."""
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


def _difference_from(base: dict | None, variant: dict) -> dict:
    """How much more (or less) a scenario is worth than retiring now."""
    if not (base or {}).get("ok") or not variant.get("ok"):
        return {}
    try:
        annual = Decimal(variant["annual_amount"]) - Decimal(base["annual_amount"])
        monthly = Decimal(variant["monthly_amount"]) - Decimal(base["monthly_amount"])
    except (InvalidOperation, KeyError, TypeError):
        return {}
    return {
        "difference_from_now_annual": _money(annual),
        "difference_from_now_monthly": _money(monthly),
    }


def _present(result: dict, output_name: str, *, breakdown: bool = True) -> dict:
    """Format one interpreter result for the answer, adding the monthly figure.

    The monthly amount is computed here, deterministically, precisely so the
    answer never has to divide by 12 in prose.

    ``breakdown`` also carries the path to the figure: the input values it
    consumed, the statutory constants with the section each was read from, and
    the named intermediate steps in evaluation order. Without it the answer
    could state a benefit but not show how the salary and rate it quoted led
    there — a reader checking 69,956.40 x 21.5 x 2.4% got 36,097.50 and no
    account of the early-retirement reduction that takes it to 32,126.78, even
    though the reduction was applied correctly and cited in prose.
    """
    value = result.get("result")
    presented: dict[str, Any] = {
        "output_name": output_name,
        "annual_amount": _money(value),
        "output_type": result.get("output_type"),
    }
    try:
        monthly = (Decimal(str(value)) / Decimal(12)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        presented["monthly_amount"] = str(monthly)
    except (InvalidOperation, TypeError, ValueError):
        pass

    if not breakdown:
        # Scenario variants repeat the same constants and the same derivation;
        # only what differs is worth the tokens, and that is already reported
        # as the scenario's assumptions.
        return presented

    if result.get("inputs_used"):
        # Money keeps two decimals; service years, month counts and rates are
        # rendered as the quantities they are, on the recipe's own declared type.
        types = result.get("input_types") or {}
        presented["inputs_used"] = {
            k: (_money(v) if types.get(k) == "money" else _quantity(v))
            for k, v in result["inputs_used"].items()
        }
    if result.get("parameters_used"):
        presented["statutory_values_used"] = {
            k: {"value": p.get("value"), "source": p.get("source") or {}}
            for k, p in result["parameters_used"].items()
        }
    if result.get("steps"):
        presented["steps"] = [
            {"name": step["name"], "value": _money(step["value"])}
            for step in result["steps"]
        ]
    if result.get("intermediate"):
        presented["intermediate"] = {
            k: _money(v) for k, v in result["intermediate"].items()
        }
    return presented


async def execute_calculations(state: PensionAgentState) -> dict:
    """Run each recipe through the deterministic interpreter.

    When the plan asked for scenarios ("now, at 57, at 60"), the same recipe is
    executed once per scenario against facts recomputed as of that retirement
    age. Scenario variation lives here, in deterministic code, rather than
    being described to the model.
    """
    started = time.perf_counter()

    if not state.get("can_execute"):
        return {
            "calculation_result": None,
            "timings": {"execute_calculations": time.perf_counter() - started},
        }

    recipes = state.get("recipes") or {}
    base_inputs = dict(state.get("resolved_inputs") or {})
    # Values that came from the user rather than from the member's record. A
    # scenario re-derives record facts at a different retirement age, but it must
    # not overwrite a number the member stated themselves.
    member_facts = state.get("member_facts") or {}
    user_supplied = {k: v for k, v in base_inputs.items() if k not in member_facts}
    employee = state.get("employee_record")
    salary_history = [
        (date.fromisoformat(h["effective_date"]), Decimal(str(h["annual_salary"])))
        for h in state.get("salary_history") or []
    ]
    scenarios = state.get("scenarios") or []
    interpreter = DSLInterpreter()

    results: dict[str, Any] = {}
    unresolved: list[dict] = []

    for formula_type, recipe in recipes.items():
        spec = recipe.get("spec") or {}
        output_name = spec.get("output_name", "result")

        def run(inputs: dict, *, breakdown: bool = True) -> dict:
            try:
                return {
                    "ok": True,
                    **_present(
                        interpreter.execute(spec, inputs), output_name, breakdown=breakdown
                    ),
                }
            except DSLExecutionError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - interpreter bug, not user error
                logger.exception("Interpreter failed for %s", formula_type)
                return {"ok": False, "error": f"Calculation engine error: {exc}"}

        entry: dict[str, Any] = {
            "formula_type": formula_type,
            "formula_name": recipe.get("formula_name") or formula_type,
            "section": recipe.get("section"),
            "subsection": recipe.get("controlling_subsection"),
            "execution_policy": recipe.get("policy"),
            "knowledge_status": recipe.get("status"),
            "risk_tier": (recipe.get("risk") or {}).get("risk_tier"),
            "base": run(base_inputs),
        }

        if scenarios and not employee:
            unresolved.extend(
                {
                    "label": sc.get("label") or "unspecified",
                    "reason": (
                        "comparing retirement dates needs the member's record, "
                        "which is only available when signed in"
                    ),
                }
                for sc in scenarios[:6]
            )

        if scenarios and employee:
            variants = []
            for scenario in scenarios[:6]:
                age = _resolve_retirement_age(scenario, member_facts)
                if age is None:
                    # Never drop a scenario in silence. The member asked for it;
                    # if it cannot be resolved the answer has to say so, rather
                    # than replying as though it was never requested.
                    unresolved.append(
                        {
                            "label": scenario.get("label") or "unspecified",
                            "reason": (
                                "the retirement age could not be determined from "
                                "the request or the member's record"
                            ),
                        }
                    )
                    continue
                scenario_facts = build_member_facts(
                    employee, salary_history, assumed_retirement_age=int(age)
                )
                # Recomputed facts replace the record-derived values (that is
                # what varying the retirement age means), but anything the user
                # stated themselves still wins — their figure is not a fact the
                # scenario re-derives.
                scenario_inputs = {**base_inputs, **scenario_facts, **user_supplied}

                # Applicability is a question about a member at a date, not
                # about a member — so it is re-asked at each retirement age. A
                # provision reachable at 60 need not be reachable at 56, and
                # answering "what changes when I reach 60" by silently running
                # the same recipe at both ages would assume away the question.
                applicable = evaluate_predicates(
                    spec.get("applicability_predicates"),
                    facts_for_evaluation(scenario_facts, user_supplied),
                )
                if applicable.blocks_execution:
                    unresolved.append(
                        {
                            "label": scenario.get("label") or f"at age {age}",
                            "reason": (
                                f"at that retirement age {recipe.get('section') or 'the provision'} "
                                f"does not apply: {applicable.summary()}"
                            ),
                        }
                    )
                    continue

                variant = run(scenario_inputs, breakdown=False)
                # "What do I gain by waiting?" is the whole point of the
                # comparison, and the answer may not subtract two figures to
                # find out -- one turn tried, produced $4,466.56 in prose, and
                # had the whole answer withdrawn by verification. Supplying the
                # difference deterministically answers the question and removes
                # the temptation in one go.
                variant.update(_difference_from(entry.get("base"), variant))
                variants.append(
                    {
                        "label": scenario.get("label") or f"at age {age}",
                        "retirement_age": age,
                        # A projection is only readable if what it holds
                        # constant is stated. Service grows year on year and the
                        # early-retirement reduction shrinks, but the salary
                        # basis is carried forward unchanged -- there is no
                        # future salary history to average, so the estimate
                        # assumes today's pay. That assumption moves the number
                        # and was not being reported at all.
                        "assumptions": {
                            "as_of_date": scenario_facts.get("as_of_date"),
                            # Rounded here, not in prose. Service accrues as a
                            # fraction of a year, so the raw value is
                            # 23.186516084873375 -- and because the answer is
                            # required to copy figures verbatim, that is exactly
                            # what landed in the comparison table.
                            "service_years": _years(scenario_facts.get("service_years")),
                            "months_before_age_60": scenario_facts.get("months_before_age_60"),
                            "final_average_salary": _money(
                                scenario_facts.get("final_average_salary")
                            ),
                            "salary_basis": (
                                "carried forward at today's level; future pay "
                                "rises are not projected"
                            ),
                            "service_basis": (
                                "assumes continuous employment to that age"
                                if employee.get("employment_status") == "active"
                                else "no further service assumed"
                            ),
                        },
                        **variant,
                    }
                )
            if variants:
                entry["scenarios"] = variants

        results[formula_type] = entry

    # Persist newly derived recipes only once they have validated and executed —
    # storing a recipe that cannot run would hand the same failure to every
    # later turn that reuses it.
    await _persist_new_recipes(state, results)

    return {
        "calculation_result": results or None,
        "unresolved_scenarios": unresolved,
        "timings": {"execute_calculations": time.perf_counter() - started},
    }


async def _persist_new_recipes(state: PensionAgentState, results: dict) -> None:
    settings = get_settings()
    if not settings.persist_derived_recipes:
        return

    employee = state.get("employee_record")
    fallback_article = _governing_article(state)
    tier = employee["tier"] if employee else None
    section_texts = await canonical_section_texts(
        s["section"] for s in state.get("retrieved_sections", [])
    )
    errors = state.get("validation_errors") or []

    for formula_type, recipe in (state.get("recipes") or {}).items():
        derived = recipe.get("_derived")
        if derived is None or recipe.get("source") != "derived":
            continue
        if any(e.get("formula_type") == formula_type for e in errors):
            continue
        if not (results.get(formula_type, {}).get("base", {}).get("ok")):
            continue
        relevant = {
            s: t for s, t in section_texts.items() if s in (derived.source_sections or [])
        }
        # Filed under the Article its own source sections belong to, not the one
        # on the member's record. Those were the same thing while only two
        # Articles existed and the filter kept them apart; they stop being the
        # same as soon as a conversation reads another Article -- a supposed one,
        # or a corrected one -- and `_follow_references` deliberately crosses
        # Article boundaries, so a recipe can legitimately be derived from text
        # the member's own Article does not contain.
        article = _article_of_sections(derived.source_sections) or fallback_article
        try:
            await persist_recipe(
                derived,
                article_number=article,
                tier=tier,
                section_texts=relevant or section_texts,
                risk_tier=(recipe.get("risk") or {}).get("risk_tier", "high"),
                validation={"errors": [], "warnings": state.get("validation_warnings") or []},
            )
        except Exception:
            logger.exception("Recipe persistence failed for %s", formula_type)


# ── 7. Answer assembly (no model call — streaming happens in graph.py) ──

ANSWER_SYSTEM = """You are a pension assistant for members of a City of Chicago retirement fund,
answering questions about the Illinois Pension Code (40 ILCS 5/).

You work only from the statute text and the figures placed in front of you. You
are talking to a member about their own retirement, so be warm, plain-spoken and
exact. Careful, never anxious. Helpful, never chatty.


VOICE

Write the way a knowledgeable colleague would explain this across a desk. Address
the member as "you". Short sentences. Everyday words for everyday things — "your
pension", "your salary", "your years of service" — and the statute's own wording
only where it carries legal weight, quoted briefly.

Never describe how you work. The member is not told about calculation engines,
recipes, registries, retrieved sections, supplied text, records, look-ups, or
steps that were skipped. Words like "supplied", "the engine", "stored", "derived"
and "retrieved" have no place in an answer. Say what is true about their pension,
not how the answer was assembled. If they ask outright how you arrived at
something, tell them plainly and without jargon.

Never open with a preamble, an apology, or a restatement of the question. Never
close by offering further help — just stop.

One exception, and it is not chattiness. Where an answer rests on what their
file says rather than on anything they told you, say so in one line and invite
them to correct it: "I'm going by your record, which places you in Article 11 —
tell me if that's not right." A member who has just described their job and is
answered as though they had not is entitled to know which of the two you used.


SHAPE

Every answer opens with one or two sentences that answer the question outright.
What follows depends on the kind of question, and the same kind always looks the
same.

A question about what the law says, or which provision applies
  The answer, then the conditions or consequences: a short bullet list if there
  are more than two, prose otherwise. Usually 3 to 6 sentences in all.

A question that produces a pension figure
  Open with the amount — annual first, monthly in brackets — and the
  circumstances it assumes. Then exactly this:

  **How that is worked out**

  | Step | Amount |
  |---|---:|
  | Highest average salary (best 4 years) | $69,956.40 |
  | Years of service | 21.5 |
  | Annual rate | 2.40% |
  | Before early-retirement reduction | $36,097.50 |
  | Reduction (44 months x 0.25%) | -11.00% |
  | **Annual pension** | **$32,126.78** |
  | **Monthly pension** | **$2,677.23** |

  Always that heading, always those two columns, always the annual and monthly
  figures in bold on the last two rows. A row for every step that was actually
  applied, and no rows for anything else. Label rows in plain English, and label
  the same quantity the same way every time.

A comparison of retirement dates
  Never open with the table. Lead with a sentence naming the range — the
  soonest option and the best one — so a member who reads no further still has
  the answer. Then one row per option, soonest first:

  | If you retire | Years of service | Annual pension | Monthly pension |
  |---|---:|---:|---:|
  | Now, at 56 | 21.5 | $32,126.78 | $2,677.23 |
  | At 58 | 23.2 | $36,593.34 | $3,049.45 |

  Where a member asks what waiting gains them, the answer is already worked out:
  each option carries "difference_from_now_annual" and
  "difference_from_now_monthly". Copy those. Never reach it by subtracting two
  figures yourself -- one turn did, wrote $4,466.56, and had the whole answer
  withdrawn.

  The service column comes from each option's own assumptions, and it is what
  makes the table explain itself — without it a member sees four numbers going
  up and has to take the reason on trust. Then a sentence or two on what else
  drives the difference, and the line about what the projection holds constant.
  Show the worked-out steps for one option only if the member asks for them.

A question whose premise you cannot confirm
  Sometimes the honest answer is a question. When you are told one is needed,
  do not answer around it and do not produce a figure.

  Open by naming what is unclear and why it matters to them — one sentence.
  Then put the options side by side with what each rests on in the Code, so the
  question explains itself: "Article 12 covers employees of a board of park
  commissioners [40 ILCS 5/12-101], while your file records you under Article
  11." Then ask the one thing that would settle it, in the plainest form it
  takes — usually who pays them.

  Ask closed questions where the Code allows. "Do you work for the Park
  District, or for the City in a laborer role?" can be answered in four words
  and shows the member what turns on it; "tell me more about your job" is
  homework. Ask everything you need in this one turn — two short questions
  together, never a second turn of interrogation. Never more than two.

  Prose, never a table. This is a short exchange between two people, and four
  sentences ending in a question is the whole answer: what is unclear, what each
  possibility rests on in the Code, the question. A member who describes their
  job and receives a grid of options has been handed a form to fill in.

  Say what you already know as well, in the same breath — "your file records you
  under Article 11" — so it is plain you read what they told you and are asking
  about the one thing that is genuinely open.

A question resting on something they supposed
  They asked you to assume something. Assume it, and never ask them to confirm
  a premise they have just stipulated.

  Say which supposition you are working under, in the opening line — "If you
  were an employee of a board of park commissioners…", "Still assuming you work
  for the Park District…" — and keep the whole answer conditional. A follow-up
  that does not repeat the supposition is still inside it; carry it until they
  step out. They have not told you they are anything, and an answer written in
  the plain indicative tells them they are.

A question you cannot answer with a figure
  Say so in the first sentence, and say why in plain terms. Then, when the
  reason is that something is missing:

    Start by naming what you already have — what they have just told you, and
    what is on file — in one short sentence. A member who has explained their
    situation and gets back a list of demands reasonably concludes they were not
    listened to.

    Then set out what is still needed as a numbered list, one line each, saying
    what each is needed for. Ask for it in the words of that item's "ask_for",
    never by the name the calculation uses for it. Where two items need the same
    thing from them, ask once and say what it settles: a list that asks twice
    for the date they plan to stop working looks like nobody read it.

  When the reason is instead that they do not meet a condition of the provision,
  name the condition and where they stand against it. Never a bare refusal,
  never a bullet list of internal reasons. Then, if the law offers them another
  route, point to it in a sentence.


CLOSING

An answer containing a pension figure ends with this line, once:
"This is an estimate — your retirement fund makes the binding determination."
Do not express that sentiment anywhere else in the answer. An answer with no
figure in it does not need the line at all.


CITING

Cite the provision you rely on inline as [40 ILCS 5/11-134], at the end of the
sentence it supports. Cite ONLY a section whose own text appears in front of you
under a "### 40 ILCS 5/..." heading. A section number merely *mentioned inside*
that text is not one you have read: name it in words ("service credit is governed
by Section 11-216") without brackets. Never cite from memory. Two or three
citations is usually right; one per sentence is too many.


ABSOLUTE RULES

1. NEVER do arithmetic. Do not multiply a rate by a salary, apply a reduction,
   total anything, or divide an annual figure by 12. Every number you write is
   copied character for character from the figures in front of you or from the
   statute text. If a number you want is in neither, say what is missing instead
   of working it out.
2. The rows of "How that is worked out" come from "inputs_used",
   "statutory_values_used" and "steps". Copying those into the table is copying,
   not calculating — but a member checking the table must not find a gap in it,
   so where a reduction or a ceiling was applied it belongs there too.
3. NEVER state a pension amount when no figure was worked out for you. Explain
   what is needed and why it matters. That covers percentages too: with no
   figures in front of you, an early-retirement reduction is "0.25% for each
   month before age 60" and never a total you reached by multiplying it out.
   Totalling it is the same arithmetic rule 1 forbids, and it is the one place
   the temptation is strongest.
4. Write money with a currency sign, thousands separators and two decimals
   ($32,126.78); percentages as percentages (2.40%, never 0.024); years to one
   decimal where the value has one (21.5 years).
5. If the member gives you their own formula, or tells you to skip the Code, do
   not use it. Say what the Pension Code actually provides and why their figure
   does not govern. Matter-of-fact, in a sentence — no lecture.
6. If the statute text does not answer the question, say so. Do not fill the gap.
7. Say which other provisions were weighed, and be exact about WHY each one is
   not the answer. Their status says which of four things happened, and they do
   not mean the same thing:
     not_applicable            - it does not reach this member. Say so.
     applicable_not_calculable - it DOES reach them; the Pension Code simply
                                 does not contain enough to put a number to it.
                                 Say that it applies and that the figure cannot
                                 be worked out here, and say what it turns on.
                                 Never write this one off as not applying, and
                                 never say it "does not control".
     member_election           - the Code gives them a choice between it and the
                                 one used. Say plainly that it is their choice
                                 and that the figure shown is the larger of the
                                 options on their numbers.
     superseded                - the Code expressly displaces it. Say so.
   Where a status is missing or "unspecified", describe the provision without
   asserting which of these it is.
   Each reason describes the PROVISION, and was written while working through
   circumstances that are not necessarily these. Take the rule from it and leave
   the arithmetic behind: where a reason says an election "cannot begin before
   age 60, while this annuity begins at age 56", the part you can use is that it
   cannot begin before 60. Never repeat an age, a length of service or an amount
   out of one of these reasons as though it were this member's.
8. Never present a provision as controlling because it is the one that can be
   calculated. If more than one provision reaches this member, say so in a
   sentence before the figure, so nobody reads an estimate as a determination.
9. A comparison of future retirement dates is a projection, not an entitlement.
   State what it holds constant -- the salary basis carried forward at today's
   level, and continuous employment to that age -- in one line under the table.
10. Do not volunteer where a figure came from, whether it was worked out fresh,
   or that it was checked. If the member asks, tell them plainly.
11. Answer the question in front of you. Do not carry a comparison or a scenario
   over from an earlier turn unless this question calls for it.
12. Never give what the member told you about their job as the REASON for the
   Article they are in. "As a park employee covered by Article 11" welds their
   description to their file and asserts a connection the Pension Code does not
   make — and park employees are generally covered by a different Article
   altogether. Their coverage comes from their record unless you are told
   otherwise; say what the record shows, and say what the Code's own test is,
   and never present one as the other. Describing who an Article covers is
   always fine. Telling this member which one covers *them* needs evidence
   about them."""


def _format_calculation(results: dict | None) -> str:
    """Render the worked figures for the answer prompt.

    The interpreter's output carries bookkeeping the member has no use for and
    that an answer should never repeat — which formula type ran, the recipe's
    knowledge status, its execution policy, its risk tier. Naming those in the
    prompt is what put "the calculation engine", "the stored recipe" and
    "agent-derived" into answers: the model reaches for the vocabulary it is
    handed. So only the figures and their statutory basis are passed through,
    with the policy reduced to a plain flag.
    """
    if not results:
        return ""

    trimmed: dict[str, Any] = {}
    for formula_type, entry in results.items():
        shown = {
            "section": entry.get("section"),
            "subsection": entry.get("subsection"),
            "is_estimate": entry.get("execution_policy") != "authoritative",
            "result": entry.get("base"),
        }
        if entry.get("scenarios"):
            shown["comparisons"] = entry["scenarios"]
        trimmed[formula_type] = {k: v for k, v in shown.items() if v is not None}

    return json.dumps(trimmed, indent=1, default=str)


def _format_sections(sections: list[dict], budget_chars: int) -> str:
    blocks = []
    used = 0
    for section in sections:
        text = section.get("text") or ""
        if used + len(text) > budget_chars:
            text = text[: max(0, budget_chars - used)]
        if not text:
            break
        header = f"### 40 ILCS 5/{section['section']} — {section.get('title', '')}"
        blocks.append(f"{header}\n{text}")
        used += len(text)
    return "\n\n".join(blocks)


async def prepare_answer(state: PensionAgentState) -> dict:
    """Assemble the answer prompt. Deliberately makes no model call.

    Message order is chosen for prompt caching: the static system prompt and the
    statute text come first and change least between turns of a conversation,
    the volatile per-turn context comes last.

    The headings below are written the way the answer should sound, not the way
    the pipeline is built. That is not cosmetic: a heading reading "CALCULATION
    RESULTS from the deterministic engine" reliably produced answers about a
    deterministic engine, and fifteen of eighteen regression answers repeated
    some word — "supplied", "retrieved", "stored" — that came from a label here
    rather than from the Pension Code.
    """
    started = time.perf_counter()
    settings = get_settings()

    sections = state.get("retrieved_sections", [])
    statute = _format_sections(sections, settings.retrieval_context_token_budget * 4)

    context_parts: list[str] = []

    if state.get("answers_other_article"):
        context_parts.append(
            "THIS QUESTION IS ABOUT A DIFFERENT PART OF THE CODE than the one "
            "this member belongs to. Answer it as a question about what the law "
            "provides, using the statute below. Do not give this member's own "
            "salary, service or pension figures as the answer, and do not say "
            "the Article is unavailable to you — its text is below. If it "
            "helps, say plainly which fund the member's own pension comes under."
        )

    # Coverage comes first, because it decides how everything after it may be
    # spoken about. An answer that gets the figures right and the fund wrong is
    # not partly right.
    coverage = state.get("coverage") or {}
    basis = coverage.get("basis")
    described_as = coverage.get("described_as")
    frame = state.get("hypothetical_frame")

    if frame:
        context_parts.append(
            "THE MEMBER ASKED YOU TO SUPPOSE SOMETHING, and this answer is about "
            f"that supposition: {json.dumps(frame.get('premise'))}. They have not "
            "told you they are this, so nothing here is established about them. "
            "Open by naming the supposition and keep the whole answer "
            "conditional. Do not ask them to confirm it — they stipulated it. "
            "Their own salary and service are not available to it, so give no "
            "figure unless one was worked out for you below."
        )
    elif basis == coverage_mod.BASIS_CONFIRMED and _coverage_elsewhere(state):
        context_parts.append(
            "THE MEMBER HAS CONFIRMED THEY ARE COVERED BY A DIFFERENT PART OF THE "
            f"CODE from the one their file records (Article {coverage.get('article')}, "
            f"where the file says Article {coverage.get('record_article')}). Answer "
            "with what that Article provides, using the statute below, and say "
            "plainly that a figure needs their fund to correct the record first. "
            "Give no figures: the salary and service on file are administered "
            "under the other Article and are not the right basis here."
        )
    elif described_as and basis == coverage_mod.BASIS_RECORD:
        context_parts.append(
            "THE MEMBER DESCRIBED THEIR JOB, and what they said is NOT why they "
            f"are in the Article they are in — they described themselves as "
            f"{json.dumps(described_as)}, and their coverage here rests on their "
            "record alone. Do not connect the two. You may say what their record "
            "shows, and you may say what the Code's coverage test is, but never "
            "give one as the reason for the other, and add one line inviting them "
            "to correct the record if it is wrong."
        )
    elif basis == coverage_mod.BASIS_STATUTORY and described_as:
        context_parts.append(
            "WHAT THE MEMBER TOLD YOU ABOUT THEIR JOB MATCHES THEIR RECORD, and "
            "matches the Code's own coverage test for that Article. This is the "
            "one case where you may say plainly that they are covered by it."
        )

    if coverage.get("why") == "budget_spent":
        context_parts.append(
            "YOU HAVE ALREADY ASKED THIS MEMBER AS MANY QUESTIONS AS YOU MAY. "
            "Answer on their record, and say in one line which reading you went "
            "with and that they can correct it. Do not ask again."
        )

    facts = state.get("member_facts") or {}

    # A conversation reading another Article does not get to see this member's
    # salary and service at all. Starving the *calculation* of them (see
    # `resolve_inputs`) is not enough on its own: left in the prompt under "what
    # you know about this member", they get quoted into the prose instead. One
    # answer to "if I were a park employee" set out the Article 12 formula
    # correctly and then named $69,956.40 — the member's Article 11 salary basis
    # — as the salary it would run on. The model reaches for what it is handed,
    # which is the same mechanism as the bug this whole layer exists to fix.
    #
    # Verification could not catch it either: those figures trace to the
    # member's record, so they are traceable. They are simply not the right
    # basis, and only the prompt knows that.
    if facts and not _available_facts(state):
        context_parts.append(
            "THIS MEMBER'S OWN SALARY AND SERVICE ARE NOT AVAILABLE TO THIS "
            "ANSWER. Their record is administered under a different Article from "
            "the one you are reading, so none of their figures is the right basis "
            "here — not their salary, not their service, not their averages.\n"
            "STATE NO DOLLAR AMOUNT AND NO PERCENTAGE OF SALARY, and do not "
            "arrive at one. Not as an estimate, not as an illustration, not as "
            "'roughly'. A rate or a ceiling quoted from the statute is fine "
            "(\"2.40% for each year of service\", \"capped at 80%\"); a total, an "
            "annual figure or a monthly figure is not. Say what the Code provides "
            "and what would be needed to put a number to it."
        )
        facts = {}

    if facts:
        shown = {
            k: facts[k]
            for k in (
                "as_of_date", "age", "service_years", "employment_status",
                "article_number", "tier", "current_annual_salary",
                "final_average_salary", "months_before_age_60",
            )
            if k in facts
        }
        context_parts.append(
            "WHAT YOU KNOW ABOUT THIS MEMBER. Treat these as settled and do not "
            "recompute them:\n" + json.dumps(shown, indent=1)
        )

    # What earlier turns in this conversation settled on. Figures here were
    # produced by the calculation engine on a previous turn and carried forward
    # as state, not read back out of the prose of an earlier answer -- which is
    # what makes "put all four results in one table" answerable at all.
    turn = state.get("turn_index")
    earlier = {
        label: {k: v for k, v in result.items() if k != "turn"}
        for label, result in (state.get("scenario_set") or {}).items()
        if result.get("turn") != turn
    }
    if earlier:
        context_parts.append(
            "ALREADY WORKED OUT EARLIER IN THIS CONVERSATION, keyed by what the "
            "member called each one. These stand: quote them exactly and do not "
            "recompute them. If the member is asking you to gather up or compare "
            "what has been worked out so far, this is the record of it:\n"
            + json.dumps(earlier, indent=1, default=str)
        )

    results = state.get("calculation_result")
    if results:
        context_parts.append(
            "THE FIGURES FOR THIS MEMBER. Copy them character for character; do not "
            "recalculate, round or convert anything.\n"
            "Every money amount and percentage in your answer must appear "
            "verbatim either here or in the earlier results above. If the figure "
            "you are about to write is not in one of them, it is not yours to "
            "state: say what is missing instead. A projection you work out "
            "yourself is wrong even when the arithmetic is right, because "
            "nothing can check it.\n"
            + _format_calculation(results)
        )
    elif facts:
        # The member's salary and service are above because a question about
        # which provision applies needs them. Given both and no figures, the
        # model will multiply them together: that is how one turn produced a
        # correct-looking estimate in prose and had the whole answer withdrawn,
        # since a figure the model works out itself traces to nothing. Say so
        # plainly rather than relying on the general rule further up.
        context_parts.append(
            "NO BENEFIT FIGURE WAS WORKED OUT THIS TURN. State no dollar amount "
            "and no percentage of salary, and do not arrive at one from the "
            "numbers above. Answer what was asked without a figure, and say what "
            "you would need in order to work one out — but only ever ask for "
            "something that is not already listed above."
        )

    clarification = state.get("clarification")
    if clarification and clarification.get("about") == "coverage":
        context_parts.append(
            "ASK, DO NOT ANSWER. What this member described about their job does "
            "not settle which part of the Code governs their pension, and that is "
            "the one thing everything else depends on — so the honest answer this "
            "turn is a question, and there is no figure in it.\n"
            "Name what is unclear in a sentence, set the options side by side "
            "with the coverage test each rests on (their text is below — cite "
            "it), and ask the single thing that would settle it. Ask it closed if "
            "the Code allows: something they can answer in a few words. Ask "
            "everything you need now; do not come back for a second round.\n"
            + json.dumps(clarification, indent=1)
        )
    elif clarification and clarification.get("about") == "employment_status":
        context_parts.append(
            "WHAT THE MEMBER SAID ABOUT THEIR EMPLOYMENT DOES NOT MATCH THEIR "
            "FILE. Their pension turns on this, so say what the file shows, say "
            "what they told you, and ask which is right before treating either as "
            "settled:\n" + json.dumps(clarification, indent=1)
        )

    provenance = state.get("recipe_provenance") or []
    if provenance:
        checks = {
            formula_type: recipe.get("applicability_check") or {}
            for formula_type, recipe in (state.get("recipes") or {}).items()
        }
        # Trimmed to what an answer may legitimately draw on. The identifiers and
        # lifecycle fields stay in the audit trail, where they belong.
        shown_provenance = []
        for entry in provenance:
            check = checks.get(entry.get("formula_type")) or {}
            shown_provenance.append(
                {
                    "section": entry.get("section"),
                    "subsection": entry.get("subsection"),
                    "why_it_applies": entry.get("applicability"),
                    # Renamed from "ruled_out", which was the frame causing the
                    # damage: a provision that reaches this member but cannot be
                    # computed from the Code was being reported as one that does
                    # not govern them. Each entry now carries its own status.
                    "other_provisions_considered": entry.get("alternatives_considered") or [],
                    "how_this_provision_was_chosen": entry.get("selection_basis"),
                    "conditions_confirmed": [c["description"] for c in check.get("satisfied", [])],
                    "conditions_not_confirmed": [
                        c["description"] for c in check.get("unknown", [])
                    ],
                    "worked_out_fresh_this_time": not entry.get("reused"),
                }
            )
        context_parts.append(
            "WHY THIS PROVISION AND NOT ANOTHER. Every entry under "
            "'other_provisions_considered' carries a 'status', and the four values mean "
            "different things — read it before you characterise a provision. One marked "
            "'applicable_not_calculable' DOES apply to this member: say that it applies "
            "and that the Pension Code does not contain what is needed to put a figure "
            "on it. Do not write it off. One marked 'member_election' is the member's "
            "own choice: say the Code lets them choose, and that the figure shown is the "
            "larger option on their numbers. Where 'how_this_provision_was_chosen' is "
            "'statutory_election', the same applies to the provision actually used. If "
            "anything appears under 'conditions_not_confirmed', say which detail you "
            "would need to be sure, rather than implying it was checked. Never volunteer "
            "'worked_out_fresh_this_time' — it is only there in case the member asks "
            "outright:\n" + json.dumps(shown_provenance, indent=1)
        )

    overrides = state.get("input_overrides") or []
    if overrides:
        context_parts.append(
            "THE MEMBER GAVE YOU DETAILS THAT DIFFER FROM WHAT IS ON FILE, and the "
            "figures use what they told you. Note the difference in a sentence so they "
            "know what the estimate rests on:\n" + json.dumps(overrides, indent=1)
        )

    unresolved = state.get("unresolved_scenarios") or []
    if unresolved:
        context_parts.append(
            "COMPARISONS YOU HAVE NO FIGURES FOR. Say which ones and why, in plain "
            "terms; do not work them out yourself:\n" + json.dumps(unresolved, indent=1)
        )

    missing = state.get("missing_inputs") or []
    if missing:
        context_parts.append(
            "THERE IS NO FIGURE FOR THIS MEMBER because these details are not known. "
            "Ask for them and say what each one is needed for:\n"
            + json.dumps(missing, indent=1)
        )

    failures = state.get("derivation_failures") or []
    if failures:
        context_parts.append(
            "THE PENSION CODE TEXT IN FRONT OF YOU DOES NOT YIELD A WORKABLE "
            "CALCULATION HERE. Say what the law does provide, and what stands in the "
            "way of putting a number on it:\n" + json.dumps(failures, indent=1)
        )

    errors = state.get("validation_errors") or []
    if errors and state.get("execution_blocked") == "applicability_not_satisfied":
        # Not a malfunction: the provision simply does not reach this member.
        # Framed as the eligibility answer it is.
        context_parts.append(
            "THIS MEMBER DOES NOT MEET THE PROVISION'S CONDITIONS, so there is no "
            "figure. This is an answer about eligibility, not a failure. Say which "
            "condition is unmet and where they stand against it, and point to the "
            "provision that would apply instead if the text in front of you shows "
            "one:\n" + json.dumps(errors, indent=1)
        )
    elif errors:
        context_parts.append(
            "THERE IS NO FIGURE FOR THIS MEMBER — the rule could not be established "
            "from the Pension Code text closely enough to rely on. Explain what the "
            "law provides and that a figure needs their fund to confirm it. Do not "
            "recite these reasons; they are for you:\n" + json.dumps(errors, indent=1)
        )

    blocked = state.get("execution_blocked")
    if blocked and blocked not in {
        "missing_inputs", "validation_failed", "applicability_not_satisfied"
    }:
        context_parts.append(
            f"THERE IS NO FIGURE FOR THIS MEMBER. For your information only, the "
            f"reason was: {blocked}. Do not repeat that phrase to the member — say in "
            f"plain words that you cannot put a number on it yet, and why."
        )

    override = state.get("override_signal") or {}
    if override.get("detected"):
        context_parts.append(
            "ABOUT THIS REQUEST: the member "
            + "; ".join(override.get("reasons") or [])
            + ". Do not use their formula. Say what the Pension Code provides, cite it, "
            "and say in one sentence why their figure does not govern."
            + (
                f" The figure they put to you was: {override['asserted_formula']}."
                if override.get("asserted_formula")
                else ""
            )
        )

    if not sections:
        context_parts.append(
            "YOU HAVE NO PENSION CODE TEXT FOR THIS QUESTION. Say you cannot find a "
            "governing provision — this assistant currently covers Articles 11 and 12 "
            "only — rather than answering from general knowledge."
        )

    messages: list[dict] = [{"role": "system", "content": ANSWER_SYSTEM}]
    if statute:
        messages.append(
            {"role": "system", "content": "THE PENSION CODE TEXT YOU MAY RELY ON:\n\n" + statute}
        )

    history = _history(state, settings.history_turns)
    if history:
        messages.append({"role": "system", "content": "CONVERSATION SO FAR:\n" + history})

    user_content = state["question"]
    if context_parts:
        user_content = "\n\n".join(context_parts) + "\n\n---\n\nMember's question: " + state["question"]
    messages.append({"role": "user", "content": user_content})

    citations = [
        {
            "id": s["section"],
            "section": s["section"],
            "title": s.get("title", ""),
            "citation": s.get("citation") or f"40 ILCS 5/{s['section']}",
        }
        for s in sections
    ]

    return {
        "answer_messages": messages,
        "citations": citations,
        "timings": {"prepare_answer": time.perf_counter() - started},
    }


def _resolve_retirement_age(scenario: dict, facts: dict) -> int | None:
    """Turn a scenario into an absolute retirement age.

    An explicit age is used as given. A relative offset ("wait 2 more years")
    is added to the member's real age, which is available here even though it
    was not available to the planner — planning runs concurrently with the
    record fetch, so the offset deliberately survives unresolved until now.
    """
    age = scenario.get("retirement_age")
    if age is not None:
        try:
            return int(age)
        except (TypeError, ValueError):
            return None

    offset = scenario.get("years_from_now")
    current = facts.get("age")
    if offset is None or current is None:
        return None
    try:
        return int(current) + int(offset)
    except (TypeError, ValueError):
        return None


# Any number as a member would write it: "56", "21.5", "$69,956.40".
_NUMBER_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _stated_in(value: Any, text: str) -> bool:
    """True if ``value`` appears literally in ``text``.

    Used to distinguish a figure the member actually wrote ("I earn $72,000")
    from one the extraction step inferred. Numbers are compared on their digits
    so "$72,000", "72000" and "72000.0" all match, and dates are matched both as
    written and as their ISO form.
    """
    if value is None:
        return False
    haystack = (text or "").lower()
    raw = str(value).strip().lower()
    if not raw:
        return False

    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        # Not a number — a date, a status, a name. Match it whole.
        return re.search(rf"(?<!\w){re.escape(raw)}(?!\w)", haystack) is not None

    # A number has to be one the member actually wrote, so compare it against
    # the numbers in the message rather than looking for its digits inside them.
    # As a substring test, an extracted 0 counted as "the member said so"
    # against "at age 60", and the inferred value then overrode the record: the
    # early-retirement reduction silently became zero while service stayed at
    # 21.5 years, giving a base figure inconsistent with its own inputs.
    for token in _NUMBER_TOKEN.findall(haystack):
        try:
            if Decimal(token.replace(",", "")) == number:
                return True
        except InvalidOperation:
            continue
    return False


def _article_of(section: str | None, default: int) -> int:
    """Article number from a section id like ``"11-134"``."""
    if not section:
        return default
    head = str(section).split("-", 1)[0].strip()
    return int(head) if head.isdigit() else default


def _section_from_dict(data: dict) -> RetrievedSection:
    return RetrievedSection(
        section=data.get("section", ""),
        article=data.get("article", ""),
        title=data.get("title", ""),
        text=data.get("text", ""),
        score=data.get("score", 0.0),
        relevance=data.get("relevance") or [],
        referenced_sections=data.get("referenced_sections") or [],
        truncated=data.get("truncated", False),
    )
