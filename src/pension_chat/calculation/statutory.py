"""Values the Code states, offered as answers to questions about the member.

A recipe's ``parameters`` are already the Code's own numbers with the section
that states each one: ``derivation.py`` refuses a constant with no citation
("every numeric constant carries a citation"), and ``validator._check_citations``
refuses a citation to a section nobody retrieved. They are persisted with the
recipe in ``formula_registry.rule_specification`` and carry its whole lifecycle.

So the registry of statutory parameters already exists. What it could not do is
answer a question *about a member*: a parameter was usable only inside its own
recipe's calculation tree, so an Article that states the age for a full annuity
still left the agent saying it could not establish a retirement age. This module
does the one missing thing -- decides which parameters are also member facts, so
a gap can be filled from the statute instead of blocking a figure or being
guessed at.

Listed rather than pattern-matched, for the reason ``thresholds._BOUNDARY_FACTS``
gives: "maximum_salary_percentage" looks exactly like a member fact to any rule
general enough to catch the real ones. A rate, a cap or a percentage is the
recipe's own arithmetic and is never offered here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Recipe parameter name -> the member fact it may supply. Every value here
#: must be absent from `assumption_policy.NEVER_ASSUMABLE`, which
#: `tests/test_statutory_parameters.py` enforces: the Code states rates,
#: thresholds and periods, and it does not contain anybody's salary or service.
FILLS: dict[str, str] = {
    "age_60": "age_at_annuity_start",
    "age_62": "age_at_annuity_start",
    "age_65": "age_at_annuity_start",
    "age_67": "age_at_annuity_start",
    "minimum_age": "age_at_annuity_start",
    "minimum_retirement_age": "age_at_annuity_start",
    "normal_retirement_age": "age_at_annuity_start",
}


@dataclass(frozen=True)
class StatutoryFill:
    """One value the Code states, and where the member can go and read it."""

    name: str
    value: Any
    parameter: str
    section: str | None
    subsection: str | None

    @property
    def basis(self) -> str:
        """Prose for the answer's block, naming the section rather than hedging."""
        where = f"Section {self.section}" if self.section else "the Code"
        if self.subsection:
            where += self.subsection
        return f"the age this Article sets for a full annuity, stated in {where}"


def fills_from(recipes: dict[str, dict]) -> dict[str, StatutoryFill]:
    """Member facts the resolved recipes' own cited parameters can supply.

    First one wins, so which section the answer cites does not depend on the
    order the recipes happen to be keyed in.
    """
    found: dict[str, StatutoryFill] = {}
    for recipe in (recipes or {}).values():
        parameters = ((recipe or {}).get("spec") or {}).get("parameters") or {}
        for parameter, spec in parameters.items():
            name = FILLS.get(parameter)
            if name is None or name in found:
                continue
            value = (spec or {}).get("value")
            source = (spec or {}).get("source") or {}
            section = source.get("section")
            if value is None or not section:
                # Uncited, so the member cannot check it. A value they cannot
                # check is exactly what this rank exists to be better than, so
                # it falls through to the assumption policy rather than being
                # dressed up as the Code speaking.
                continue
            found[name] = StatutoryFill(
                name=name,
                value=value,
                parameter=parameter,
                section=section,
                subsection=source.get("subsection"),
            )
    return found
