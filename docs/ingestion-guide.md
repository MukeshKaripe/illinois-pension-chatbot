# Ingestion guide

How statute text gets into the search index the assistant answers from, how to re-run it, and what to check afterwards.

## What ingestion does

`scripts/ingest_articles.py` runs this pipeline for each Article:

1. **Convert** the source document to text (`.docx`, `.pdf`, `.md` or `.txt`).
2. **Check** it, and refuse it if it's damaged.
3. **Split** it into sections.
4. **Chunk** each section into subsections.
5. **Embed** each chunk (Azure OpenAI `text-embedding-3-large`).
6. **Upload** to Azure AI Search, replacing each document by its key.
7. **Prune** any document for that Article that this run didn't produce.
8. **Record** the Article's status in Postgres (`pension_articles.ingestion_status`).

It writes to the index named by `AZURE_SEARCH_INDEX_NAME`. Today that's **`pension-code-prod`**.

## Corpora

You must pass `--corpus`; there is deliberately no default.

| Corpus | What goes in | Use |
|---|---|---|
| `meabf` (current) | The client's Article 8 from `Illinois Pension Law MEABF.docx`; Articles **1** and **20** whole from the ILGA texts; and the sections of other funds' Articles that Article 8 cites (one hop) | The MEABF deployment |
| `ilga-all` | Every integer Article (1–24) from the ILGA `.txt` files | The whole Code. Don't run it against the client index: it would widen it to 24 Articles and replace the client's Article 8 with ILGA's. |

**What's in the index now:** 334 sections. Article 8 (211), Article 1 (79), Article 20 (32), plus 12 cited sections from Articles 2, 5, 7, 9, 12, 14, 18 and 19. The backend log confirms this at start-up with `Corpus built: 334 sections`.

**Where the documents live:**
- **Local (default):** `data/statutes/`, holding `Illinois Pension Law MEABF.docx` and `article_N.txt`. This folder is never committed.
- **Azure Blob:** with `--source blob`, the primary documents come from the container in `AZURE_STORAGE_CONNECTION_STRING` and `AZURE_STORAGE_CONTAINER`. The ILGA texts always come from `data/statutes/`.

## Before you run

- **`.env` needs:**
  - Azure AI Search: endpoint, key, and `AZURE_SEARCH_INDEX_NAME`.
  - Azure OpenAI with the embedding deployment.
  - Postgres (`PG*`), for article status.
  - The `AZURE_STORAGE_*` settings, only for `--source blob`.
- **Don't run it while the chatbot is being demoed.** Embedding a few hundred chunks uses the Azure OpenAI quota, and the index changes under live answers while it runs.

## Standard run

Run every command from `C:\dev\pension_chat`.

**1. Dry run.** It converts, checks and parses everything, compares the result with what's indexed, and writes nothing:

```powershell
uv run python scripts/ingest_articles.py --corpus meabf --dry-run
```

**2. Read the report.** One line per Article:

```
  Art  8 whole    dry-run  sections=211  records=…   +0 -0  Illinois Pension Law MEABF.docx
```

| Part | Meaning |
|---|---|
| `whole` or `sections` | A whole Article, or only the cited sections of another fund's Article |
| `dry-run` / `ingested` / `refused` | The outcome. A refusal prints a `reason:` line. |
| `+N` | Sections the run would add that aren't indexed yet |
| `-N` | Sections indexed now that the run would **remove** (see "When text changes" below) |
| `FILE REFUSED` | A document couldn't be converted safely (see Troubleshooting) |
| `Cited but not available` | Sections Article 8 cites that weren't found in their Article's text |
| `Cited from other Acts` | Citations to other laws (the School Code and so on). Reported, never ingested. |

The last line is `=== OK` or `=== FAILED`.

**3. Real run.** Same command without `--dry-run`:

```powershell
uv run python scripts/ingest_articles.py --corpus meabf
```

**4. Restart the backend.** The section graph it builds at start-up (`Corpus built: N sections`) is read once, so the running app won't see the new text until it restarts.

## Clearing and re-ingesting the same documents

**Just run it again; nothing needs deleting first:**

```powershell
uv run python scripts/ingest_articles.py --corpus meabf --dry-run   # expect +0 -0 everywhere
uv run python scripts/ingest_articles.py --corpus meabf
```

For every Article it processes, the run does three things:
1. **Re-embeds every chunk.**
2. **Overwrites each document by its key**, so nothing is duplicated.
3. **Deletes any document for that Article the run didn't produce**, for example left over from an older parser.

So a re-run is effectively a clear and re-ingest of those Articles, and the index is never empty while it happens.

**What it doesn't touch:**
- **Articles outside the corpus.** A `meabf` run never deletes, say, Article 11 documents left over from an old `ilga-all` run. Today's index has none.
- **The approved formulas.** Their evidence fingerprints are computed on each section's id and text, so identical documents leave every formula fresh.

To re-ingest **one Article**:

```powershell
uv run python scripts/ingest_articles.py --corpus meabf --article 8
```

### A full wipe

Only needed if the index **schema** changes (fields, vector settings). There's no script for it, on purpose:

1. In the Azure portal, open the Search service and delete the `pension-code-prod` index. **From this moment the chatbot can't answer statute questions,** so don't do this near a demo.
2. Run the standard dry run and then the real run. The script recreates the index with the current schema when it doesn't exist.
3. Restart the backend.

## When the text changes (a new version of the client document)

1. Replace `data/statutes/Illinois Pension Law MEABF.docx`, or the blob.
2. **Dry run**, and read the `+N -N` for Article 8:
   - `+N`: new sections.
   - `-N`: sections that would disappear. **The run refuses an Article that would lose sections**, which guards against a damaged conversion quietly deleting law. If the removals are intended (a section was repealed or renumbered), allow them for that Article:
     ```powershell
     uv run python scripts/ingest_articles.py --corpus meabf --allow-shrink 8
     ```
3. Run it, then restart the backend.
4. **Check the formulas.** Any formula whose sections' text changed is marked `stale` the next time it's looked up, including the three approved core formulas. A stale formula isn't reused, so the agent derives a fresh one, which brings back the derivation problems the core formulas exist to avoid.
   - If §8-138 or §1-160 changed, have the hand-written formulas (`src/pension_chat/calculation/core_meabf.py`) reviewed against the new text.
   - Then re-approve them:
     ```powershell
     uv run python scripts/approve_core_formulas.py                       # print them for review
     uv run python scripts/approve_core_formulas.py --approve --reviewer "Jaffer"
     ```

## Checking what's indexed

Count indexed sections per Article:

```powershell
uv run python -c "from pension_chat.ingestion.indexer import list_indexed_sections as l; c={a: len(l(str(a))) for a in range(1,25)}; print({a:n for a,n in c.items() if n}, 'total', sum(c.values()))"
```

Expected today: `{1: 79, 2: 1, 5: 2, 7: 1, 8: 211, 9: 3, 12: 1, 14: 2, 18: 1, 19: 1, 20: 32} total 334`.

## Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| `FILE REFUSED … extracted text shorter than its text nodes` | The `.docx` keeps text in nested tables the converter can't read safely | Fix the document (flatten the tables) or supply it as `.txt` or `.md` |
| `FILE REFUSED` for a PDF | No text layer (a scan) | Run OCR first, or get a text PDF |
| `refused: header mid-line` / `not followed by its own title` / `another act` | The text isn't laid out like the statute, or contains another Act | Check the source document; don't force it through |
| `refused: would remove N indexed section(s)` | The shrink guard | Check that the removals are intended, then use `--allow-shrink N` |
| `refused: write failed … 429` or `uploaded X of Y records` | Rate limit or upload failure. The Article is left at `validating` | Wait a minute and re-run; re-running is safe |
| `refused: parsed to zero sections` | Nothing in the file matched the section-header pattern | Check that it's the right file. Articles 1A, 22A, 22B and 22C are never ingested (their numbers aren't integers), and the run says so. |
| Index listing failed | Search unreachable, or the key is wrong | The run refuses rather than guess; fix the connection and re-run |
| The chatbot doesn't see new text | The backend wasn't restarted | Restart it |

## Known limits

- **Long subsections are only partly embedded.** The embedder truncates text at 8,191 *characters*, where the model's limit is 8,191 *tokens*, so roughly the last three-quarters of a very long subsection is missing from its vector. Its full text is still indexed for keyword search.
- **Partial Articles** (cited sections of other funds) are indexed but get no row in `pension_articles`.
- **Every embedding is billed,** even on a re-run with identical documents.
