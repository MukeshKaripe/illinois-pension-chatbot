"""Statute text for the evidence drawer (spec §5).

Public, like the Code itself: reading a section needs no identity and touches
no member data. The one thing this must get right is the id gate --
`retrieval.fetch_sections` interpolates ids into an OData filter string, so
anything but the Code's own section shapes is refused before it gets there.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from pension_chat.agent.retrieval import fetch_sections, merge_variants

router = APIRouter(prefix="/api/v1", tags=["sections"])

#: Article (optionally lettered, as 22A is), a dash, the section number, an
#: optional decimal part and an optional letter suffix (1-113.4a).
SECTION_ID = re.compile(r"\d{1,2}[A-C]?-\d{1,4}(?:\.\d{1,3})?[a-z]?")


class SectionResponse(BaseModel):
    section: str
    title: str
    article: str
    citation: str
    text: str


def _natural(value: str) -> list[str | int]:
    """Subsection (2) before (10): runs of digits compare as numbers."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def assemble_section(section: str, chunks: list[dict]) -> SectionResponse | None:
    """One section's indexed chunks, in subsection order, as a single text."""
    if not chunks:
        return None
    ordered = sorted(chunks, key=lambda chunk: _natural(str(chunk.get("subsection_id") or "")))
    first = ordered[0]
    return SectionResponse(
        section=section,
        title=str(first.get("section_title") or ""),
        article=str(first.get("article") or ""),
        citation=f"40 ILCS 5/{section}",
        text="\n\n".join(merge_variants(chunks, subsection_key=_natural)),
    )


@router.get("/sections/{section_id}", response_model=SectionResponse)
async def get_section(section_id: str) -> SectionResponse:
    if not SECTION_ID.fullmatch(section_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Not a section of the Pension Code.",
        )
    found = await fetch_sections([section_id])
    section = assemble_section(section_id, found.get(section_id) or [])
    if section is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That section is not in the index.",
        )
    return section
