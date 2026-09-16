"""What kind of gap this is, which decides what to do about it.

``coverage.py`` already draws this line for *conflicts*: "a conflict in a
routing field (``article_number``, ``employment_status``) is a question; a
conflict in a value (salary, service) stays an override-with-note". An absence
is the same distinction with nothing on one side, so it gets the same answer.

``tier`` joins the routing fields here because it keys the recipe registry --
``registry.lookup_recipe(formula_type, article, tier)`` -- so an unknown tier is
not a missing number, it is two different provisions. Filling it in silently
would answer a question about a member who may not exist, with a real citation
on it.

The list is short and closed, and everything else is a value. Treating an
unrecognised input as governing would turn every new recipe input into a
question the member has to answer before they can be told anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: Fields that decide *which* provision applies rather than what it computes.
#: Every one of these is also in `assumption_policy.NEVER_ASSUMABLE`, and
#: `tests/test_gaps.py` holds the two tables together: a field that routes and
#: is assumable would be filled in before anyone thought to ask about it.
GOVERNING = frozenset({"article_number", "employment_status", "tier"})

VALUE = "value"
GOVERNING_GAP = "governing"


def classify(name: str) -> str:
    """Whether this gap changes the law that applies, or only the number."""
    return GOVERNING_GAP if name in GOVERNING else VALUE


def split(
    missing: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``missing`` in two lists, governing first, each keeping its own order.

    Copies each entry rather than aliasing it: the caller annotates these for
    the answer, and mutating in place would rewrite what ``missing_inputs``
    reports to everything else reading the same state.
    """
    governing: list[dict[str, Any]] = []
    value: list[dict[str, Any]] = []
    for item in missing:
        target = (
            governing
            if classify(str(item.get("name") or "")) == GOVERNING_GAP
            else value
        )
        target.append(dict(item))
    return governing, value
