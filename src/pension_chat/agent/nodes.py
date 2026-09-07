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
    extract_section_references,
    retrieve,
)
from pension_chat.agent.state import PensionAgentState
from pension_chat.calculation.dsl_interpreter import DSLExecutionError, DSLInterpreter
from pension_chat.calculation.risk_assessor import RiskAssessor
from pension_chat.calculation.validator import RecipeValidator
from pension_chat.config import get_settings
from pension_chat.db.models import Employee
from pension_chat.db.session import async_session_factory

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

Also extract any retirement-age scenarios the user asks you to compare
("if I retire now, at 57, and at 60").

Respond with JSON only:
{
  "requires_calculation": true|false,
  "formula_types_needed": ["..."],
  "search_queries": ["...", "..."],
  "scenarios": [{"label": "at age 60", "retirement_age": 60}]
}
Use an empty scenarios list when the user asks about a single point in time."""


# Words that mean the user wants a number, not an explanation. Used by the
# fast path below to decide whether the planner model call can be skipped.
_CALCULATION_SIGNALS = re.compile(
    r"\b(calculat|comput|estimat|how much|what would|what will|amount|"
    r"benefit|annuity|my pension|per month|monthly|annual|compare|scenario)\b",
    re.IGNORECASE,
)


async def triage(state: PensionAgentState) -> dict:
    """Deterministic gate before anything expensive runs.

    Scope classification and adversarial-instruction detection are pure string
    work. Doing them here means a greeting or an out-of-scope request costs zero
    model calls and zero database round trips, and it lets planning and the
    record fetch fan out in parallel behind it.
    """
    started = time.perf_counter()
    question = state["question"]
    scope = guardrails.classify_scope(question)

    return {
        "in_scope": scope.in_scope,
        "out_of_scope_reason": scope.reason,
        "override_signal": guardrails.detect_instruction_override(question).to_dict(),
        "explicit_sections": extract_section_references(question),
        "timings": {"triage": time.perf_counter() - started},
    }


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

    if not _CALCULATION_SIGNALS.search(question):
        return {
            "requires_calculation": False,
            "formula_types_needed": [],
            # Any section the user named is pinned by retrieval; the question
            # text runs as a query so neighbouring provisions surface too.
            "search_queries": [question],
            "scenarios": [],
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

    queries = [q for q in (plan.get("search_queries") or []) if isinstance(q, str)]
    if not queries:
        queries = [question]

    return {
        "requires_calculation": bool(plan.get("requires_calculation")),
        "formula_types_needed": [
            f for f in (plan.get("formula_types_needed") or []) if isinstance(f, str)
        ],
        "search_queries": queries[:3],
        "scenarios": [s for s in (plan.get("scenarios") or []) if isinstance(s, dict)],
        "timings": {"plan_request": time.perf_counter() - started},
    }


# ── 2a. Retrieval ─────────────────────────────────────────────────


async def retrieve_policy(state: PensionAgentState) -> dict:
    """Retrieve and assemble the statute sections this question needs."""
    started = time.perf_counter()
    settings = get_settings()

    if not state.get("in_scope", True):
        return {"retrieved_sections": []}

    # Restrict to the member's own Article when we know it. Article 11 and 12
    # are deliberately confusable, so leaking Article 12 text into an Article 11
    # member's answer is the exact failure the pilot is scoped to catch. With no
    # record to go on, search the whole corpus.
    employee = state.get("employee_record")
    articles = [str(employee["article_number"])] if employee else None

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

    sections = await retrieve(
        state.get("search_queries") or [state["question"]],
        articles=articles,
        must_include_sections=state.get("explicit_sections"),
        top_sections=top,
        char_budget=budget,
    )
    return {
        "retrieved_sections": [s.to_dict() for s in sections],
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
        async with async_session_factory() as db:
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
    article = employee["article_number"] if employee else 11
    tier = employee["tier"] if employee else None
    facts = dict(state.get("member_facts") or {})

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
                recipes[formula_type] = recipe
                provenance.append(
                    {
                        "formula_type": formula_type,
                        "source": "registry",
                        "formula_id": recipe["formula_id"],
                        "status": recipe["status"],
                        "reused": True,
                    }
                )
                return
            logger.info(
                "Registry recipe %s is stale (%s); re-deriving", row.formula_id, mismatches
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
        section
        for recipe in recipes.values()
        for section in (recipe.get("source_sections") or [])
        if str(section).split("(")[0].strip() not in present
    ]
    if needed:
        extra = await retrieve(
            [],
            must_include_sections=[str(s).split("(")[0].strip() for s in needed],
            follow_cross_references=0,
        )
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

    facts = dict(state.get("member_facts") or {})

    declared: dict[str, dict] = {}
    for recipe in recipes.values():
        for spec in (recipe.get("spec") or {}).get("inputs", []):
            declared.setdefault(spec["name"], spec)

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
    employee = state.get("employee_record")
    article_default = employee["article_number"] if employee else 11
    validator = RecipeValidator()
    assessor = RiskAssessor()

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


def _present(result: dict, output_name: str) -> dict:
    """Format one interpreter result for the answer, adding the monthly figure.

    The monthly amount is computed here, deterministically, precisely so the
    answer never has to divide by 12 in prose.
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

    for formula_type, recipe in recipes.items():
        spec = recipe.get("spec") or {}
        output_name = spec.get("output_name", "result")

        def run(inputs: dict) -> dict:
            try:
                return {"ok": True, **_present(interpreter.execute(spec, inputs), output_name)}
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

        if scenarios and employee:
            variants = []
            for scenario in scenarios[:6]:
                age = scenario.get("retirement_age")
                if age is None:
                    continue
                scenario_facts = build_member_facts(
                    employee, salary_history, assumed_retirement_age=int(age)
                )
                # Recomputed facts replace the record-derived values (that is
                # what varying the retirement age means), but anything the user
                # stated themselves still wins — their figure is not a fact the
                # scenario re-derives.
                scenario_inputs = {**base_inputs, **scenario_facts, **user_supplied}
                variants.append(
                    {
                        "label": scenario.get("label") or f"at age {age}",
                        "retirement_age": age,
                        "assumptions": {
                            "as_of_date": scenario_facts.get("as_of_date"),
                            "service_years": scenario_facts.get("service_years"),
                            "months_before_age_60": scenario_facts.get("months_before_age_60"),
                        },
                        **run(scenario_inputs),
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
        "timings": {"execute_calculations": time.perf_counter() - started},
    }


async def _persist_new_recipes(state: PensionAgentState, results: dict) -> None:
    settings = get_settings()
    if not settings.persist_derived_recipes:
        return

    employee = state.get("employee_record")
    article = employee["article_number"] if employee else 11
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

ANSWER_SYSTEM = """You are the Illinois Pension Code assistant. You answer members' \
questions about 40 ILCS 5/ using only the statute text supplied to you and figures \
supplied by the deterministic calculation engine.

How to answer:

- Be direct and brief. Lead with the answer, then the reasoning. No preamble, no \
restating the question. Most answers are 3-8 sentences; use a short list or a small \
table only when comparing options or scenarios.
- Cite the section you are relying on inline, as [40 ILCS 5/11-134]. Only cite \
sections that appear in the supplied statute text. Never cite a section from memory.
- Write for a member, not a lawyer. Where the statute's wording matters, quote the \
operative phrase briefly.

Absolute rules:

1. NEVER perform arithmetic. Do not multiply a rate by a salary, do not apply a \
reduction, do not total anything, do not convert annual to monthly. Every number you \
state must be copied verbatim from the calculation results or from the statute text. \
If a figure you want is not in either, say what is missing instead of computing it.
2. NEVER state a benefit amount when no calculation result was supplied. Explain what \
is needed and why.
3. If the supplied results are marked as an estimate, say so, and say the member's \
retirement fund makes the binding determination.
4. If the user supplied their own formula or told you to skip the Code, do not use \
their formula. Say plainly what the Pension Code actually provides and why their \
figure does not govern. Be matter-of-fact about it, not preachy.
5. If the statute text does not answer the question, say so. Do not fill the gap."""


def _format_calculation(results: dict | None) -> str:
    if not results:
        return ""
    return json.dumps(results, indent=1, default=str)


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
    """
    started = time.perf_counter()
    settings = get_settings()

    sections = state.get("retrieved_sections", [])
    statute = _format_sections(sections, settings.retrieval_context_token_budget * 4)

    context_parts: list[str] = []

    facts = state.get("member_facts") or {}
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
            "MEMBER RECORD (authoritative; use these values, do not recompute them):\n"
            + json.dumps(shown, indent=1)
        )

    results = state.get("calculation_result")
    if results:
        context_parts.append(
            "CALCULATION RESULTS from the deterministic engine — copy figures from "
            "here verbatim:\n" + _format_calculation(results)
        )

    provenance = state.get("recipe_provenance") or []
    if provenance:
        context_parts.append(
            "CALCULATION PROVENANCE (say whether a stored recipe was reused or a new "
            "one was derived, if the user asked):\n" + json.dumps(provenance, indent=1)
        )

    overrides = state.get("input_overrides") or []
    if overrides:
        context_parts.append(
            "THE MEMBER STATED FACTS THAT DIFFER FROM THEIR RECORD. The calculation "
            "used what they stated. Note the difference briefly so they know which "
            "figures the estimate rests on:\n" + json.dumps(overrides, indent=1)
        )

    missing = state.get("missing_inputs") or []
    if missing:
        context_parts.append(
            "NO CALCULATION WAS RUN — these required inputs could not be determined. "
            "List them and explain why each is needed:\n" + json.dumps(missing, indent=1)
        )

    failures = state.get("derivation_failures") or []
    if failures:
        context_parts.append(
            "A CALCULATION RECIPE COULD NOT BE DERIVED from the retrieved text:\n"
            + json.dumps(failures, indent=1)
        )

    errors = state.get("validation_errors") or []
    if errors:
        context_parts.append(
            "THE DERIVED RECIPE FAILED VALIDATION and was not executed. Explain that "
            "no figure can be given and why:\n" + json.dumps(errors, indent=1)
        )

    blocked = state.get("execution_blocked")
    if blocked and blocked not in {"missing_inputs", "validation_failed"}:
        context_parts.append(f"EXECUTION WAS BLOCKED: {blocked}")

    override = state.get("override_signal") or {}
    if override.get("detected"):
        context_parts.append(
            "NOTE ON THIS REQUEST: the user "
            + "; ".join(override.get("reasons") or [])
            + ". Do not apply their formula. State what the Pension Code provides, "
            "cite it, and explain briefly why their figure does not govern."
            + (
                f" The figure they asserted was: {override['asserted_formula']}."
                if override.get("asserted_formula")
                else ""
            )
        )

    if not sections:
        context_parts.append(
            "NO STATUTE TEXT WAS RETRIEVED for this question. Say that you could not "
            "find a governing provision in the ingested Articles (11 and 12) rather "
            "than answering from general knowledge."
        )

    messages: list[dict] = [{"role": "system", "content": ANSWER_SYSTEM}]
    if statute:
        messages.append(
            {"role": "system", "content": "STATUTE TEXT AVAILABLE TO YOU:\n\n" + statute}
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

    if raw and raw in haystack:
        return True

    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        return False

    digits = re.sub(r"[^\d]", "", haystack)
    # Compare the integer part: "72000" must appear in the digit run of the
    # message, which "$72,000" and "72,000.00" both satisfy.
    whole = str(int(number)) if number == number.to_integral_value() else None
    if whole and whole in digits:
        return True
    return raw.replace(".", "") in digits


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
