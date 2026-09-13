"""Which sections verifiably answered a question like this one before.

Deliberately not called learning: nothing here changes what the model believes
about the law. It remembers only that an answer resting on these sections
**passed verification**, and offers them back as a hint.

Two safety properties, and they are the whole design:

- **Only a verified answer is remembered.** The guardrail's pass is the label,
  so nothing enters on the strength of the model's own opinion, and a replaced
  answer teaches the system nothing.
- **A hint is enrichment on a search result, never a filter.** A section that
  once got lucky must not be able to entrench itself by suppressing the search
  that would have found a better one.

Offline: the embedder and the database are faked.
"""

from __future__ import annotations

from pension_chat.agent import memory
from pension_chat.agent.guardrails import GuardrailReport


def _passed() -> GuardrailReport:
    report = GuardrailReport()
    report.verified_citations = ["11-134", "11-216"]
    return report


def _failed() -> GuardrailReport:
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "cited 11-124, never read it")
    report.verified_citations = ["11-134"]
    return report


def _state(**over):
    base = {
        "session_id": "s-1",
        "question": "can I retire early without losing too much?",
        "coverage": {"article": "11", "basis": "record"},
        "member_facts": {"tier": 1},
        "retrieved_sections": [{"section": "11-134"}, {"section": "11-216"}],
    }
    base.update(over)
    return base


def _writes(monkeypatch):
    rows: list[dict] = []

    async def fake_write(**row):
        rows.append(row)

    async def fake_embed(text: str):
        return [float(len(text)), 1.0, 0.0]

    monkeypatch.setattr(memory, "_write", fake_write)
    monkeypatch.setattr(memory, "embed", fake_embed)
    return rows


async def test_only_a_verified_answer_is_remembered(monkeypatch):
    rows = _writes(monkeypatch)
    await memory.remember(_state(), _passed())
    assert rows and rows[0]["sections"] == ["11-134", "11-216"]
    assert rows[0]["article"] == "11"
    assert rows[0]["tier"] == 1


async def test_a_replaced_answer_teaches_nothing(monkeypatch):
    """The guardrail's pass is the label. An answer it threw away is a labelled
    example of the wrong thing, and it goes to `capture`, not here."""
    rows = _writes(monkeypatch)
    await memory.remember(_state(), _failed())
    assert rows == []


async def test_an_answer_that_cited_nothing_is_not_remembered(monkeypatch):
    """Passing with no citations is passing with nothing to remember -- an
    answer with no citations has nothing for the verifier to reject either."""
    rows = _writes(monkeypatch)
    report = GuardrailReport()
    await memory.remember(_state(), report)
    assert rows == []


async def test_the_member_is_stripped_out_of_the_concept(monkeypatch):
    """The key is what was asked, not who asked it. "I'm 56 with 21.5 years and
    $69,956.40" embeds a great deal of one member and very little question."""
    rows = _writes(monkeypatch)
    await memory.remember(
        _state(question="I'm 56 with 21.5 years and $69,956.40 -- what do I get at 60?"),
        _passed(),
    )
    concept = rows[0]["concept"]
    assert "56" not in concept and "69,956.40" not in concept
    assert "what do i get at" in concept.lower()


async def test_coverage_determinations_ride_along(monkeypatch):
    """"Employee of a board of park commissioners" -> Article 12 is a statutory
    test, not a fact about a person, so it generalises where a figure does not."""
    rows = _writes(monkeypatch)
    await memory.remember(
        _state(
            coverage={
                "article": "12",
                "basis": "member_confirmed",
                "described_as": "an employee of a board of park commissioners",
            }
        ),
        _passed(),
    )
    concepts = [r["concept"] for r in rows]
    assert any(c.startswith("coverage: an employee of a board") for c in concepts)


async def test_an_unsettled_coverage_is_not_remembered(monkeypatch):
    """Only a determination the member confirmed or the Code matched. A record
    basis is the file's opinion, which the corpus never agreed with."""
    rows = _writes(monkeypatch)
    await memory.remember(
        _state(coverage={"article": "11", "basis": "record", "described_as": "a park employee"}),
        _passed(),
    )
    assert not any(r["concept"].startswith("coverage:") for r in rows)


# ── Recall ────────────────────────────────────────────────────────


def _rows(monkeypatch, stored):
    async def fake_read(article, tier):
        return list(stored)

    async def fake_embed(text: str):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(memory, "_read", fake_read)
    monkeypatch.setattr(memory, "embed", fake_embed)


async def test_recall_returns_what_answered_a_similar_question(monkeypatch):
    _rows(
        monkeypatch,
        [
            {"concept": "early retirement reduction", "embedding": [1.0, 0.0, 0.0],
             "sections": ["11-134", "11-216"]},
            {"concept": "widow's annuity", "embedding": [0.0, 1.0, 0.0],
             "sections": ["11-145"]},
        ],
    )
    assert await memory.recall("retiring early", article="11", tier=1) == ["11-134", "11-216"]


async def test_a_distant_question_recalls_nothing(monkeypatch):
    """Below the threshold there is no hint, and no hint is the right answer --
    a bad one costs a search's worth of attention on the wrong sections."""
    _rows(
        monkeypatch,
        [{"concept": "widow's annuity", "embedding": [0.0, 1.0, 0.0], "sections": ["11-145"]}],
    )
    assert await memory.recall("retiring early", article="11", tier=1) == []


async def test_recall_never_raises(monkeypatch):
    """A memory that fails must cost a hint, never the turn."""

    async def boom(text):
        raise RuntimeError("embedding deployment is down")

    monkeypatch.setattr(memory, "embed", boom)
    assert await memory.recall("anything", article="11", tier=1) == []


async def test_remember_never_raises(monkeypatch):
    async def boom(**row):
        raise RuntimeError("table is missing")

    async def fake_embed(text):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(memory, "_write", boom)
    monkeypatch.setattr(memory, "embed", fake_embed)
    await memory.remember(_state(), _passed())  # must not raise


async def test_a_section_reference_survives_the_stripper(monkeypatch):
    """Found by reading what it had actually stored.

    "What does Section 11-134 say about the annuity formula?" was remembered as
    "What does Section - say about the annuity formula" -- the particulars
    stripper ate the section number, which is the single most matchable thing
    in that question. Ages and salaries are about a person; a section reference
    is about the law.
    """
    assert (
        memory.concept_for("What does Section 11-134 say about the annuity formula?")
        == "What does Section 11-134 say about the annuity formula"
    )
    # "Tier 2" loses its digit and that is the right trade: tier is a column
    # here and is filtered on in SQL, so it does not need to survive in prose.
    assert "1-160" in memory.concept_for("is 1-160 the Tier 2 rule?")
    assert "11-134.2" in memory.concept_for("does 11-134.2 apply to me?")


async def test_the_asker_is_still_stripped(monkeypatch):
    concept = memory.concept_for("I'm 56 with 21.5 years and $69,956.40 -- what at 60?")
    assert "56" not in concept and "69,956.40" not in concept


async def test_an_answer_about_no_particular_article_is_not_remembered(monkeypatch):
    """Also found in the table: a gap-admitting answer about Article 22A was
    stored against `article: None` carrying sections from Articles 1, 15 and 16.

    A memory keyed to no Article is a hint for everybody and evidence for
    nobody -- and the one case that reliably produces it is an answer that
    could not place the question in the first place.
    """
    rows = _writes(monkeypatch)
    await memory.remember(_state(coverage={}), _passed())
    assert rows == []
