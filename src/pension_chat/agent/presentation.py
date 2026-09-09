"""How much of an answer the evidence supports, and what shape it takes.

The system had two moves: state a figure, or say it could not. Everything
between them -- a figure resting on one stated assumption, the formula with the
one missing value shown in it, the law plus what is established -- did not
exist, and that gap is the whole of the "rigid" complaint.

The rung is chosen here, in Python, from the sheet. The model is then given one
shape rather than six, which is both less to get wrong and less to read.
"""

from __future__ import annotations

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
