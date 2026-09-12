# Corpus Structure Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent structural knowledge of the Pension Code — a forward *and backward* citation graph, and a per-Article outline of section headings — derived from data already in the search index.

**Architecture:** One new module, `agent/corpus.py`, that scans the Azure AI Search index once per process and builds two views over it: a citation graph (which sections cite which, and crucially which sections *cite this one*) and an ordered outline of headings per Article. Both are cached for the process lifetime, exactly as `retrieval._SECTION_CACHE` already is, because statute text is immutable until a re-ingestion restarts the API. `retrieval._follow_references` is then rewired to use the graph instead of its top-3/first-2 heuristic, which is what makes this phase ship value on its own.

**Tech Stack:** Python 3.12, `uv`, pytest (`asyncio_mode = "auto"` — async tests need no marker), Azure AI Search async client, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-12-intelligent-agent-design.md` (§3, "The Code as a graph and a map")

## Global Constraints

- Backend commands run under `uv`: `uv run pytest`, `uv run ruff check .`, `uv run mypy .`.
- **Tests must not touch the network.** Every test here monkeypatches the single scan seam, `corpus._scan_index`, or `retrieval.fetch_sections`. No test may construct an Azure client.
- `uv run ruff check .` currently reports **37 pre-existing errors**. Do not fix unrelated ones; just do not add any. Compare counts before and after.
- **No re-ingestion.** `referenced_sections` is already parsed (`ingestion/parser.py:306-339`) and already indexed (`ingestion/indexer.py:148`); `section_title` is already indexed. This phase reads them and adds nothing to the pipeline.
- Section ids carry a meaningful trailing letter (`1-113.4a` is not `1-113.4`) and an optional decimal (`11-134.1`). Never strip either.
- Module docstrings in this codebase explain *why the code is shaped this way*, usually naming the failure that motivated it. Match that voice.
- Use `from __future__ import annotations` at the top of every new module.

### Deviation from the spec, deliberate

The spec's §3 says the graph lives in a `section_references` Postgres table (migration 007). **This plan builds it in memory instead, and adds no migration.** Reasons:

1. The edges are derived *from the search index*. A Postgres copy goes silently stale the moment an Article is re-ingested — which is the exact failure class `indexer.prune_stale_records` exists to prevent. An in-process build is always consistent with the index it was built from.
2. The data is small: ~4,766 documents collapsing to roughly 2,000 sections and some thousands of edges.
3. `retrieval.py` already caches immutable statute data this way (`_SECTION_CACHE`, with `reset_caches()` as the escape), so this follows an established pattern rather than inventing one.
4. It removes a migration, a build script, and the question of whether a public reference table needs an RLS policy.

If a future admin UI needs to browse the graph outside the API process, that is when a table earns its place. Amend the spec's §3 to record this.

---

### Task 1: Scan the index once

**Files:**
- Create: `src/pension_chat/agent/corpus.py`
- Test: `tests/test_corpus.py`

**Interfaces:**
- Consumes: `pension_chat.agent.llm.get_async_search_client` (existing).
- Produces: `async def _scan_index() -> list[dict]` — the one seam every later test monkeypatches. Returns one dict per indexed chunk with keys `section`, `article`, `section_title`, `referenced_sections`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_corpus.py`:

```python
"""The shape of the Code: the citation graph and the outline built over it."""

from __future__ import annotations

import pytest

from pension_chat.agent import corpus


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test builds its own corpus; a cached one from a neighbour would hide bugs."""
    corpus.reset_corpus_cache()
    yield
    corpus.reset_corpus_cache()


def chunk(section: str, article: str, title: str, refs: list[str] | None = None) -> dict:
    return {
        "section": section,
        "article": article,
        "section_title": title,
        "referenced_sections": refs or [],
    }


async def test_scan_pages_until_the_index_is_exhausted(monkeypatch):
    """One page is not the corpus.

    The index holds thousands of chunks and the client returns them a page at a
    time. A scan that reads the first page builds a graph missing most of its
    edges -- and looks perfectly healthy doing it.
    """
    pages = [
        [chunk("11-134", "11", "Minimum annuities")],
        [chunk("11-216", "11", "Computation of service")],
        [],
    ]
    calls: list[int] = []

    async def fake_page(skip: int):
        calls.append(skip)
        return pages[len(calls) - 1]

    monkeypatch.setattr(corpus, "_scan_page", fake_page)

    docs = await corpus._scan_index()

    assert [d["section"] for d in docs] == ["11-134", "11-216"]
    assert len(calls) == 3, "must keep paging until a page comes back empty"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.agent.corpus'`

- [ ] **Step 3: Write minimal implementation**

Create `src/pension_chat/agent/corpus.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/corpus.py tests/test_corpus.py
git commit -m "Corpus: scan the index once, paged to exhaustion"
```

---

### Task 2: Normalise a section id, and order sections the way the Code does

**Files:**
- Modify: `src/pension_chat/agent/corpus.py`
- Test: `tests/test_corpus.py`

**Interfaces:**
- Produces: `def normalize_section(raw: str) -> str` and `def section_sort_key(section: str) -> tuple[int, int, int, str]`. Task 3 uses `normalize_section` to build edges; Task 4 uses `section_sort_key` to order an outline.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_corpus.py`:

```python
def test_normalize_strips_a_subsection_but_never_the_section_id():
    """A subsection is not a section; a trailing letter can be either.

    "11-134(a)" is Section 11-134. But Section 1-113.4a is its own section --
    dropping the "a" pins 1-113.4, a different rule, and that exact confusion
    already cost the ingestion pipeline a whole section's text.
    """
    assert corpus.normalize_section("11-134(a)") == "11-134"
    assert corpus.normalize_section(" 40 ILCS 5/11-134 ") == "11-134"
    assert corpus.normalize_section("Section 11-134") == "11-134"
    assert corpus.normalize_section("1-113.4a") == "1-113.4a"
    assert corpus.normalize_section("11-134.1") == "11-134.1"
    assert corpus.normalize_section("") == ""
    assert corpus.normalize_section("not a section") == ""


def test_sections_order_the_way_the_code_prints_them():
    """Lexical order puts 11-134.1 before 11-134 and 11-9 after 11-130.

    An outline is a reading aid. In the wrong order it is worse than none,
    because it implies the Code says something it does not.
    """
    unsorted = ["11-134.1", "11-9", "11-130", "11-134", "1-113.4a", "1-113.4"]
    assert sorted(unsorted, key=corpus.section_sort_key) == [
        "1-113.4",
        "1-113.4a",
        "11-9",
        "11-130",
        "11-134",
        "11-134.1",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_corpus.py -k "normalize or order" -v`
Expected: FAIL — `AttributeError: module 'pension_chat.agent.corpus' has no attribute 'normalize_section'`

- [ ] **Step 3: Write minimal implementation**

Add `import re` to the module's import block, then add below `_SCAN_FIELDS`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/corpus.py tests/test_corpus.py
git commit -m "Corpus: a section id keeps its letter, and sorts numerically"
```

---

### Task 3: Build the graph, and invert it

**Files:**
- Modify: `src/pension_chat/agent/corpus.py`
- Test: `tests/test_corpus.py`

**Interfaces:**
- Consumes: `_scan_index()`, `normalize_section()` from Tasks 1-2.
- Produces: `class SectionNode` (frozen dataclass: `section: str`, `article: str`, `title: str`); `class Corpus` with `nodes: dict[str, SectionNode]`, `cites(section: str) -> tuple[str, ...]`, `cited_by(section: str) -> tuple[str, ...]`, `outline(article: str) -> tuple[SectionNode, ...]`; `async def load_corpus() -> Corpus` (cached); `reset_corpus_cache()` now clears it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_corpus.py`:

```python
async def test_the_graph_runs_backwards_as_well_as_forwards(monkeypatch):
    """The backward edge is the whole point.

    Section 11-134 computes on service "as defined in Section 11-216", so a
    reader of 11-216 needs to know 11-134 leans on it. Forward edges were
    already in the index and nothing had ever inverted them.
    """
    async def fake_scan():
        return [
            chunk("11-134", "11", "Minimum annuities", ["11-216", "11-134(f-1)"]),
            chunk("11-130", "11", "Age and service annuity", ["11-216"]),
            chunk("11-216", "11", "Computation of service", []),
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    built = await corpus.load_corpus()

    assert built.cites("11-134") == ("11-216",), "a self-reference is not an edge"
    assert built.cited_by("11-216") == ("11-130", "11-134")
    assert built.cited_by("11-134") == ()
    assert built.cites("not-a-section") == ()


async def test_chunks_of_one_section_collapse_into_one_node(monkeypatch):
    """The index stores a record per subsection, not per section.

    Section 11-134 is a dozen chunks. Treated as a dozen nodes, its references
    are counted a dozen times and its title arrives from whichever chunk landed
    last.
    """
    async def fake_scan():
        return [
            chunk("11-134", "11", "Minimum annuities", ["11-216"]),
            chunk("11-134", "11", "Minimum annuities", ["11-216", "11-131"]),
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    built = await corpus.load_corpus()

    assert list(built.nodes) == ["11-134"]
    assert built.cites("11-134") == ("11-131", "11-216")


async def test_the_corpus_is_built_once_per_process(monkeypatch):
    """A full index scan on every tool call would be indefensible."""
    builds = 0

    async def fake_scan():
        nonlocal builds
        builds += 1
        return [chunk("11-134", "11", "Minimum annuities", [])]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)

    await corpus.load_corpus()
    await corpus.load_corpus()
    assert builds == 1

    corpus.reset_corpus_cache()
    await corpus.load_corpus()
    assert builds == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_corpus.py -k backwards -v`
Expected: FAIL — `AttributeError: module 'pension_chat.agent.corpus' has no attribute 'load_corpus'`

- [ ] **Step 3: Write minimal implementation**

Add `import asyncio` and `from dataclasses import dataclass` to the module's
import block, then add:

```python
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
```

Replace the placeholder `reset_corpus_cache` body with:

```python
def reset_corpus_cache() -> None:
    """Drop the built corpus. For tests and for a post-ingestion rebuild."""
    global _CORPUS
    _CORPUS = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/corpus.py tests/test_corpus.py
git commit -m "Corpus: the citation graph, and the inverse nobody had built"
```

---

### Task 4: The two public entry points

**Files:**
- Modify: `src/pension_chat/agent/corpus.py`
- Test: `tests/test_corpus.py`

**Interfaces:**
- Consumes: `load_corpus()` from Task 3.
- Produces: `async def related_sections(section: str, direction: str = "cites") -> tuple[str, ...]` and `async def outline(article: str | int) -> tuple[SectionNode, ...]`. These are the two functions Phase 3 wraps as agent tools; nothing else in this phase calls them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_corpus.py`:

```python
async def test_related_sections_walks_either_direction(monkeypatch):
    async def fake_scan():
        return [
            chunk("11-134", "11", "Minimum annuities", ["11-216"]),
            chunk("11-216", "11", "Computation of service", []),
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)

    assert await corpus.related_sections("11-134") == ("11-216",)
    assert await corpus.related_sections("11-134(a)", "cites") == ("11-216",)
    assert await corpus.related_sections("11-216", "cited_by") == ("11-134",)


async def test_an_unknown_direction_is_refused_rather_than_guessed(monkeypatch):
    """A typo'd direction must not silently return the forward edges.

    This argument reaches here from a model-authored tool call, where "citedBy"
    and "backward" are exactly the kinds of near-misses to expect.
    """
    async def fake_scan():
        return [chunk("11-134", "11", "Minimum annuities", ["11-216"])]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)

    with pytest.raises(ValueError, match="cites"):
        await corpus.related_sections("11-134", "backward")


async def test_outline_is_one_articles_headings_in_order(monkeypatch):
    async def fake_scan():
        return [
            chunk("11-134", "11", "Minimum annuities"),
            chunk("11-130", "11", "Age and service annuity"),
            chunk("12-118", "12", "Employee defined"),
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)

    eleven = await corpus.outline(11)
    assert [(n.section, n.title) for n in eleven] == [
        ("11-130", "Age and service annuity"),
        ("11-134", "Minimum annuities"),
    ]
    assert [n.section for n in await corpus.outline("12")] == ["12-118"]
    assert await corpus.outline(99) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_corpus.py -k "related or outline" -v`
Expected: FAIL — `AttributeError: module 'pension_chat.agent.corpus' has no attribute 'related_sections'`

- [ ] **Step 3: Write minimal implementation**

Add to the end of `corpus.py`:

```python
#: The only directions `related_sections` accepts. Spelled out so a model's
#: near-miss ("backward", "citedBy") is refused rather than read as the default.
DIRECTIONS = ("cites", "cited_by")


async def related_sections(section: str, direction: str = "cites") -> tuple[str, ...]:
    """Walk one hop of the citation graph.

    `cites` is what this section depends on; `cited_by` is what depends on it,
    which is where an exception hides.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, not {direction!r}")
    built = await load_corpus()
    return built.cites(section) if direction == "cites" else built.cited_by(section)


async def outline(article: str | int) -> tuple[SectionNode, ...]:
    """Every heading in one Article, in statutory order.

    The Code's own vocabulary for its own concepts: a member asks about
    "retiring early without losing too much", and the heading that answers it
    says "annuity reduction". Reading the outline is how that gap is crossed
    without guessing.
    """
    built = await load_corpus()
    return built.outline(str(article))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/corpus.py tests/test_corpus.py
git commit -m "Corpus: related_sections and outline, the two ways in"
```

---

### Task 5: Let retrieval follow references it can actually see

**Files:**
- Modify: `src/pension_chat/agent/retrieval.py:572-618` (`_follow_references`)
- Modify: `src/pension_chat/agent/retrieval.py:63-67` (`reset_caches`)
- Test: `tests/test_retrieval_references.py` (create)

**Interfaces:**
- Consumes: `corpus.load_corpus()` from Task 3, `corpus.normalize_section()` from Task 2.
- Produces: no new public API. `_follow_references` keeps its signature — `(assembled: list[RetrievedSection], char_budget: int, limit: int) -> list[RetrievedSection]`.

This is the task that makes the phase worth shipping on its own. Today `_follow_references` reads the references of `assembled[:3]` only, and stops at the first `limit` it finds — so a dependency named by the fourth-ranked section is unreachable, and which two arrive is an accident of iteration order. The graph replaces that with a *centrality* rule: a section cited by more of what was retrieved is more likely to be the one the answer needs.

- [ ] **Step 1: Write the failing test**

Create `tests/test_retrieval_references.py`:

```python
"""Which cross-references retrieval follows, and why the old rule missed them."""

from __future__ import annotations

import pytest

from pension_chat.agent import corpus, retrieval
from pension_chat.agent.retrieval import RetrievedSection


@pytest.fixture(autouse=True)
def _clean():
    retrieval.reset_caches()
    yield
    retrieval.reset_caches()


def section(section_id: str, refs: list[str], score: float) -> RetrievedSection:
    return RetrievedSection(
        section=section_id,
        article="11",
        title=f"Section {section_id}",
        text="x" * 2000,
        score=score,
        referenced_sections=refs,
    )


@pytest.fixture
def fetched(monkeypatch):
    """Every requested section comes back with enough text to survive the budget."""
    async def fake_fetch(section_ids, articles=None):
        return {
            s: [{"section": s, "subsection_id": "(a)", "content": "y" * 2000,
                 "article": "11", "section_title": f"Section {s}"}]
            for s in section_ids
        }

    monkeypatch.setattr(retrieval, "fetch_sections", fake_fetch)


async def test_a_reference_below_the_top_three_is_now_reachable(monkeypatch, fetched):
    """The old rule read `assembled[:3]` and could not see the fourth.

    Rank is about how well a section matched the query. Whether it names the
    definition the answer turns on is a different question, and slicing by rank
    answered it by accident.
    """
    async def fake_scan():
        return [
            {"section": "11-130", "article": "11", "section_title": "A", "referenced_sections": []},
            {"section": "11-131", "article": "11", "section_title": "B", "referenced_sections": []},
            {"section": "11-132", "article": "11", "section_title": "C", "referenced_sections": []},
            {"section": "11-134", "article": "11", "section_title": "D",
             "referenced_sections": ["11-216"]},
            {"section": "11-216", "article": "11", "section_title": "Service", "referenced_sections": []},
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    corpus.reset_corpus_cache()

    assembled = [
        section("11-130", [], 0.9),
        section("11-131", [], 0.8),
        section("11-132", [], 0.7),
        section("11-134", ["11-216"], 0.6),
    ]

    extra = await retrieval._follow_references(assembled, char_budget=20000, limit=2)

    assert [s.section for s in extra] == ["11-216"]


async def test_the_most_depended_on_section_is_followed_first(monkeypatch, fetched):
    """With budget for one, take what the retrieved text leans on most.

    The old rule took whichever reference it met first while walking the
    sections in rank order, so the single passing mention in the best-matching
    section beat the definition two other sections both turned on.
    """
    async def fake_scan():
        return [
            {"section": "11-130", "article": "11", "section_title": "A",
             "referenced_sections": ["11-169"]},
            {"section": "11-134", "article": "11", "section_title": "B",
             "referenced_sections": ["11-216"]},
            {"section": "11-131", "article": "11", "section_title": "C",
             "referenced_sections": ["11-216"]},
            {"section": "11-216", "article": "11", "section_title": "Service", "referenced_sections": []},
            {"section": "11-169", "article": "11", "section_title": "Other", "referenced_sections": []},
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    corpus.reset_corpus_cache()

    assembled = [
        section("11-130", ["11-169"], 0.9),
        section("11-134", ["11-216"], 0.8),
        section("11-131", ["11-216"], 0.7),
    ]

    extra = await retrieval._follow_references(assembled, char_budget=20000, limit=1)

    assert [s.section for s in extra] == ["11-216"], "two sections cite it; 11-169 only one"


async def test_a_section_already_retrieved_is_never_followed(monkeypatch, fetched):
    async def fake_scan():
        return [
            {"section": "11-134", "article": "11", "section_title": "B",
             "referenced_sections": ["11-216"]},
            {"section": "11-216", "article": "11", "section_title": "Service", "referenced_sections": []},
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    corpus.reset_corpus_cache()

    assembled = [section("11-134", ["11-216"], 0.9), section("11-216", [], 0.5)]

    assert await retrieval._follow_references(assembled, char_budget=20000, limit=2) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_retrieval_references.py -v`
Expected: FAIL — the first test fails with `assert [] == ['11-216']`, because `assembled[:3]` never reaches `11-134`.

- [ ] **Step 3: Write minimal implementation**

In `retrieval.py`, add the import beside the existing ones:

```python
from pension_chat.agent import corpus
```

Replace the body of `_follow_references` from its docstring through the `wanted = wanted[:limit]` line with:

```python
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
```

Everything from `grouped = await fetch_sections(wanted)` onward is unchanged.

Then extend `reset_caches` so a test clearing retrieval's caches clears the corpus too:

```python
def reset_caches() -> None:
    """Drop cached statute text, query embeddings, and the corpus graph."""
    _SECTION_CACHE.clear()
    _EMBED_CACHE.clear()
    corpus.reset_corpus_cache()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_retrieval_references.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Check nothing else regressed**

Run: `uv run pytest -q`
Expected: all tests pass — the count should be 1003 plus the 12 added by this plan.

- [ ] **Step 6: Commit**

```bash
git add src/pension_chat/agent/retrieval.py tests/test_retrieval_references.py
git commit -m "Retrieval: follow the references the whole result set names"
```

---

### Task 6: Build the corpus at startup, and check it against the real index

**Files:**
- Modify: `src/pension_chat/main.py` (the lifespan warm-up)
- Test: manual, against the live index — this is the task that proves the graph is real

**Interfaces:**
- Consumes: `corpus.load_corpus()` from Task 3.
- Produces: nothing new. Moves the first scan off the first member's turn.

- [ ] **Step 1: Read the existing warm-up**

`src/pension_chat/main.py:25-61` already warms the database, search and model
connections, each under a 20s timeout, logging and swallowing failures so a
warm-up cannot stop the app from starting. The corpus build belongs in that
same list and inherits that same contract — which matters here, because a
corpus that fails to build must fall through to Task 5's per-section fallback
rather than take the API down.

- [ ] **Step 2: Add a fourth warm-up**

In `_warm_connections`, add the import beside the others already inside the
function:

```python
    from pension_chat.agent.corpus import load_corpus
```

Add the closure beside `warm_llm`:

```python
    async def warm_corpus():
        await load_corpus()
```

And add it to the loop's tuple, last, so the connections it depends on are
already open:

```python
    for name, task in (
        ("database", warm_db),
        ("search", warm_search),
        ("model", warm_llm),
        ("corpus", warm_corpus),
    ):
```

No extra logging is needed: `load_corpus` already logs `Corpus built: N
sections, M citing edges` on the build itself.

- [ ] **Step 3: Run the API against the real index**

```bash
uv run uvicorn pension_chat.main:app --port 8000
```

Expected in the log: `Corpus built: N sections, M citing edges`, followed by
`Warmed corpus connection`, where **N is between 1,500 and 2,500**. The index holds 4,766 documents and the index stores one record per *subsection*, so anything close to 4,766 means chunks did not collapse into sections (Task 3 is wrong), and anything under ~1,000 means the scan stopped early (Task 1 is wrong).

- [ ] **Step 4: Spot-check the edge the design was written for**

With the API running, in a Python shell:

```bash
uv run python -c "
import asyncio
from pension_chat.agent import corpus

async def main():
    built = await corpus.load_corpus()
    print('11-134 cites:', built.cites('11-134'))
    print('11-216 cited_by:', built.cited_by('11-216'))
    print('Article 11 outline, first 5:', [(n.section, n.title) for n in built.outline('11')][:5])

asyncio.run(main())
"
```

Expected: `11-134 cites` contains `11-216`. `11-216 cited_by` is non-empty and contains `11-134`. The outline's first five are Article 11 sections in ascending order with real headings.

**If `cited_by` for 11-216 is empty, stop.** That is the capability this phase exists for, and an empty result means either the parser never recorded the reference or `normalize_section` is dropping it. Check `uv run python -c "import asyncio; from pension_chat.agent.retrieval import fetch_sections; print(asyncio.run(fetch_sections(['11-134']))['11-134'][0]['referenced_sections'])"` to see what the index actually holds before changing anything.

- [ ] **Step 5: Lint and type-check**

```bash
uv run ruff check .
uv run mypy .
```

Expected: ruff reports **37 errors, the pre-existing count** — no new ones. mypy clean for `corpus.py`.

- [ ] **Step 6: Commit**

```bash
git add src/pension_chat/main.py
git commit -m "Build the corpus at startup, beside the other warm-ups"
```

---

## Done when

- `uv run pytest` passes, with 12 new tests across `tests/test_corpus.py` and `tests/test_retrieval_references.py`.
- `uv run ruff check .` reports no more than the 37 pre-existing errors.
- The API logs a corpus of 1,500–2,500 sections at startup.
- `cited_by("11-216")` contains `11-134` against the live index.
- No migration, no re-ingestion, no change to any answer's shape — only to which sections retrieval can reach.

## Not in this phase

- **Wrapping `related_sections` and `outline` as agent tools.** They are built and tested here; Phase 3 exposes them, because a tool has no caller until the loop exists.
- **The 24-line Article index for the system prompt** (spec §3). It is prompt content, so it lands with the prompt in Phase 3.
- **A `section_references` table.** See the deviation note above.
