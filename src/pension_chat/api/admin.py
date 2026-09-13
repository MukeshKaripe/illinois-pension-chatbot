"""Admin endpoints for formula registry and article monitoring.

- GET  /api/v1/admin/formulas            — most load-bearing first
- POST /api/v1/admin/formulas/{id}/retire  — drop it out of reuse
- POST /api/v1/admin/formulas/{id}/suspend — keep it visible, stop it computing
- GET  /api/v1/admin/articles            — ingestion status per article

**There is no promotion.** The gate on a recipe is `validator.py`, which checks
structure and evidence completeness deterministically before anything runs, and
always was. Admin approval never had that job. A recipe derived on this turn is
validated, executed, answered with and immediately reusable at `agent_derived`,
and no member waits for a human.

Positive control does not scale past a handful of Articles, makes the assistant
worst exactly when a member asks something new, and in practice nobody clicks
the button. **The admin's power is to correct and retire, not to admit** — which
is what `retired` and `no_execute` were always for, and what had no surface.

"Trusted" here means trusted to run and be reused, never trusted as an
authoritative reading of the law. A derived recipe executes at `estimate`, keeps
its estimate line, and is labelled `agent_derived` wherever it is shown.
`authoritative` stays unreachable in the pilot: the fund makes the binding
determination whoever blessed the formula, so dropping the estimate line is a
promise this system cannot make.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update

from pension_chat.api.auth import TokenData, require_admin
from pension_chat.db.models import FormulaRegistry, PensionArticle
from pension_chat.db.session import get_admin_session

logger = logging.getLogger("pension_chat.api.admin")

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ── Schemas ───────────────────────────────────────────────────────

class FormulaListItem(BaseModel):
    id: str
    formula_id: str
    formula_name: str
    formula_type: str
    article_number: int
    tier: int | None
    section: str | None
    knowledge_status: str
    risk_tier: str
    execution_policy: str
    version: int
    description: str
    source_sections: list | None = None
    created_at: str
    # What this recipe is carrying, so the reviewer reads the load-bearing one
    # first: an error in a recipe that has produced four hundred figures costs
    # more than an error in one that has produced none.
    reuse_count: int = 0
    last_reused_at: str | None = None
    contested_count: int = 0
    superseded_by: str | None = None


class WithdrawRequest(BaseModel):
    """Why, and by whom. Both are the audit trail; neither is optional."""

    reason: str
    reviewed_by: str


class ArticleStatusItem(BaseModel):
    article_number: int
    system_name: str
    ingestion_status: str


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/formulas", response_model=list[FormulaListItem])
async def list_formulas(
    user: TokenData = Depends(require_admin),
):
    """List calculation-knowledge rows, lifecycle state, execution policy."""
    async with get_admin_session() as db:
        result = await db.execute(
            # Most load-bearing first. The old ordering was by Article, which
            # is the order they were written rather than the order they matter.
            select(FormulaRegistry).order_by(
                FormulaRegistry.contested_count.desc(),
                FormulaRegistry.reuse_count.desc(),
                FormulaRegistry.created_at.desc(),
            )
        )
        formulas = result.scalars().all()

        return [
            FormulaListItem(
                id=str(f.id),
                formula_id=f.formula_id,
                formula_name=f.formula_name,
                formula_type=f.formula_type,
                article_number=f.article_number,
                tier=f.tier,
                section=f.section,
                knowledge_status=f.knowledge_status,
                risk_tier=f.risk_tier,
                execution_policy=f.execution_policy,
                version=f.version,
                description=f.description,
                source_sections=f.source_sections,
                created_at=f.created_at.isoformat(),
                reuse_count=f.reuse_count,
                last_reused_at=f.last_reused_at.isoformat() if f.last_reused_at else None,
                contested_count=f.contested_count,
                superseded_by=f.superseded_by,
            )
            for f in formulas
        ]


async def _withdraw(
    formula_db_id: str, body: WithdrawRequest, *, knowledge_status: str | None,
    execution_policy: str | None, verb: str,
) -> dict:
    """Retire or suspend one recipe, recording who and why.

    Both are negative control and neither is destructive: `retired` drops the
    row out of `lookup_recipe`'s reusable set, `no_execute` leaves it visible
    and stops it producing figures. A recipe under suspicion is better suspended
    than deleted -- the answers it has already given are in the transcript, and
    a reviewer needs to see what produced them.
    """
    formula_uuid = uuid.UUID(formula_db_id)
    values: dict = {"reviewed_by": body.reviewed_by, "reviewed_at": datetime.now(UTC)}
    if knowledge_status:
        values["knowledge_status"] = knowledge_status
    if execution_policy:
        values["execution_policy"] = execution_policy

    async with get_admin_session() as db:
        result = await db.execute(
            select(FormulaRegistry).where(FormulaRegistry.id == formula_uuid)
        )
        formula = result.scalar_one_or_none()
        if formula is None:
            raise HTTPException(status_code=404, detail="Formula not found")

        await db.execute(
            update(FormulaRegistry).where(FormulaRegistry.id == formula_uuid).values(**values)
        )
        await db.commit()

    logger.warning(
        "Recipe %s %s by %s: %s", formula.formula_id, verb, body.reviewed_by, body.reason
    )
    return {"status": verb, "formula_id": formula.formula_id, "reason": body.reason}


@router.post("/formulas/{formula_db_id}/retire")
async def retire_formula(
    formula_db_id: str,
    body: WithdrawRequest,
    user: TokenData = Depends(require_admin),
):
    """Drop a recipe out of reuse. It stays readable; nothing will pick it up."""
    return await _withdraw(
        formula_db_id, body, knowledge_status="retired", execution_policy=None, verb="retired"
    )


@router.post("/formulas/{formula_db_id}/suspend")
async def suspend_formula(
    formula_db_id: str,
    body: WithdrawRequest,
    user: TokenData = Depends(require_admin),
):
    """Stop a recipe producing figures, without hiding it.

    For a recipe under suspicion rather than one known wrong: it still resolves
    and is still shown, and `no_execute` means no member gets a number from it
    while somebody reads it.
    """
    return await _withdraw(
        formula_db_id, body, knowledge_status=None, execution_policy="no_execute",
        verb="suspended",
    )


@router.get("/articles", response_model=list[ArticleStatusItem])
async def list_articles(
    user: TokenData = Depends(require_admin),
):
    """Ingestion status per Article — progress at a glance."""
    async with get_admin_session() as db:
        result = await db.execute(
            select(PensionArticle).order_by(PensionArticle.article_number)
        )
        articles = result.scalars().all()

        return [
            ArticleStatusItem(
                article_number=a.article_number,
                system_name=a.system_name,
                ingestion_status=a.ingestion_status,
            )
            for a in articles
        ]
