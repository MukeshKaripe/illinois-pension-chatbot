"""LangGraph agent nodes (§9.3).

The rule the whole design turns on: **the model plans, retrieves and writes
prose; it never performs authoritative arithmetic.** Every benefit figure comes
out of the deterministic DSL interpreter, from a recipe whose every constant
cites statute text the model was actually shown.

Node order matters and is not the order the first implementation used. Recipe
resolution has a hard dependency on retrieval (you cannot derive a rule from
text you have not fetched) and input resolution has a hard dependency on recipe
resolution (you cannot know which inputs are needed until the recipe declares
them). The original graph fanned all four out concurrently from the planning node,
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
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from pension_chat.agent import coverage as coverage_mod
from pension_chat.agent import gaps, guardrails, ledger
from pension_chat.agent.derivation import derive_recipe
from pension_chat.agent.facts import build_member_facts
from pension_chat.agent.llm import complete
from pension_chat.agent.registry import (
    RegistryRecipe,
    lookup_recipe,
    note_contested,
    note_reuse_later,
    persist_recipe,
    verify_freshness,
)
from pension_chat.agent.retrieval import (
    RetrievedSection,
    canonical_section_texts,
    extract_article_references,
    extract_section_references,
    fetch_sections,
    retrieve,
)
from pension_chat.agent.section_scope import restricted_sources, scoped_evidence
from pension_chat.agent.state import PensionAgentState
from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_DERIVED,
    SOURCE_RECORD,
    SOURCE_STAND_IN,
    SOURCE_STATED,
    SOURCE_STATUTORY,
    WorkSheet,
)
from pension_chat.calculation.applicability import (
    evaluate_predicates,
    facts_for_evaluation,
)
from pension_chat.calculation.assumption_policy import (
    default_for,
    is_assumable,
    rule_for,
)
from pension_chat.calculation.dsl_interpreter import DSLExecutionError, DSLInterpreter
from pension_chat.calculation.risk_assessor import RiskAssessor
from pension_chat.calculation.statutory import fills_from as statutory_fills
from pension_chat.calculation.thresholds import find_thresholds
from pension_chat.calculation.validator import RecipeValidator
from pension_chat.config import get_settings
from pension_chat.db.models import Employee
from pension_chat.db.session import get_db_session
from pension_chat.records import agent_history, current_provider

logger = logging.getLogger("pension_chat.agent.nodes")


#: The benefits a recipe can be resolved for. Lived in `understanding.py` until
#: the cutover, where it bounded what the turn planner was allowed to ask for;
#: it belongs here now, beside `resolve_recipes`, which is what actually has to
#: find one. `tools.compute_benefit` offers it as its enum, so a benefit the
#: model can name always has a resolver.
BENEFIT_TYPES = (
    "retirement_annuity",
    "survivor_annuity",
    "disability_benefit",
    "refund_of_contributions",
    "service_credit",
)


def _history(state: PensionAgentState, turns: int) -> str:
    """Render the last N conversation turns as plain text."""
    lines = []
    for message in state.get("messages", [])[-(turns * 2 + 1) : -1]:
        role = "User" if message.type == "human" else "Assistant"
        content = str(message.content)
        lines.append(f"{role}: {content[:600]}")
    return "\n".join(lines)


# ── 1. Triage ─────────────────────────────────────────────────────

async def triage(state: PensionAgentState) -> dict:
    """Deterministic gate before anything expensive runs.

    What survives here is only what a regular expression can be trusted with:
    a deny list that is right whenever it fires, a greeting, the section numbers
    the member typed, and the adversarial-instruction scan. Deciding what the
    member *meant* moved to `understand_turn`, because a word list cannot be
    written for that -- "retiring" does not contain "retire", and the vocabulary
    held "spouse" and "widow" but not "wife".

    A refused greeting or poem still costs zero model calls and zero database
    round trips. What it no longer does is refuse by default: an off-topic aside
    the deny list does not name now costs one routing call, which is the price of
    never turning away a real question for want of a substring.
    """
    started = time.perf_counter()
    question = state["question"]
    # A bare "yes" or "ok" is an answer to whatever was asked last turn, and a
    # pleasantry only when nothing precedes it, so the check needs to know
    # whether this turn has history behind it. `messages` always holds at least
    # this turn's own question.
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


# ── 2a. Coverage ──────────────────────────────────────────────────


# How many coverage sections may be pinned into one turn's retrieval. Bounded
# because each is statute text the answer prompt then carries, but large
# enough to cover every candidate the question names.
_MAX_COVERAGE_SECTIONS = 6

# How many sections a named cross-cutting Article contributes on its own query.
# Small: it is supplying the rule the member's Article refers to, not replacing
# their Article's provisions.
_MAX_GENERAL_SECTIONS = 3


def _record_coverage(record_article: str | None, turn: int) -> dict:
    """What we believe when the member has said nothing about themselves.

    The record first. With no record, an assistant deployed for one fund
    (`deployment_article`, D4: MEABF, so Article 8) is answering about that
    fund's members; without one configured, coverage stays unresolved.
    """
    deployed = get_settings().deployment_article
    if record_article:
        article, basis = record_article, coverage_mod.BASIS_RECORD
    elif deployed is not None:
        article, basis = str(deployed), coverage_mod.BASIS_DEPLOYMENT
    else:
        article, basis = None, coverage_mod.BASIS_UNRESOLVED
    return {
        "article": article,
        "basis": basis,
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


def _stand_ins_allowed(state: PensionAgentState) -> bool:
    """May the member's own values stand in for the supposed job's?

    Only under a premise they set up, and only when they asked for it: "if I
    were a Chicago police officer **with my salary and service**". The
    invitation is read by the model and arrives here as a flag; whether it
    permits anything is decided here, because what it permits is borrowing a
    record filed under another Article — the exact move the coverage layer
    exists to stop when nobody asked for it.

    Both halves are load-bearing. Without the premise there is nothing to stand
    in for, so the flag must not quietly relabel an ordinary member's own facts
    as borrowed. Without the invitation the facts stay withheld and every input
    is reported missing, which is today's behaviour and stays the default.
    """
    return bool(state.get("stand_in_facts")) and _coverage_elsewhere(state)


def _coverage_unresolved(state: PensionAgentState) -> bool:
    """Is the routing question still open?

    `conflict: True` means exactly that, and the two ways out both clear it:
    `bind_reply` settles it when the member answers, and `assess_coverage`
    clears it explicitly when the clarification budget is spent and the turn
    proceeds on the record saying what it assumed.
    """
    return bool((state.get("coverage") or {}).get("conflict"))


def _record_facts_usable(state: PensionAgentState) -> bool:
    """Whether this turn may compute on the member's record at all.

    Two ways it may not, and the second was missing on both agents until the
    Phase 4 measurement went looking for it.

    The record is filed under **another** Article -- a premise the member
    supposed, or an Article they confirmed against their file -- so its salary
    and service are administered by a fund this turn is not reading. Unless they
    asked for them as stand-ins.

    Or **which Article governs is still an open question**. The rule is the one
    already written down: a figure is computable exactly when no input is
    borrowed from a record filed under another Article, and while the Article is
    unsettled every input is potentially borrowed. Nothing enforced it: a
    `record` basis is not "elsewhere", so an unresolved conflict stopped no
    calculation. The pipeline passed G31 only because `understand_turn` happened
    to read "how is my pension calculated?" as wanting no figure, and the loop
    quoted the member's own Article 11 annuity at somebody asking which fund
    they were in -- the park-employee failure this layer exists to prevent,
    arriving by a route nobody had closed.
    """
    if _coverage_unresolved(state):
        return False
    return not _coverage_elsewhere(state) or _stand_ins_allowed(state)


async def fetch_employee_record(state: PensionAgentState) -> dict:
    """Load the member's record and salary history for this turn.

    Salary history is not optional detail: Article 11's minimum-annuity formulas
    are written on "the highest average annual salary for any 4 consecutive
    years", which cannot be computed from a single current-salary column.

    **A conversation keeps the record it loaded on its first turn** (spec §6).
    Profiles are editable, and a profile edited mid-conversation must change new
    chats, not this one -- whose earlier answers rest on the facts it started
    with. The first load is pinned in `pinned_record` (durable) and every later
    turn rebuilds its facts from the pin, which also saves a database round trip.
    A pin is used only for the profile it was taken from.
    """
    started = time.perf_counter()
    employee_id = state.get("employee_id")
    if not employee_id:
        return {"employee_record": None, "salary_history": []}

    pinned = state.get("pinned_record") or {}
    if (pinned.get("record") or {}).get("id") == str(employee_id):
        record, history = pinned["record"], pinned["salary_history"]
    else:
        loaded = await _from_provider(str(employee_id)) if current_provider() else await _load_record(employee_id)
        if loaded is None:
            return {"employee_record": None, "salary_history": []}
        record, history = loaded

    facts = build_member_facts(
        record, [(date.fromisoformat(h["effective_date"]), Decimal(str(h["annual_salary"]))) for h in history]
    )

    return {
        "employee_record": record,
        "salary_history": history,
        "member_facts": facts,
        "pinned_record": {"record": record, "salary_history": history},
        "timings": {"fetch_employee_record": time.perf_counter() - started},
    }


async def _from_provider(key: str) -> tuple[dict, list[dict]] | None:
    """The record from the provider in force: PBMS in production, fixtures in evals."""
    provider = current_provider()
    assert provider is not None
    record = await provider.get_record(key)
    if record is None:
        return None
    history = await provider.get_salary_history(key)
    return record.to_agent_record(history), agent_history(history)


async def _load_record(employee_id: str) -> tuple[dict, list[dict]] | None:
    """The record and salary history as stored, or None if unavailable."""
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
                return None

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
        return None

    record = {
        "id": str(employee.id),
        "article_number": employee.article_number,
        "tier": employee.tier,
        "birth_date": employee.birth_date.isoformat(),
        "hire_date": employee.hire_date.isoformat(),
        "employment_status": employee.employment_status,
        "total_service_years": float(employee.total_service_years),
        "current_annual_salary": float(employee.current_annual_salary),
        # Facts only some funds have; `build_member_facts` publishes them.
        "extra_facts": dict(employee.extra_facts or {}),
    }
    return record, history


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

    What survives the starving is what the member said themselves. A figure is
    computable exactly when no input is borrowed from a record filed under
    another Article, and a value they typed is borrowed from no record at all --
    which is why "if I were a park employee with 25 years and $70,000" has
    always been answerable where the bare supposition is not. That route existed
    only for values typed in the current message; the ledger makes it hold for
    values typed three turns ago.

    The ledger is overlaid rather than consulted first: `member_facts` is
    rebuilt from the file every turn, so a ledger entry at the record's own rank
    must not pin a stale copy over a fresher read.
    """
    if not _record_facts_usable(state):
        return dict(ledger.stated_only(state.get("fact_ledger") or {}, ledger.SELF))
    facts = dict(state.get("member_facts") or {})
    held = state.get("fact_ledger") or {}
    sources = ledger.provenance(held, ledger.SELF)
    for name, value in ledger.recall(held, ledger.SELF).items():
        # Strictly better than the record, or the record does not supply it.
        # `outranks` is non-strict on purpose -- a later statement replaces an
        # earlier one -- but here a tie means a ledger copy of the record facing
        # a fresher read of the same file, and the fresher read wins.
        if name in facts and ledger.rank(sources.get(name, "")) >= ledger.rank(
            SOURCE_RECORD
        ):
            continue
        facts[name] = value
    return facts


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
    if named:
        return int(named[0])

    # Nobody said, and there is no record. Ask the text that was actually
    # retrieved rather than reaching for a default: with no record, retrieval
    # ran unfiltered, so the sections that came back are the closest thing to
    # evidence of which Article the question was about. Falling straight to 11
    # picked one fund for an anonymous member on no evidence at all, and then
    # used it as the registry lookup key.
    from_sections = _article_of_sections(
        [s["section"] for s in state.get("retrieved_sections") or []]
    )
    if from_sections is not None:
        return from_sections

    # Nothing agrees, so this is a guess and stays one. Article 11 is the pilot's
    # own fund and the only one with a validated recipe, which makes it the least
    # bad default -- but validation still has to catch a recipe whose sections do
    # not match, and it is the thing to revisit first if that ever slips.
    logger.info("No evidence of a governing Article this turn; defaulting to 11")
    return 11


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


#: Knowledge statuses a reviewer has vouched for. Everything else must meet the
#: validation a new derivation meets today.
_REVIEWED_STATUSES = frozenset({"approved", "validated"})


def _meets_current_validation(recipe: dict) -> bool:
    """Whether a saved recipe may be reused as it stands.

    Recipes saved before quote anchoring carry no quotes, and one of the first
    two saved on the client's registry (2026-09-30) was a Tier 2 formula missing
    §1-160(d)'s reduction below 67. Unless a reviewer approved it, a saved recipe
    whose statutory numbers are not all quoted is re-derived, so the version
    that is reused has had every number checked against the statute's words.
    """
    if recipe.get("status") in _REVIEWED_STATUSES:
        return True
    spec = recipe.get("spec") or {}
    anchors = [*(spec.get("parameters") or {}).values()] + [
        p for p in spec.get("applicability_predicates") or []
        if isinstance(p, dict) and isinstance(p.get("value"), (int, float)) and not isinstance(p.get("value"), bool)
    ]
    return all(isinstance(a, dict) and a.get("quote") for a in anchors)


#: What a derivation searches for, in the Code's own words rather than the
#: question's. "What would their annuity be at 67?" finds the provisions about
#: age 67; the amount is set by the Article's formula section, which reads
#: "... % of final average salary for each year of service".
_FORMULA_QUERIES: dict[str, list[str]] = {
    "retirement_annuity": [
        "retirement annuity equal to a percentage of final average salary for each year of service",
        "age and service annuity amount computed from final average salary and years of service",
    ],
    "survivor_annuity": ["amount of the widow's or surviving spouse's annuity"],
    "disability_benefit": ["amount of the duty disability and ordinary disability benefit as a percentage of salary"],
    "refund_of_contributions": ["refund of accumulated employee contributions with interest on withdrawal"],
    "service_credit": ["periods of service counted as service credit and how service is computed"],
}


async def _derivation_evidence(
    formula_type: str, article: Any, sections: list[RetrievedSection]
) -> list[RetrievedSection]:
    """The governing Article's own provision for this benefit, then the turn's.

    A turn's search runs on the member's question, and the one that derived
    1.25% for a Tier 2 MEABF member never saw §8-138 -- Article 8's formula,
    2.4% in (g-1) -- so the derivation borrowed another fund's rate. Searched
    within the governing Article alone: the cross-cutting Articles are already
    in the turn's evidence, and searching them here surfaces §1-161 and §1-162,
    other funds' optional benefits, above the fund's own formula.
    """
    queries = _FORMULA_QUERIES.get(formula_type)
    if not queries or not article:
        return scoped_evidence(sections, article)
    settings = get_settings()
    try:
        found = await retrieve(
            queries,
            articles=[str(article)],
            top_sections=settings.derivation_top_sections,
            char_budget=settings.derivation_context_token_budget * 4,
            follow_cross_references=0,
        )
    except Exception:
        logger.exception("Derivation search failed for %s under Article %s", formula_type, article)
        return scoped_evidence(sections, article)
    have = {s.section for s in found}
    return scoped_evidence(list(found) + [s for s in sections if s.section not in have], article)


def _at_scenario_age(state: PensionAgentState, facts: dict) -> dict:
    """The member as they would be at the retirement the question is about.

    Which provision governs is a question about a member at a date:
    `compute_benefit(retirement_age=67)` asked about a 58-year-old at 67, and
    handing derivation (and the registry's applicability check) the member as
    of today asked it about an annuity at 58 instead -- which §1-160 does not
    pay, so every Tier 2 turn derived nothing. Execution already re-evaluates
    each scenario at its own age; this makes the recipe step agree with it.

    Only the facts the retirement age moves are replaced (the age names, the
    dates, service accrued to that date), so anything the user stated keeps
    its own value. The first scenario that resolves to an age decides; without
    one, today stands.
    """
    member_facts = state.get("member_facts") or {}
    age = next(
        (
            resolved
            for scenario in state.get("scenarios") or []
            if (resolved := _resolve_retirement_age(scenario, member_facts)) is not None
        ),
        None,
    )
    if age is None:
        return facts
    employee = state.get("employee_record")
    if not employee or not _record_facts_usable(state):
        # A described employee: no record to project, but the annuity still
        # starts at the age asked about.
        return {**facts, "age_at_annuity_start": age}
    history = [
        (date.fromisoformat(h["effective_date"]), Decimal(str(h["annual_salary"])))
        for h in state.get("salary_history") or []
    ]
    at_age = build_member_facts(employee, history, assumed_retirement_age=age)
    moved = {name: value for name, value in at_age.items() if member_facts.get(name) != value}
    return {**facts, **moved}


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
    facts = _at_scenario_age(state, _available_facts(state))

    # Tier keys the registry, so an unknown tier is not a missing number -- it
    # is two provisions. Ask for both and price whichever are genuinely
    # different, rather than naming the branch and refusing to compute either:
    # "If you are Tier 1... If you are Tier 2..." followed by no figure is what
    # the transcripts show, and both figures were available all along.
    #
    # Only the registry lookup branches. Derivation reads the statute, not the
    # member, so running it twice would spend two model calls producing one
    # recipe.
    branches: list[int | None] = [tier] if tier is not None else [1, 2]

    recipes: dict[str, dict] = {}
    provenance: list[dict] = []
    failures: list[dict] = []

    async def from_registry(
        formula_type: str, branch_tier: int | None
    ) -> tuple[dict, dict, Any] | None:
        """This tier's registry recipe, its provenance entry, and the row.

        ``None`` when nothing is on file, the statute has moved under what is,
        or the provision does not reach this member.
        """
        row = await lookup_recipe(formula_type, article, branch_tier)
        if row is not None:
            # Reuse only if the statute it was derived from still reads the same.
            # Fetch the recipe's own source sections rather than intersecting
            # with this turn's retrieval: whether the ranker happened to surface
            # 11-134 today says nothing about whether 11-134 has changed.
            relevant = await canonical_section_texts(row.source_sections or [])
            mismatches = await verify_freshness(row, relevant) if relevant else {}
            if not mismatches:
                recipe = RegistryRecipe(row).to_dict()
                if not _meets_current_validation(recipe):
                    logger.info(
                        "Registry recipe %s predates quote anchoring and is unapproved; re-deriving",
                        row.formula_id,
                    )
                    return None
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
                    entry = {
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
                    return recipe, entry, row
            else:
                logger.info(
                    "Registry recipe %s is stale (%s); re-deriving",
                    row.formula_id,
                    mismatches,
                )
        return None

    async def resolve_one(formula_type: str) -> None:
        found = [
            (branch_tier, result)
            for branch_tier, result in zip(
                branches,
                await asyncio.gather(
                    *(from_registry(formula_type, b) for b in branches)
                ),
                strict=True,
            )
            if result is not None
        ]

        # Two branches are only two provisions when they are different rows.
        # One recipe answering for either tier is one provision, and printing
        # the same figure twice under two conditions tells the member their
        # tier matters when it does not.
        distinct = {result[0]["formula_id"] for _, result in found}
        branching = len(found) > 1 and len(distinct) > 1

        for branch_tier, (recipe, entry, row) in found:
            key = formula_type
            if branching:
                key = f"{formula_type}@tier{branch_tier}"
                recipe = {**recipe, "branch": {"fact": "tier", "value": branch_tier}}
                entry = {**entry, "branch": {"fact": "tier", "value": branch_tier}}
            recipes[key] = recipe
            provenance.append(entry)
            # Counted where the reuse actually happens. A row that
            # `lookup_recipe` returned and freshness then refused has not been
            # reused, and counting it there would make the column measure
            # lookups rather than load.
            note_reuse_later(row)
            if not branching:
                break
        if found:
            return

        derived = await derive_recipe(
            formula_type, await _derivation_evidence(formula_type, article, sections), facts
        )
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


def apply_assumptions(
    sheet: WorkSheet,
    missing: list[dict[str, Any]],
    known: dict[str, Any],
    ceiling: int,
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    """Fill what policy allows, record why, and return what still has to be asked.

    Order is the order `missing` arrives in, so the ceiling bites the least
    important inputs rather than an arbitrary set of them.

    **The ceiling counts inventions, not fills.** A rule that derives its value
    from facts already held creates no new claim about the member, so it passes
    free and is recorded at `derived`. Counting it was how a member who stated
    their age could be refused a figure for want of a second slot -- having
    supplied the very fact the value was computed from.

    Returns the fills, the rank each resolved at, and what is still missing.
    The ranks go to `_input_origins` rather than being re-derived there: the
    calculation card shows them to the member, so the one place that knows how
    a value was arrived at is the place that arrived at it.
    """
    filled: dict[str, Any] = {}
    origins: dict[str, str] = {}
    still_missing: list[dict[str, Any]] = []
    invented = 0

    for item in missing:
        name = str(item.get("name") or "")
        rule = rule_for(name)
        derives = bool(rule and rule.derives)
        if not is_assumable(name) or (not derives and invented >= ceiling):
            still_missing.append(item)
            continue
        value = default_for(name, known)
        if value is None:
            still_missing.append(item)
            continue
        source = SOURCE_DERIVED if derives else SOURCE_ASSUMED
        basis = rule.basis if rule else ""
        sheet.record_fact(name, value, source, basis=basis)
        filled[name] = value
        origins[name] = source
        if not derives:
            invented += 1

    return filled, origins, still_missing


def gap_verdict(
    missing: Sequence[Mapping[str, Any]],
    *,
    filled: Mapping[str, str],
) -> str:
    """What this turn's gaps allow, decided by counting what the ledger holds.

    Four states, and only one of them is a question:

    ==========================  ==========================================
    ledger state                behaviour
    ==========================  ==========================================
    no gaps                     ``compute``
    gaps filled below the       ``compute_with_assumptions`` -- compute,
    ceiling                     and name what was filled
    a gap nobody can fill, or   ``worked_example`` -- work the provision
    too many inventions         through on the Code's own figures, and say
                                what would make it theirs
    any governing gap           ``ask``, or branch and compare
    ==========================  ==========================================

    The count is over *invented* fills only: a value derived from what the
    member said, or cited to the section that states it, is not a gap that was
    papered over. ``filled`` maps each name to the rank it resolved at, which
    code assigned at resolution time -- so the ceiling cannot be evaded by a
    model that would rather answer than ask, and "what I assumed" cannot be
    wrong.

    A governing gap wins outright however few there are. It does not change the
    number, it changes which provision computes it, so filling it in produces a
    real citation about a member who may not exist.
    """
    governing, value = gaps.split(missing)
    if governing:
        return "ask"
    invented = sum(1 for source in filled.values() if source == SOURCE_ASSUMED)
    if value or invented > get_settings().assumption_ceiling:
        return "worked_example"
    if filled:
        return "compute_with_assumptions"
    return "compute"


def _input_origins(
    *,
    resolved: dict[str, Any],
    taken: dict[str, Any],
    filled_origins: dict[str, str],
    sheet: WorkSheet,
    spoken: str,
    stand_in: bool = False,
    held: dict[str, str] | None = None,
) -> dict[str, str]:
    """Where each resolved input came from, decided here because here it is known.

    - Filled while resolving: the rank the fill itself resolved at, carried in
      `filled_origins` -- `derived` for a value computed from facts already
      held, `statutory_parameter` for one the Code states, `assumed` only for
      one nobody supplied.
    - Read out of the conversation by the model: `stated` when the member wrote
      that value anywhere in the recent conversation, otherwise `derived` --
      "I am 60" states an age; the months before 60 are worked out from it.
      Scoped to the current message this labelled a correction made two turns
      ago as something the assistant had worked out, and the calculation card
      then told the member so.
    - Otherwise it came through the facts layer: the sheet's own label when it
      has one (a framing default is `assumed`, an earlier statement `stated`),
      else `record`.

    The calculation card shows these to the member, so a guess here is a false
    claim about their own figure. The card used to reconstruct them from the
    sheet, which holds a stated value only when the model also reported it as a
    revision -- the same question came out "stated" on one run, "derived" on
    the next.
    """
    origins: dict[str, str] = {}
    for name in resolved:
        if name in filled_origins:
            origins[name] = filled_origins[name]
        elif name in taken:
            # The ledger first: `_stated_in` only ever saw the current message,
            # so a figure the member gave three turns ago came back `derived`
            # and the card told them the assistant had worked out a number they
            # had typed themselves. A value they stated stays stated until they
            # supersede it.
            if (held or {}).get(name) == SOURCE_STATED:
                origins[name] = SOURCE_STATED
            else:
                origins[name] = (
                    SOURCE_STATED
                    if _stated_in(taken[name], spoken)
                    else SOURCE_DERIVED
                )
        else:
            fact = sheet.fact(name)
            default = SOURCE_STAND_IN if stand_in else SOURCE_RECORD
            origins[name] = fact.source if fact is not None else default
    return origins


def _member_words(state: PensionAgentState) -> str:
    """Everything the member themselves has typed in this conversation.

    Their words only. The assistant's are not evidence about the member, and
    treating them as such would let a figure the assistant offered come back as
    something the member had stated.
    """
    said = [
        str(message.content)
        for message in (state.get("messages") or [])
        if getattr(message, "type", None) == "human"
    ]
    said.append(str(state.get("question") or ""))
    return "\n".join(said)


def _ledger_update(
    *,
    resolved: dict[str, Any],
    origins: dict[str, str],
    turn: int,
) -> dict[str, Any]:
    """Turn this turn's resolution into something the next turn can read.

    Grouped by source so each group is recorded at its own rank: a value the
    member stated must not be written at the record's rank simply because both
    resolved on the same turn. A resolved input with no recorded origin is
    skipped rather than guessed -- the calculation card shows these, so a guess
    here is a false claim about the member's own figure.
    """
    by_source: dict[str, dict[str, Any]] = {}
    for name, value in resolved.items():
        source = origins.get(name)
        if not source:
            continue
        by_source.setdefault(source, {})[name] = value

    update: dict[str, Any] = {}
    for source, values in by_source.items():
        written = ledger.remember(update, ledger.SELF, values, source, turn)
        if written:
            update = {**update, **written}
    return update


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
    # Values taken from the conversation, so `_input_origins` can say so.
    taken: dict[str, Any] = {}
    outstanding = [spec for name, spec in declared.items() if name not in resolved]

    # Extract whenever an input is still unresolved, and also whenever the user
    # put numbers in their message. A member exploring a hypothetical ("I am 60
    # with 25 years of service") states facts that contradict their record on
    # purpose; answering from the record and ignoring what they said produces a
    # confidently wrong answer to a question they did not ask.
    question_has_figures = bool(re.search(r"\d", state["question"]))

    # A member who corrected the record on an earlier turn must not have to say
    # it again on the turn that computes.
    #
    # `outstanding` is measured against the *record*, so when the file covers
    # every input the conversation was never read at all -- and a correction
    # made two turns ago was silently ignored while the figure was worked from
    # the file. Found by `gold_continuity.yaml` C05: "I actually have 25 years
    # of service, not what your records say", then an annuity computed on 21.5.
    #
    # Bounded twice, because this puts a model call on the critical path. It
    # runs only when the conversation actually holds a number, and only while
    # the ledger has nothing stated for a declared input -- once a value is
    # written down it is read from there, so the cost is paid once rather than
    # on every calculating turn.
    stated_already = set(ledger.stated_only(state.get("fact_ledger") or {}, ledger.SELF))
    # What the member has actually typed, this turn and before it.
    #
    # Built from their own messages rather than from `_history`, which renders
    # a transcript for a prompt: it pairs turns and drops an unpaired one, so a
    # value stated in the very message the assistant has not yet answered is
    # invisible to it. The provenance question here is only "did the member
    # write this down", and their messages are where.
    spoken = _member_words(state)
    history_has_figures = bool(re.search(r"\d", spoken))
    unheard = history_has_figures and not (set(declared) & stated_already)

    if outstanding or question_has_figures or unheard:
        catalogue = [
            {
                "name": spec["name"],
                "type": spec.get("type"),
                "description": spec.get("description", ""),
            }
            # Everything declared, not just what the record could not supply:
            # the point of reading the conversation here is to hear a value
            # that contradicts the file, and an input the file answered is
            # exactly the one being contradicted.
            for spec in (
                declared.values()
                if (question_has_figures or unheard)
                else outstanding
            )
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
            # Written by the member *anywhere in the conversation*, not only in
            # this message. The guard's purpose is that the model may fill a
            # gap but may not quietly replace a value the record supplied
            # unless the member actually typed it -- it read "I am 56" and
            # supplied months_before_age_60 = 48 where the record gives 44.
            #
            # Scoped to the current message, it also threw away a correction
            # made two turns earlier, which is a member being ignored rather
            # than a model being caught.
            if name in from_record and not _stated_in(value, spoken):
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
            taken[name] = value

    # An age the member named is not one fact among several: months before 60,
    # service and the salary window are all computed *from* it. Splicing the age
    # in and leaving its dependents at today's values is how a base figure came
    # to pair age_at_annuity_start 58 with months_before_age_60 44, which is the
    # reduction for a member of 56 -- a figure whose own inputs disagree.
    #
    # Only the facts the record supplied are recomputed. Anything the member
    # stated stays theirs, which is the precedence `execute_calculations`
    # already uses for scenario variants.
    named_age = taken.get("age_at_annuity_start")
    employee_for_age = state.get("employee_record")
    if named_age is not None and employee_for_age and _record_facts_usable(state):
        try:
            at_age = build_member_facts(
                employee_for_age,
                [
                    (date.fromisoformat(h["effective_date"]), Decimal(str(h["annual_salary"])))
                    for h in (state.get("salary_history") or [])
                ],
                assumed_retirement_age=int(named_age),
            )
        except (TypeError, ValueError, KeyError):
            logger.warning("Could not recompute facts at age %r", named_age)
            at_age = {}
        for name in declared:
            if name in from_record and name not in taken and name in at_age:
                resolved[name] = at_age[name]

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

    # A member disputing a value a recipe computes on is the one signal the
    # system gets for free about a recipe being worth reading again. It reached
    # the answer's prose and was then dropped. Not a verdict -- they may be
    # wrong, or the record may be out of date -- so it is counted, and counts
    # prioritise attention rather than decide anything.
    if overrides:
        for recipe in recipes.values():
            if formula_id := recipe.get("formula_id"):
                await note_contested(str(formula_id))

    sheet = WorkSheet.from_state(
        state,
        record_usable=_record_facts_usable(state),
        as_stand_in=_stand_ins_allowed(state),
    )
    filtered = sheet.filter_asks(missing)
    settings_ceiling = settings.assumption_ceiling

    # The Code before the guess. A threshold this Article states is already in
    # the recipe with the section that states it, so it fills a gap at its own
    # rank rather than spending the assumption ceiling -- and the answer names
    # the section instead of saying it could not establish the value.
    statutory = statutory_fills(recipes)
    cited: dict[str, Any] = {}
    cited_origins: dict[str, str] = {}
    outstanding_asks: list[dict[str, Any]] = []
    for item in filtered:
        fill = statutory.get(str(item.get("name") or ""))
        if fill is None:
            outstanding_asks.append(item)
            continue
        sheet.record_fact(fill.name, fill.value, SOURCE_STATUTORY, basis=fill.basis)
        cited[fill.name] = fill.value
        cited_origins[fill.name] = SOURCE_STATUTORY
    resolved.update(cited)

    assumed, assumed_origins, still_missing = apply_assumptions(
        sheet, outstanding_asks, {**facts, **cited}, settings_ceiling
    )
    filled_origins = {**cited_origins, **assumed_origins}
    resolved.update(assumed)

    origins = _input_origins(
        resolved=resolved,
        taken=taken,
        filled_origins=filled_origins,
        sheet=sheet,
        spoken=spoken,
        stand_in=_stand_ins_allowed(state),
        held=ledger.provenance(state.get("fact_ledger") or {}, ledger.SELF),
    )

    return {
        "resolved_inputs": resolved,
        "input_sources": origins,
        "fact_ledger": _ledger_update(
            resolved=resolved,
            origins=origins,
            turn=int(state.get("turn_index") or 0),
        ),
        # An input the sheet holds is not missing, and an input it holds but
        # cannot use under this premise is not missing either -- it is withheld,
        # and saying so is the difference between "tell me your age" and "your
        # file has your age, but I am not using it while we talk about Article 12".
        "missing_inputs": _dedupe_missing_inputs(still_missing),
        # What these gaps allow: compute, compute and say what was filled, work
        # the provision through on the Code's own figures, or ask. Decided here
        # because here the ranks are known, and read by `_describe` so the
        # model is told what to do rather than left to pick.
        "gap_verdict": gap_verdict(
            _dedupe_missing_inputs(still_missing), filled=filled_origins
        ),
        # What the Code supplied, so the answer's block can cite it. Only the
        # fills that were actually used: a parameter that filled nothing is the
        # recipe's own arithmetic and has no business in a list of the member's
        # facts.
        "statutory_fills": [
            {
                "name": fill.name,
                "value": fill.value,
                "section": fill.section,
                "subsection": fill.subsection,
                "basis": fill.basis,
            }
            for fill in statutory.values()
            if fill.name in cited
        ],
        "input_overrides": overrides,
        **sheet.to_state_update(),
        "timings": {"resolve_inputs": time.perf_counter() - started},
    }


def _dedupe_missing_inputs(missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse asks that cash out to the same question.

    Two declared inputs can share an `ask_for` -- `withdrawal_date` and
    `annuity_start_date` both read back as "the date you plan to stop
    working" -- and asking for it twice looks like nobody read the first
    answer. The first entry to name a given `ask_for` is kept exactly as it
    is, `withheld`/`withheld_basis`/`already_asked` included if it carries
    them; later entries asking the same thing are dropped rather than
    merged, so nothing about the surviving entry needs to change.
    """
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in missing:
        ask_for = str(item.get("ask_for") or "")
        if ask_for in seen:
            continue
        seen.add(ask_for)
        deduped.append(item)
    return deduped


# ── 5. Validation ─────────────────────────────────────────────────


async def _parameter_texts(recipes: dict[str, dict]) -> dict[str, dict[str, str]]:
    """The text of every section a recipe's constants cite, by subsection.

    What `_validate_parameters_in_source` checks each constant against. Every
    printed version of a subsection is joined, so a value any version states is
    found. Fetched whole rather than taken from this turn's retrieval, which is
    budget-trimmed and could cut the very subsection a constant cites.
    """
    anchors = [
        anchor
        for recipe in recipes.values()
        for anchor in [
            *((recipe.get("spec") or {}).get("parameters") or {}).values(),
            *((recipe.get("spec") or {}).get("applicability_predicates") or []),
        ]
        if isinstance(anchor, dict)
    ]
    cited = sorted({
        str((anchor.get("source") or {}).get("section") or "").split("(")[0].strip()
        for anchor in anchors
    } - {""})
    if not cited:
        return {}
    texts: dict[str, dict[str, str]] = {}
    for section, chunks in (await fetch_sections(cited)).items():
        by_subsection: dict[str, list[str]] = {}
        for chunk in chunks:
            by_subsection.setdefault(str(chunk.get("subsection_id") or ""), []).append(
                str(chunk.get("content") or "")
            )
        texts[section] = {sub: "\n".join(parts) for sub, parts in by_subsection.items()}
    return texts


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
    #
    # And at the age the question is about: "annuity at 67" for a 58-year-old
    # was blocked by §8-138(g-1)'s "no earlier than age 60", tested against
    # today. Execution re-asks each scenario at its own age; this gate agrees.
    checkable_facts = _at_scenario_age(
        state,
        facts_for_evaluation(_available_facts(state), state.get("resolved_inputs")),
    )

    errors: list[dict] = []
    warnings: list[dict] = []
    highest = "low"
    ranks = {"low": 1, "medium": 2, "high": 3}
    blocked: str | None = None
    can_execute = True

    parameter_texts = await _parameter_texts(recipes)

    for formula_type, recipe in recipes.items():
        spec = recipe.get("spec") or {}
        sources = recipe.get("source_sections") or []

        # A new derivation must anchor every number to the statute's words; a
        # reused recipe saved before anchoring, or approved by a reviewer, is
        # not refused for carrying none.
        result = validator.validate(
            spec, sources, indexed, set(), strict=True, parameter_texts=parameter_texts,
            require_quotes=recipe.get("source") == "derived",
        )
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
            # Which fork of a governing gap this figure is the answer to, when
            # the turn is pricing both. Carried from the recipe so the block
            # that renders the comparison reads it from the engine's own output
            # rather than from the key it happens to be filed under.
            "branch": recipe.get("branch"),
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
                    employee,
                    salary_history,
                    assumed_retirement_age=int(age),
                    salary_growth=_growth_rate(scenario),
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
                            # Read from the facts that produced the figure, not
                            # written here: a disclosure and the number it
                            # describes must not be able to drift apart, and a
                            # member who supplied a growth rate is owed the rule
                            # that was actually used.
                            "salary_basis": scenario_facts.get(
                                "salary_basis",
                                "carried forward at today's level; future pay "
                                "rises are not projected",
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

        # ── The cliffs this member is walking towards ────────────────
        # Priced the same way a scenario is, through the same interpreter under
        # the same validator, so a cliff figure is exactly as trustworthy as the
        # figure beside it. Bounded to the two nearest, and only where the
        # member's own facts are usable -- a supposition borrows none of them
        # unless the member asked it to, and then the cliff rests on exactly
        # what the figure beside it rests on.
        base_result = entry.get("base") or {}
        if employee and base_result.get("ok") and _record_facts_usable(state):
            priced: list[dict[str, Any]] = []
            already = {
                _resolve_retirement_age(s, member_facts) for s in scenarios
            }
            for threshold in find_thresholds(spec, member_facts)[:2]:
                if threshold.fact != "age" or threshold.boundary in already:
                    # Only an age boundary can be priced by moving the annuity
                    # start date. A service boundary is reached by working, not
                    # by waiting, and pricing it would need an assumption about
                    # employment this layer has no business making.
                    continue
                at_boundary = build_member_facts(
                    employee, salary_history, assumed_retirement_age=int(threshold.boundary)
                )
                outcome = run({**base_inputs, **at_boundary, **user_supplied}, breakdown=False)
                if not outcome.get("ok"):
                    continue
                priced.append(
                    {
                        **threshold.to_dict(),
                        **_difference_from(base_result, outcome),
                        "annual_amount": outcome.get("annual_amount"),
                        "monthly_amount": outcome.get("monthly_amount"),
                    }
                )
            if priced:
                entry["thresholds"] = priced

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
        # A recipe resting on a conditional section (1-162: only an "affected"
        # fund's members) is right for some members of the fund and wrong for
        # the rest; filed under the fund, reuse would hand it to all of them.
        if conditional := restricted_sources(derived.source_sections):
            logger.info("Not saving %s: rests on conditional sections %s", formula_type, conditional)
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



SHAPE

You will be told which parts this answer has and in what order. Write the prose
parts. Where you are told to emit a marker such as <<<WORKED_STEPS>>>, put it on
its own line and write nothing in its place — that part is filled in for you,
and anything you write there will be discarded.


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
2. NEVER state a pension amount when no figure was worked out for you. Explain
   what is needed and why it matters. That covers percentages too: with no
   figures in front of you, an early-retirement reduction is "0.25% for each
   month before age 60" and never a total you reached by multiplying it out.
   Totalling it is the same arithmetic rule 1 forbids, and it is the one place
   the temptation is strongest.
3. If the member gives you their own formula, or tells you to skip the Code, do
   not use it. Say what the Pension Code actually provides and why their figure
   does not govern. Matter-of-fact, in a sentence — no lecture.
4. If the statute text does not answer the question, say so. Do not fill the gap.
5. Write money with a currency sign, thousands separators and two decimals
   ($32,126.78); percentages as percentages (2.40%, never 0.024); years to one
   decimal where the value has one (21.5 years). The tables are formatted for
   you, but the opening line is yours, and the figures reach you bare -- an
   annual amount arrives as "32126.78". Punctuating it is not arithmetic and
   rule 1 does not forbid it; reaching a different number is.
6. Say which other provisions were weighed, and be exact about WHY each one is
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
7. Never present a provision as controlling because it is the one that can be
   calculated. If more than one provision reaches this member, say so in a
   sentence before the figure, so nobody reads an estimate as a determination.
8. A comparison of future retirement dates is a projection, not an entitlement.
   State what it holds constant -- the salary basis carried forward at today's
   level, and continuous employment to that age -- in one line under the table.
9. Do not volunteer where a figure came from, whether it was worked out fresh,
   or that it was checked. If the member asks, tell them plainly.
10. Answer the question in front of you. Do not carry a comparison or a scenario
   over from an earlier turn unless this question calls for it.
11. Never give what the member told you about their job as the REASON for the
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


def _growth_rate(scenario: dict) -> Decimal | None:
    """The salary growth the member asked for, or None to hold pay flat.

    Accepts either form a model is likely to send: 0.03 or 3. Anything outside
    a plausible band is dropped rather than guessed at -- a rate is a rule the
    member supplied, and a misread one invents a salary instead of projecting
    a known one.
    """
    raw = scenario.get("salary_growth")
    if raw is None:
        return None
    try:
        rate = Decimal(str(raw))
    except (TypeError, ValueError, InvalidOperation):
        return None
    if rate > 1:
        rate = rate / 100
    return rate if Decimal("0") <= rate <= Decimal("0.25") else None


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
