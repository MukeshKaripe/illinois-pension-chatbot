# Multi-Format Ingestion — Design

**Date:** 2026-09-28
**Status:** Approved in brainstorming across three sections; awaiting review of this written spec.
**Scope:** Ingesting the Illinois Pension Code (40 ILCS 5) from the client's Azure Blob container in whatever file format it arrives in — `.docx`, `.pdf`, `.txt`, `.md`, more later — through the parser that already exists, without that parser's behaviour changing.

## 1. Why

Ingestion today reads one ILGA `.txt` file per Article from `data/statutes/`, takes the Article number from the filename, and hands the path to `parse_statute_file`. The client will instead deliver the Code as documents in a Blob container (`novitasaiblob`), in mixed formats, split across files in a way nobody has fixed: one Article per file, several, or the whole Code in one.

The documents are **the same statute text** — confirmed by the client for 40 ILCS 5. Other chapters of the Illinois Compiled Statutes are possible later but unconfirmed. Structured documents of other kinds (plan handbooks, fund rules) are confirmed **not** in scope.

What makes this worth designing rather than just writing is the failure mode ingestion already has. Uploading to Azure AI Search merges by key, so a section that goes missing is not an error — the index is quietly a section short. Three ILGA formatting quirks have already done exactly that (`tests/test_ingestion.py`). Converting from PDF and Word adds new ways to lose a line break or scramble line order, and `prune_stale_records` turns a *partial* source into a *deleted* Article. The design is mostly about making those failures loud.

## 2. Decisions and their reasons

| Decision | Reason |
|---|---|
| **Convert every format to ILGA-shaped plain text, then run the existing parser.** (Approach A.) | The parser's value is not text extraction; it is the structure every downstream feature depends on — section ids the citation verifier checks, the Article the retrieval filter keys on, cross-references, coverage tags — plus three ILGA fixes with tests. One parser keeps one set of fixes. A converter's whole job is "produce text that looks like ILGA's `.txt`", which is small and testable. |
| **Not MarkItDown** (Approach B). | It emits Markdown, so headers come back as `**(40 ILCS 5/11-134)**` or `\(40 ILCS…` and must be un-marked before a parser anchored on `^\(40 ILCS 5/` can see them. A large dependency tree, and when a section goes missing the defect is inside someone else's conversion. |
| **Not format-native structure parsing** (Approach C, e.g. Word heading styles, PDF font sizes). | One parser per format, each needing its own copy of every ILGA fix. |
| **Not LlamaIndex / LlamaParse / Document Intelligence.** | LlamaIndex's readers wrap the same libraries named below and would bring a second RAG framework beside LangGraph; LlamaParse sends documents outside the client's Azure. Document Intelligence earns its place only for scanned documents, which are rejected (§5) rather than OCR'd until a scanned file actually arrives. |
| **Libraries: `pdfplumber` (MIT), `python-docx` (MIT), `azure-storage-blob` (MIT).** | Permissive licences for a client deployment. **Not PyMuPDF**: AGPL. `pdfplumber` reconstructs line order from character positions, which is what the parser's line-anchored patterns need. |
| **The Article comes from the text, never the filename.** | Files may hold any number of Articles. The number in each `(40 ILCS 5/N-…)` header is the Code's own statement of which Article the section belongs to. |
| **Only 40 ILCS 5 is accepted; any other act is refused by name.** | "Be flexible for other chapters" is met by making the act a lookup key, not by building for acts nobody has confirmed. Another act is more than a parser change: `pension_articles`, the index's `article` field, the retrieval filter and coverage are all organised around *Pension Code Articles*. That is its own project, and the refusal message says where it would start. |
| **Manual trigger** (script, or the same script as a Kubernetes Job). | Chosen by the user. A bad file never reaches the live index unattended, and `--dry-run` gives the run a review step. Event Grid and a CronJob were considered and rejected. |
| **Provenance in the log, not in the index.** | A `source_file` field would change the index schema and force a full re-index; a log line answers "which upload produced this" just as well. |
| **Tests use the client's actual documents,** not synthetic conversions of our `.txt`. | Chosen by the user. A converter tested only on documents we generated tests our generator. The ILGA `.txt` parse of the same Article remains the *expected answer* — it is the one rendering whose parse has been verified — but the documents under test are the real ones. |

## 3. Architecture

```
source (LocalDirSource | BlobSource)
   └─ list files ─> converter by extension ─> plain text
        └─ normaliser (page furniture, broken lines, markdown escapes)
             └─ file checks (§5.1)
                  └─ split by Article (from each header's own number)
                       └─ parse_statute_text(text, article)     ← today's parser, text in instead of a path
                            └─ Article checks (§5.2) ─> chunk ─> embed ─> index ─> prune   (unchanged)
```

Everything after `parse_statute_text` — `chunk_sections`, `generate_embeddings`, `upload_records`, `prune_stale_records`, `update_article_status` — is unchanged. The index schema is unchanged.

## 4. Components

Each unit has one job, is usable without reading its internals, and is testable without the network.

### 4.1 Sources — `ingestion/sources.py`

```python
@dataclass(frozen=True)
class SourceFile:
    name: str          # path within the source, e.g. "article_11.pdf"
    data: bytes
    version: str       # Blob ETag; file mtime for local

class Source(Protocol):
    def files(self) -> Iterator[SourceFile]: ...
```

- `LocalDirSource(path)` — development and tests; replaces the hard-coded `data/statutes` walk.
- `BlobSource(connection_string, container, prefix="")` — the client. Lists and downloads; never writes. The connection string is read from settings (`AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_CONTAINER`), added to `config.py` as optional so nothing that doesn't ingest from Blob requires them. A managed identity replaces the connection string once the cluster exists; only this class changes.

### 4.2 Converters — `ingestion/converters.py`

```python
def to_text(file: SourceFile) -> str   # raises UnsupportedFormat, ConversionFailed
```

A table from lower-cased extension to function:

| Extension | Conversion |
|---|---|
| `.txt` | Decode UTF-8 (BOM stripped; `cp1252` fallback, logged). |
| `.md` | Decode, then the normaliser removes markup. |
| `.docx` | `python-docx`: body paragraphs and tables **interleaved in document order** (python-docx's `paragraphs` and `tables` are separate lists, so walk the body elements instead); each cell's paragraphs emitted in order, **recursing into nested tables** (`cell.tables` — `cell.paragraphs` omits them), and **each paragraph split on embedded `\n`** — soft line breaks carry the section structure. The client's first document keeps *all* its text in 212 single-column tables with every body paragraph empty, **74 of them nested inside other tables**, and indents with non-breaking spaces (5,104 of them). Reading only `paragraphs` gets nothing; reading cells without recursing silently drops ~3,300 words (measured) while every section header still appears — exactly the thin-index failure §5 exists for, and one the header checks alone would not catch. |
| `.pdf` | `pdfplumber`: per page, text lines in reading order. Pages kept separate until the normaliser has removed page furniture. |

An extension not in the table raises `UnsupportedFormat` naming the file. Adding a format is one function and one table row.

### 4.3 Normaliser — `ingestion/normalise.py`

One function every converter's output passes through, so the formats cannot drift apart:

- CRLF/CR → LF; BOM, non-breaking and zero-width spaces removed.
- **Page furniture (PDF):** a line that recurs at the top or bottom of most pages (running header, footer, page number, `Page N of M`) is removed.
- **Broken lines (PDF):** a line is joined to the next only when the join is unambiguous — the line ends mid-word with a hyphen, or ends without terminal punctuation *and* the next line starts lower-case. A next line matching any parser anchor (`(40 ILCS 5/`, `Sec. `, `(a) `, `(1) `, `(Source:`, `(Text of Section`) is **never** joined to.
- **Markdown:** heading hashes, emphasis markers and backslash escapes removed; list bullets removed only where they precede a parser anchor.

### 4.4 Splitter — `ingestion/splitter.py`

```python
def split_by_article(text: str, act: str = "40 ILCS 5") -> dict[int, str]
```

- A cut happens at an Article heading `(40 ILCS 5/Art. N heading)`, a division heading `(40 ILCS 5/Art. N, div. …)`, or a section header whose Article differs from the current one — whichever comes first. Lines before a cut belong to the Article after it (headings precede their sections).
- Blocks for the same Article that are not contiguous within one file are concatenated in order.
- The **act guard** lives here: a header naming any act other than `act` raises `UnsupportedAct` for the file.
- Non-integer Articles (`1A`, `22A`–`22C`) stay refused as today (`UNSUPPORTED_ARTICLES`), now detected from text rather than from filenames.

### 4.5 Parser — `ingestion/parser.py`

`parse_statute_file(filepath, article)` becomes a wrapper that reads the file and calls the new `parse_statute_text(content, article)`. The body moves unchanged. `tests/test_ingestion.py` is not edited, and passing unedited is the evidence nothing changed.

### 4.6 Script — `scripts/ingest_articles.py`

- `--source local|blob` (default `local`, pointing at `data/statutes` — today's behaviour).
- `--dry-run` (§5.3).
- `--allow-shrink N` (repeatable) (§5.2).
- `--article N` (repeatable) — restrict a run, as `target_articles` does today.

## 5. Safety checks

The rule: **a document that converts badly fails loudly and never produces a thinner index.** Three levels of consequence.

### 5.1 Per file — reject the file by name, continue with the rest

1. **No text layer.** If more than half of a PDF's pages yield fewer than 50 characters, the file is refused as needing OCR.
2. **Another act** — the act guard (§4.4).
3. **A header lost mid-line.** A `(40 ILCS 5/N-M` pattern anywhere but the start of a line means a lost line break; its section would be appended to the one above (the §1-113.4a failure). Counted and refused, never repaired by guessing.
4. **Header and title disagree.** Each `(40 ILCS 5/11-134)` header must be followed — past blank lines, `(Text of Section …)` markers and `WITH/WITHOUT the changes …` lines — by `Sec. 11-134.`. A mismatch means extracted text is out of order (two-column layouts). Refused.

Checks 3 and 4 are enabled only after their **baseline is measured on the ILGA `.txt` corpus** (first implementation step): the count of mid-line headers and of header/title mismatches across all 28 files. Any non-zero baseline is investigated and the check made to accept those exact cases, so a correct file never trips it.

### 5.2 Per Article — skip the Article, leave its indexed state untouched

5. **The same Article from two files** in one run — refused, both files named. Choosing one is a guess.
6. **Zero sections** — unchanged: stays `validating`, never recorded as ingested.
7. **Duplicate record ids** within the Article's records — checked before upload, since the second silently overwrites the first.
8. **Shrink guard.** Before `prune_stale_records`, the set of *section* ids produced is compared with the section ids currently indexed for that Article. If any indexed section is missing, the Article is refused and the missing ids listed. Repealed sections remain in ILGA text as `(Repealed)`, so a legitimate update does not lose sections; when one genuinely does, `--allow-shrink N` permits it for that Article alone. **The guard fails closed:** if the index cannot be listed, the Article is refused (today `prune_stale_records` logs and returns 0 on a listing error; the guard must not inherit that). An Article with nothing indexed yet passes — a first ingestion cannot shrink.

### 5.3 Per run

9. **`--dry-run`** — the review step before every real run. Converts, checks and parses everything; prints per Article the source file(s), section count, and sections added/missing against the live index. No embedding calls, no writes.
10. **Exit code** non-zero if any file or Article was refused, so a Kubernetes Job shows failed rather than completed-with-warnings.
11. **Provenance log line per Article:** source file name(s), version (Blob ETag), section count.

## 6. Testing

All in `tests/`, no network, consistent with the existing suite.

1. **Real-document equivalence — the core test.** Fixtures are the **client's actual documents** in `tests/fixtures/ingestion/client/`. For each, every Article it contains must parse to exactly the same section ids, subsection ids and record ids as the ILGA `.txt` of that Article. **The first arrived 2026-09-29:** *Illinois Pension Law MEABF.docx* (120 KB) — Article 8, 211 section headers, §8-101 to §8-253, the same set as `article_8.txt` with none missing or extra (checked by extracting table text and matching headers). Word for word, with nested tables read, it is 99.7% identical to ours: all of its text is in `article_8.txt`, which additionally carries one pending-amendment variant of §8-230.1 (P.A. 104-457) and footnote asterisks. The test therefore compares **section and subsection ids exactly, and text by containment** (every word of the document present in the parsed output, in order) rather than byte equality, and names the §8-230.1 variant as the known difference.

   **A word-count check is added to §5.1 as a result:** per section, words extracted versus words the parser kept. Header-level checks passed on the extraction that dropped ~3,300 words; only a text-level comparison caught it.

**Corpus for the MEABF focus** (per the scope, `2026-09-29-employer-first-scope.md`): Article 8 from the client's document; Articles 1 and 20 in full and 14 cited sections of other funds' Articles from our ILGA copies. So ingestion takes a **section allow-list per source**, not only whole Articles — a new option on the script (`--sections`), with the one-hop list derived from Article 8's own cross-references rather than typed by hand.
2. **Each safety check has a test that trips it,** asserting both that it fired and what was left untouched. Most need only a few lines of `.txt`/`.md` (another act, mid-line header, header/title mismatch, same Article twice, shrink against a mocked indexed set, `--allow-shrink`, duplicate ids, index listing failure → refused). The no-text-layer check needs one small image-only PDF, committed as a fixture.
3. **Normaliser unit tests:** page furniture removed; hyphenated and soft breaks joined; markdown stripped — each paired with a line that must **not** change (a real `(a)` after a page break, a `Sec.` line after a wrapped line).
4. **Splitter:** one Article, several, non-contiguous blocks of one Article, division headings, a non-integer Article refused.
5. **Sources:** `LocalDirSource` on a temp directory; `BlobSource` against a fake container client.
6. **Existing tests unedited and green** — the parser-behaviour guarantee (§4.5).
7. **Live, manual, after upload:** `--dry-run --source blob` against `novitasaiblob`, comparing per-Article counts with the `.txt` parse and with the current index (4,766 documents). Any disagreement becomes a fixture before it is fixed.

Agent evals are not re-run for this change: if equivalence holds, the indexed content is identical. They are re-run when the app moves to the client's index and to `gpt-6-luna`, for that reason.

## 7. Out of scope

- Other ILCS chapters (refused by the act guard; §2).
- OCR for scanned documents (refused; §5.1).
- Non-statute documents — confirmed not coming.
- Automatic or scheduled triggering.
- Changing the index schema.
- The `embedder.py` character-vs-token truncation noted in `CLAUDE.md` — unrelated, and unchanged by this.

## 8. Dependencies on the client

- The documents themselves — blocks §6.1 and §6.7.
- A container in `novitasaiblob` (none exists; the account key can create one, verified 2026-09-28 without creating it).
- Later: a managed identity or read-only SAS in place of the account-key connection string (§4.1).
