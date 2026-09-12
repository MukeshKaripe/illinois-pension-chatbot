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

import logging
from typing import Any

from pension_chat.agent.llm import get_async_search_client

logger = logging.getLogger("pension_chat.agent.corpus")

#: Chunks per page. The index holds ~4,766 documents, so this is a handful of
#: round trips rather than one enormous response.
_PAGE_SIZE = 1000

_SCAN_FIELDS = ["section", "article", "section_title", "referenced_sections"]


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
    return None
