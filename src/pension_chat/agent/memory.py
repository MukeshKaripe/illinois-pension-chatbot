"""Which sections verifiably answered a question like this one before.

Deliberately not called learning. Nothing here changes what the model believes
about the law, and nothing here produces a figure. It remembers one thing: that
an answer resting on these sections **passed verification**, and offers them
back the next time a similar question arrives.

Two properties carry the whole design, and both are refusals.

**Only a verified answer is remembered.** The guardrail's pass is the label, so
nothing enters on the strength of the model's own opinion. An answer the
guardrail threw away is a labelled example of the wrong thing and goes to
`capture`, not here.

**A hint is enrichment on a search result, never a filter.** `search_statute`
still runs its own query and still returns whatever it finds; the hint is a line
of text beside the results. A section that once got lucky therefore cannot
entrench itself by suppressing the search that would have found a better one --
which is the failure mode every retrieval cache has, and the reason this one is
shaped as a suggestion rather than a shortcut.

The concept key is the member's question with their own particulars stripped
out. "I'm 56 with 21.5 years and $69,956.40, what do I get at 60?" embeds a
great deal of one member and very little question, and the thing worth matching
on is what was asked. A model-emitted canonical phrase would be better still;
`concept_source` records where this one came from, so adding that later needs no
migration.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from pension_chat.agent.guardrails import GuardrailReport
from pension_chat.agent.llm import get_llm
from pension_chat.config import get_settings
from pension_chat.db.models import AnswerEvidence
from pension_chat.db.session import async_session_factory

logger = logging.getLogger("pension_chat.agent.memory")

#: How close a remembered concept must be before it is worth mentioning. A bad
#: hint is not free: it spends the model's attention on the wrong sections, and
#: no hint at all is the honest answer to "nothing like this has been asked".
SIMILARITY_FLOOR = 0.82

#: How many rows to weigh, and how many sections to offer back.
MAX_CANDIDATES = 200
MAX_HINTS = 4

#: A statute reference, which is the most matchable thing a question can
#: contain and must survive the stripper below. Found by reading what the table
#: had actually stored: "What does Section 11-134 say about the annuity
#: formula?" had been remembered as "What does Section - say about the annuity
#: formula", with the one identifying token gone.
_SECTION_REF = re.compile(r"\b\d{1,2}-\d{1,3}(?:\.\d{1,2}[a-z]?)?\b")

#: The member's own particulars: ages, years, money, dates. Stripped because
#: they are the part that is about a person rather than about a question. Two
#: members asking the same thing differ in their age and their salary; they do
#: not differ in which section they named.
_PARTICULARS = re.compile(
    r"\$[\d,]+(?:\.\d{2})?|\b\d{4}-\d{2}-\d{2}\b|\b\d[\d,]*(?:\.\d+)?\b"
)

#: Coverage bases a determination may be remembered under. A `record` basis is
#: the file's opinion, which the corpus never agreed with, and remembering it
#: would let one member's filing become the Code's answer for everyone who
#: describes themselves the same way.
_GENERALISABLE = ("member_confirmed", "statutory_match")


def concept_for(question: str) -> str:
    """The question, with the asker taken out of it and the law left in."""
    kept: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        # Letters, not an index. A numbered placeholder is itself a number, and
        # the stripper below duly ate it -- leaving "Section \x00 \x00 say".
        return "\x00" + "z" * len(kept) + "\x00"

    held = _SECTION_REF.sub(_hold, question or "")
    stripped = _PARTICULARS.sub(" ", held)
    for index, reference in enumerate(kept, start=1):
        stripped = stripped.replace("\x00" + "z" * index + "\x00", reference)
    return re.sub(r"\s+", " ", stripped).strip(" ,.;:?!-")


async def embed(text: str) -> list[float]:
    """One embedding, through the pooled client the agent already owns."""
    settings = get_settings()
    response = await get_llm().embeddings.create(
        input=[text[:8191]], model=settings.azure_openai_embedding_deployment
    )
    return list(response.data[0].embedding)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    left = math.sqrt(sum(x * x for x in a))
    right = math.sqrt(sum(y * y for y in b))
    return dot / (left * right) if left and right else 0.0


async def _write(**row: Any) -> None:
    async with async_session_factory() as db:
        db.add(AnswerEvidence(**row))
        await db.commit()


async def _read(article: str | None, tier: int | None) -> list[dict[str, Any]]:
    """Rows worth weighing: this Article, and this tier or none.

    Filtered in SQL rather than by similarity, because an Article 12 concept is
    not a weaker match for an Article 11 member -- it is the wrong law, and no
    distance metric says so.
    """
    async with async_session_factory() as db:
        query = select(AnswerEvidence).order_by(AnswerEvidence.verified_at.desc())
        if article:
            query = query.where(AnswerEvidence.article == str(article))
        rows = (await db.execute(query.limit(MAX_CANDIDATES))).scalars().all()
    return [
        {
            "concept": r.concept,
            "embedding": list(r.embedding or []),
            "sections": list(r.sections or []),
            "tier": r.tier,
        }
        for r in rows
        if r.tier is None or tier is None or r.tier == tier
    ]


async def remember(state: dict[str, Any], report: GuardrailReport) -> None:
    """Record what a verified answer rested on. Never raises.

    A memory that fails must cost a hint, never the member's turn — and this
    runs after the answer has already been streamed.
    """
    try:
        if not get_settings().answer_memory_enabled:
            return
        if not report.passed:
            return
        sections = list(report.verified_citations)
        if not sections:
            # Nothing to remember. An answer with no citations also had nothing
            # for the verifier to reject, so its pass says very little.
            return

        coverage = state.get("coverage") or {}
        article = str(coverage.get("article") or "") or None
        if not article:
            # A memory keyed to no Article is a hint for everybody and evidence
            # for nobody. The case that reliably produces one is an answer that
            # could not place the question at all: a gap-admitting answer about
            # Article 22A was stored carrying sections from Articles 1, 15 and
            # 16 — three wrong hints wearing a verified answer's label.
            return
        tier = (state.get("member_facts") or {}).get("tier")
        session_id = str(state.get("session_id") or "")

        concept = concept_for(str(state.get("question") or ""))
        if concept:
            await _write(
                concept=concept,
                concept_source="question",
                embedding=await embed(concept),
                article=article,
                tier=int(tier) if tier is not None else None,
                sections=sections,
                verified_at=datetime.now(UTC),
                session_id=session_id,
            )

        # A coverage determination generalises where a figure does not:
        # "employee of a board of park commissioners" → Article 12 is a
        # statutory test, not a fact about a person.
        described = coverage.get("described_as")
        if described and coverage.get("basis") in _GENERALISABLE:
            key = f"coverage: {described}"
            await _write(
                concept=key,
                concept_source="coverage",
                embedding=await embed(key),
                article=article,
                tier=None,
                sections=sections,
                verified_at=datetime.now(UTC),
                session_id=session_id,
            )
    except Exception as exc:  # noqa: BLE001 - a hint is never worth a turn
        logger.warning("Could not remember this answer: %s", exc)


async def recall(question: str, *, article: str | None, tier: int | None) -> list[str]:
    """Sections that previously answered questions like this one. Never raises.

    Returns section ids only, for `search_statute` to mention beside its own
    results. It cannot narrow that search and is not given the chance to.
    """
    try:
        if not get_settings().answer_memory_enabled:
            return []
        concept = concept_for(question)
        if not concept:
            return []
        wanted = await embed(concept)
        scored = [
            (_cosine(wanted, row["embedding"]), row)
            for row in await _read(article, tier)
        ]
        hits = sorted(
            (s for s in scored if s[0] >= SIMILARITY_FLOOR),
            key=lambda s: s[0],
            reverse=True,
        )
        sections: list[str] = []
        for _, row in hits:
            for section in row["sections"]:
                if section not in sections:
                    sections.append(str(section))
        return sections[:MAX_HINTS]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not recall anything for %r: %s", question[:60], exc)
        return []
