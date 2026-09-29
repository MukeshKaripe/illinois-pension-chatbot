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
from pension_chat.ingestion.indexer import IndexListingError
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
            refused[article] = (
                f"Article {article} appears in both {texts[article][0]} and {label}; choosing one would be a guess"
            )
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

        def refuse(reason: str, result: ArticleResult = result) -> None:
            result.outcome, result.reason = "refused", reason
            logger.error("Article %d refused: %s", result.article, reason)

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
        except IndexListingError as exc:
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
