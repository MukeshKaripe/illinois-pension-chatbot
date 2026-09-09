"""How much of an answer the evidence supports, and what shape it takes.

The system had two moves: state a figure, or say it could not. Everything
between them -- a figure resting on one stated assumption, the formula with the
one missing value shown in it, the law plus what is established -- did not
exist, and that gap is the whole of the "rigid" complaint.

The rung is chosen here, in Python, from the sheet. The model is then given one
shape rather than six, which is both less to get wrong and less to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pension_chat.agent.worksheet import WorkSheet

RUNG_CALCULATED = 1
RUNG_ASSUMED = 2
RUNG_ONE_MORE = 3
RUNG_PATHWAYS = 4
RUNG_ASK = 5

#: What the member is shown. The rung is the status: the badge cannot drift
#: from reality because nobody writes it, it names the branch that produced
#: the answer.
STATUS_LABELS: dict[int, str] = {
    RUNG_CALCULATED: "Calculated",
    RUNG_ASSUMED: "Calculated, with assumptions",
    RUNG_ONE_MORE: "Needs one more detail",
    RUNG_PATHWAYS: "More than one pathway",
    RUNG_ASK: "Needs information",
}


def _has_figure(state: dict[str, Any]) -> bool:
    for entry in (state.get("calculation_result") or {}).values():
        if isinstance(entry, dict) and (entry.get("base") or {}).get("ok"):
            return True
    return False


def select_rung(sheet: WorkSheet, state: dict[str, Any]) -> int:
    """The highest rung the evidence supports.

    Monotonicity is defined over input resolution only, not over changes to
    candidate_articles. Coverage ambiguity and evidence completeness are
    different axes and cannot be totally ordered: when there is more than one
    candidate Article and two or more missing inputs, some transition must
    worsen whichever way the branches are arranged. Pinning the invariant to
    the input axis is the honest reading of "adding a fact never selects a
    lower rung", and it is the axis the design describes.
    """
    if _has_figure(state):
        return RUNG_ASSUMED if sheet.assumed_facts() else RUNG_CALCULATED

    if len(state.get("candidate_articles") or []) > 1:
        return RUNG_PATHWAYS
    if len(state.get("missing_inputs") or []) >= 2:
        return RUNG_ASK
    return RUNG_ONE_MORE


def shape_for(sheet: WorkSheet, state: dict[str, Any]) -> str:
    """Which response template this turn uses.

    Three shapes carry no figure and so are not rungs: an explanation of the
    law, a comparison of dates, and an answer whose question was half outside
    the Pension Code.
    """
    if state.get("scenario_set"):
        return "comparison"
    if state.get("answerable_part"):
        return "partial"
    if _has_figure(state):
        return "figure"
    if not state.get("requires_calculation"):
        return "legal"
    return "clarify"


#: Slots Python renders. Everything else is prose the model writes.
COMPUTED_SLOTS = frozenset({"STATUS", "WORKED_STEPS", "COMPARISON", "ASSUMPTIONS", "BASIS"})


@dataclass(frozen=True)
class Template:
    """One response shape: what appears, in what order, and what to say in it."""

    shape: str
    slots: tuple[str, ...]
    guidance: str


TEMPLATES: dict[str, Template] = {
    "figure": Template(
        shape="figure",
        slots=("STATUS", "lede", "WORKED_STEPS", "why", "ASSUMPTIONS", "alternatives", "BASIS"),
        guidance=(
            "lede: the amount, annual first with monthly in brackets, and the "
            "circumstances it assumes. One or two sentences.\n"
            "why: why this provision governs, in the Code's own terms. Two "
            "sentences at most.\n"
            "alternatives: which other provisions were weighed and exactly why "
            "each is not the answer. Never write a provision off as inapplicable "
            "when it applies and simply cannot be put to a number."
        ),
    ),
    "comparison": Template(
        shape="comparison",
        slots=("STATUS", "lede", "COMPARISON", "what_changes", "ASSUMPTIONS", "BASIS"),
        guidance=(
            "lede: name the range -- the soonest option and the best one -- so a "
            "member who reads no further still has the answer. Never open with "
            "the table.\n"
            "what_changes: a sentence or two on what drives the difference, then "
            "one line on what the projection holds constant. Where the member "
            "asked what waiting gains them, each option already carries "
            "difference_from_now_annual and difference_from_now_monthly -- copy "
            "those. Never reach the figure by subtracting two options' amounts "
            "yourself; one turn did, wrote $4,466.56, and had the whole answer "
            "withdrawn."
        ),
    ),
    "legal": Template(
        shape="legal",
        slots=("lede", "detail", "BASIS"),
        guidance=(
            "lede: answer the question outright in plain English, before any "
            "subsection is named.\n"
            "detail: the conditions or consequences -- a short bullet list if "
            "there are more than two, prose otherwise. Three to six sentences."
        ),
    ),
    "partial": Template(
        shape="partial",
        slots=("lede", "detail", "outside", "BASIS"),
        guidance=(
            "lede: answer the part of the question the Pension Code covers.\n"
            "detail: the conditions that bear on it.\n"
            "outside: one line naming the part that is not in the Code and who "
            "does own it -- the fund, HR, the IRS. Never answer it yourself, and "
            "never guess at a tax or insurance rule."
        ),
    ),
    "clarify": Template(
        shape="clarify",
        slots=("STATUS", "lede", "established", "question", "BASIS"),
        guidance=(
            "lede: name what is unclear and why it matters to them. One sentence.\n"
            "established: what you already have, in one short sentence, so it is "
            "plain you read what they told you.\n"
            "question: the one or two things that would settle it, in the "
            "plainest form they take. Closed questions where the Code allows. "
            "Prose, never a table, and never more than two questions. Where "
            "something is missing, ask for it in the words of that item's own "
            "ask_for, never by the name the calculation uses for it -- asking "
            "for 'the number of full months your annuity begins before age 60' "
            "is asking the member to do the derivation themselves."
        ),
    ),
}


def template_for(shape: str) -> Template:
    return TEMPLATES.get(shape, TEMPLATES["legal"])
