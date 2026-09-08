"""Admin endpoints for formula registry and article monitoring.

- GET  /api/v1/admin/formulas            — list formula registry entries
- POST /api/v1/admin/formulas/{id}/promote — promote to approved/authoritative
- GET  /api/v1/admin/articles            — ingestion status per article
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update

from pension_chat.api.auth import TokenData, require_auth
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


class PromoteRequest(BaseModel):
    knowledge_status: str = "approved"
    execution_policy: str = "authoritative"
    reviewed_by: str


class ArticleStatusItem(BaseModel):
    article_number: int
    system_name: str
    ingestion_status: str


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/formulas", response_model=list[FormulaListItem])
async def list_formulas(
    user: TokenData = Depends(require_auth),
):
    """List calculation-knowledge rows, lifecycle state, execution policy."""
    async with get_admin_session() as db:
        result = await db.execute(
            select(FormulaRegistry).order_by(
                FormulaRegistry.article_number,
                FormulaRegistry.formula_type,
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
            )
            for f in formulas
        ]


@router.post("/formulas/{formula_db_id}/promote")
async def promote_formula(
    formula_db_id: str,
    body: PromoteRequest,
    user: TokenData = Depends(require_auth),
):
    """Promote a validated/agent_derived capability to approved/authoritative."""
    formula_uuid = uuid.UUID(formula_db_id)

    if body.knowledge_status not in ("approved", "validated"):
        raise HTTPException(status_code=400, detail="Can only promote to 'approved' or 'validated'")

    if body.execution_policy not in ("estimate", "authoritative"):
        raise HTTPException(status_code=400, detail="Execution policy must be 'estimate' or 'authoritative'")

    async with get_admin_session() as db:
        result = await db.execute(
            select(FormulaRegistry).where(FormulaRegistry.id == formula_uuid)
        )
        formula = result.scalar_one_or_none()

        if formula is None:
            raise HTTPException(status_code=404, detail="Formula not found")

        if formula.knowledge_status in ("stale", "retired"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot promote a {formula.knowledge_status} formula"
            )

        await db.execute(
            update(FormulaRegistry)
            .where(FormulaRegistry.id == formula_uuid)
            .values(
                knowledge_status=body.knowledge_status,
                execution_policy=body.execution_policy,
                reviewed_by=body.reviewed_by,
                reviewed_at=datetime.now(UTC),
            )
        )
        await db.commit()

        return {"status": "promoted", "formula_id": formula.formula_id}


@router.get("/articles", response_model=list[ArticleStatusItem])
async def list_articles(
    user: TokenData = Depends(require_auth),
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
