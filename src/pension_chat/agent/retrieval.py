"""Hybrid statute retrieval over the Azure AI Search index.

The first implementation issued one semantic-only query per planner-generated
string, kept the top 10 *chunks*, and truncated each to 500 characters. Three
things went wrong with that:

* **No article filter.** An Article 11 member's questions came back with
  Article 12 sections mixed in — the two articles are deliberately adjacent and
  confusable (architecture doc §2), so this is the failure mode the pilot exists
  to catch.
* **Chunks, not sections.** The index stores one record per *subsection*, so a
  top-10 chunk list gives the model fragments of a rule. Section 11-134(a) alone
  is 6KB; truncating it at 500 characters removes the accrual rates.
* **No vector leg.** Semantic-only search misses paraphrases.

This module does hybrid retrieval (BM25 + vector + semantic reranker), filters
by article, then *expands* the winning chunks back into whole assembled
sections before applying a token budget.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from azure.search.documents.models import VectorizedQuery

from pension_chat.agent import corpus, instrument
from pension_chat.agent.llm import get_async_search_client, get_llm
from pension_chat.config import get_settings

logger = logging.getLogger("pension_chat.agent.retrieval")

# "Section 11-134", "Sec. 11-134(a)", "40 ILCS 5/11-134.1", "11-134(f-1)",
# "Section 1-113.4a" — the trailing letter is part of the section id, not a
# typo, and dropping it pins Section 1-113.4 instead, which is a different rule.
_SECTION_REF = re.compile(
    r"(?:40\s*ILCS\s*5/)?(?:Sec(?:tion|\.)?\s*)?\b(\d{1,2}-\d{1,3}(?:\.\d{1,2})?[a-z]?)\b",
    re.IGNORECASE,
)
_SELECT_FIELDS = [
    "id", "content", "article", "section", "section_title", "subsection_id",
    "calculation_relevance", "referenced_sections", "effective_date",
    "source_public_act",
]

# Statute text is immutable for the life of the process — an Article is
# re-ingested by running the ingestion script, which restarts the API. Caching
# assembled section chunks removes a ~0.3s round trip from the request path,
# and the hot sections (11-130, 11-134) are hit by almost every calculation
# question. `reset_caches()` exists so tests and the ingestion path can clear it.
_SECTION_CACHE: dict[str, list[dict]] = {}
_EMBED_CACHE: dict[str, list[float]] = {}
_EMBED_CACHE_MAX = 512

# Below this much remaining budget a section is dropped rather than truncated.
_MIN_SECTION_CHARS = 1200


def reset_caches() -> None:
    """Drop cached statute text, query embeddings, and the corpus graph."""
    _SECTION_CACHE.clear()
    _EMBED_CACHE.clear()
    corpus.reset_corpus_cache()


@dataclass
class RetrievedSection:
    """One statute section, assembled from its indexed subsection chunks."""

    section: str
    article: str
    title: str
    text: str
    score: float = 0.0
    subsection_ids: list[str] = field(default_factory=list)
    relevance: list[str] = field(default_factory=list)
    referenced_sections: list[str] = field(default_factory=list)
    source_public_act: str | None = None
    truncated: bool = False

    @property
    def citation(self) -> str:
        return f"40 ILCS 5/{self.section}"

    def to_dict(self) -> dict:
        return {
            "id": self.section,
            "section": self.section,
            "article": self.article,
            "title": self.title,
            "text": self.text,
            "citation": self.citation,
            "score": self.score,
            "relevance": self.relevance,
            "referenced_sections": self.referenced_sections,
            "truncated": self.truncated,
        }


def extract_section_references(text: str) -> list[str]:
    """Pull explicit statute section references out of free text.

    Used both to short-circuit retrieval when the user names a section outright
    ("What does Section 11-116 define as salary?") and to resolve the
    cross-references a retrieved section makes to other sections.
    """
    seen: list[str] = []
    for match in _SECTION_REF.finditer(text or ""):
        ref = match.group(1)
        if ref not in seen:
            seen.append(ref)
    return seen


_ARTICLE_REF = re.compile(r"\bArt(?:icle)?\.?\s*(\d{1,2})\b", re.IGNORECASE)

# An Article named by its title rather than its number.
#
# Restricted to `CROSS_CUTTING_ARTICLES` on purpose, and the restriction is the
# whole safety argument. Naming an Article *moves* the retrieval filter and
# bypasses `assess_coverage` entirely — which is correct for "what does Article
# 12 provide", and would be a serious regression for "I am a park employee",
# since that sentence is coverage evidence to be checked against the Code, not
# an instruction about which law to read. Fund Articles are therefore never
# matched by name; describing yourself is coverage's job.
#
# Articles nobody is a member of have no such ambiguity. You cannot describe
# yourself into the Reciprocal Act, so the only way anyone refers to it is by
# its title — and without this, ingesting Article 20 leaves it reachable only by
# someone who already knows to type "Article 20".
_ARTICLE_BY_NAME = (
    (
        "20",
        re.compile(
            r"\bretirement\s+systems?\s+reciprocal\s+act\b|\breciprocal\s+act\b",
            re.IGNORECASE,
        ),
    ),
)


def extract_article_references(text: str) -> list[str]:
    """Pull explicit Article references out of free text.

    Retrieval is normally filtered to the member's own Article, because Articles
    11 and 12 are deliberately confusable and leaking one into the other's
    answer is the failure the pilot exists to catch. But that filter also made
    an Article 12 question unanswerable for an Article 11 member: the text was
    indexed and retrievable, and the answer still said the Article was not
    covered.

    Widening the filter to both Articles does not fix it. Semantic ranking
    favours Article 11's fuller provisions even on a query that names Article 12
    outright, so an unfiltered search returns no Article 12 sections at all.
    Naming an Article has to *move* the filter, not merely relax it.

    A section reference implies its Article, so "what does 12-133 say" resolves
    the same way as "Article 12". So does an Article named by its title, but
    only for the Articles nobody is a member of -- see `_ARTICLE_BY_NAME`, where
    the reason that restriction matters is the whole point.
    """
    seen: list[str] = []

    def add(value: str) -> None:
        if value not in seen:
            seen.append(value)

    for match in _ARTICLE_REF.finditer(text or ""):
        add(match.group(1).lstrip("0") or "0")
    for ref in extract_section_references(text):
        add(ref.split("-", 1)[0].lstrip("0") or "0")
    for article, pattern in _ARTICLE_BY_NAME:
        if pattern.search(text or ""):
            add(article)
    return seen


def _subsection_sort_key(subsection_id: str) -> tuple:
    """Order subsections the way the statute prints them: (a), (a-5), (b), (f-1).

    Chunks with no subsection id sort first — they are the section preamble.
    """
    if not subsection_id:
        return (0, "", 0)
    inner = subsection_id.strip("()")
    match = re.match(r"([a-z]+)(?:-(\d+))?", inner, re.IGNORECASE)
    if not match:
        return (2, inner, 0)
    letter, suffix = match.group(1), match.group(2)
    return (1, letter.lower(), int(suffix) if suffix else 0)


async def _embed(queries: list[str]) -> list[list[float] | None]:
    """Embed every query in one batched call rather than one call per query.

    Returns a list positionally aligned with ``queries``; an entry is ``None``
    when that query could not be embedded, so the caller can still run the
    keyword and semantic legs for it.
    """
    settings = get_settings()
    client = get_llm()

    missing = [q for q in queries if q not in _EMBED_CACHE]
    if missing:
        try:
            async with instrument.timed("embedding", "query"):
                response = await client.embeddings.create(
                    model=settings.azure_openai_embedding_deployment, input=missing
                )
            if len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
                _EMBED_CACHE.clear()
            for query, item in zip(missing, response.data):
                _EMBED_CACHE[query] = item.embedding
        except Exception as exc:  # embedding is an optimisation, not a hard dependency
            logger.warning("Embedding failed, falling back to keyword+semantic: %s", exc)

    return [_EMBED_CACHE.get(q) for q in queries]


# Reciprocal rank fusion constant. 60 is the value from the original paper and
# the usual default; it damps the difference between the top few ranks, so a
# section ranked second by one query still beats one ranked fifth by two.
_RRF_K = 60


# Articles that govern the members of *other* Articles rather than having
# members of their own. Article 1 is general provisions — Section 1-160 is what
# makes a Tier 2 member of Article 11 accrue differently from a Tier 1 one, and
# names Articles 8 through 12 outright. Article 20 is the Reciprocal Act, which
# is how service in two systems is combined.
#
# They are **not** added to the search filter, and that was measured rather than
# assumed. Adding them looked necessary — a member filtered to Article 11 could
# otherwise never reach the section that overrides Article 11 for them — but the
# reachability was already there: `fetch_sections` is called with no article
# filter on every path, so a section the question names or the retrieved text
# cross-references resolves whatever Article it lives in. What widening the
# filter actually did was put 309 more documents into every member's search.
# Three benchmark cases then retrieved Article 1 and 20 sections for an ordinary
# Article 11 question, and — the real cost — Section 11-134 and Section 11-215
# were displaced out of the top sections on cases that had been citing them.
#
# The one thing this leaves uncovered: a question that is *about* Tier 2, names
# no section, and whose best Article 11 hits happen not to cross-reference
# Section 1-160. Pinning it for members the record shows are Tier 2 is the fix
# that fits — `coverage_sections` already pins that way — not a wider filter.
#
# `coverage.py` reads this for a different consequence of the same fact: an
# Article nobody is a member of can never be the answer to which Article covers
# someone.
CROSS_CUTTING_ARTICLES = ("1", "20")


def _article_filter(articles: Iterable[str] | None) -> str | None:
    values = [a for a in (articles or []) if a]
    if not values:
        return None
    joined = ",".join(str(a) for a in values)
    return f"search.in(article, '{joined}', ',')"


async def _search_one(
    query: str,
    vector: list[float] | None,
    filter_expr: str | None,
    top: int,
) -> list[dict]:
    """Run one hybrid query, degrading gracefully if a leg is unavailable."""
    settings = get_settings()
    client = get_async_search_client()
    kwargs: dict[str, Any] = {
        "search_text": query,
        "top": top,
        "select": _SELECT_FIELDS,
        "query_type": "semantic",
        "semantic_configuration_name": "pension-semantic-config",
    }
    if filter_expr:
        kwargs["filter"] = filter_expr
    if vector:
        kwargs["vector_queries"] = [
            VectorizedQuery(
                vector=vector,
                k_nearest_neighbors=settings.retrieval_vector_k,
                fields="content_vector",
            )
        ]

    try:
        # Timed with the iteration: results are fetched lazily as they are read.
        async with instrument.timed("search", query):
            results = await client.search(**kwargs)
            return [dict(doc) async for doc in results]
    except Exception as exc:
        instrument.note("semantic_fallback", query)
        logger.warning("Semantic hybrid search failed for %r: %s", query, exc)

    # Retry without the semantic ranker — it has its own quota and is the most
    # likely leg to fail. Keyword+vector still gives usable results.
    kwargs.pop("query_type", None)
    kwargs.pop("semantic_configuration_name", None)
    try:
        # Timed with the iteration: results are fetched lazily as they are read.
        async with instrument.timed("search", query):
            results = await client.search(**kwargs)
            return [dict(doc) async for doc in results]
    except Exception as exc:
        logger.error("Search failed entirely for %r: %s", query, exc)
        return []


def _chunk_score(doc: dict) -> float:
    """Prefer the reranker score; fall back to the raw BM25/RRF score."""
    reranker = doc.get("@search.reranker_score")
    if reranker:
        return float(reranker)
    return float(doc.get("@search.score") or 0.0)


async def fetch_sections(
    section_ids: list[str],
    articles: Iterable[str] | None = None,
) -> dict[str, list[dict]]:
    """Fetch every indexed chunk for the given sections, keyed by section id."""
    if not section_ids:
        return {}

    cached = {s: _SECTION_CACHE[s] for s in section_ids if s in _SECTION_CACHE}
    pending = [s for s in section_ids if s not in _SECTION_CACHE]
    if not pending:
        return cached

    client = get_async_search_client()
    quoted = ",".join(pending)
    filter_parts = [f"search.in(section, '{quoted}', ',')"]
    article_expr = _article_filter(articles)
    if article_expr:
        filter_parts.append(article_expr)

    try:
        async with instrument.timed("search", "fetch"):
            results = await client.search(
                search_text="*",
                filter=" and ".join(filter_parts),
                top=200,
                select=_SELECT_FIELDS,
            )
            docs = [dict(doc) async for doc in results]
    except Exception as exc:
        logger.error("Section fetch failed for %s: %s", pending, exc)
        return cached

    grouped: dict[str, list[dict]] = dict(cached)
    fetched: dict[str, list[dict]] = {}
    for doc in docs:
        fetched.setdefault(doc.get("section", ""), []).append(doc)

    # Only cache when the article filter did not restrict the result, so a
    # filtered fetch never poisons the cache with a partial section.
    for section_id, chunks in fetched.items():
        if not article_expr:
            _SECTION_CACHE[section_id] = chunks
        grouped[section_id] = chunks
    return grouped


async def canonical_section_texts(section_ids: Iterable[str]) -> dict[str, str]:
    """Return the complete, untrimmed text of each section.

    This is what evidence fingerprints must hash. The text carried on a
    ``RetrievedSection`` has been through a character budget, so it varies with
    how many other sections shared that budget on a given turn — fingerprinting
    it makes an unchanged statute hash differently between two runs, every
    stored recipe look stale, and the registry re-derive from scratch each time.
    Canonical text depends only on what was ingested.
    """
    ids = [s for s in dict.fromkeys(section_ids) if s]
    if not ids:
        return {}

    grouped = await fetch_sections(ids)
    texts: dict[str, str] = {}
    for section_id, chunks in grouped.items():
        ordered = sorted(
            chunks, key=lambda d: _subsection_sort_key(d.get("subsection_id") or "")
        )
        body = "\n".join((c.get("content") or "").strip() for c in ordered)
        if body.strip():
            texts[section_id] = body
    return texts


#: The chunker's version prefix: "(from P.A. 102-719)" on its own first line.
_VARIANT_LABEL_RE = re.compile(r"\A\(([^()\n]+)\)\n")


def _variant_of(chunk: dict[str, Any]) -> tuple[str | None, str]:
    """Which printed version a chunk belongs to, and its text without the label.

    Read off what the chunker wrote: a key suffixed ``__<label>`` and content
    prefixed with the label on its own line. Both, because subsection text can
    itself open with a parenthesis.
    """
    content = (chunk.get("content") or "").strip()
    if "__" in str(chunk.get("id") or ""):
        match = _VARIANT_LABEL_RE.match(content)
        if match:
            return match.group(1), content[match.end():].strip()
    return None, content


def merge_variants(
    chunks: list[dict[str, Any]], subsection_key: Callable[[str], Any] = _subsection_sort_key
) -> list[str]:
    """A section's text in statute order, with each printed version read once.

    ILGA prints a section amended by concurrent Public Acts once per Act, and
    ingestion keeps every copy (choosing one is a reading of the law). Sorted by
    subsection alone they interleave -- Section 1-160 read (a)(a)(a)(b)(b)(b)...
    -- and the copies of Section 1-160, which differ only in (g) and (g-5),
    tripled the text until the budget ran out before (c), the Tier 2 age-67
    rule. So text every version shares is given once, a subsection the versions
    word differently is given once per wording and labelled with the versions
    that carry it, and a note up front says which is which. A section with one
    version, labelled or not, comes back exactly as indexed.
    """
    ordered = sorted(
        chunks,
        key=lambda c: (subsection_key(str(c.get("subsection_id") or "")), str(c.get("id") or "")),
    )
    read = [(str(c.get("subsection_id") or ""), *_variant_of(c)) for c in ordered]
    labels = list(dict.fromkeys(label for _, label, _ in read if label))
    if len(labels) < 2:
        texts = [(c.get("content") or "").strip() for c in ordered]
        return [text for text in texts if text]

    # A subsection is matched across versions by its letter and by how many
    # times that letter has come up in its own version, so a version that
    # letters two lists (Section 9-219) keeps both.
    groups: dict[tuple[str, int], dict[str, tuple[str, list[str | None]]]] = {}
    seen: dict[tuple[str | None, str], int] = {}
    for subsection, label, body in read:
        if not body:
            continue
        occurrence = seen[(label, subsection)] = seen.get((label, subsection), 0) + 1
        wordings = groups.setdefault((subsection, occurrence), {})
        normalised = " ".join(body.split())
        wordings.setdefault(normalised, (body, []))[1].append(label)

    parts = [
        f"(This Section is printed in {len(labels)} versions: {'; '.join(labels)}. "
        "Text common to all versions is given once; where they differ, each text "
        "is labelled with the versions it belongs to.)"
    ]
    for wordings in groups.values():
        if len(wordings) == 1:
            body, holders = next(iter(wordings.values()))
            if set(holders) >= set(labels):
                parts.append(body)
                continue
        for body, holders in wordings.values():
            named = [h for h in dict.fromkeys(holders) if h]
            parts.append(f"({'; '.join(named)})\n{body}" if named else body)
    return parts


def _assemble(
    section_id: str, chunks: list[dict], score: float, char_budget: int
) -> RetrievedSection:
    """Join a section's chunks into one ordered text block."""
    chunks = sorted(chunks, key=lambda d: _subsection_sort_key(d.get("subsection_id") or ""))
    first = chunks[0] if chunks else {}

    parts: list[str] = []
    used = 0
    truncated = False
    relevance: list[str] = []
    references: list[str] = []

    for chunk in chunks:
        for tag in chunk.get("calculation_relevance") or []:
            if tag not in relevance:
                relevance.append(tag)
        for ref in chunk.get("referenced_sections") or []:
            if ref not in references:
                references.append(ref)

    for body in merge_variants(chunks):
        if used + len(body) > char_budget:
            remaining = max(0, char_budget - used)
            if remaining > 400:
                parts.append(body[:remaining])
                used += remaining
            truncated = True
            break
        parts.append(body)
        used += len(body)

    return RetrievedSection(
        section=section_id,
        article=first.get("article", ""),
        title=first.get("section_title", ""),
        text="\n".join(parts),
        score=score,
        subsection_ids=[c.get("subsection_id") or "" for c in chunks],
        relevance=relevance,
        referenced_sections=references,
        source_public_act=first.get("source_public_act") or None,
        truncated=truncated,
    )


async def retrieve(
    queries: list[str],
    *,
    articles: Iterable[str] | None = None,
    must_include_sections: Iterable[str] | None = None,
    top_sections: int | None = None,
    char_budget: int | None = None,
    follow_cross_references: int = 2,
) -> list[RetrievedSection]:
    """Retrieve and assemble the statute sections relevant to ``queries``.

    Args:
        queries: Search strings. Embedded and searched concurrently.
        articles: Article numbers to restrict to (e.g. ``["11"]``). Omitting
            this searches the whole corpus, which is right for a general legal
            question but wrong for a member-specific one.
        must_include_sections: Sections to retrieve regardless of score —
            explicit references from the question, or cross-references being
            resolved.
        top_sections: How many distinct sections to assemble.
        char_budget: Total character budget across all assembled sections.
        follow_cross_references: How many sections named *inside* the retrieved
            text to pull in as well. Article 11 is written in cross-references —
            11-134(a) computes on service "as service is defined or computed in
            Section 11-216" — so an answer that reads 11-134 without 11-216 is
            reading half a rule, and citing 11-216 from it would be citing text
            the agent was never shown. Set to 0 to disable.

    Returns:
        Assembled sections, highest scoring first.
    """
    settings = get_settings()
    top_sections = top_sections or settings.retrieval_top_sections
    # ~4 characters per token is a good approximation for statute prose.
    char_budget = char_budget or settings.retrieval_context_token_budget * 4

    queries = [q.strip() for q in queries if q and q.strip()][:4]
    pinned = list(dict.fromkeys(must_include_sections or []))

    section_scores: dict[str, float] = {}
    fused: dict[str, float] = {}

    if queries:
        vectors = await _embed(queries)
        filter_expr = _article_filter(articles)
        hits = await asyncio.gather(
            *(
                _search_one(
                    query,
                    vectors[i] if i < len(vectors) else None,
                    filter_expr,
                    settings.retrieval_per_query_top,
                )
                for i, query in enumerate(queries)
            )
        )
        # Ranked by reciprocal rank fusion across the queries rather than by
        # best raw score. Taking a global maximum let one query drown the
        # others: asked which salary definition applies, the member's own
        # phrasing ranked Section 11-116 second, while the statute-vocabulary
        # rewrites beside it scored higher across the board -- so 11-116 never
        # appeared, and the answer said the Code provided no definition.
        # Fusion lets every query put its best hit forward regardless of how
        # its scores compare with another's.
        for docs in hits:
            best_rank: dict[str, int] = {}
            for rank, doc in enumerate(docs):
                section_id = doc.get("section")
                if not section_id:
                    continue
                best_rank.setdefault(section_id, rank)
                # A section's score is its best-scoring chunk: a long section
                # should not be penalised for having many weak subsections.
                score = _chunk_score(doc)
                if score > section_scores.get(section_id, 0.0):
                    section_scores[section_id] = score
            for section_id, rank in best_rank.items():
                fused[section_id] = fused.get(section_id, 0.0) + 1.0 / (_RRF_K + rank + 1)

    ranked = sorted(
        section_scores,
        key=lambda s: (fused.get(s, 0.0), section_scores[s]),
        reverse=True,
    )
    # Pinned sections come first and do not consume a ranked slot.
    chosen = pinned + [
        s for s in ranked if s not in pinned
    ][: max(0, top_sections - len(pinned))]
    if not chosen:
        return []

    # No article filter here: section ids carry their article as a prefix
    # ("11-134"), so the ids chosen above already encode the restriction, and a
    # pinned section named explicitly by the user should be honoured even when
    # it sits outside the filter (cross-article references are legitimate).
    grouped = await fetch_sections(chosen)

    # Budget allocation is greedy by rank, and it *drops* a section rather than
    # truncating it once little budget is left. A half-included section is worse
    # than an absent one: the derivation step refuses to derive from text marked
    # truncated, so mid-truncating the controlling provision to make room for a
    # weak tail section loses the answer entirely.
    # Hold back part of the budget for cross-referenced sections. Without a
    # reservation the top few sections consume everything (11-134 alone is 13KB)
    # and the definitions they depend on never get fetched.
    reserved = int(char_budget * 0.2) if follow_cross_references else 0

    assembled: list[RetrievedSection] = []
    remaining = char_budget - reserved
    for section_id in chosen:
        chunks = grouped.get(section_id)
        if not chunks:
            continue
        if remaining < _MIN_SECTION_CHARS:
            break
        section = _assemble(
            section_id, chunks, section_scores.get(section_id, 0.0), remaining
        )
        if not section.text:
            continue
        remaining -= len(section.text)
        assembled.append(section)

    reference_budget = remaining + reserved
    if follow_cross_references and reference_budget >= _MIN_SECTION_CHARS:
        assembled.extend(
            await _follow_references(
                assembled, reference_budget, limit=follow_cross_references
            )
        )

    assembled.sort(key=lambda s: (s.section not in pinned, -s.score))
    return assembled


async def _follow_references(
    assembled: list[RetrievedSection],
    char_budget: int,
    limit: int,
) -> list[RetrievedSection]:
    """Pull in the sections that the retrieved text points at.

    Ranked by how many of the retrieved sections name each target, not by which
    retrieved section happened to rank highest. The old rule read the top three
    and took the first two references it found, so a definition named only by
    the fourth-best hit was unreachable -- and Section 11-134 computes on
    service "as defined in Section 11-216", which is exactly that shape.

    Falls back to the sections' own `referenced_sections` if the corpus cannot
    be built, so a search outage degrades to the previous behaviour rather than
    dropping cross-references entirely.
    """
    already = {section.section for section in assembled}

    try:
        built = await corpus.load_corpus()
        edges = {s.section: built.cites(s.section) for s in assembled}
    except Exception as exc:  # pragma: no cover - exercised only on an outage
        logger.warning("Corpus unavailable, falling back to per-section refs: %s", exc)
        edges = {s.section: tuple(s.referenced_sections) for s in assembled}

    # How many of the retrieved sections lean on each target. A target two
    # sections cite is likelier to be load-bearing than one cited once.
    weight: dict[str, int] = {}
    for targets in edges.values():
        for raw in targets:
            target = corpus.normalize_section(raw)
            if target and target not in already:
                weight[target] = weight.get(target, 0) + 1

    wanted = sorted(weight, key=lambda s: (-weight[s], corpus.section_sort_key(s)))[:limit]
    if not wanted:
        return []

    grouped = await fetch_sections(wanted)
    extra: list[RetrievedSection] = []
    remaining = char_budget
    for section_id in wanted:
        chunks = grouped.get(section_id)
        if not chunks or remaining < _MIN_SECTION_CHARS:
            continue
        # Referenced sections are supporting context, so they get a bounded
        # share rather than whatever is left.
        share = min(remaining, max(_MIN_SECTION_CHARS, char_budget // max(1, len(wanted))))
        section = _assemble(section_id, chunks, 0.0, share)
        if not section.text:
            continue
        remaining -= len(section.text)
        extra.append(section)

    if extra:
        logger.debug(
            "Followed cross-references from %s to %s",
            [s.section for s in assembled[:3]],
            [s.section for s in extra],
        )
    return extra


# The Azure filter for the coverage lane. `calculation_relevance` is a filterable
# collection on every chunk, so restricting to it costs nothing extra.
_COVERAGE_FILTER = "calculation_relevance/any(t: t eq 'coverage_rule')"


async def retrieve_coverage(
    description: str,
    *,
    also_covering: str | int | None = None,
    top_sections: int = 8,
    char_budget: int = 12000,
) -> list[RetrievedSection]:
    """Find the sections that say who each Article covers.

    Deliberately unfiltered by Article -- the only query in the system where
    that is right, because the question being asked *is* which Article. Every
    other retrieval path filters to the member's own Article precisely so that
    Article 12 text cannot leak into an Article 11 answer; here the whole point
    is to let every Article's coverage test compete on the member's own words.

    The corpus makes this cheap. Coverage sections are a tiny, stable slice of
    each Article, and every Article carries its own, so this scales to the full
    Code with no per-Article maintenance. That was a prediction when it was
    written and is now measured: ingesting Articles 8, 9 and 20 found their
    coverage sections -- 8-113, 9-108, 20-104 and the "Creation of fund" and
    "Future entrant" sections beside them -- with no new tagger patterns, 23
    across the six Articles ingested. Nothing here needs the member's record, so
    nothing here can quietly confirm it.

    Note that Article 1 contributes none, correctly: it defines no fund
    membership. Article 20 contributes two that must never be routed to, which
    ``coverage.NON_MEMBERSHIP_ARTICLES`` is what handles.

    Args:
        description: How the member described themselves, in their own words.
        also_covering: An Article whose own coverage sections must be included
            whatever the description ranked. Passed the member's Article, so
            that the assessment reads the test their record rests on and not
            only the tests their words matched. Searching "park employee"
            returns four Article 12 sections and nothing from Article 11, which
            is correct ranking and a one-sided brief.
        top_sections: Distinct sections to assemble.
        char_budget: Total character budget across them.

    Returns:
        Assembled coverage sections, best first. Empty if the corpus has not
        been re-indexed since ``coverage_rule`` was added, which is a normal
        outcome and not an error -- the caller falls back to the record.
    """
    description = (description or "").strip()
    if not description:
        return []

    settings = get_settings()
    vectors = await _embed([description])
    docs = await _search_one(
        description,
        vectors[0] if vectors else None,
        _COVERAGE_FILTER,
        settings.retrieval_per_query_top,
    )

    section_scores: dict[str, float] = {}
    for doc in docs:
        section_id = doc.get("section")
        if not section_id:
            continue
        score = _chunk_score(doc)
        if score > section_scores.get(section_id, 0.0):
            section_scores[section_id] = score

    # The record's own Article, fetched by its filter rather than ranked, so it
    # is present however poorly the member's wording happens to match it.
    home: list[str] = []
    if also_covering:
        home_docs = await _search_one(
            description or "coverage",
            None,
            f"{_COVERAGE_FILTER} and article eq '{also_covering}'",
            4,
        )
        home = list(
            dict.fromkeys(
                doc["section"] for doc in home_docs if doc.get("section")
            )
        )

    if not section_scores and not home:
        return []

    ranked = sorted(section_scores, key=lambda s: section_scores[s], reverse=True)
    chosen = list(dict.fromkeys(ranked[:top_sections] + home))
    grouped = await fetch_sections(chosen)

    assembled: list[RetrievedSection] = []
    remaining = char_budget
    for section_id in chosen:
        chunks = grouped.get(section_id)
        if not chunks or remaining <= 0:
            continue
        section = _assemble(
            section_id, chunks, section_scores.get(section_id, 0.0), remaining
        )
        if not section.text:
            continue
        remaining -= len(section.text)
        assembled.append(section)

    return assembled
