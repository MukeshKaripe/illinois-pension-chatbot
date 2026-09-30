"""Sections of the Code that apply only to some funds, or only on a condition.

Written by hand from each section's own applicability clause, never inferred:
which fund a provision reaches is the determination, and a regex over the text
is not one. Small on purpose -- a section is added here when the evals show its
wording misleading a derivation, with the statute words it rests on.

The two that did (2026-09-30): §1-161(g) and §1-162(g) both read "the amount of
the retirement annuity ... shall be computed by multiplying 1.25% for each year
of service credit", and a Tier 2 MEABF derivation took that rate two times in
three with §8-138's 2.4% in front of it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pension_chat.agent.retrieval import RetrievedSection


@dataclass(frozen=True)
class Scope:
    #: Articles whose members the section can reach; empty means any.
    articles: frozenset[int] = frozenset()
    #: A condition that must hold before it applies, as the statute states it.
    condition: str = ""
    #: The words of the section this rests on.
    basis: str = ""


SECTION_SCOPE: dict[str, Scope] = {
    "1-161": Scope(
        articles=frozenset({14, 15, 16}),
        basis='§1-161(a): "apply to a person who first becomes a member or a participant '
        'under Article 14, 15, or 16 on or after the implementation date"',
    ),
    "1-162": Scope(
        condition=(
            "the member's fund has been designated an affected pension fund by its unit of "
            "local government, and the member first became a member at least 6 months after "
            "that designation (or elected these benefits under subsection (c)). It is not the "
            "default Tier 2 rule; without that designation, Section 1-160 governs"
        ),
        basis='§1-162(a)-(b): "Affected pension fund" means a fund the governing body "has '
        'designated as an affected pension fund by adoption of a resolution or ordinance"',
    ),
}


def scoped_evidence(sections: list[RetrievedSection], article: int | str | None) -> list[RetrievedSection]:
    """The sections a derivation for `article` may read, conditions stated first.

    A section scoped to other funds is left out; a conditional one is kept with
    its condition at the top of its text, where the model reads it before the
    rate. An unknown Article changes nothing -- scoping needs a determination.
    """
    if article is None or str(article).strip() == "":
        return sections
    governing = int(str(article))
    kept: list[RetrievedSection] = []
    for section in sections:
        scope = SECTION_SCOPE.get(section.section)
        if scope is None:
            kept.append(section)
            continue
        if scope.articles and governing not in scope.articles:
            continue
        if scope.condition:
            section = replace(
                section,
                text=f"[Applies only if {scope.condition}.]\n{section.text}",
            )
        kept.append(section)
    return kept


def restricted_sources(source_sections: list[str] | None) -> list[str]:
    """Sources that make a recipe conditional, so it must not be saved fund-wide."""
    return [s for s in source_sections or [] if SECTION_SCOPE.get(s, Scope()).condition]
