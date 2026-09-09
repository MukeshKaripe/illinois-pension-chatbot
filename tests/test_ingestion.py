"""Tests for parsing the statute text the way ILGA actually publishes it.

Three ways a section can be lost or corrupted between the .txt file and the
index, all three found by ingesting Articles 1, 8, 9 and 20 and all three
already present in the two Articles the pilot had been running on:

* **A section printed more than once under one header.** ILGA marks each copy
  with a "(Text of Section ...)" line — text a court has struck beside text that
  stands, or several concurrent amendments nobody has reconciled. Concatenated,
  they read as one section contradicting itself; and since the index is keyed on
  section plus subsection, the repeated letters overwrite each other on upload.
  Section 11-134.1, the automatic annuity increase, had been indexed since the
  pilot began with the text of P.A. 98-641 in it — a Public Act held
  unconstitutional.

* **A section id with a letter suffix.** Section 1-113.4a exists, immediately
  after Section 1-113.4. The header pattern required a digit or a decimal and
  nothing else, so the header never matched, the section was never opened, and
  its text was appended to the section above it.

* **A section that letters two lists.** Section 9-219 enumerates what counts as
  service, then enumerates it again for a different class of employee, starting
  over at (a). Four subsections landed on keys already taken.

The corpus check at the bottom is the one that matters most. It is a bug-class
guard in the style of `test_planning.py` and `test_coverage.py`: rather than
asserting the three known sections behave, it walks every statute file present
and asserts that no variant survived carrying "unconstitutional" in its label. A
future file phrasing the marker differently would be classified as an ordinary
concurrent amendment and kept — struck text, indexed and citable, with nothing
in the answer path able to tell.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.agent.retrieval import extract_section_references
from pension_chat.ingestion.chunker import chunk_sections
from pension_chat.ingestion.parser import (
    VARIANT_MARKER_RE,
    _split_variants,
    parse_statute_file,
)

STATUTE_DIR = Path(__file__).resolve().parents[1] / "data" / "statutes"

# `data/statutes/*.txt` is gitignored — the corpus is third-party text fetched
# separately, so a clean checkout has none of it. The tests built on synthetic
# blocks below cover the logic and always run; the ones marked here check that
# logic against what ILGA actually publishes, which is where every one of these
# bugs came from, so they skip rather than fail when the corpus is absent.
requires_corpus = pytest.mark.skipif(
    not list(STATUTE_DIR.glob("article_*.txt")),
    reason="no statute files in data/statutes (they are gitignored)",
)


def _lines(text: str) -> list[str]:
    return text.strip("\n").split("\n")


# ── Splitting a header's block into the texts it contains ─────────


def test_a_section_with_no_marker_is_left_alone() -> None:
    """The ordinary case, and much the most common one."""
    block = _lines(
        """
(40 ILCS 5/12-101)
Sec. 12-101. Creation of fund.
In each city of over 500,000 inhabitants a fund shall be created.
(Source: Laws 1963, p. 161.)
"""
    )
    assert _split_variants(block) == [(None, block)]


def test_text_held_unconstitutional_is_dropped() -> None:
    """A court has struck it; the index has no way to mark text as void."""
    block = _lines(
        """
(40 ILCS 5/20-106)
(Text of Section WITH the changes made by P.A. 98-599, which has been held unconstitutional)
Sec. 20-106. Final average salary.
(a) Subject to the limitations imposed by this amendatory Act.
(Source: P.A. 98-599, eff. 6-1-14.)
(Text of Section WITHOUT the changes made by P.A. 98-599, which has been held unconstitutional)
Sec. 20-106. Final average salary.
(a) The average salary considered by a participating system.
(Source: P.A. 88-593, eff. 8-22-94.)
"""
    )
    variants = _split_variants(block)

    assert len(variants) == 1
    label, lines = variants[0]
    # With its counterpart gone this is simply the section, so it carries no
    # label — nothing survives for a label to distinguish it from.
    assert label is None
    text = "\n".join(lines)
    assert "The average salary considered by a participating system." in text
    assert "amendatory Act" not in text
    # The header above the first marker belongs to every copy.
    assert text.startswith("(40 ILCS 5/20-106)")


def test_concurrent_amendments_are_all_kept_and_labelled() -> None:
    """Section 1-160 is printed three times, one per Public Act.

    All three are on the books. Choosing between them is a reading of the law,
    not a parsing decision, so the parser keeps them and says which is which.
    """
    block = _lines(
        """
(40 ILCS 5/1-160)
(Text of Section from P.A. 102-719)
Sec. 1-160. Provisions applicable to new hires.
(a) First version.
(Text of Section from P.A. 102-813)
Sec. 1-160. Provisions applicable to new hires.
(a) Second version.
(Text of Section from P.A. 102-956)
Sec. 1-160. Provisions applicable to new hires.
(a) Third version.
"""
    )
    variants = _split_variants(block)

    assert [label for label, _ in variants] == [
        "from P.A. 102-719",
        "from P.A. 102-813",
        "from P.A. 102-956",
    ]
    assert "First version." in "\n".join(variants[0][1])
    assert "Second version." not in "\n".join(variants[0][1])


def test_a_pending_amendment_keeps_both_texts() -> None:
    """One is current law and one takes effect later; neither may be dropped."""
    block = _lines(
        """
(40 ILCS 5/11-221.1)
(Text of Section before amendment by P.A. 104-457)
Sec. 11-221.1. Service with the Metropolitan Transit Authority Act.
(Text of Section after amendment by P.A. 104-457)
Sec. 11-221.1. Service with the Chicago Transit Authority Act.
"""
    )
    variants = _split_variants(block)

    assert [label for label, _ in variants] == [
        "before amendment by P.A. 104-457",
        "after amendment by P.A. 104-457",
    ]


def test_a_block_whose_only_copy_was_struck_is_not_lost() -> None:
    """Losing the section entirely is worse than keeping it with a warning."""
    block = _lines(
        """
(40 ILCS 5/9-999)
(Text of Section WITH the changes made by P.A. 98-599, which has been held unconstitutional)
Sec. 9-999. Something.
"""
    )
    variants = _split_variants(block)

    assert len(variants) == 1
    assert variants[0][0] is None
    assert "Sec. 9-999" in "\n".join(variants[0][1])


# ── Section ids that are not purely numeric ───────────────────────


def test_a_letter_suffixed_section_is_its_own_section(tmp_path: Path) -> None:
    """Section 1-113.4a used to be swallowed by Section 1-113.4 above it."""
    statute = tmp_path / "article_1.txt"
    statute.write_text(
        "(40 ILCS 5/Art. 1 heading)\n"
        "ARTICLE 1. GENERAL PROVISIONS\n"
        "\n"
        "(40 ILCS 5/1-113.4)\n"
        "Sec. 1-113.4. List of additional permitted investments.\n"
        "(a) Investments permitted to funds with net assets over $10,000,000.\n"
        "\n"
        "(40 ILCS 5/1-113.4a)\n"
        "Sec. 1-113.4a. List of additional permitted investments for Article 3.\n"
        "(a) Investments permitted to Article 3 and 4 funds.\n",
        encoding="utf-8",
    )

    sections = {s.section_id: s for s in parse_statute_file(str(statute), 1)}

    assert set(sections) == {"1-113.4", "1-113.4a"}
    assert "Article 3 and 4 funds" not in sections["1-113.4"].full_text
    assert sections["1-113.4a"].section_title.startswith("List of additional")


# ── Record keys, which are what the index is actually keyed on ────


@requires_corpus
def test_variants_do_not_overwrite_each_other_on_upload() -> None:
    sections = parse_statute_file(str(STATUTE_DIR / "article_1.txt"), 1)
    records = chunk_sections([s for s in sections if s.section_id == "1-160"])

    ids = [r.id for r in records]
    assert len(ids) == len(set(ids))
    # Every record still cites the section it came from, suffix or not.
    assert {r.section for r in records} == {"1-160"}
    # And says which text it is, in the content the model reads.
    assert all(r.content.startswith("(from P.A. 10") for r in records)


@requires_corpus
def test_a_restarted_letter_list_keeps_both_subsections() -> None:
    """Section 9-219 letters (a)-(f), then letters (a)-(d) again."""
    sections = parse_statute_file(str(STATUTE_DIR / "article_9.txt"), 9)
    records = chunk_sections([s for s in sections if s.section_id == "9-219"])

    ids = [r.id for r in records]
    assert len(ids) == len(set(ids))

    # The key is disambiguated; the citation is not. An answer quoting the
    # second list still cites subsection (a), because that is what it is.
    first_a = next(r for r in records if r.id == "9-219_a")
    second_a = next(r for r in records if r.id == "9-219_a_2")
    assert first_a.subsection_id == "(a)"
    assert second_a.subsection_id == "(a)"
    assert first_a.content != second_a.content


@pytest.mark.parametrize(
    ("filename", "article"),
    [
        ("article_1.txt", 1),
        ("article_8.txt", 8),
        ("article_9.txt", 9),
        ("article_11.txt", 11),
        ("article_12.txt", 12),
        ("article_20.txt", 20),
    ],
)
@requires_corpus
def test_no_record_key_collides_across_a_whole_article(
    filename: str, article: int
) -> None:
    """The invariant the index depends on, checked against the real corpus.

    A collision is not an error at upload time — the later document simply
    replaces the earlier one, and the section is quietly short a subsection.
    """
    path = STATUTE_DIR / filename
    if not path.exists():  # pragma: no cover - corpus is checked in
        pytest.skip(f"{filename} not present")

    records = chunk_sections(parse_statute_file(str(path), article))
    ids = [r.id for r in records]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"colliding record keys in Article {article}: {duplicates}"


# ── The bug-class guard ───────────────────────────────────────────


@requires_corpus
def test_no_struck_text_survives_anywhere_in_the_corpus() -> None:
    """Every "(Text of Section ...)" form in the corpus, classified.

    The failure this guards against is silent and one-directional: a marker
    phrased in a way `VOID_VARIANT_RE` does not recognise falls through to the
    "concurrent amendment" branch and is *kept*, labelled, embedded and citable.
    Nothing downstream can tell that a court struck it.

    So rather than asserting the three known sections behave, walk the corpus:
    no surviving variant may describe itself as unconstitutional, and every
    marker actually present must have been either dropped or accepted by the
    operative branch.
    """
    markers: set[str] = set()
    surviving_labels: set[str] = set()

    for path in sorted(STATUTE_DIR.glob("article_*.txt")):
        article = int(path.stem.split("_")[1])
        for line in path.read_text(encoding="utf-8").splitlines():
            if match := VARIANT_MARKER_RE.match(line.strip()):
                markers.add(match.group(1).strip())
        for section in parse_statute_file(str(path), article):
            if section.variant_label:
                surviving_labels.add(section.variant_label)

    assert markers, "corpus has no variant markers — this test is not testing anything"
    assert not any("unconstitutional" in label.lower() for label in surviving_labels), (
        f"struck text survived as a labelled variant: {sorted(surviving_labels)}"
    )
    # And the struck text is not merely unlabelled — it is gone.
    art_20 = parse_statute_file(str(STATUTE_DIR / "article_20.txt"), 20)
    section_20_106 = next(s for s in art_20 if s.section_id == "20-106")
    assert "defined contribution plan" not in section_20_106.full_text


# ── Naming one of these sections in a question ────────────────────


def test_a_letter_suffixed_section_can_be_asked_about_by_name():
    """Extraction has to accept the ids the parser now produces.

    Section 1-113.4a and Section 1-113.4 are different rules — one lists
    permitted investments for large funds, the other for Article 3 and 4 funds.
    Dropping the suffix pins the wrong one, and pinning is the mechanism that
    makes a named section retrievable at all.
    """
    assert extract_section_references("What does Section 1-113.4a permit?") == [
        "1-113.4a"
    ]
    assert extract_section_references("Compare 1-113.4 with 1-113.4a") == [
        "1-113.4",
        "1-113.4a",
    ]
    # The ordinary forms are unchanged.
    assert extract_section_references("Explain 40 ILCS 5/11-134") == ["11-134"]
    assert extract_section_references("Sec. 11-134.1 and Section 12-133") == [
        "11-134.1",
        "12-133",
    ]
