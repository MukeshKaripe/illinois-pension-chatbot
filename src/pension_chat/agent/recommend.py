"""Whether a recommendation follows from the figures it rests on.

Spec §11.1: "A model saying 'I recommend 58 because it gives you the highest
pension' against computed values of 58 -> $2,850 and 60 -> $3,240 is rejected by
arithmetic, not by style."

Style cannot catch that sentence. It is well formed, it names a real option, and
both figures trace to engine output -- so `guardrails.verify_answer` passes it.
The only thing wrong with it is that it is false, and the only way to know is to
compare the numbers.

Nothing here prices anything. The candidates are `scenario_set`, which
`compute_benefit` filled and the checkpointer carries across turns, so every
figure this reasons about was produced by the interpreter and is already
provenanced. This layer only decides whether the choice follows.

Every refusal says what *would* be right, so the model can correct itself while
it still has the turn rather than guessing at what the check wanted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from pension_chat.agent.objectives import EXCLUDED, objective_for

#: Below this there is nothing to choose between, and calling the answer a
#: recommendation implies a comparison nobody made.
MIN_CANDIDATES = 2


@dataclass(frozen=True)
class Verdict:
    """Whether the recommendation stands, and what it was judged against."""

    ok: bool
    reason: str
    choice: str
    objective: str
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)


def _money(raw: Any) -> str:
    try:
        value = Decimal(str(raw).replace(",", "").replace("$", ""))
    except (InvalidOperation, ValueError):
        return str(raw)
    return f"${value:,.2f}"


def _better(
    objective_name: str, candidates: Mapping[str, Mapping[str, Any]]
) -> tuple[str, Mapping[str, Any]] | None:
    """A candidate that does satisfy the objective, for the refusal to name."""
    objective = objective_for(objective_name)
    if objective is None:
        return None
    for label, entry in candidates.items():
        if objective.satisfied_by(label, candidates):
            return label, entry
    return None


def check(
    *,
    choice: str,
    objective: str,
    candidates: Mapping[str, Mapping[str, Any]],
) -> Verdict:
    """Does recommending ``choice`` for ``objective`` follow from ``candidates``?

    Refused in order, each with a reason the model can act on rather than
    argue with:

    * an objective the Code cannot evaluate -- named as excluded where it is one
      of the things a member really does weigh, because "the Code does not say"
      is a true and useful answer and "unrecognised" is not;
    * fewer than two candidates;
    * a choice nobody computed;
    * a choice the objective's own predicate rejects, named beside the option
      that does satisfy it and its figure.
    """
    held = {label: dict(entry) for label, entry in (candidates or {}).items()}

    def verdict(ok: bool, reason: str) -> Verdict:
        """One shape for every exit, so a refusal always carries what it judged."""
        return Verdict(
            ok=ok,
            reason=reason,
            choice=choice,
            objective=objective,
            candidates=held,
        )

    if (excluded := EXCLUDED.get(objective.strip().lower())) is not None:
        return verdict(
            False,
            f"{objective} is not in the Pension Code: {excluded}. Say so plainly "
            "and recommend on something the Code does settle, or leave the "
            "choice with the member.",
        )

    wanted = objective_for(objective)
    if wanted is None:
        return verdict(
            False,
            f"{objective!r} is not an objective this can check. A recommendation "
            "nobody can check against the figures is the thing this refuses.",
        )

    if len(held) < MIN_CANDIDATES:
        return verdict(
            False,
            "There is one option, so there is nothing to recommend between. "
            "Give the answer; do not present it as a choice.",
        )

    if choice not in held:
        return verdict(
            False,
            f"{choice!r} is not among the options that were computed "
            f"({', '.join(sorted(held))}). Compute it first, or recommend one "
            "of these.",
        )

    if not wanted.satisfied_by(choice, held):
        if best := _better(objective, held):
            label, entry = best
            figure = entry.get(wanted.reads) if wanted.reads else None
            detail = f" ({_money(figure)})" if figure is not None else ""
            return verdict(
                False,
                f"{choice!r} does not give {wanted.prose}. {label!r}{detail} "
                "does. Recommend that, or recommend on a different objective.",
            )
        return verdict(
            False,
            f"No option gives {wanted.prose} -- at least one of them has no "
            "figure, so they cannot be compared. Compute the missing one first.",
        )

    return verdict(True, "")
