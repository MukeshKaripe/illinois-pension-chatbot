"""Tests for how a provision that was *not* used gets described.

The recipe stored for the pilot recorded this, verbatim:

    "11-130(1): not used because it provides an annuity from accumulated
     employee and city contributions, but the supplied text does not provide the
     actuarial conversion factors needed to compute that annuity."

Three separate faults in one sentence.

It conflates "does not apply" with "cannot be computed". Section 11-130 does
reach a member who withdraws between 55 and 60 with ten years in; what is
missing is the mortality table the annuity conversion needs, which lives outside
the statute text. Reporting that as "does not control" tells the member the law
does not govern them, which is not true — and it lets the easy-to-calculate
provision win by default.

It presents a statutory election as a determination. Where the Code says an
employee "may elect to receive, in lieu of any other annuity provided in this
Section", picking the larger figure is a convenience on the member's behalf, not
a finding that the other formula is wrong. The member owns that choice and has
to be told they have it.

And it leaks. These strings are stored, reloaded and quoted to the member almost
verbatim, so "the supplied text" outlived an answer prompt that had been told
never to say "supplied" — the instruction cannot win against text handed to the
model as content.
"""

from __future__ import annotations

import pytest

from pension_chat.agent.derivation import (
    ALTERNATIVE_STATUSES,
    SELECTION_BASES,
    normalize_alternatives,
    plain_language,
)

LEGACY = (
    "11-130(1): not used because it provides an annuity from accumulated "
    "employee and city contributions, but the supplied text does not provide "
    "the actuarial conversion factors needed to compute that annuity."
)


# ── Machine vocabulary in text the member will read ───────────────

@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        ("the supplied text does not provide the factors", "supplied text"),
        ("not used on the supplied facts", "supplied facts"),
        ("the calculation engine applied the reduction", "engine"),
        ("the stored recipe was reused", "recipe"),
        ("absent from the retrieved text", "retrieved text"),
    ],
)
def test_internal_vocabulary_is_rewritten(raw, gone):
    assert gone not in plain_language(raw).lower()


def test_plain_language_keeps_the_substance():
    cleaned = plain_language(LEGACY)

    assert "supplied" not in cleaned.lower()
    assert "actuarial conversion factors" in cleaned
    assert "11-130(1)" in cleaned


# ── Typed dispositions ────────────────────────────────────────────

def test_the_four_dispositions_are_distinct():
    assert ALTERNATIVE_STATUSES == {
        "not_applicable",
        "applicable_not_calculable",
        "member_election",
        "superseded",
    }
    assert SELECTION_BASES == {
        "sole_applicable_provision",
        "statutory_election",
        "statutory_minimum",
    }


def test_a_typed_alternative_survives_intact():
    [record] = normalize_alternatives(
        [
            {
                "section": "11-130",
                "subsection": "(1)",
                "status": "applicable_not_calculable",
                "reason": "The Pension Code does not set out the conversion factors.",
            }
        ]
    )

    assert record["status"] == "applicable_not_calculable"
    assert record["section"] == "11-130"
    assert record["subsection"] == "(1)"


def test_an_unknown_status_is_reported_as_unknown_not_guessed():
    """Guessing here would recreate the exact bug being fixed.

    Defaulting an unrecognised status to "not_applicable" would turn a provision
    that merely could not be computed back into one that supposedly does not
    govern the member.
    """
    [record] = normalize_alternatives(
        [{"section": "11-130", "status": "not used", "reason": "..."}]
    )

    assert record["status"] == "unspecified"


def test_a_legacy_string_alternative_still_loads():
    # Recipes stored before alternatives were typed must not vanish from the
    # provenance on reload; they carry no status, and none is invented.
    [record] = normalize_alternatives([LEGACY])

    assert record["section"] == "11-130"
    assert record["status"] == "unspecified"
    assert "supplied" not in record["reason"].lower()


def test_a_subsection_is_stripped_from_the_section_id():
    # Section ids are pinned into retrieval, which keys on "11-130", not
    # "11-130(1)".
    [record] = normalize_alternatives(
        [{"section": "11-130(1)", "status": "not_applicable", "reason": "x"}]
    )

    assert record["section"] == "11-130"


@pytest.mark.parametrize("raw", [None, [], [123], ["", None]])
def test_malformed_alternatives_do_not_raise(raw):
    assert isinstance(normalize_alternatives(raw), list)


# ── Facts belonging to whoever the recipe was derived for ─────────

@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        # The exact reason that told a member who had just said they were 58
        # that their annuity begins at 56.
        (
            "This election requires the annuity to begin no earlier than age 60, "
            "while this annuity begins at age 56.",
            "This election requires the annuity to begin no earlier than age 60.",
        ),
        (
            "This provision applies to an employee who attains age 60 or more but "
            "less than age 65 in service, whereas the member withdraws at age 56.",
            "This provision applies to an employee who attains age 60 or more but "
            "less than age 65 in service.",
        ),
    ],
)
def test_the_contrast_clause_is_dropped_and_the_rule_kept(stored, expected):
    assert plain_language(stored) == expected


def test_a_rule_that_merely_mentions_ages_is_left_alone():
    """The scrub must not eat the statute.

    Trimming on "but" removed the upper bound from "age 60 or more but less
    than age 65" — a rule the member needs — so only "while" and "whereas"
    trigger it.
    """
    rule = (
        "The member may elect the alternative annuity using 1.80% for the first "
        "10 years, 2.00% for the next 10 years, and 2.20% for years over 20 "
        "through 30."
    )

    assert plain_language(rule) == rule


def test_a_reason_with_no_contrast_clause_is_unchanged_but_punctuated():
    assert plain_language("It cannot begin before age 60") == (
        "It cannot begin before age 60."
    )
