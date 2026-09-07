"""Section/subsection chunker producing the §5.2 index schema.

Each indexed record contains:
- content (the subsection or section text)
- content_vector (from embedder)
- Full hierarchy metadata
- calculation_relevance array
- Cross-references
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pension_chat.ingestion.parser import ParsedSection
from pension_chat.ingestion.relevance_tagger import tag_calculation_relevance

logger = logging.getLogger("pension_chat.ingestion.chunker")


@dataclass
class IndexRecord:
    """One record ready for Azure AI Search indexing (§5.2 schema)."""
    id: str
    content: str
    content_vector: list[float] = field(default_factory=list)  # populated by embedder

    subsection_id: str | None = None
    subsection_text: str | None = None
    section_text: str = ""

    chapter: str = "40"
    act: str = "5"
    article: str = ""
    division: str | None = None
    section: str = ""
    section_title: str = ""

    effective_date: str | None = None
    status: str = "active"
    calculation_relevance: list[str] = field(default_factory=list)
    referenced_sections: list[str] = field(default_factory=list)
    referenced_subsections: list[str] = field(default_factory=list)
    source_public_act: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for Azure AI Search upload."""
        d = {
            "id": self.id,
            "content": self.content,
            "subsection_id": self.subsection_id or "",
            "subsection_text": self.subsection_text or "",
            "section_text": self.section_text,
            "chapter": self.chapter,
            "act": self.act,
            "article": self.article,
            "division": self.division or "",
            "section": self.section,
            "section_title": self.section_title,
            "effective_date": self.effective_date or "",
            "status": self.status,
            "calculation_relevance": self.calculation_relevance,
            "referenced_sections": self.referenced_sections,
            "referenced_subsections": self.referenced_subsections,
            "source_public_act": self.source_public_act or "",
        }
        if self.content_vector:
            d["content_vector"] = self.content_vector
        return d


def chunk_sections(sections: list[ParsedSection]) -> list[IndexRecord]:
    """Convert parsed sections into indexable records.

    Strategy:
    - If a section has subsections, create one record per subsection
      (with the full section text available for context assembly)
    - If a section has no subsections, create one record for the whole section
    - Each record includes the full section text for context-walk-up (§5.3)

    The ~800 token budget for context assembly (§5.3) is handled at
    query time, not at chunking time.
    """
    records: list[IndexRecord] = []

    for section in sections:
        article_str = str(section.article_number)

        # Tag calculation relevance
        calc_relevance = tag_calculation_relevance(section)

        # Truncate section text if extremely long (for the section_text field)
        section_text = section.full_text
        if len(section_text) > 10000:
            section_text = section_text[:10000] + "..."

        if section.subsections:
            # One record per subsection
            for sub in section.subsections:
                record_id = f"{section.section_id}_{sub.subsection_id.strip('()')}"
                # Clean up the ID for Azure Search (alphanumeric + hyphens + underscores)
                record_id = record_id.replace("(", "").replace(")", "").replace(".", "_")

                records.append(IndexRecord(
                    id=record_id,
                    content=sub.text,
                    subsection_id=sub.subsection_id,
                    subsection_text=sub.text,
                    section_text=section_text,
                    chapter=section.chapter,
                    act=section.act,
                    article=article_str,
                    division=section.division,
                    section=section.section_id,
                    section_title=section.section_title,
                    effective_date=section.effective_date,
                    status=section.status,
                    calculation_relevance=calc_relevance,
                    referenced_sections=section.referenced_sections,
                    referenced_subsections=section.referenced_subsections,
                    source_public_act=section.source_public_act,
                ))
        else:
            # One record for the whole section
            record_id = section.section_id.replace(".", "_")

            records.append(IndexRecord(
                id=record_id,
                content=section.full_text,
                section_text=section_text,
                chapter=section.chapter,
                act=section.act,
                article=article_str,
                division=section.division,
                section=section.section_id,
                section_title=section.section_title,
                effective_date=section.effective_date,
                status=section.status,
                calculation_relevance=calc_relevance,
                referenced_sections=section.referenced_sections,
                referenced_subsections=section.referenced_subsections,
                source_public_act=section.source_public_act,
            ))

    logger.info("Chunked %d sections into %d index records", len(sections), len(records))
    return records
