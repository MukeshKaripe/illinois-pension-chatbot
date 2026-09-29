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
        self.files: dict[str, str] = files
        self.read: list[str] = []

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
    report = await run(CORPUS, _Source({"client.txt": CLIENT_ART8}), _Source(ILGA),
                       _Sinks(listing_error=True).sinks())
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
