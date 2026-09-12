"""The shape of the Code, as distinct from its text.

Retrieval answers "which sections are about this?". It cannot answer "what does
this section depend on?" or -- the question that actually ruins legal answers --
"what elsewhere in the Code modifies this one?". In statute the provision that
changes your answer is usually an exception living somewhere else, and a ranker
has no reason to surface it.

Both answers are already sitting in the index. `ingestion/parser.py` extracts
every `Section N-NNN` a section names into `referenced_sections`, and
`indexer.py` makes it a retrievable field; `section_title` is indexed beside it.
Nothing had ever assembled them into a graph, and nothing had ever inverted one.

Built once per process and cached, like `retrieval._SECTION_CACHE` and for the
same reason: statute text is immutable until a re-ingestion restarts the API.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from pension_chat.agent.llm import get_async_search_client

logger = logging.getLogger("pension_chat.agent.corpus")

#: Chunks per page. The index holds ~4,766 documents, so this is a handful of
#: round trips rather than one enormous response.
_PAGE_SIZE = 1000

_SCAN_FIELDS = ["section", "article", "section_title", "referenced_sections"]

#: "11-134", "11-134(a)", "40 ILCS 5/11-134.1", "Section 1-113.4a". The trailing
#: letter belongs to the section id; the parenthesised part does not.
_SECTION_ID = re.compile(r"\b(\d{1,2})-(\d{1,3})(\.\d{1,2})?([a-z])?\b", re.IGNORECASE)


def normalize_section(raw: str) -> str:
    """The bare section id inside whatever the text wrapped it in.

    Empty string when there is no section in it at all, so callers can filter
    on falsiness rather than catching.
    """
    match = _SECTION_ID.search(str(raw or ""))
    if match is None:
        return ""
    article, number, decimal, letter = match.groups()
    return f"{article}-{number}{decimal or ''}{(letter or '').lower()}"


def section_sort_key(section: str) -> tuple[int, int, int, str]:
    """Order sections as the Code prints them, not as strings sort.

    Lexically "11-134.1" precedes "11-134" and "11-9" follows "11-130", which
    makes an outline actively misleading about what the Article contains.
    """
    match = _SECTION_ID.search(section or "")
    if match is None:
        return (999, 999, 999, "")
    article, number, decimal, letter = match.groups()
    return (
        int(article),
        int(number),
        int((decimal or ".0").lstrip(".")),
        (letter or "").lower(),
    )


async def _scan_page(skip: int) -> list[dict]:
    """One page of the index, projected to the fields the graph needs."""
    client = get_async_search_client()
    results = await client.search(
        search_text="*",
        select=_SCAN_FIELDS,
        top=_PAGE_SIZE,
        skip=skip,
    )
    return [dict(doc) async for doc in results]


async def _scan_index() -> list[dict]:
    """Every indexed chunk, paged until the index is exhausted.

    The single seam the tests replace, so nothing below it ever needs a client.
    """
    docs: list[dict[str, Any]] = []
    skip = 0
    while True:
        page = await _scan_page(skip)
        if not page:
            break
        docs.extend(page)
        skip += len(page)
    return docs


def reset_corpus_cache() -> None:
    """Drop the built corpus. For tests and for a post-ingestion rebuild."""
    global _CORPUS
    _CORPUS = None


@dataclass(frozen=True)
class SectionNode:
    """One section as the outline knows it: its id, its Article, its heading."""

    section: str
    article: str
    title: str


class Corpus:
    """The Code's structure: which sections exist, and which point at which."""

    def __init__(
        self,
        nodes: dict[str, SectionNode],
        cites: dict[str, tuple[str, ...]],
        cited_by: dict[str, tuple[str, ...]],
    ) -> None:
        self.nodes = nodes
        self._cites = cites
        self._cited_by = cited_by

    def cites(self, section: str) -> tuple[str, ...]:
        """The sections this one names, in statutory order."""
        return self._cites.get(normalize_section(section), ())

    def cited_by(self, section: str) -> tuple[str, ...]:
        """The sections that name this one -- the exception you did not look for."""
        return self._cited_by.get(normalize_section(section), ())

    def outline(self, article: str) -> tuple[SectionNode, ...]:
        """Every section of one Article, in the order the Code prints them."""
        wanted = str(article)
        return tuple(
            sorted(
                (n for n in self.nodes.values() if n.article == wanted),
                key=lambda n: section_sort_key(n.section),
            )
        )


_CORPUS: Corpus | None = None
_BUILD_LOCK = asyncio.Lock()


def _build(docs: list[dict]) -> Corpus:
    """Collapse chunks into sections, then edges, then the inverse of the edges."""
    nodes: dict[str, SectionNode] = {}
    forward: dict[str, set[str]] = {}

    for doc in docs:
        section = normalize_section(doc.get("section") or "")
        if not section:
            continue
        if section not in nodes:
            nodes[section] = SectionNode(
                section=section,
                article=str(doc.get("article") or ""),
                title=str(doc.get("section_title") or ""),
            )
        targets = forward.setdefault(section, set())
        for raw in doc.get("referenced_sections") or []:
            target = normalize_section(raw)
            # A section naming its own subsections is not a dependency on
            # anything; left in, every section cites itself and `cited_by`
            # returns noise for the sections that matter most.
            if target and target != section:
                targets.add(target)

    backward: dict[str, set[str]] = {}
    for source, targets in forward.items():
        for target in targets:
            backward.setdefault(target, set()).add(source)

    def in_order(ids: set[str]) -> tuple[str, ...]:
        return tuple(sorted(ids, key=section_sort_key))

    return Corpus(
        nodes=nodes,
        cites={s: in_order(t) for s, t in forward.items()},
        cited_by={s: in_order(t) for s, t in backward.items()},
    )


async def load_corpus() -> Corpus:
    """The built corpus, scanning the index on first use only.

    Locked because two concurrent tool calls on the first turn would otherwise
    both scan -- twice the latency for one result.
    """
    global _CORPUS
    if _CORPUS is not None:
        return _CORPUS
    async with _BUILD_LOCK:
        if _CORPUS is None:
            docs = await _scan_index()
            _CORPUS = _build(docs)
            logger.info(
                "Corpus built: %d sections, %d citing edges",
                len(_CORPUS.nodes),
                sum(len(v) for v in _CORPUS._cites.values()),
            )
    return _CORPUS
