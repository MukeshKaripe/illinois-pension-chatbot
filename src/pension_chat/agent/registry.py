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

import logging
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from pension_chat.agent.derivation import DerivedRecipe
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
        self.applicability = (row.applicability or {}).get("summary", "")
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
            "version": self.version,
        }


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

    candidates.sort(
        key=lambda r: (
            _STATUS_RANK.get(r.knowledge_status, 9),
            -(r.version or 1),
            -(r.created_at.timestamp() if r.created_at else 0),
        )
    )
    return candidates[0]


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
        # Nothing to compare against — treat as unverifiable rather than fresh.
        return {"__unfingerprinted__": "missing"}

    mismatches = verify_fingerprints(
        stored,
        [{"section_id": s, "content": t} for s, t in current_sections.items()],
    )
    if mismatches:
        logger.warning(
            "Recipe %s is stale; evidence changed: %s", row.formula_id, mismatches
        )
        async with async_session_factory() as db:
            await db.execute(
                update(FormulaRegistry)
                .where(FormulaRegistry.id == row.id)
                .values(knowledge_status="stale")
            )
            await db.commit()
    return mismatches


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
                    },
                    dependencies=[],
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
