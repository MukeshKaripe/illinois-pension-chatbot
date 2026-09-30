"""A section ILGA prints in several versions is read once, not once per version.

Section 1-160 -- Tier 2 and Tier 3 for every fund but the downstate police and
fire -- is printed three times, "(Text of Section from P.A. 102-719)", "... 102-813"
and "... 102-956", and the three differ only in (g) and (g-5), which name who
gets Article 14's State-employee benefits. Assembly sorted chunks by subsection
alone, so the model read (a)(a)(a)(b)(b)(b)... with nothing between the copies,
and the character budget ran out in (b-10), before the age-67 rule in (c). An
MEABF Tier 2 question got a hedge built on Section 1-162 (measured 2026-09-30,
three runs of three).
"""

from __future__ import annotations

from pension_chat.agent.retrieval import _assemble, merge_variants
from pension_chat.api.sections import assemble_section

VERSIONS = ("from P.A. 102-719", "from P.A. 102-813", "from P.A. 102-956")


def _slug(label: str) -> str:
    return label.replace(" ", "_").replace(".", "_").replace("-", "_").lower()


def _chunk(label: str | None, subsection: str, body: str, *, key: str | None = None) -> dict:
    """An index document as the chunker writes it: the label prefixes the content."""
    suffix = f"__{_slug(label)}" if label else ""
    return {
        "id": f"1-160{suffix}_{key or subsection.strip('()')}",
        "section": "1-160",
        "section_title": "Provisions applicable to new hires",
        "article": "1",
        "subsection_id": subsection,
        "content": (f"({label})\n" if label else "") + body,
    }


def _one_sixty(filler: int = 0) -> list[dict]:
    """Three versions: identical except (g); (g-5) exists in the first alone."""
    pad = " x" * filler
    chunks: list[dict] = []
    for label in VERSIONS:
        chunks.append(_chunk(label, "(a)", "(a) The provisions of this Section apply." + pad))
        chunks.append(_chunk(label, "(b)", "(b) Final average salary." + pad))
        chunks.append(_chunk(label, "(c)", "(c) A member who has attained age 67 with 10 years."))
        g = "(g) applies if fire fighter" if label == VERSIONS[0] else "(g) applies only if State policeman"
        chunks.append(_chunk(label, "(g)", g))
    chunks.append(_chunk(VERSIONS[0], "(g-5)", "(g-5) applies if State policeman"))
    # Search returns documents in no particular order.
    return list(reversed(chunks))


def test_text_every_version_shares_appears_once() -> None:
    text = "\n".join(merge_variants(_one_sixty()))
    assert text.count("(a) The provisions of this Section apply.") == 1
    assert text.count("(c) A member who has attained age 67 with 10 years.") == 1


def test_a_subsection_the_versions_disagree_on_is_given_per_version() -> None:
    parts = merge_variants(_one_sixty())
    text = "\n".join(parts)
    assert "(g) applies if fire fighter" in text
    assert text.count("(g) applies only if State policeman") == 1
    # Each differing text says which versions carry it.
    fire = next(p for p in parts if "fire fighter" in p)
    police = next(p for p in parts if "only if State policeman" in p)
    assert "P.A. 102-719" in fire and "P.A. 102-813" not in fire
    assert "P.A. 102-813" in police and "P.A. 102-956" in police and "P.A. 102-719" not in police
    # A subsection only one version has is labelled with that version.
    g5 = next(p for p in parts if "(g-5)" in p)
    assert "P.A. 102-719" in g5 and "P.A. 102-813" not in g5


def test_the_reader_is_told_the_section_has_versions() -> None:
    first = merge_variants(_one_sixty())[0]
    for label in VERSIONS:
        assert label.removeprefix("from ") in first
    # The unlabelled text is the text all versions share, and the note says so.
    assert "common to all" in first


def test_subsections_stay_in_statute_order() -> None:
    text = "\n".join(merge_variants(_one_sixty()))
    order = [text.index(marker) for marker in ("(a) The", "(b) Final", "(c) A member", "(g) applies", "(g-5)")]
    assert order == sorted(order)


def test_the_rule_in_c_survives_a_budget_the_repeated_copies_exhausted() -> None:
    # Sized like the real thing: (a) and (b) together ~2,500 chars per version.
    chunks = _one_sixty(filler=1200)
    section = _assemble("1-160", chunks, 2.45, char_budget=6000)
    assert "attained age 67" in section.text


def test_a_section_with_one_version_is_assembled_as_before() -> None:
    chunks = [
        _chunk(None, "(b)", "(b) second"),
        _chunk(None, "(a)", "(a) first"),
    ]
    assert merge_variants(chunks) == ["(a) first", "(b) second"]


def test_a_single_labelled_version_keeps_its_label() -> None:
    # ILGA printed two copies and the parser dropped the struck one: what is
    # left still says which text it is.
    chunks = [_chunk(VERSIONS[0], "(a)", "(a) only")]
    assert merge_variants(chunks) == [f"({VERSIONS[0]})\n(a) only"]


def test_a_letter_a_version_repeats_is_kept_both_times() -> None:
    # Section 9-219 letters two lists; the repeat is keyed a_2 and says (a).
    chunks = [
        _chunk(None, "(a)", "(a) service list two", key="a_2"),
        _chunk(None, "(a)", "(a) service list one", key="a"),
    ]
    assert merge_variants(chunks) == ["(a) service list one", "(a) service list two"]


def test_the_evidence_drawer_reads_the_same_merged_text() -> None:
    section = assemble_section("1-160", _one_sixty())
    assert section is not None
    assert section.text.count("(a) The provisions of this Section apply.") == 1
    assert "(g) applies if fire fighter" in section.text
