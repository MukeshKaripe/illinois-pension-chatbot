# Multi-Format Ingestion (MEABF Corpus) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest statute text from any supported file format (`.txt`, `.md`, `.docx`, `.pdf`) through the existing ILGA parser, and ingest the MEABF corpus — the client's Article 8 document plus Articles 1 and 20 and the sections Article 8 cites — with checks that make a bad conversion fail loudly instead of thinning the index.

**Architecture:** A converter per format turns bytes into ILGA-shaped plain text; a shared normaliser cleans it; file-level checks reject text whose structure was damaged; a splitter groups the text by Article; the existing parser runs unchanged on each Article's text (new entry point `parse_statute_text`). A pipeline module orchestrates sources, corpus selection, the shrink guard and the existing chunk → embed → upload → prune steps through injected "sinks", so it is fully testable without Azure. `scripts/ingest_articles.py` becomes a thin CLI over it.

**Tech Stack:** Python 3.12, `uv`, pytest (+ pytest-asyncio, `asyncio_mode = "auto"`), `python-docx`, `pdfplumber`, `azure-storage-blob`, Azure AI Search SDK (existing).

**Spec:** `docs/superpowers/specs/2026-09-28-multi-format-ingestion-design.md`, and the corpus decision in `docs/superpowers/specs/2026-09-29-employer-first-scope.md` ("Statute corpus for the MEABF focus").

**Amendments to the spec, decided after it was written — follow these, not the spec's original wording:**
1. **Client documents are never committed** (`data/` is gitignored). The real-document tests read from `data/statutes/` and **skip** when the file is absent, exactly like the existing corpus tests in `tests/test_ingestion.py`. Nothing goes in `tests/fixtures/ingestion/client/`.
2. **The "word-count check" is a converter completeness check.** The ~3,300 lost words were lost *during extraction* (nested tables not read), before any parsing, so the check lives in the `.docx` converter: every character in the document's text nodes must appear in the converted output, or the file is rejected.
3. **Partial Articles** (only the cited sections of another fund's Article) are ingested but **not recorded in `pension_articles`** — that table means "this Article is in the index", and recording Article 9 as `ingested` when three of its sections are would be false. No migration.
4. **ILGA texts are always read from the local `data/statutes/` directory**; only the client's documents come from the chosen source (local or Blob).
5. **No `--sections` option.** The spec proposed a per-source section allow-list flag; a *corpus* definition (`pipeline.CORPORA`) replaces it, with the one-hop sections derived from the primary documents' own citations — which is what the spec asked the flag's list to be.

## Global Constraints

- Work on a feature branch, never `main`: `git checkout -b ingestion-multiformat`.
- Unit tests need no network: Azure clients are faked or monkeypatched; real-document tests skip when `data/statutes/` files are absent.
- `tests/test_ingestion.py` is **not edited**. Passing unedited is the evidence the parser's behaviour did not change.
- Licences: `pdfplumber` (MIT), `python-docx` (MIT), `azure-storage-blob` (MIT). **Never PyMuPDF** (AGPL).
- Only act `40 ILCS 5` is accepted; any other act is refused by name.
- A refused file or Article never changes the index; the run exits non-zero if anything was refused.
- The shrink guard **fails closed**: if the index cannot be listed, the Article is refused. A missing index (first ingestion) counts as empty.
- Commit messages follow the repo's style (`Area: what changed`) and end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Lint: `uv run ruff check <changed files>` clean for new files.

## Review Focus

1. **A `.docx` whose text sits in merged table cells** — python-docx returns a horizontally merged cell once per grid column, so naive extraction duplicates its text and creates colliding sections. Expected: text appears once. *(Test in Task 3: `test_docx_merged_cell_text_appears_once`.)*
2. **A file named with an upper-case extension (`ARTICLE_8.TXT`, `MEABF.DOCX`)** — Blob uploads from Windows often are. Expected: converted like its lower-case twin. *(Task 3: `test_extension_is_case_insensitive`.)*
3. **Headings outside tables interleaved with tables** — python-docx exposes `paragraphs` and `tables` as separate lists, so reading one then the other scrambles order and puts headings after their sections. Expected: document order. *(Task 3: `test_docx_paragraphs_and_tables_keep_document_order`.)*
4. **Running the MEABF corpus against an index that already holds whole Articles** (our dev index holds all of Article 9) — pruning to the three cited sections would silently delete the rest. Expected: Article 9 refused by the shrink guard unless `--allow-shrink 9`. *(Task 8: `test_partial_article_over_a_whole_indexed_article_is_refused`.)*
5. **Article 8 cites a section that the other Article's file does not contain** (renumbered, repealed, or a typo in the statute). Expected: listed as unavailable, no crash, the rest ingested. *(Task 8: `test_a_cited_section_missing_from_its_file_is_reported_unavailable`.)*

---

## File Structure

| File | Responsibility |
|---|---|
| `src/pension_chat/ingestion/parser.py` (modify) | Add `parse_statute_text`; `parse_statute_file` becomes a wrapper. |
| `src/pension_chat/ingestion/normalise.py` (create) | Text normalisation shared by all formats; markdown stripping; PDF page-furniture removal and broken-line joining; the shared `ANCHOR_RE`. |
| `src/pension_chat/ingestion/sources.py` (create) | `SourceFile`, `Source` protocol, `LocalDirSource`, `BlobSource`. |
| `src/pension_chat/ingestion/converters.py` (create) | `to_text(SourceFile) -> str` per extension; `.docx` completeness check; `.pdf` text-layer check. |
| `src/pension_chat/ingestion/splitter.py` (create) | `split_by_article(text) -> dict[int, str]`; act guard; letter-suffixed Article refusal. |
| `src/pension_chat/ingestion/checks.py` (create) | File checks (mid-line headers, header/title agreement) and record-id uniqueness; the ingestion error types. |
| `src/pension_chat/ingestion/selection.py` (create) | `one_hop_references(sections, own_articles)`. |
| `src/pension_chat/ingestion/indexer.py` (modify) | Add `list_indexed_sections(article)` and `IndexListingFailed`. |
| `src/pension_chat/ingestion/pipeline.py` (create) | `Corpus`, `CORPORA`, `Sinks`, `ArticleResult`, `RunReport`, `async run(...)`. |
| `src/pension_chat/config.py` (modify) | Optional `azure_storage_connection_string`, `azure_storage_container`. |
| `scripts/ingest_articles.py` (rewrite) | CLI: `--corpus`, `--source`, `--dry-run`, `--allow-shrink`, `--article`. |
| `CLAUDE.md` (modify) | Ingestion section: corpora, formats, checks, dry-run. |
| `tests/_pdf.py` (create) | `make_pdf(pages)` — builds tiny real PDFs in tests, no committed binaries. |
| `tests/test_ingestion_parser_text.py`, `test_ingestion_normalise.py`, `test_ingestion_converters.py`, `test_ingestion_splitter.py`, `test_ingestion_checks.py`, `test_ingestion_sources.py`, `test_ingestion_selection.py`, `test_ingestion_pipeline.py`, `test_ingestion_client_docs.py` (create) | One test file per unit. |

---

### Task 1: Dependencies and the parser's text entry point

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Modify: `src/pension_chat/ingestion/parser.py:130-208`
- Test: `tests/test_ingestion_parser_text.py`

**Interfaces:**
- Produces: `parse_statute_text(content: str, article_number: int, source: str = "<text>") -> list[ParsedSection]` — identical behaviour to `parse_statute_file` on the same content.

- [ ] **Step 1: Create the branch and add dependencies**

```bash
git checkout -b ingestion-multiformat
uv add python-docx pdfplumber azure-storage-blob
```
Expected: `pyproject.toml` gains the three packages; `uv.lock` updates.

- [ ] **Step 2: Write the failing test**

Create `tests/test_ingestion_parser_text.py`:

```python
"""`parse_statute_text` is `parse_statute_file` without the file.

Converters hand the parser text, not a path. The body of the parser moved
unchanged; this pins that the two entry points agree.
"""

from __future__ import annotations

from pathlib import Path

from pension_chat.ingestion.parser import parse_statute_file, parse_statute_text

TEXT = (
    "(40 ILCS 5/Art. 8 heading)\n"
    "(40 ILCS 5/8-101) (from Ch. 108 1/2, par. 8-101)\n"
    "Sec. 8-101. Creation of fund. In each city of more than 500,000 inhabitants a fund shall be created.\n"
    "(Source: Laws 1963, p. 161.)\n"
    "(40 ILCS 5/8-102) (from Ch. 108 1/2, par. 8-102)\n"
    "Sec. 8-102. Terms defined.\n"
    "(a) The terms used in this Article have the meanings in Sections 8-103 to 8-110.\n"
    "(b) See also Section 1-160.\n"
    "(Source: Laws 1963, p. 161.)\n"
)


def test_text_and_file_entry_points_agree(tmp_path: Path) -> None:
    path = tmp_path / "article_8.txt"
    path.write_text(TEXT, encoding="utf-8")

    from_file = parse_statute_file(str(path), 8)
    from_text = parse_statute_text(TEXT, 8)

    assert [s.section_id for s in from_text] == ["8-101", "8-102"]
    assert from_text == from_file


def test_crlf_text_parses_like_lf_text() -> None:
    assert parse_statute_text(TEXT.replace("\n", "\r\n"), 8) == parse_statute_text(TEXT, 8)
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_ingestion_parser_text.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_statute_text'`.

- [ ] **Step 4: Implement**

In `src/pension_chat/ingestion/parser.py`, replace the head of `parse_statute_file` (lines 130–144, through the line-ending normalisation) so the function body becomes `parse_statute_text`:

```python
def parse_statute_file(filepath: str, article_number: int) -> list[ParsedSection]:
    """Parse an ILGA statute text file into structured sections.

    A thin wrapper: converters for other formats call `parse_statute_text`
    directly with the text they produced.
    """
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    return parse_statute_text(content, article_number, source=filepath)


def parse_statute_text(
    content: str, article_number: int, source: str = "<text>"
) -> list[ParsedSection]:
    """Parse ILGA-shaped statute text into structured sections.

    Args:
        content: The text of one Article, as ILGA publishes it.
        article_number: The article number (e.g. 8, 11).
        source: Where the text came from, for the log line only.

    Returns:
        List of ParsedSection objects.
    """
    # Normalize line endings
    content = content.replace("\r\n", "\n").replace("\r", "\n")
```

Leave everything after that line unchanged, except the final log call, which must use `source` instead of `filepath`:

```python
    logger.info(
        "Parsed Article %d: %d sections from %s",
        article_number,
        len(sections),
        source,
    )

    return sections
```

- [ ] **Step 5: Run the new and existing tests**

Run: `uv run pytest tests/test_ingestion_parser_text.py tests/test_ingestion.py -v`
Expected: all PASS; `tests/test_ingestion.py` unedited.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/pension_chat/ingestion/parser.py tests/test_ingestion_parser_text.py
git commit -m "Ingestion: parse statute text, not only a file path" -m "Converters for other formats produce text; parse_statute_file becomes a wrapper and its body moves unchanged. Adds python-docx, pdfplumber and azure-storage-blob." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The normaliser

**Files:**
- Create: `src/pension_chat/ingestion/normalise.py`
- Test: `tests/test_ingestion_normalise.py`

**Interfaces:**
- Produces:
  - `ANCHOR_RE: re.Pattern[str]` — a line the parser treats as structure; never joined onto the line before it.
  - `normalise(text: str) -> str`
  - `strip_markdown(text: str) -> str`
  - `remove_page_furniture(pages: list[list[str]]) -> list[str]`
  - `join_broken_lines(text: str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingestion_normalise.py`:

```python
"""Every converter's output passes through the same cleanup, so formats cannot drift apart."""

from __future__ import annotations

from pension_chat.ingestion.normalise import (
    join_broken_lines,
    normalise,
    remove_page_furniture,
    strip_markdown,
)


def test_normalise_replaces_nbsp_and_removes_invisible_characters() -> None:
    raw = "﻿\xa0\xa0\xa0\xa0(40 ILCS 5/8-101)\xa0(from Ch. 108 1/2)\r\nSec.​ 8-101.\xa0Creation of fund.   \r\n"
    assert normalise(raw) == "    (40 ILCS 5/8-101) (from Ch. 108 1/2)\nSec. 8-101. Creation of fund.\n"


def test_strip_markdown_uncovers_parser_anchors() -> None:
    md = "## (40 ILCS 5/8-101)\n**Sec. 8-101.** Creation of fund.\n\\(a\\) First.\n- (b) Second.\n- an ordinary bullet"
    assert strip_markdown(md).split("\n") == [
        "(40 ILCS 5/8-101)",
        "Sec. 8-101. Creation of fund.",
        "(a) First.",
        "(b) Second.",
        "- an ordinary bullet",
    ]


def test_page_furniture_is_removed_and_a_subsection_after_a_page_break_is_kept() -> None:
    pages = [
        ["Illinois Compiled Statutes", "(40 ILCS 5/8-101)", "Sec. 8-101. Creation of fund.", "Page 1 of 3"],
        ["Illinois Compiled Statutes", "(a) A subsection that starts a page.", "more text here", "Page 2 of 3"],
        ["Illinois Compiled Statutes", "(b) Another subsection.", "(Source: Laws 1963, p. 161.)", "Page 3 of 3"],
    ]
    assert remove_page_furniture(pages) == [
        "(40 ILCS 5/8-101)",
        "Sec. 8-101. Creation of fund.",
        "(a) A subsection that starts a page.",
        "more text here",
        "(b) Another subsection.",
        "(Source: Laws 1963, p. 161.)",
    ]


def test_furniture_detection_needs_three_pages() -> None:
    # With two pages a repeated line is as likely to be text as a header.
    pages = [["Same line", "text a"], ["Same line", "text b"]]
    assert remove_page_furniture(pages) == ["Same line", "text a", "Same line", "text b"]


def test_broken_lines_are_joined_only_when_unambiguous() -> None:
    text = "\n".join([
        "Sec. 8-101. Creation of fund. The fund shall be admin-",
        "istered by the board for the benefit of",
        "employees of the city.",
        "(a) Never joined onto the line above, even though it follows a sentence",
        "(40 ILCS 5/8-102) never joined either",
        "Ends with a full stop.",
        "lower case after a full stop stays on its own line",
    ])
    assert join_broken_lines(text).split("\n") == [
        "Sec. 8-101. Creation of fund. The fund shall be administered by the board for the benefit of employees of the city.",
        "(a) Never joined onto the line above, even though it follows a sentence",
        "(40 ILCS 5/8-102) never joined either",
        "Ends with a full stop.",
        "lower case after a full stop stays on its own line",
    ]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingestion_normalise.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.ingestion.normalise'`.

- [ ] **Step 3: Implement**

Create `src/pension_chat/ingestion/normalise.py`:

```python
"""Text cleanup shared by every format's converter.

The parser anchors on the start of a line — `(40 ILCS 5/`, `Sec. `, `(a) ` — so
everything here exists to put those anchors back at the start of their lines,
and to avoid ever moving one off it.
"""

from __future__ import annotations

import re
from collections import Counter

#: A line the parser treats as structure. Nothing is ever joined onto the line
#: before one of these: merging a header into the previous line is exactly how a
#: section gets absorbed into its neighbour.
ANCHOR_RE = re.compile(
    r"^\s*(\(40 ILCS 5/|Sec\. |\([a-z](?:-\d+)?\)\s|\(\d+\)\s|\(Source:|\(Text of Section|WITH |WITHOUT )"
)

_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_PAGE_NUMBER_RE = re.compile(r"^(page\s+)?\d+(\s+of\s+\d+)?$", re.IGNORECASE)


def normalise(text: str) -> str:
    """Line endings to LF, invisible characters removed, NBSP to space, trailing space trimmed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE).replace("\xa0", " ")
    return "\n".join(line.rstrip() for line in text.split("\n"))


def strip_markdown(text: str) -> str:
    """Remove the markup that would hide a parser anchor; leave everything else."""
    out: list[str] = []
    for line in text.split("\n"):
        s = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        s = s.replace("**", "").replace("__", "")
        s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>])", r"\1", s)
        bullet = re.match(r"^\s*[-*+]\s+(.*)$", s)
        if bullet and ANCHOR_RE.match(bullet.group(1)):
            s = bullet.group(1)
        out.append(s)
    return "\n".join(out)


def remove_page_furniture(pages: list[list[str]]) -> list[str]:
    """Drop running headers, footers and page numbers from PDF pages.

    A line counts as furniture when it sits in the first or last two non-blank
    lines of a page and either looks like a page number or recurs at a page edge
    on more than half the pages. Fewer than three pages cannot establish
    recurrence, so only page numbers are removed then.
    """
    repeated: set[str] = set()
    if len(pages) >= 3:
        edges: Counter[str] = Counter()
        for page in pages:
            nonblank = [line.strip() for line in page if line.strip()]
            edges.update(set(nonblank[:2] + nonblank[-2:]))
        repeated = {line for line, count in edges.items() if count * 2 > len(pages)}

    out: list[str] = []
    for page in pages:
        nonblank_idx = [i for i, line in enumerate(page) if line.strip()]
        edge_idx = set(nonblank_idx[:2] + nonblank_idx[-2:])
        for i, line in enumerate(page):
            s = line.strip()
            if i in edge_idx and (s in repeated or _PAGE_NUMBER_RE.match(s)):
                continue
            out.append(line)
    return out


def join_broken_lines(text: str) -> str:
    """Rejoin lines a PDF wrapped mid-sentence — only where the join is unambiguous.

    Joined: a line ending in a hyphenated word fragment followed by a lower-case
    continuation (hyphen removed), or a line without terminal punctuation
    followed by a lower-case continuation. Never joined: a line matching
    `ANCHOR_RE`, or anything after terminal punctuation.
    """
    out: list[str] = []
    for line in text.split("\n"):
        if out and out[-1].strip() and line.strip() and not ANCHOR_RE.match(line):
            prev = out[-1].rstrip()
            nxt = line.lstrip()
            if nxt[:1].islower():
                if re.search(r"[A-Za-z]-$", prev):
                    out[-1] = prev[:-1] + nxt
                    continue
                if not re.search(r"[.;:)\"']$", prev):
                    out[-1] = prev + " " + nxt
                    continue
        out.append(line)
    return "\n".join(out)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_ingestion_normalise.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/ingestion/normalise.py tests/test_ingestion_normalise.py
git commit -m "Ingestion: one normaliser every format passes through" -m "Puts parser anchors back at the start of their lines and never moves one off it: NBSP and invisible characters, markdown markup, PDF page furniture, and joining only unambiguous wrapped lines." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Sources and converters

**Files:**
- Create: `src/pension_chat/ingestion/sources.py`
- Create: `src/pension_chat/ingestion/converters.py`
- Modify: `src/pension_chat/config.py` (Azure AI Search block, after `azure_search_index_name`)
- Create: `tests/_pdf.py`
- Test: `tests/test_ingestion_sources.py`, `tests/test_ingestion_converters.py`

**Interfaces:**
- Consumes: `normalise`, `strip_markdown`, `remove_page_furniture`, `join_broken_lines` (Task 2).
- Produces:
  - `SourceFile(name: str, data: bytes, version: str)` (frozen dataclass)
  - `class Source(Protocol): def get(self, name: str) -> SourceFile | None`
  - `LocalDirSource(path: str | Path)`, `BlobSource(container_client)`, `BlobSource.from_settings() -> BlobSource`
  - `IngestionError(Exception)`; `UnsupportedFormat(IngestionError)`; `ConversionFailed(IngestionError)`; `NoTextLayer(ConversionFailed)`
  - `to_text(file: SourceFile) -> str`

- [ ] **Step 1: Write the failing source tests**

Create `tests/test_ingestion_sources.py`:

```python
"""Sources read files; they never write."""

from __future__ import annotations

from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError

from pension_chat.ingestion.sources import BlobSource, LocalDirSource, SourceFile


def test_local_source_reads_a_file_and_reports_missing(tmp_path: Path) -> None:
    (tmp_path / "article_8.txt").write_bytes(b"hello")
    src = LocalDirSource(tmp_path)

    got = src.get("article_8.txt")
    assert isinstance(got, SourceFile)
    assert (got.name, got.data) == ("article_8.txt", b"hello")
    assert got.version  # mtime, non-empty
    assert src.get("absent.txt") is None


class _Props:
    etag = '"0x8DC"'


class _Downloader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.properties = _Props()

    def readall(self) -> bytes:
        return self._data


class _BlobClient:
    def __init__(self, blobs: dict[str, bytes], name: str) -> None:
        self._blobs, self._name = blobs, name

    def download_blob(self) -> _Downloader:
        if self._name not in self._blobs:
            raise ResourceNotFoundError("no such blob")
        return _Downloader(self._blobs[self._name])


class _Container:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs

    def get_blob_client(self, name: str) -> _BlobClient:
        return _BlobClient(self._blobs, name)


def test_blob_source_downloads_by_name_with_its_etag() -> None:
    src = BlobSource(_Container({"Illinois Pension Law MEABF.docx": b"docx-bytes"}))

    got = src.get("Illinois Pension Law MEABF.docx")
    assert got == SourceFile("Illinois Pension Law MEABF.docx", b"docx-bytes", '"0x8DC"')
    assert src.get("missing.docx") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingestion_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.ingestion.sources'`.

- [ ] **Step 3: Implement sources and settings**

In `src/pension_chat/config.py`, directly after `azure_search_index_name: str = "pension-code-dev"`:

```python
    # ── Azure Blob (statute documents supplied by the client) ─────
    # Optional: only ingestion with `--source blob` reads them.
    azure_storage_connection_string: str | None = None
    azure_storage_container: str | None = None
```

Create `src/pension_chat/ingestion/sources.py`:

```python
"""Where statute documents come from. Sources read; they never write."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from azure.core.exceptions import ResourceNotFoundError


@dataclass(frozen=True)
class SourceFile:
    name: str       # file name within the source, e.g. "article_8.txt"
    data: bytes
    version: str    # Blob ETag, or file mtime for local files — for provenance logs


class Source(Protocol):
    def get(self, name: str) -> SourceFile | None:
        """The named file, or None when the source does not have it."""
        ...


class LocalDirSource:
    """Files in one local directory (development, tests, and the ILGA texts)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def get(self, name: str) -> SourceFile | None:
        path = self._path / name
        if not path.is_file():
            return None
        return SourceFile(name, path.read_bytes(), str(path.stat().st_mtime_ns))


class BlobSource:
    """Files in one Azure Blob container. Replace the connection string with a
    managed identity once the cluster exists; only `from_settings` changes."""

    def __init__(self, container_client: Any) -> None:
        self._container = container_client

    @classmethod
    def from_settings(cls) -> BlobSource:
        from azure.storage.blob import ContainerClient

        from pension_chat.config import get_settings

        settings = get_settings()
        if not settings.azure_storage_connection_string or not settings.azure_storage_container:
            raise RuntimeError(
                "--source blob needs AZURE_STORAGE_CONNECTION_STRING and AZURE_STORAGE_CONTAINER in .env"
            )
        return cls(
            ContainerClient.from_connection_string(
                settings.azure_storage_connection_string, settings.azure_storage_container
            )
        )

    def get(self, name: str) -> SourceFile | None:
        try:
            downloader = self._container.get_blob_client(name).download_blob()
        except ResourceNotFoundError:
            return None
        return SourceFile(name, downloader.readall(), str(downloader.properties.etag))
```

- [ ] **Step 4: Run source tests**

Run: `uv run pytest tests/test_ingestion_sources.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Write the PDF test helper**

Create `tests/_pdf.py`:

```python
"""Build tiny real PDFs in tests, so no binary fixture is ever committed."""

from __future__ import annotations


def make_pdf(pages: list[list[str]]) -> bytes:
    """One Helvetica text line per string, top to bottom, one list per page.
    An empty list makes a page with no text layer."""
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [%s] /Count %d >>" % (
            " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages))), len(pages))).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, lines in enumerate(pages):
        objs.append((
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
        ).encode())
        ops, y = [], 740
        for line in lines:
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 9 Tf 40 {y} Td ({esc}) Tj ET")
            y -= 14
        stream = "\n".join(ops).encode("latin-1")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
```

- [ ] **Step 6: Write the failing converter tests**

Create `tests/test_ingestion_converters.py`:

```python
"""Each converter's only job: produce text that looks like ILGA's .txt."""

from __future__ import annotations

import io

import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from pension_chat.ingestion.converters import (
    ConversionFailed,
    NoTextLayer,
    UnsupportedFormat,
    to_text,
)
from pension_chat.ingestion.sources import SourceFile
from tests._pdf import make_pdf


def _file(name: str, data: bytes) -> SourceFile:
    return SourceFile(name, data, "v1")


def _docx(build) -> bytes:  # type: ignore[no-untyped-def]
    doc = Document()
    build(doc)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _nonblank(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


def test_txt_with_bom_and_crlf() -> None:
    data = "﻿(40 ILCS 5/8-101)\r\nSec. 8-101. Creation of fund.\r\n".encode("utf-8")
    assert to_text(_file("article_8.txt", data)) == "(40 ILCS 5/8-101)\nSec. 8-101. Creation of fund.\n"


def test_extension_is_case_insensitive() -> None:
    data = b"(40 ILCS 5/8-101)\nSec. 8-101. Creation of fund.\n"
    assert to_text(_file("ARTICLE_8.TXT", data)) == to_text(_file("article_8.txt", data))


def test_md_is_stripped_of_markup() -> None:
    data = b"## (40 ILCS 5/8-101)\n**Sec. 8-101.** Creation of fund.\n"
    assert _nonblank(to_text(_file("a.md", data))) == ["(40 ILCS 5/8-101)", "Sec. 8-101. Creation of fund."]


def test_unknown_extension_is_refused_by_name() -> None:
    with pytest.raises(UnsupportedFormat, match="scan.tiff"):
        to_text(_file("scan.tiff", b"..."))


def test_docx_text_in_nested_tables_is_extracted_in_order() -> None:
    def build(doc):  # type: ignore[no-untyped-def]
        outer = doc.add_table(rows=1, cols=1)
        cell = outer.cell(0, 0)
        cell.paragraphs[0].text = "\xa0\xa0(40 ILCS 5/8-101)\nSec. 8-101. Creation of fund."
        inner = cell.add_table(rows=1, cols=1)
        inner.cell(0, 0).paragraphs[0].text = "(a) Text held in a table inside a table."
        cell.add_paragraph("(Source: Laws 1963, p. 161.)")

    assert _nonblank(to_text(_file("art8.docx", _docx(build)))) == [
        "(40 ILCS 5/8-101)",
        "Sec. 8-101. Creation of fund.",
        "(a) Text held in a table inside a table.",
        "(Source: Laws 1963, p. 161.)",
    ]


def test_docx_paragraphs_and_tables_keep_document_order() -> None:
    def build(doc):  # type: ignore[no-untyped-def]
        doc.add_paragraph("(40 ILCS 5/Art. 8 heading)")
        doc.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0].text = "(40 ILCS 5/8-101)"
        doc.add_paragraph("Sec. 8-101. Creation of fund.")

    assert _nonblank(to_text(_file("a.docx", _docx(build)))) == [
        "(40 ILCS 5/Art. 8 heading)",
        "(40 ILCS 5/8-101)",
        "Sec. 8-101. Creation of fund.",
    ]


def test_docx_merged_cell_text_appears_once() -> None:
    def build(doc):  # type: ignore[no-untyped-def]
        table = doc.add_table(rows=1, cols=2)
        merged = table.cell(0, 0).merge(table.cell(0, 1))
        merged.paragraphs[0].text = "(40 ILCS 5/8-101)"

    assert _nonblank(to_text(_file("a.docx", _docx(build)))) == ["(40 ILCS 5/8-101)"]


def test_docx_text_the_walker_cannot_reach_fails_the_file() -> None:
    # A content control at body level holds text the walker does not visit. The
    # completeness check must notice rather than silently drop it — the same
    # failure as nested tables, which cost ~3,300 words of Article 8.
    def build(doc):  # type: ignore[no-untyped-def]
        doc.add_paragraph("(40 ILCS 5/8-101)")
        sdt = parse_xml(
            f'<w:sdt {nsdecls("w")}><w:sdtContent><w:p><w:r>'
            "<w:t>Text inside a content control.</w:t></w:r></w:p></w:sdtContent></w:sdt>"
        )
        doc.element.body.insert(0, sdt)

    with pytest.raises(ConversionFailed, match="characters"):
        to_text(_file("a.docx", _docx(build)))


LONG = "and further text so that the page carries a real text layer of some length"


def test_pdf_lines_come_back_joined_and_without_furniture() -> None:
    pdf = make_pdf([
        ["Illinois Compiled Statutes", "(40 ILCS 5/8-101)", "Sec. 8-101. Creation of fund. The fund shall be admin-", "Page 1 of 3"],
        ["Illinois Compiled Statutes", "istered by the board " + LONG, "(Source: Laws 1963, p. 161.)", "Page 2 of 3"],
        ["Illinois Compiled Statutes", "(40 ILCS 5/8-102)", "Sec. 8-102. Terms defined. " + LONG, "Page 3 of 3"],
    ])
    assert _nonblank(to_text(_file("a.pdf", pdf))) == [
        "(40 ILCS 5/8-101)",
        "Sec. 8-101. Creation of fund. The fund shall be administered by the board " + LONG,
        "(Source: Laws 1963, p. 161.)",
        "(40 ILCS 5/8-102)",
        "Sec. 8-102. Terms defined. " + LONG,
    ]


def test_pdf_without_a_text_layer_is_refused() -> None:
    with pytest.raises(NoTextLayer, match="scan.pdf"):
        to_text(_file("scan.pdf", make_pdf([[], [], ["(40 ILCS 5/8-101)"]])))
```

- [ ] **Step 7: Run to verify failure**

Run: `uv run pytest tests/test_ingestion_converters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.ingestion.converters'`.

- [ ] **Step 8: Implement the converters**

Create `src/pension_chat/ingestion/converters.py`:

```python
"""Bytes in, ILGA-shaped plain text out — one function per file format.

Everything the parser needs is on the lines, so each converter's only job is to
emit one line per paragraph, in document order, and lose nothing. Losing text is
the failure that matters: the Word document the client supplied keeps Article 8
in 212 tables, 74 of them nested, and reading cells without recursing dropped
~3,300 words while every section header survived. So `.docx` carries a
completeness check, and `.pdf` refuses files with no text layer.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any

from pension_chat.ingestion.normalise import (
    join_broken_lines,
    normalise,
    remove_page_furniture,
    strip_markdown,
)
from pension_chat.ingestion.sources import SourceFile

logger = logging.getLogger("pension_chat.ingestion.converters")


class IngestionError(Exception):
    """A file or Article that must be refused, with a reason a person can act on."""


class UnsupportedFormat(IngestionError):
    pass


class ConversionFailed(IngestionError):
    pass


class NoTextLayer(ConversionFailed):
    pass


def to_text(file: SourceFile) -> str:
    """Convert one file to normalised ILGA-shaped text."""
    ext = Path(file.name).suffix.lower()
    if ext == ".txt":
        raw = _decode(file)
    elif ext == ".md":
        raw = strip_markdown(_decode(file))
    elif ext == ".docx":
        raw = _docx_text(file)
    elif ext == ".pdf":
        raw = _pdf_text(file)
    else:
        raise UnsupportedFormat(f"{file.name}: no converter for '{ext or '(no extension)'}'")
    return normalise(raw)


def _decode(file: SourceFile) -> str:
    try:
        return file.data.decode("utf-8-sig")
    except UnicodeDecodeError:
        logger.warning("%s is not UTF-8; decoding as cp1252", file.name)
        return file.data.decode("cp1252")


# ── .docx ────────────────────────────────────────────────────────


def _docx_text(file: SourceFile) -> str:
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(io.BytesIO(file.data))
    lines: list[str] = []
    _walk(doc.element.body, doc, lines)
    text = "\n".join(lines)

    source_chars = sum(len(re.sub(r"\s", "", t.text or "")) for t in doc.element.body.iter(qn("w:t")))
    output_chars = len(re.sub(r"\s", "", text))
    if source_chars != output_chars:
        raise ConversionFailed(
            f"{file.name}: the document holds {source_chars} characters of text but "
            f"{output_chars} were extracted — some text sits where the converter does not read"
        )
    return text


def _walk(container: Any, parent: Any, lines: list[str]) -> None:
    """Paragraphs and tables in document order, recursing into nested tables."""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in container.iterchildren():
        if child.tag == qn("w:p"):
            lines.extend(Paragraph(child, parent).text.split("\n"))
        elif child.tag == qn("w:tbl"):
            for row in Table(child, parent).rows:
                previous = None
                for cell in row.cells:
                    # A horizontally merged cell is returned once per grid column.
                    if cell._tc is previous:
                        continue
                    previous = cell._tc
                    _walk(cell._tc, cell, lines)


# ── .pdf ─────────────────────────────────────────────────────────


def _pdf_text(file: SourceFile) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(file.data)) as pdf:
        pages = [(page.extract_text() or "").split("\n") for page in pdf.pages]

    thin = sum(1 for page in pages if len("".join(page).strip()) < 50)
    if not pages or thin * 2 > len(pages):
        raise NoTextLayer(
            f"{file.name}: {thin} of {len(pages)} pages have no extractable text — "
            "it looks scanned and needs OCR, which this pipeline does not do"
        )
    return join_broken_lines("\n".join(remove_page_furniture(pages)))
```

- [ ] **Step 9: Run to verify pass**

Run: `uv run pytest tests/test_ingestion_converters.py tests/test_ingestion_sources.py -v`
Expected: all PASS. If `test_pdf_lines_come_back_joined_and_without_furniture` fails only on spacing inside a line, print `to_text(...)` for that PDF and adjust the **test's** expected strings to pdfplumber's exact spacing — never loosen `join_broken_lines` to pass.

- [ ] **Step 10: Commit**

```bash
git add src/pension_chat/config.py src/pension_chat/ingestion/sources.py src/pension_chat/ingestion/converters.py tests/_pdf.py tests/test_ingestion_sources.py tests/test_ingestion_converters.py
git commit -m "Ingestion: sources and a converter per format" -m "Local and Blob sources that only read. .docx walks paragraphs and tables in document order, recursing into nested tables, and refuses a file whose extracted text is shorter than its text nodes; .pdf refuses a missing text layer." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The splitter

**Files:**
- Create: `src/pension_chat/ingestion/splitter.py`
- Test: `tests/test_ingestion_splitter.py`

**Interfaces:**
- Consumes: `IngestionError` (Task 3).
- Produces:
  - `UnsupportedAct(IngestionError)`, `UnsupportedArticle(IngestionError)`
  - `split_by_article(text: str) -> dict[int, str]` — `{}` when the text has no Pension Code headers.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingestion_splitter.py`:

```python
"""A file may hold one Article, several, or the whole Code: split by the Code's own numbers."""

from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.ingestion.splitter import UnsupportedAct, UnsupportedArticle, split_by_article

STATUTE_DIR = Path(__file__).resolve().parents[1] / "data" / "statutes"


def _join(*lines: str) -> str:
    return "\n".join(lines)


def test_one_article_comes_back_whole() -> None:
    text = _join("(40 ILCS 5/Art. 8 heading)", "(40 ILCS 5/8-101)", "Sec. 8-101. Creation of fund.")
    assert split_by_article(text) == {8: text}


def test_two_articles_are_cut_at_the_heading() -> None:
    text = _join(
        "(40 ILCS 5/Art. 1 heading)", "(40 ILCS 5/1-101)", "Sec. 1-101. Short title.",
        "(40 ILCS 5/Art. 8 heading)", "(40 ILCS 5/8-101)", "Sec. 8-101. Creation of fund.",
    )
    parts = split_by_article(text)
    assert set(parts) == {1, 8}
    assert parts[8].startswith("(40 ILCS 5/Art. 8 heading)")
    assert "1-101" not in parts[8]


def test_a_cut_without_a_heading_happens_at_the_first_header_of_the_next_article() -> None:
    text = _join("(40 ILCS 5/8-101)", "Sec. 8-101. A.", "(40 ILCS 5/20-101)", "Sec. 20-101. B.")
    assert split_by_article(text) == {
        8: _join("(40 ILCS 5/8-101)", "Sec. 8-101. A."),
        20: _join("(40 ILCS 5/20-101)", "Sec. 20-101. B."),
    }


def test_non_contiguous_blocks_of_one_article_are_concatenated() -> None:
    text = _join("(40 ILCS 5/8-101)", "Sec. 8-101. A.", "(40 ILCS 5/20-101)", "Sec. 20-101. B.",
                 "(40 ILCS 5/8-102)", "Sec. 8-102. C.")
    assert split_by_article(text)[8] == _join("(40 ILCS 5/8-101)", "Sec. 8-101. A.",
                                              "(40 ILCS 5/8-102)", "Sec. 8-102. C.")


def test_a_division_heading_stays_with_its_article() -> None:
    text = _join("(40 ILCS 5/Art. 9, div. 2 heading)", "(40 ILCS 5/9-201)", "Sec. 9-201. A.")
    assert split_by_article(text) == {9: text}


def test_another_act_is_refused_by_name() -> None:
    with pytest.raises(UnsupportedAct, match="5 ILCS 375"):
        split_by_article(_join("(5 ILCS 375/6)", "Sec. 6. Something else."))


def test_a_letter_suffixed_article_is_refused() -> None:
    with pytest.raises(UnsupportedArticle, match="22A"):
        split_by_article(_join("(40 ILCS 5/22A-101)", "Sec. 22A-101. Short title."))


def test_text_without_headers_splits_to_nothing() -> None:
    assert split_by_article("Just a cover page.\nNothing else.") == {}


_INTEGER_ARTICLES = sorted(
    (p, int(p.stem.split("_")[1]))
    for p in STATUTE_DIR.glob("article_*.txt")
    if p.stem.split("_")[1].isdigit()
)


@pytest.mark.skipif(not _INTEGER_ARTICLES, reason="ILGA corpus not on disk (data/ is never committed)")
@pytest.mark.parametrize(("path", "article"), _INTEGER_ARTICLES, ids=lambda v: str(v))
def test_every_ilga_file_splits_into_exactly_its_own_article(path: Path, article: int) -> None:
    assert set(split_by_article(path.read_text(encoding="utf-8-sig"))) == {article}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingestion_splitter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.ingestion.splitter'`.

- [ ] **Step 3: Implement**

Create `src/pension_chat/ingestion/splitter.py`:

```python
"""Group converted text by Pension Code Article, using the Code's own numbers.

A cut happens at an Article or division heading, or at a section header whose
Article differs from the current one. Lines before the first cut belong to the
Article after it. Only 40 ILCS 5 is accepted: another act is a different corpus
with no Article numbering for `pension_articles` or the index to hold.
"""

from __future__ import annotations

import re

from pension_chat.ingestion.converters import IngestionError

_ART_HEADING_RE = re.compile(r"^\(40 ILCS 5/Art\.\s+(\d+[A-Z]?)\b")
_SECTION_RE = re.compile(r"^\(40 ILCS 5/(\d+[A-Z]?)-")
_ANY_ACT_RE = re.compile(r"^\((\d+)\s+ILCS\s+(\d+)/")


class UnsupportedAct(IngestionError):
    pass


class UnsupportedArticle(IngestionError):
    pass


def split_by_article(text: str) -> dict[int, str]:
    blocks: dict[int, list[str]] = {}
    pending: list[str] = []
    current: int | None = None

    for line in text.split("\n"):
        s = line.strip()
        act = _ANY_ACT_RE.match(s)
        if act and (act.group(1), act.group(2)) != ("40", "5"):
            raise UnsupportedAct(
                f"{act.group(1)} ILCS {act.group(2)} is not the Pension Code (40 ILCS 5); "
                "only the Pension Code can be ingested"
            )
        marker = _ART_HEADING_RE.match(s) or _SECTION_RE.match(s)
        if marker:
            article = _article_number(marker.group(1))
            if current is None:
                blocks.setdefault(article, []).extend(pending)
                pending = []
            current = article
            blocks.setdefault(current, []).append(line)
        elif current is None:
            pending.append(line)
        else:
            blocks[current].append(line)

    return {article: "\n".join(lines) for article, lines in blocks.items()}


def _article_number(raw: str) -> int:
    if not raw.isdigit():
        raise UnsupportedArticle(
            f"Article {raw}: letter-suffixed Articles cannot be recorded "
            "(pension_articles.article_number is an integer)"
        )
    return int(raw)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_ingestion_splitter.py -v`
Expected: all PASS, including the corpus test for every integer Article when `data/statutes/` is present. If a corpus file raises `UnsupportedAct`, open it at the reported line: a legitimate line-start reference to another act means `_ANY_ACT_RE` must only consider lines that *also* look like headers (followed by `)`); fix the pattern, add that line as a test case, re-run.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/ingestion/splitter.py tests/test_ingestion_splitter.py
git commit -m "Ingestion: split text by Article from the Code's own numbers" -m "One file may hold one Article, several or the whole Code. Refuses other acts and letter-suffixed Articles by name." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: File checks and record-id uniqueness

**Files:**
- Create: `src/pension_chat/ingestion/checks.py`
- Test: `tests/test_ingestion_checks.py`

**Interfaces:**
- Consumes: `IngestionError` (Task 3), `IndexRecord` (existing, `chunker.py`).
- Produces:
  - `FileRejected(IngestionError)`
  - `headers_not_at_line_start(text: str) -> list[str]`
  - `header_title_mismatches(text: str) -> list[str]`
  - `check_file_text(name: str, text: str) -> None` — raises `FileRejected` listing up to five problems.
  - `duplicate_record_ids(records: list[IndexRecord]) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingestion_checks.py`:

```python
"""A conversion that damaged structure is refused, never repaired by guessing."""

from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.ingestion.checks import (
    FileRejected,
    check_file_text,
    duplicate_record_ids,
    header_title_mismatches,
    headers_not_at_line_start,
)
from pension_chat.ingestion.chunker import IndexRecord

STATUTE_DIR = Path(__file__).resolve().parents[1] / "data" / "statutes"


def test_a_header_lost_mid_line_is_reported() -> None:
    text = "Sec. 8-101. Creation of fund. (40 ILCS 5/8-102) Sec. 8-102. Terms."
    assert len(headers_not_at_line_start(text)) == 1


def test_an_article_heading_mid_line_is_not_a_section_header() -> None:
    assert headers_not_at_line_start("See (40 ILCS 5/Art. 8 heading) above.") == []


def test_header_followed_by_its_own_title_passes() -> None:
    text = "(40 ILCS 5/8-230.1)\n\n(Text of Section before amendment by P.A. 104-457)\nSec. 8-230.1. Right to contribute."
    assert header_title_mismatches(text) == []


def test_header_followed_by_another_sections_title_fails() -> None:
    text = "(40 ILCS 5/8-101)\nSec. 8-102. Terms defined."
    assert header_title_mismatches(text) == [
        "§8-101: header not followed by 'Sec. 8-101.' (next line: 'Sec. 8-102. Terms defined.')"
    ]


def test_header_at_the_very_end_fails() -> None:
    assert header_title_mismatches("(40 ILCS 5/8-101)") == ["§8-101: header with no title after it"]


def test_check_file_text_names_the_file_and_the_problems() -> None:
    with pytest.raises(FileRejected, match=r"art8\.pdf.*8-101"):
        check_file_text("art8.pdf", "(40 ILCS 5/8-101)\nSec. 8-102. Wrong.")


def test_duplicate_record_ids_are_listed() -> None:
    records = [IndexRecord(id="8-101_a", content="x"), IndexRecord(id="8-101_a", content="y"),
               IndexRecord(id="8-101_b", content="z")]
    assert duplicate_record_ids(records) == ["8-101_a"]


_CORPUS = sorted(STATUTE_DIR.glob("article_*.txt"))


@pytest.mark.skipif(not _CORPUS, reason="ILGA corpus not on disk (data/ is never committed)")
@pytest.mark.parametrize("path", _CORPUS, ids=lambda p: p.name)
def test_every_ilga_file_passes_the_file_checks(path: Path) -> None:
    # The baseline the spec requires before checks 3 and 4 go live: a correct
    # file must never trip them.
    text = path.read_text(encoding="utf-8-sig")
    assert headers_not_at_line_start(text) == []
    assert header_title_mismatches(text) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingestion_checks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.ingestion.checks'`.

- [ ] **Step 3: Implement**

Create `src/pension_chat/ingestion/checks.py`:

```python
"""Checks that turn a structurally damaged conversion into a refusal.

Uploading merges by key, so a lost section is never an error on its own — the
index is just quietly a section short. These checks make the damage loud.
"""

from __future__ import annotations

import re
from collections import Counter

from pension_chat.ingestion.chunker import IndexRecord
from pension_chat.ingestion.converters import IngestionError

_HEADER_ANY_RE = re.compile(r"\(40 ILCS 5/\d+[A-Z]?-\d")
_HEADER_START_RE = re.compile(r"^\(40 ILCS 5/(\d+[A-Z]?-\d+(?:\.\d+)?[a-z]?)\)")
_TITLE_RE = re.compile(r"^Sec\.\s+(\d+[A-Z]?-\d+(?:\.\d+)?[a-z]?)\.")
#: Lines ILGA legitimately prints between a header and its "Sec." title.
_BETWEEN_HEADER_AND_TITLE_RE = re.compile(
    r"^(\(Text of Section .*\)|WITH(OUT)? the changes made by .*)$", re.IGNORECASE
)


class FileRejected(IngestionError):
    pass


def headers_not_at_line_start(text: str) -> list[str]:
    """A section header anywhere but the start of a line means a lost line break."""
    problems = []
    for number, line in enumerate(text.split("\n"), start=1):
        for match in _HEADER_ANY_RE.finditer(line):
            if line[: match.start()].strip():
                problems.append(f"line {number}: section header mid-line: {line.strip()[:80]!r}")
    return problems


def header_title_mismatches(text: str) -> list[str]:
    """Each header must be followed by its own 'Sec. N-M.' — otherwise text is out of order."""
    lines = text.split("\n")
    problems = []
    for i, line in enumerate(lines):
        header = _HEADER_START_RE.match(line.strip())
        if not header:
            continue
        section_id = header.group(1)
        for following in lines[i + 1:]:
            s = following.strip()
            if not s or _BETWEEN_HEADER_AND_TITLE_RE.match(s):
                continue
            title = _TITLE_RE.match(s)
            if not title or title.group(1) != section_id:
                problems.append(
                    f"§{section_id}: header not followed by 'Sec. {section_id}.' (next line: {s[:60]!r})"
                )
            break
        else:
            problems.append(f"§{section_id}: header with no title after it")
    return problems


def check_file_text(name: str, text: str) -> None:
    problems = headers_not_at_line_start(text) + header_title_mismatches(text)
    if problems:
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        raise FileRejected(f"{name}: " + "; ".join(problems[:5]) + more)


def duplicate_record_ids(records: list[IndexRecord]) -> list[str]:
    counts = Counter(record.id for record in records)
    return sorted(record_id for record_id, count in counts.items() if count > 1)
```

- [ ] **Step 4: Run and measure the baseline**

Run: `uv run pytest tests/test_ingestion_checks.py -v`
Expected: the unit tests PASS. The corpus test is the **baseline measurement**: if any ILGA file fails, read each reported line in that file.
- A legitimate ILGA form between a header and its title (for example a `(Section scheduled to be repealed ...)` line): add it to `_BETWEEN_HEADER_AND_TITLE_RE`, and add a unit test with that exact line to `test_header_followed_by_its_own_title_passes`'s neighbours.
- A legitimate mid-line header (a genuine citation in the form `(40 ILCS 5/N-M)` inside body text): narrow `headers_not_at_line_start` to flag only occurrences followed by `)` and then whitespace and `(from Ch.` or end of line — the shape of a real header — and add that sentence as a passing unit test.
Re-run until the whole file passes. Record the final baseline (number of files, zero problems) in the commit message.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/ingestion/checks.py tests/test_ingestion_checks.py
git commit -m "Ingestion: refuse text whose structure the conversion damaged" -m "Headers lost mid-line and headers not followed by their own title are refused, never repaired. Baseline: every ILGA Article file passes both checks." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Listing what the index holds, failing closed

**Files:**
- Modify: `src/pension_chat/ingestion/indexer.py` (append after `prune_stale_records`)
- Test: `tests/test_ingestion_checks.py` (append)

**Interfaces:**
- Produces: `IndexListingFailed(Exception)`; `list_indexed_sections(article: str) -> set[str]` — section ids indexed for the Article; empty set when the index does not exist; raises `IndexListingFailed` on any other error.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingestion_checks.py`:

```python
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from pension_chat.ingestion import indexer


class _FakeSearch:
    def __init__(self, docs=None, error=None):  # type: ignore[no-untyped-def]
        self.docs, self.error, self.calls = docs or [], error, []

    def search(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return iter(self.docs)


def test_indexed_sections_are_listed_per_article(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeSearch(docs=[{"section": "9-101"}, {"section": "9-101"}, {"section": "9-102"}])
    monkeypatch.setattr(indexer, "get_search_client", lambda: fake)
    assert indexer.list_indexed_sections("9") == {"9-101", "9-102"}
    assert fake.calls[0]["filter"] == "article eq '9'"


def test_a_missing_index_counts_as_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(indexer, "get_search_client", lambda: _FakeSearch(error=ResourceNotFoundError("no index")))
    assert indexer.list_indexed_sections("8") == set()


def test_any_other_listing_error_fails_closed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(indexer, "get_search_client", lambda: _FakeSearch(error=HttpResponseError("503")))
    with pytest.raises(indexer.IndexListingFailed, match="Article 8"):
        indexer.list_indexed_sections("8")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingestion_checks.py -k indexed_sections -v`
Expected: FAIL — `AttributeError: module 'pension_chat.ingestion.indexer' has no attribute 'list_indexed_sections'`.

- [ ] **Step 3: Implement**

Append to `src/pension_chat/ingestion/indexer.py`:

```python
class IndexListingFailed(Exception):
    """The index could not be listed, so a shrink cannot be ruled out."""


def list_indexed_sections(article: str) -> set[str]:
    """Section ids currently indexed for one Article.

    Fails closed, unlike `prune_stale_records`: the shrink guard asks "would this
    run delete sections?", and "I could not look" must refuse the run rather than
    answer "no". A missing index is the one honest empty answer — a first
    ingestion cannot shrink anything.
    """
    try:
        results = get_search_client().search(
            search_text="*", filter=f"article eq '{article}'", select=["section"], top=100000
        )
        return {doc["section"] for doc in results}
    except ResourceNotFoundError:
        return set()
    except Exception as exc:
        raise IndexListingFailed(f"could not list Article {article}'s indexed sections: {exc}") from exc
```

Add to the imports at the top of `indexer.py`:

```python
from azure.core.exceptions import ResourceNotFoundError
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_ingestion_checks.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/ingestion/indexer.py tests/test_ingestion_checks.py
git commit -m "Ingestion: list an Article's indexed sections, failing closed" -m "The shrink guard needs to know what a run would delete; an index that cannot be listed refuses the run instead of reading as empty. A missing index is empty." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: One-hop reference selection

**Files:**
- Create: `src/pension_chat/ingestion/selection.py`
- Test: `tests/test_ingestion_selection.py`

**Interfaces:**
- Consumes: `ParsedSection` (existing), `parse_statute_file` (existing).
- Produces: `one_hop_references(sections: list[ParsedSection], own_articles: set[int]) -> dict[int, set[str]]` — Article number → section ids cited from `sections` that belong to Articles outside `own_articles`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingestion_selection.py`:

```python
"""The sections another Article's text cites are derived from that text, never typed by hand."""

from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.ingestion.parser import ParsedSection, parse_statute_file
from pension_chat.ingestion.selection import one_hop_references

ARTICLE_8 = Path(__file__).resolve().parents[1] / "data" / "statutes" / "article_8.txt"


def _section(section_id: str, refs: list[str]) -> ParsedSection:
    return ParsedSection(article_number=int(section_id.split("-")[0]), section_id=section_id,
                         section_title="t", full_text="t", referenced_sections=refs)


def test_references_outside_own_articles_are_grouped_by_article() -> None:
    sections = [
        _section("8-101", ["8-102", "1-160", "20-122"]),
        _section("8-102", ["1-160", "9-121.1", "1-113"]),
    ]
    assert one_hop_references(sections, {8}) == {
        1: {"1-160", "1-113"},
        20: {"20-122"},
        9: {"9-121.1"},
    }


def test_references_into_an_own_article_are_excluded() -> None:
    assert one_hop_references([_section("8-101", ["1-160"])], {8, 1}) == {}


@pytest.mark.skipif(not ARTICLE_8.exists(), reason="ILGA Article 8 not on disk (data/ is never committed)")
def test_article_8_cites_the_sections_the_scope_names() -> None:
    # The list in the scope document ("Statute corpus for the MEABF focus").
    refs = one_hop_references(parse_statute_file(str(ARTICLE_8), 8), {8})
    assert refs == {
        1: {"1-103.1", "1-109", "1-113", "1-160"},
        2: {"2-117.1"},
        3: {"3-101"},
        5: {"5-233.1", "5-234"},
        7: {"7-139"},
        9: {"9-121.1", "9-121.11", "9-128.1"},
        12: {"12-127.1"},
        14: {"14-108.2a", "14-108.2b"},
        18: {"18-112"},
        19: {"19-101"},
        20: {"20-122"},
        21: {"21-30"},
        34: {"34-60.1"},
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingestion_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.ingestion.selection'`.

- [ ] **Step 3: Implement**

Create `src/pension_chat/ingestion/selection.py`:

```python
"""Which sections of other Articles a corpus needs: the ones its own text cites."""

from __future__ import annotations

from pension_chat.ingestion.parser import ParsedSection


def one_hop_references(sections: list[ParsedSection], own_articles: set[int]) -> dict[int, set[str]]:
    refs: dict[int, set[str]] = {}
    for section in sections:
        for ref in section.referenced_sections:
            prefix = ref.split("-", 1)[0]
            if not prefix.isdigit() or int(prefix) in own_articles:
                continue
            refs.setdefault(int(prefix), set()).add(ref)
    return refs
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_ingestion_selection.py -v`
Expected: all PASS (the corpus test runs when `data/statutes/article_8.txt` exists).

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/ingestion/selection.py tests/test_ingestion_selection.py
git commit -m "Ingestion: derive the one-hop sections a corpus cites" -m "Article 8's citations of other Articles come from its own text: 20 sections across 13 Articles, one of them (34-60.1) outside the Pension Code." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The pipeline

**Files:**
- Create: `src/pension_chat/ingestion/pipeline.py`
- Test: `tests/test_ingestion_pipeline.py`

**Interfaces:**
- Consumes: `Source`, `SourceFile` (Task 3); `to_text`, `IngestionError` (Task 3); `split_by_article` (Task 4); `check_file_text`, `duplicate_record_ids` (Task 5); `IndexListingFailed` (Task 6); `one_hop_references` (Task 7); `parse_statute_text` (Task 1); `chunk_sections`, `IndexRecord` (existing).
- Produces:
  - `Corpus(name: str, primary: tuple[str, ...], whole_articles: tuple[int, ...], one_hop: bool)`; `CORPORA: dict[str, Corpus]` with `"meabf"` and `"ilga-all"`.
  - `Sinks(list_indexed_sections, embed, upload, prune, record_status)` — `record_status` is `async (int, str) -> None`; the rest are sync.
  - `ArticleResult`, `RunReport` (with `.failed: bool`).
  - `async run(corpus, primary, ilga, sinks, *, dry_run=False, allow_shrink=frozenset(), only_articles=None) -> RunReport`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingestion_pipeline.py`:

```python
"""The whole run, with Azure replaced by fakes."""

from __future__ import annotations

from dataclasses import dataclass, field

from pension_chat.ingestion.chunker import IndexRecord
from pension_chat.ingestion.indexer import IndexListingFailed
from pension_chat.ingestion.pipeline import Corpus, Sinks, run
from pension_chat.ingestion.sources import SourceFile


def _sec(sid: str, body: str = "Text of the section.") -> str:
    return f"(40 ILCS 5/{sid})\nSec. {sid}. Title of {sid}.\n{body}\n(Source: Laws 1963, p. 161.)"


CLIENT_ART8 = "\n".join([
    "(40 ILCS 5/Art. 8 heading)",
    _sec("8-101", "Governed also by Section 1-160 and Section 20-122."),
    _sec("8-102", "See Section 9-101, Section 9-999 and Section 34-60.1."),
])
ILGA = {
    "article_1.txt": "\n".join(["(40 ILCS 5/Art. 1 heading)", _sec("1-101"), _sec("1-160")]),
    "article_20.txt": "\n".join(["(40 ILCS 5/Art. 20 heading)", _sec("20-101"), _sec("20-122")]),
    "article_9.txt": "\n".join(["(40 ILCS 5/Art. 9 heading)", _sec("9-101"), _sec("9-102"), _sec("9-103")]),
    "article_8.txt": "\n".join(["(40 ILCS 5/Art. 8 heading)", _sec("8-101")]),  # must NOT be read
}
CORPUS = Corpus("test", primary=("client.txt",), whole_articles=(1, 20), one_hop=True)


class _Source:
    def __init__(self, files: dict[str, str]) -> None:
        self.files, self.read = files, []

    def get(self, name: str) -> SourceFile | None:
        self.read.append(name)
        return SourceFile(name, self.files[name].encode(), "v1") if name in self.files else None


@dataclass
class _Sinks:
    indexed: dict[str, set[str]] = field(default_factory=dict)
    listing_error: bool = False
    uploaded: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    status: list[tuple[int, str]] = field(default_factory=list)
    embedded: int = 0

    def list_indexed_sections(self, article: str) -> set[str]:
        if self.listing_error:
            raise IndexListingFailed(f"could not list Article {article}")
        return set(self.indexed.get(article, set()))

    def embed(self, records: list[IndexRecord]) -> list[IndexRecord]:
        self.embedded += len(records)
        return records

    def upload(self, records: list[IndexRecord]) -> int:
        self.uploaded.extend(r.id for r in records)
        return len(records)

    def prune(self, article: str, keep: set[str]) -> int:
        self.pruned.append(article)
        return 0

    async def record_status(self, article: int, status: str) -> None:
        self.status.append((article, status))

    def sinks(self) -> Sinks:
        return Sinks(self.list_indexed_sections, self.embed, self.upload, self.prune, self.record_status)


def _by_article(report):  # type: ignore[no-untyped-def]
    return {a.article: a for a in report.articles}


async def test_meabf_shaped_corpus_ingests_whole_and_one_hop_articles() -> None:
    fakes, ilga = _Sinks(), _Source(ILGA)
    report = await run(CORPUS, _Source({"client.txt": CLIENT_ART8}), ilga, fakes.sinks())

    got = _by_article(report)
    assert {n: (a.scope, a.outcome) for n, a in got.items()} == {
        1: ("whole", "ingested"), 8: ("whole", "ingested"), 20: ("whole", "ingested"), 9: ("sections", "ingested"),
    }
    assert got[9].sections == 1                       # only 9-101, the cited one
    assert "article_8.txt" not in ilga.read           # Article 8 comes from the client's document
    assert sorted(report.unavailable) == ["34-60.1", "9-999"]
    assert not report.failed
    # Partial Articles are not recorded in pension_articles.
    assert {n for n, _ in fakes.status} == {1, 8, 20}
    assert (8, "ingested") in fakes.status


async def test_dry_run_changes_nothing() -> None:
    fakes = _Sinks()
    report = await run(CORPUS, _Source({"client.txt": CLIENT_ART8}), _Source(ILGA), fakes.sinks(), dry_run=True)
    assert {a.outcome for a in report.articles} == {"dry-run"}
    assert (fakes.embedded, fakes.uploaded, fakes.pruned, fakes.status) == (0, [], [], [])
    assert _by_article(report)[8].added == ["8-101", "8-102"]


async def test_partial_article_over_a_whole_indexed_article_is_refused() -> None:
    fakes = _Sinks(indexed={"9": {"9-101", "9-102", "9-103"}})
    report = await run(CORPUS, _Source({"client.txt": CLIENT_ART8}), _Source(ILGA), fakes.sinks())
    nine = _by_article(report)[9]
    assert (nine.outcome, nine.missing) == ("refused", ["9-102", "9-103"])
    assert "--allow-shrink 9" in nine.reason
    assert "9" not in fakes.pruned and report.failed
    assert _by_article(report)[8].outcome == "ingested"     # one refusal does not stop the others


async def test_allow_shrink_lets_the_named_article_through() -> None:
    fakes = _Sinks(indexed={"9": {"9-101", "9-102", "9-103"}})
    report = await run(CORPUS, _Source({"client.txt": CLIENT_ART8}), _Source(ILGA), fakes.sinks(),
                       allow_shrink=frozenset({9}))
    assert _by_article(report)[9].outcome == "ingested" and "9" in fakes.pruned


async def test_an_index_that_cannot_be_listed_refuses_every_article() -> None:
    report = await run(CORPUS, _Source({"client.txt": CLIENT_ART8}), _Source(ILGA), _Sinks(listing_error=True).sinks())
    assert {a.outcome for a in report.articles} == {"refused"}


async def test_the_same_article_from_two_files_is_refused_naming_both() -> None:
    corpus = Corpus("dup", primary=("client.txt",), whole_articles=(8,), one_hop=False)
    report = await run(corpus, _Source({"client.txt": CLIENT_ART8}), _Source(ILGA), _Sinks().sinks())
    eight = _by_article(report)[8]
    assert eight.outcome == "refused"
    assert "client.txt" in eight.reason and "article_8.txt" in eight.reason


async def test_a_missing_primary_document_is_a_file_error() -> None:
    report = await run(CORPUS, _Source({}), _Source(ILGA), _Sinks().sinks())
    assert report.file_errors == [("client.txt", "not found in source")]
    assert report.failed


async def test_a_cited_section_missing_from_its_file_is_reported_unavailable() -> None:
    report = await run(CORPUS, _Source({"client.txt": CLIENT_ART8}), _Source(ILGA), _Sinks().sinks())
    assert "9-999" in report.unavailable and _by_article(report)[9].outcome == "ingested"


async def test_an_article_that_parses_to_nothing_is_refused_and_left_validating() -> None:
    fakes = _Sinks()
    corpus = Corpus("empty", primary=("client.txt",), whole_articles=(), one_hop=False)
    report = await run(corpus, _Source({"client.txt": "(40 ILCS 5/Art. 8 heading)"}), _Source(ILGA), fakes.sinks())
    assert _by_article(report)[8].outcome == "refused"
    assert fakes.status == [(8, "validating")]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingestion_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.ingestion.pipeline'`.

- [ ] **Step 3: Implement**

Create `src/pension_chat/ingestion/pipeline.py`:

```python
"""One ingestion run: sources → text → Articles → sections → checks → index.

Azure is reached only through `Sinks`, so the whole run is testable without it.
A refusal is per file or per Article: it names the reason, changes nothing in
the index, and never stops the rest of the run.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pension_chat.ingestion.checks import check_file_text, duplicate_record_ids
from pension_chat.ingestion.chunker import IndexRecord, chunk_sections
from pension_chat.ingestion.converters import IngestionError, to_text
from pension_chat.ingestion.indexer import IndexListingFailed
from pension_chat.ingestion.parser import ParsedSection, parse_statute_text
from pension_chat.ingestion.selection import one_hop_references
from pension_chat.ingestion.sources import Source, SourceFile
from pension_chat.ingestion.splitter import split_by_article

logger = logging.getLogger("pension_chat.ingestion.pipeline")


@dataclass(frozen=True)
class Corpus:
    name: str
    primary: tuple[str, ...]        # documents from the chosen source; every Article in them, whole
    whole_articles: tuple[int, ...]  # whole Articles from the ILGA texts
    one_hop: bool                    # plus the other Articles' sections the primary documents cite


CORPORA: dict[str, Corpus] = {
    # The scope's MEABF focus: the client's Article 8, Articles 1 and 20 whole
    # (§1-160 governs Tiers 2 and 3; Article 20 is reciprocal service), and the
    # sections of other funds' Articles that Article 8 cites.
    "meabf": Corpus("meabf", ("Illinois Pension Law MEABF.docx",), (1, 20), True),
    # Every integer Article from the ILGA texts — what ingestion did before.
    "ilga-all": Corpus("ilga-all", (), tuple(range(1, 25)), False),
}


@dataclass
class Sinks:
    list_indexed_sections: Callable[[str], set[str]]
    embed: Callable[[list[IndexRecord]], list[IndexRecord]]
    upload: Callable[[list[IndexRecord]], int]
    prune: Callable[[str, set[str]], int]
    record_status: Callable[[int, str], Awaitable[None]]


@dataclass
class ArticleResult:
    article: int
    scope: str                      # "whole" | "sections"
    sources: list[str]
    sections: int = 0
    records: int = 0
    added: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    outcome: str = ""               # "ingested" | "dry-run" | "refused"
    reason: str = ""


@dataclass
class RunReport:
    articles: list[ArticleResult] = field(default_factory=list)
    file_errors: list[tuple[str, str]] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.file_errors) or any(a.outcome == "refused" for a in self.articles)


def _label(file: SourceFile) -> str:
    return f"{file.name}@{file.version}"


def _article_texts(file: SourceFile) -> dict[int, str]:
    text = to_text(file)
    check_file_text(file.name, text)
    return split_by_article(text)


async def run(
    corpus: Corpus,
    primary: Source,
    ilga: Source,
    sinks: Sinks,
    *,
    dry_run: bool = False,
    allow_shrink: frozenset[int] = frozenset(),
    only_articles: set[int] | None = None,
) -> RunReport:
    report = RunReport()
    texts: dict[int, tuple[str, str]] = {}      # article -> (source label, text)
    scope: dict[int, str] = {}
    refused: dict[int, str] = {}

    def claim(article: int, label: str, text: str, how: str) -> None:
        if article in texts:
            refused[article] = f"Article {article} appears in both {texts[article][0]} and {label}; choosing one would be a guess"
            return
        texts[article], scope[article] = (label, text), how

    # 1. The primary documents: every Article in them, whole.
    for name in corpus.primary:
        file = primary.get(name)
        if file is None:
            report.file_errors.append((name, "not found in source"))
            continue
        try:
            per_article = _article_texts(file)
        except IngestionError as exc:
            report.file_errors.append((name, str(exc)))
            continue
        if not per_article:
            report.file_errors.append((name, "no Pension Code section headers found"))
        for article, text in per_article.items():
            claim(article, _label(file), text, "whole")
    primary_articles = set(texts)

    # 2. Whole Articles from the ILGA texts.
    def ilga_text(article: int) -> tuple[str, str] | None:
        file = ilga.get(f"article_{article}.txt")
        if file is None:
            return None
        per_article = _article_texts(file)
        if set(per_article) != {article}:
            raise IngestionError(f"{file.name} holds Articles {sorted(per_article)}, expected only {article}")
        return _label(file), per_article[article]

    for article in corpus.whole_articles:
        try:
            loaded = ilga_text(article)
        except IngestionError as exc:
            report.file_errors.append((f"article_{article}.txt", str(exc)))
            continue
        if loaded is None:
            refused[article] = f"no ILGA text for Article {article} (article_{article}.txt)"
            continue
        claim(article, *loaded, "whole")

    sections: dict[int, list[ParsedSection]] = {
        a: parse_statute_text(t, a, source=label) for a, (label, t) in texts.items() if a not in refused
    }

    # 3. One hop: the other Articles' sections the primary documents cite.
    if corpus.one_hop:
        cited = one_hop_references(
            [s for a in primary_articles for s in sections.get(a, [])], primary_articles
        )
        for article, wanted in sorted(cited.items()):
            if article in texts:
                continue            # already ingested whole
            try:
                loaded = ilga_text(article)
            except IngestionError as exc:
                report.file_errors.append((f"article_{article}.txt", str(exc)))
                continue
            if loaded is None:
                report.unavailable.extend(sorted(wanted))
                continue
            parsed = parse_statute_text(loaded[1], article, source=loaded[0])
            chosen = [s for s in parsed if s.section_id in wanted]
            report.unavailable.extend(sorted(wanted - {s.section_id for s in chosen}))
            if chosen:
                texts[article], scope[article], sections[article] = loaded, "sections", chosen

    # 4. Per Article: check, then write.
    for article in sorted(set(sections) | set(refused)):
        if only_articles and article not in only_articles:
            continue
        result = ArticleResult(article, scope.get(article, "whole"),
                               [texts[article][0]] if article in texts else [])
        report.articles.append(result)

        def refuse(reason: str) -> None:
            result.outcome, result.reason = "refused", reason
            logger.error("Article %d refused: %s", article, reason)

        if article in refused:
            refuse(refused[article])
            continue
        parsed = sections[article]
        result.sections = len({s.section_id for s in parsed})
        if not parsed:
            if result.scope == "whole" and not dry_run:
                await sinks.record_status(article, "validating")
            refuse("parsed to zero sections")
            continue

        records = chunk_sections(parsed)
        result.records = len(records)
        duplicates = duplicate_record_ids(records)
        if duplicates:
            refuse(f"duplicate record ids would overwrite each other: {', '.join(duplicates[:10])}")
            continue

        produced = {s.section_id for s in parsed}
        try:
            indexed = sinks.list_indexed_sections(str(article))
        except IndexListingFailed as exc:
            refuse(str(exc))
            continue
        result.added, result.missing = sorted(produced - indexed), sorted(indexed - produced)
        if result.missing and article not in allow_shrink:
            refuse(
                f"would remove {len(result.missing)} indexed section(s) "
                f"({', '.join(result.missing[:10])}); pass --allow-shrink {article} if intended"
            )
            continue

        logger.info("Article %d (%s) from %s: %d sections, %d records, +%d -%d",
                    article, result.scope, ", ".join(result.sources), result.sections,
                    result.records, len(result.added), len(result.missing))
        if dry_run:
            result.outcome = "dry-run"
            continue

        if result.scope == "whole":
            await sinks.record_status(article, "validating")
        uploaded = sinks.upload(sinks.embed(records))
        if uploaded < len(records):
            refuse(f"uploaded {uploaded} of {len(records)} records; status left at 'validating'")
            continue
        sinks.prune(str(article), {r.id for r in records})
        if result.scope == "whole":
            await sinks.record_status(article, "ingested")
        result.outcome = "ingested"

    return report
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_ingestion_pipeline.py -v`
Expected: all 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/ingestion/pipeline.py tests/test_ingestion_pipeline.py
git commit -m "Ingestion: a pipeline over corpora, with a shrink guard" -m "A corpus names primary documents, whole ILGA Articles and whether cited sections of other Articles come along. Refusals are per file or Article and change nothing; an Article that would lose indexed sections is refused unless --allow-shrink names it. Partial Articles are not recorded in pension_articles." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The CLI and the docs

**Files:**
- Rewrite: `scripts/ingest_articles.py`
- Modify: `CLAUDE.md` (the "Ingestion pipeline" section's opening paragraph and the "Ingested" paragraph)

**Interfaces:**
- Consumes: `CORPORA`, `Sinks`, `run`, `RunReport` (Task 8); `LocalDirSource`, `BlobSource` (Task 3); `list_indexed_sections` (Task 6); existing `create_search_index`, `get_index_client`, `upload_records`, `prune_stale_records`, `generate_embeddings`, `get_admin_session`, `PensionArticle`.

- [ ] **Step 1: Rewrite the script around the pipeline**

Edit `scripts/ingest_articles.py` in place:
- **Keep unchanged:** `ARTICLE_NAMES` (all 24 entries and comments), `UNSUPPORTED_ARTICLES` and its comment (`tests/test_ingestion.py`'s comments point at it; the splitter now enforces it), and `update_article_status`.
- **Delete:** `check_index_exists()` and `ingest()`, and the old `if __name__ == "__main__":` block.
- **Replace** the module docstring and the import block, and **add** the new functions and entry point, so that everything *other than* the three kept items reads exactly as below.

```python
"""Ingest statute text into Azure AI Search.

Usage:
    uv run python scripts/ingest_articles.py --corpus meabf --dry-run
    uv run python scripts/ingest_articles.py --corpus meabf
    uv run python scripts/ingest_articles.py --corpus meabf --source blob
    uv run python scripts/ingest_articles.py                        # ilga-all, as before

Always run --dry-run first: it converts, checks and parses everything and
prints what each Article would add or remove, without embedding or writing.
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

from azure.core.exceptions import ResourceNotFoundError
from sqlalchemy import update

from pension_chat.config import get_settings
from pension_chat.db.models import PensionArticle
from pension_chat.db.session import get_admin_session
from pension_chat.ingestion.embedder import generate_embeddings
from pension_chat.ingestion.indexer import (
    create_search_index,
    get_index_client,
    list_indexed_sections,
    prune_stale_records,
    upload_records,
)
from pension_chat.ingestion.pipeline import CORPORA, RunReport, Sinks, run
from pension_chat.ingestion.sources import BlobSource, LocalDirSource

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion_cli")

STATUTE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "statutes")

# (ARTICLE_NAMES, UNSUPPORTED_ARTICLES and update_article_status stay here, unchanged.)


def index_exists() -> bool:
    try:
        get_index_client().get_index(get_settings().azure_search_index_name)
        return True
    except ResourceNotFoundError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", choices=sorted(CORPORA), default="ilga-all")
    parser.add_argument("--source", choices=["local", "blob"], default="local",
                        help="where the corpus's primary documents come from (ILGA texts are always local)")
    parser.add_argument("--dry-run", action="store_true", help="convert, check and parse; change nothing")
    parser.add_argument("--allow-shrink", type=int, action="append", default=[], metavar="N",
                        help="let Article N lose indexed sections (repeatable)")
    parser.add_argument("--article", type=int, action="append", default=[], metavar="N",
                        help="only process Article N (repeatable)")
    return parser


def print_report(report: RunReport) -> None:
    print("\n=== Ingestion report")
    for name, reason in report.file_errors:
        print(f"  FILE REFUSED  {name}: {reason}")
    for a in report.articles:
        line = f"  Art {a.article:>2} {a.scope:<8} {a.outcome:<8} sections={a.sections:<4} records={a.records:<5}"
        line += f" +{len(a.added)} -{len(a.missing)}  {', '.join(a.sources)}"
        print(line + (f"\n           reason: {a.reason}" if a.reason else ""))
    if report.unavailable:
        print(f"  Cited but not available: {', '.join(sorted(report.unavailable))}")
    print(f"=== {'FAILED' if report.failed else 'OK'}")


async def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    logger.info("Corpus %s into index '%s'%s", args.corpus, settings.azure_search_index_name,
                " (dry run)" if args.dry_run else "")

    if not args.dry_run and not index_exists():
        logger.info("Creating search index...")
        create_search_index()

    primary = BlobSource.from_settings() if args.source == "blob" else LocalDirSource(STATUTE_DIR)
    sinks = Sinks(
        list_indexed_sections=list_indexed_sections,
        embed=generate_embeddings,
        upload=upload_records,
        prune=prune_stale_records,
        record_status=update_article_status,
    )
    report = await run(
        CORPORA[args.corpus], primary, LocalDirSource(STATUTE_DIR), sinks,
        dry_run=args.dry_run,
        allow_shrink=frozenset(args.allow_shrink),
        only_articles=set(args.article) or None,
    )
    print_report(report)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Verify the CLI parses and the old behaviour is the default**

Run: `uv run python scripts/ingest_articles.py --help`
Expected: usage text listing `--corpus {ilga-all,meabf}`, `--source {local,blob}`, `--dry-run`, `--allow-shrink N`, `--article N`.

- [ ] **Step 3: Update `CLAUDE.md`**

In the "Ingestion pipeline" section, replace the first paragraph ("`parser.py` → `chunker.py` → ...") with:

```markdown
`sources.py` → `converters.py` → `normalise.py` → `checks.py` → `splitter.py` → `parser.py` → `chunker.py` → `embedder.py` → `indexer.py`, orchestrated by `pipeline.py` and run by `scripts/ingest_articles.py`. **A corpus** (`pipeline.CORPORA`) names primary documents (any of `.txt`, `.md`, `.docx`, `.pdf`, from `data/statutes/` or Blob), whole Articles from the ILGA texts, and whether the Articles the primary documents cite come along one hop. `meabf` is the current focus: the client's Article 8 `.docx`, Articles 1 and 20 whole, and the 14 sections of other funds' Articles that Article 8 cites. `ilga-all` is every integer Article, as before. **Always `--dry-run` first.** A damaged conversion is refused, not repaired: a `.docx` whose extracted text is shorter than its text nodes (the client's keeps Article 8 in nested tables), a PDF without a text layer, a header mid-line, a header not followed by its own title, another act. **The shrink guard** refuses any Article that would lose indexed sections unless `--allow-shrink N` names it, and fails closed when the index cannot be listed. Client documents live in `data/`, which is never committed.
```

- [ ] **Step 4: Run the full suite and lint**

Run: `uv run pytest -q`
Expected: all tests PASS (the pre-existing suite plus the new files).

Run: `uv run ruff check src/pension_chat/ingestion scripts/ingest_articles.py tests/_pdf.py tests/test_ingestion_*.py`
Expected: no findings in the new files. Fix any in files this plan created.

- [ ] **Step 5: Commit**

```bash
git add scripts/ingest_articles.py CLAUDE.md
git commit -m "Ingestion: CLI over corpora, with --dry-run and --allow-shrink" -m "--corpus meabf ingests the client's Article 8 plus what it cites; ilga-all remains the default. The run exits non-zero if anything was refused." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The client's document, end to end

**Files:**
- Test: `tests/test_ingestion_client_docs.py`

**Interfaces:**
- Consumes: `to_text`, `SourceFile` (Task 3); `check_file_text` (Task 5); `split_by_article` (Task 4); `parse_statute_text`, `parse_statute_file` (Task 1); `chunk_sections` (existing); `normalise` (Task 2).

- [ ] **Step 1: Write the test**

Create `tests/test_ingestion_client_docs.py`:

```python
"""The client's Article 8 document against ILGA's Article 8.

Measured 2026-09-29: the same 211 sections; with nested tables read, every word
of the client's text appears in ILGA's, in order; ILGA additionally carries a
pending-amendment variant of §8-230.1 (P.A. 104-457) and footnote asterisks.
Skipped when either file is absent: data/ is never committed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pension_chat.ingestion.checks import check_file_text
from pension_chat.ingestion.chunker import chunk_sections
from pension_chat.ingestion.converters import to_text
from pension_chat.ingestion.normalise import normalise
from pension_chat.ingestion.parser import parse_statute_file, parse_statute_text
from pension_chat.ingestion.sources import SourceFile
from pension_chat.ingestion.splitter import split_by_article

STATUTES = Path(__file__).resolve().parents[1] / "data" / "statutes"
DOC = STATUTES / "Illinois Pension Law MEABF.docx"
TXT = STATUTES / "article_8.txt"
KNOWN_DIFFERENCE = "8-230.1"

pytestmark = pytest.mark.skipif(
    not (DOC.exists() and TXT.exists()),
    reason="client document or ILGA Article 8 not on disk (data/ is never committed)",
)


@pytest.fixture(scope="module")
def client_text() -> str:
    text = to_text(SourceFile(DOC.name, DOC.read_bytes(), "test"))
    check_file_text(DOC.name, text)          # raises if structure is damaged
    parts = split_by_article(text)
    assert set(parts) == {8}
    return parts[8]


def test_same_sections_as_ilga(client_text: str) -> None:
    client = {s.section_id for s in parse_statute_text(client_text, 8)}
    ilga = {s.section_id for s in parse_statute_file(str(TXT), 8)}
    assert client == ilga
    assert len(client) == 211


def test_same_records_except_the_known_difference(client_text: str) -> None:
    def ids(sections):  # type: ignore[no-untyped-def]
        return {r.id for r in chunk_sections(sections) if r.section != KNOWN_DIFFERENCE}

    assert ids(parse_statute_text(client_text, 8)) == ids(parse_statute_file(str(TXT), 8))


def test_every_client_word_is_in_the_ilga_text_in_order(client_text: str) -> None:
    client_words = re.findall(r"\S+", normalise(client_text))
    ilga_words = iter(re.findall(r"\S+", normalise(TXT.read_text(encoding="utf-8-sig"))))
    missing = [w for w in client_words if not any(w == x for x in ilga_words)]
    assert missing == []
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_ingestion_client_docs.py -v`
Expected with `data/statutes/` present: 3 PASS. If `test_same_records_except_the_known_difference` fails, print the symmetric difference of the two id sets: any section other than `8-230.1` is a real conversion or parsing defect — fix the converter or normaliser (with a unit test in its own file reproducing the case), never widen `KNOWN_DIFFERENCE`.

- [ ] **Step 3: Dry-run the MEABF corpus against the current (dev) index**

Run: `uv run python scripts/ingest_articles.py --corpus meabf --dry-run`
Expected (the dev index holds all of Articles 1–24 from ILGA):
- `Art 8 whole dry-run`, `Art 1 whole dry-run`, `Art 20 whole dry-run`, each `-0`.
- Articles 2, 3, 5, 7, 9, 12, 14, 18, 19, 21 `sections refused`, each with a reason naming `--allow-shrink N` — the guard working: pruning them to their cited sections would delete the rest of those Articles from the dev index.
- `Cited but not available: 34-60.1`.
- Final line `=== FAILED` (because of the expected refusals); exit code 1.

This proves the guard without writing anything. **Do not** run the non-dry form against the dev index. The real run targets the client's fresh `pension-code-prod` index, where nothing is indexed yet and nothing can shrink — that is the next plan (client environment), not this one.

- [ ] **Step 4: Commit**

```bash
git add tests/test_ingestion_client_docs.py
git commit -m "Ingestion: the client's Article 8 document against ILGA's, end to end" -m "Same 211 sections and records, every client word present in ILGA's text in order; the one known difference is ILGA's pending-amendment variant of 8-230.1. Skips when data/ is absent." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
