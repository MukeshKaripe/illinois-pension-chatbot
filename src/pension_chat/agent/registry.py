"""Persistence and lookup for the calculation knowledge registry.

This is layer 2 of the three-layer model in the architecture doc: recipes the
agent discovers from statute text, stored so they are derived once and reused
thereafter. Without it every calculation question pays the full derivation cost
(~26s), and "recalculate with a different contribution balance" re-derives the
same rule from scratch instead of substituting a parameter.

Reuse is guarded by an **evidence fingerprint**: the hash of the statute text
each recipe was derived from. If that text changes under the recipe, the stored
row is marked ``stale`` and the agent re-derives rather than silently applying a
rule to superseded law.

Recipes the agent derives itself are never ``authoritative``. They enter the
registry as ``agent_derived`` with an ``estimate`` execution policy; promotion to
``approved``/``authoritative`` is a human review step (``api/admin.py``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from pension_chat.agent.derivation import DerivedRecipe, normalize_alternatives
from pension_chat.calculation.fingerprint import (
    compute_evidence_fingerprint,
    compute_implementation_hash,
    verify_fingerprints,
)
from pension_chat.config import get_settings
from pension_chat.db.models import FormulaRegistry
from pension_chat.db.session import async_session_factory

logger = logging.getLogger("pension_chat.agent.registry")

# Ordered best-first: an approved recipe outranks a merely validated one, which
# outranks one the agent derived this week.
_STATUS_RANK = {
    "approved": 0,
    "validated": 1,
    "agent_derived": 2,
    "discovered": 3,
}
_REUSABLE_STATUSES = ("approved", "validated", "agent_derived")


class RegistryRecipe:
    """A recipe loaded from the registry, with the provenance the answer needs."""

    def __init__(self, row: FormulaRegistry):
        self.formula_id = row.formula_id
        self.formula_type = row.formula_type
        self.formula_name = row.formula_name
        self.section = row.section
        self.spec: dict = row.rule_specification or {}
        self.source_sections: list[str] = list(row.source_sections or [])
        self.knowledge_status = row.knowledge_status
        self.execution_policy = row.execution_policy
        self.risk_tier = row.risk_tier
        applicability = row.applicability or {}
        self.applicability = applicability.get("summary", "")
        # Why this provision and not the neighbouring ones. Derived once and
        # then dropped on every reuse, which is why a reused recipe could not
        # explain its own branch selection.
        # Normalised on read so rows written before alternatives were typed
        # still load, rather than being silently dropped or mis-typed.
        self.alternatives_considered: list[dict] = normalize_alternatives(
            applicability.get("alternatives_considered")
        )
        self.controlling_subsection = applicability.get("controlling_subsection")
        self.selection_basis = applicability.get(
            "selection_basis", "sole_applicable_provision"
        )
        self.cross_references: list[str] = list(
            applicability.get("cross_references") or []
        )
        self.description = row.description
        self.version = row.version
        self.evidence_fingerprint: dict = row.evidence_fingerprint or {}

    def to_dict(self) -> dict:
        return {
            "source": "registry",
            "formula_id": self.formula_id,
            "formula_type": self.formula_type,
            "formula_name": self.formula_name,
            "spec": self.spec,
            "section": self.section,
            "source_sections": self.source_sections,
            "status": self.knowledge_status,
            "policy": self.execution_policy,
            "risk_tier": self.risk_tier,
            "applicability": self.applicability,
            "alternatives_considered": self.alternatives_considered,
            "controlling_subsection": self.controlling_subsection,
            "selection_basis": self.selection_basis,
            "cross_references": self.cross_references,
            "version": self.version,
        }


def _reuse_rank(row: FormulaRegistry) -> tuple:
    """Which of several reusable recipes to prefer, in one place.

    A recipe carrying machine-checkable applicability conditions beats an
    equally-ranked one without them: only the first can be re-tested against the
    member asking today. This is also how recipes derived before predicates
    existed get superseded rather than pinned.

    Read by `lookup_recipe` and by `recipes_for`, so the provision named to a
    member who asked which one applies is the same provision a later figure
    rests on. Two rankings would have made "the provision you selected earlier"
    a coin toss.
    """
    return (
        _STATUS_RANK.get(row.knowledge_status, 9),
        0 if (row.rule_specification or {}).get("applicability_predicates") else 1,
        -(row.version or 1),
        -(row.created_at.timestamp() if row.created_at else 0),
    )


async def lookup_recipe(
    formula_type: str,
    article_number: int,
    tier: int | None = None,
    *,
    as_of: date | None = None,
) -> FormulaRegistry | None:
    """Find the best reusable recipe for a formula type, or ``None``.

    Matches on article and formula type, prefers a tier-specific row over a
    tier-agnostic one, and honours the effective-date window.
    """
    as_of = as_of or date.today()

    async with async_session_factory() as db:
        result = await db.execute(
            select(FormulaRegistry).where(
                FormulaRegistry.article_number == article_number,
                FormulaRegistry.formula_type == formula_type,
                FormulaRegistry.knowledge_status.in_(_REUSABLE_STATUSES),
                FormulaRegistry.effective_start_date <= as_of,
            )
        )
        rows = list(result.scalars().all())

    candidates = [
        row for row in rows
        if row.effective_end_date is None or row.effective_end_date >= as_of
    ]
    if tier is not None:
        tier_matched = [r for r in candidates if r.tier == tier]
        candidates = tier_matched or [r for r in candidates if r.tier is None]

    if not candidates:
        return None

    candidates.sort(key=_reuse_rank)
    return candidates[0]


async def recipes_for(article_number: int, tier: int | None) -> list[dict]:
    """Every provision the engine would use for this member, without computing.

    G17: "Which retirement provision applies to me? Do not calculate anything
    yet." The registry is the authority for that — it answers with Section
    11-134(a) for an Article 11 Tier 1 member, chosen over 11-130, 11-131 and
    11-164 — and the only route to it was `compute_benefit`, which is what the
    member just forbade. The loop guessed from headings instead and named
    11-130, then computed from 11-134 two turns later.

    One query and no model call. Returns the same shape `resolve_recipes`
    reuses, picked by the same ranking, so the provision named here is the one
    a later figure rests on rather than a second opinion about it.
    """
    as_of = date.today()
    async with async_session_factory() as db:
        result = await db.execute(
            select(FormulaRegistry).where(
                FormulaRegistry.article_number == article_number,
                FormulaRegistry.knowledge_status.in_(_REUSABLE_STATUSES),
                FormulaRegistry.effective_start_date <= as_of,
            )
        )
        rows = list(result.scalars().all())

    live = [
        row for row in rows
        if row.effective_end_date is None or row.effective_end_date >= as_of
    ]
    best: list[dict] = []
    for formula_type in sorted({row.formula_type for row in live}):
        candidates = [r for r in live if r.formula_type == formula_type]
        if tier is not None:
            candidates = [c for c in candidates if c.tier == tier] or [
                c for c in candidates if c.tier is None
            ]
        if not candidates:
            continue
        candidates.sort(key=_reuse_rank)
        recipe = RegistryRecipe(candidates[0])
        if not recipe.section:
            # A recipe with nowhere to point cannot answer "which provision".
            continue
        best.append(
            {
                "formula_type": recipe.formula_type,
                "formula_name": recipe.formula_name,
                "section": recipe.section,
                "subsection": recipe.controlling_subsection,
                "selection_basis": recipe.selection_basis,
                "alternatives_considered": recipe.alternatives_considered,
                "status": recipe.knowledge_status,
            }
        )
    return best


async def verify_freshness(
    row: FormulaRegistry,
    current_sections: dict[str, str],
) -> dict[str, str]:
    """Check a stored recipe against the statute text as it reads today.

    Args:
        row: The registry row being reused.
        current_sections: ``{section_id: current_text}`` for its source sections.

    Returns:
        A mapping of mismatched section ids to ``"changed"``/``"missing"``.
        Empty means the recipe is still backed by the text it was derived from.
        A non-empty result also marks the row ``stale`` so later turns skip it.
    """
    stored = row.evidence_fingerprint or {}
    if not stored:
        # Nothing to compare against — unverifiable, which is not fresh.
        #
        # This used to return here, *above* the marking below, and the
        # consequence was silent in the worst way: the row stayed reusable, so
        # `lookup_recipe` kept returning it, freshness kept refusing it, and the
        # agent re-derived the same recipe on every turn forever. A cache that
        # never hits and a status that never changes, and nothing logs anything
        # because from each turn's point of view it worked.
        mismatches = {"__unfingerprinted__": "missing"}
    else:
        mismatches = verify_fingerprints(
            stored,
            [{"section_id": s, "content": t} for s, t in current_sections.items()],
        )

    if mismatches:
        logger.warning(
            "Recipe %s is stale; evidence %s", row.formula_id, mismatches
        )
        await _mark(row, knowledge_status="stale")
    return mismatches


async def _mark(row: FormulaRegistry, **values: Any) -> None:
    """Write to one registry row, or do nothing if there is no row to write to."""
    if getattr(row, "id", None) is None or not get_settings().registry_writes_enabled:
        return
    async with async_session_factory() as db:
        await db.execute(
            update(FormulaRegistry).where(FormulaRegistry.id == row.id).values(**values)
        )
        await db.commit()


async def _mark_by_formula_id(formula_id: str, **values: Any) -> None:
    """Write to one registry row addressed by its `formula_id`.

    Used where the row itself is not to hand. A recipe reaches the rest of the
    graph as a plain dict on purpose -- state is checkpointed, and an ORM object
    in a checkpoint is a detached instance waiting to happen -- so the string is
    the only handle a downstream node has.
    """
    if not formula_id or not get_settings().registry_writes_enabled:
        return
    async with async_session_factory() as db:
        await db.execute(
            update(FormulaRegistry)
            .where(FormulaRegistry.formula_id == formula_id)
            .values(**values)
        )
        await db.commit()


async def note_reuse(row: FormulaRegistry) -> None:
    """Count a recipe being reused, and when.

    Repurposed from "what earned approval" to **what is load-bearing**. A
    reviewer should see the recipe that has quietly produced four hundred
    figures first, because that is where an error costs most — and no column
    could answer that question before.
    """
    await _mark(
        row,
        reuse_count=FormulaRegistry.reuse_count + 1,
        last_reused_at=datetime.now(UTC),
    )


#: Background bookkeeping writes, held so the event loop does not collect a
#: task nobody is awaiting. Discarded as each finishes.
_BACKGROUND: set[asyncio.Task[None]] = set()


def note_reuse_later(row: FormulaRegistry) -> None:
    """Count the reuse without the member waiting for the write.

    `note_reuse` runs inside `compute_benefit`, on the way to a figure. Against
    Azure Postgres the UPDATE cost 0.72s of a member's time-to-first-token --
    BEGIN, the statement, COMMIT -- to increment a column nobody reads until a
    reviewer opens the registry. The reviewer's question is "what is
    load-bearing", and the answer is no less true for arriving a second late.

    Deliberately not `await`ed and deliberately not silent: a task nobody holds
    is collected mid-flight, and a task nobody awaits swallows its exception
    until the loop complains about it long after the turn it belonged to.
    """

    async def _quietly() -> None:
        try:
            await note_reuse(row)
        except Exception as exc:  # noqa: BLE001 - bookkeeping never costs a turn
            logger.warning("Could not count the reuse of %s: %s", row.formula_id, exc)

    task = asyncio.ensure_future(_quietly())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


async def drain_background_writes() -> None:
    """Wait for the bookkeeping writes still in flight. For tests and shutdown."""
    while _BACKGROUND:
        await asyncio.gather(*tuple(_BACKGROUND), return_exceptions=True)


async def note_contested(formula_id: str) -> None:
    """Count a member disputing a value the recipe computed on.

    `resolve_inputs` already detects this — `input_overrides` is written
    whenever a member states a value their record contradicts — and until now
    it reached only the answer's prose and was then thrown away. A recipe whose
    inputs members keep correcting is a recipe worth reading again.

    Not a claim that the recipe is wrong. The member may be wrong, or their
    record may be out of date. It is a count, and counts are for prioritising
    attention, not for deciding anything.
    """
    await _mark_by_formula_id(
        formula_id, contested_count=FormulaRegistry.contested_count + 1
    )


async def persist_recipe(
    recipe: DerivedRecipe,
    *,
    article_number: int,
    tier: int | None,
    section_texts: dict[str, str],
    risk_tier: str,
    validation: dict,
    message_id: int | None = None,
) -> str | None:
    """Store a freshly derived recipe so later turns can reuse it.

    The ``formula_id`` is content-addressed — it carries a hash of the rule
    specification — so re-deriving logically identical rules converges on one
    row instead of accumulating near-duplicates, and a genuinely different
    derivation gets its own row rather than colliding with the existing one.

    Returns the stored ``formula_id``, or ``None`` if persistence was skipped
    or failed. Persistence is best-effort: a registry write must never take
    down an answer the user is already receiving.
    """
    settings = get_settings()
    if not settings.persist_derived_recipes or not recipe.ok:
        return None

    spec = recipe.rule_specification or {}
    implementation_hash = compute_implementation_hash(spec)
    section = recipe.controlling_section or "unknown"
    formula_id = f"{article_number}_{recipe.formula_type}_{section}_{implementation_hash[:8]}"

    fingerprint = compute_evidence_fingerprint(
        [{"section_id": s, "content": t} for s, t in section_texts.items()]
    )
    required = [i for i in spec.get("inputs", []) if i.get("required", True)]
    optional = [i for i in spec.get("inputs", []) if not i.get("required", True)]

    async with async_session_factory() as db:
        try:
            existing = await db.execute(
                select(FormulaRegistry).where(FormulaRegistry.formula_id == formula_id)
            )
            if existing.scalar_one_or_none() is not None:
                return formula_id

            db.add(
                FormulaRegistry(
                    article_number=article_number,
                    formula_id=formula_id,
                    section=section,
                    formula_name=recipe.formula_name or recipe.formula_type,
                    formula_type=recipe.formula_type,
                    tier=tier,
                    description=recipe.derivation_notes or recipe.applicability or "",
                    required_inputs=required,
                    optional_inputs=optional,
                    output_schema={
                        "name": spec.get("output_name", "result"),
                        "type": spec.get("output_type", "money"),
                    },
                    applicability={
                        "summary": recipe.applicability,
                        "alternatives_considered": recipe.alternatives_considered,
                        "controlling_subsection": recipe.controlling_subsection,
                        # Whether the member is choosing, or the statute leaves
                        # one route. Reused recipes could not tell the two apart.
                        "selection_basis": recipe.selection_basis,
                        "cross_references": recipe.cross_references,
                        # Denormalised from the spec so a reuse decision can be
                        # made without unpacking the calculation tree.
                        "predicates": spec.get("applicability_predicates") or [],
                    },
                    dependencies=list(recipe.cross_references),
                    rule_specification=spec,
                    source_sections=recipe.source_sections,
                    evidence_fingerprint=fingerprint,
                    execution_type=spec.get("execution_type", "declarative"),
                    implementation_hash=implementation_hash,
                    version=1,
                    effective_start_date=date.today(),
                    # An agent-derived recipe is an estimate until a human
                    # promotes it — never authoritative on first write.
                    knowledge_status="agent_derived",
                    risk_tier=risk_tier,
                    execution_policy=settings.derived_recipe_execution_policy,
                    derivation_method="llm_derived_from_retrieved_statute",
                    completeness_check=validation,
                    created_from_message_id=message_id,
                )
            )
            await db.commit()
            logger.info("Persisted derived recipe %s", formula_id)
            return formula_id
        except IntegrityError:
            await db.rollback()
            # Another concurrent turn stored the same content-addressed recipe.
            return formula_id
        except Exception:
            await db.rollback()
            logger.exception("Failed to persist recipe %s", formula_id)
            return None
