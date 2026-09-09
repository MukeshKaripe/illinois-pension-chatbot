"""The parts of an answer that Python renders rather than the model writing.

The worked-steps table, the comparison table, the assumptions block and the
legal-basis footer are pure functions of what the interpreter produced and what
the sheet holds. Asking a model to transcribe them was always a small,
unnecessary risk -- and it cost a whole absolute rule in the answer prompt
insisting that transcription is not calculation.

The architecture's rule gains one clause. The LLM plans and writes, code
calculates -- and code formats what it calculated.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from pension_chat.agent.presentation import STATUS_LABELS
from pension_chat.agent.worksheet import WorkSheet

#: Placeholders the model emits where a computed block belongs.
MARKERS = {
    "WORKED_STEPS": "the worked-steps table",
    "COMPARISON": "the comparison table",
    "ASSUMPTIONS": "the assumptions block",
    "BASIS": "the legal-basis footer",
    "STATUS": "the status line",
}

_MARKER_RE = re.compile(r"<<<([A-Z_]{1,32})>>>")
#: Longest possible marker text, so a marker split across deltas is never missed.
_MAX_MARKER = len("<<<") + 32 + len(">>>")

_LABELS = {
    "final_average_salary": "Highest average salary (best 4 years)",
    "highest_average_annual_salary_4yr": "Highest average salary (best 4 years)",
    "service_years": "Years of service",
    "years_of_service": "Years of service",
    "creditable_service_years": "Years of service",
    "annual_rate": "Annual rate",
    "age": "Age",
}

#: Field names that should be rendered as plain quantities, not as money.
_QUANTITY_FIELDS = {
    "service_years",
    "years_of_service",
    "creditable_service_years",
    "age",
    "months_before_age_60",
    "months_of_service",
    "months",
}


def _money(value: Any) -> str:
    try:
        return f"${Decimal(str(value)):,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


def _amount(value: Any, field_name: str | None = None) -> str:
    """Money, a percentage or a plain quantity, formatted as it should read.

    Args:
        value: The value to format.
        field_name: The field name, used to determine if this is a quantity
                   (not money). If None, guess based on the value's shape.
    """
    text = str(value).strip()
    if text.endswith("%"):
        # Already a percentage, positive or negative. Leave it alone.
        return text
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return text

    # If this is a known quantity field, format as a plain number.
    if field_name in _QUANTITY_FIELDS:
        return f"{number.normalize():f}"

    # Otherwise, use the heuristic: small whole numbers are quantities,
    # others are money.
    if number == number.to_integral_value() and abs(number) < 100:
        return f"{number.normalize():f}"
    return _money(number)


def _label(name: str) -> str:
    return _LABELS.get(name, name.replace("_", " ").capitalize())


def render_worked_steps(result: Mapping[str, Any]) -> str:
    """The two-column table showing how one figure was reached."""
    rows: list[tuple[str, str]] = []
    for name, value in (result.get("inputs_used") or {}).items():
        rows.append((_label(name), _amount(value, name)))
    for name, value in (result.get("statutory_values_used") or {}).items():
        rows.append((_label(name), _amount(value, name)))
    for step in result.get("steps") or []:
        # Steps have labels, not field names, so don't pass a field_name.
        # The value's shape (percentage, decimal, etc.) determines the format.
        rows.append((str(step.get("label") or ""), _amount(step.get("amount"))))

    if annual := result.get("annual_amount"):
        rows.append(("**Annual pension**", f"**{_money(annual)}**"))
    if monthly := result.get("monthly_amount"):
        rows.append(("**Monthly pension**", f"**{_money(monthly)}**"))

    body = "\n".join(f"| {label} | {amount} |" for label, amount in rows)
    return f"**How that is worked out**\n\n| Step | Amount |\n|---|---:|\n{body}"


def render_comparison(options: Sequence[Mapping[str, Any]]) -> str:
    """One row per retirement date, soonest first."""
    header = (
        "| If you retire | Years of service | Annual pension | Monthly pension |\n"
        "|---|---:|---:|---:|"
    )
    rows = [
        f"| {option.get('label', '')} | {option.get('service_years', '')} | "
        f"{_money(option.get('annual'))} | {_money(option.get('monthly'))} |"
        for option in options
    ]
    return "\n".join([header, *rows])


def render_assumptions(sheet: WorkSheet) -> str:
    """What was filled in, and on whose say-so. Empty when nothing was."""
    assumed = sheet.assumed_facts()
    if not assumed:
        return ""
    lines = [f"- {fact.basis or fact.name}" for fact in assumed.values()]
    return "**What I assumed**\n\n" + "\n".join(lines)


def render_basis(citations: Sequence[str]) -> str:
    """The provisions the answer rested on, once, at the end."""
    if not citations:
        return ""
    joined = " · ".join(f"[40 ILCS 5/{section}]" for section in citations)
    return f"**Legal basis** {joined}"


def render_status(rung: int) -> str:
    return f"**{STATUS_LABELS.get(rung, '')}**"


class MarkerSubstituter:
    """Swap computed blocks into a token stream as it passes.

    Deltas arrive in arbitrary pieces, so a marker can be split across two of
    them. The substituter holds back the last few characters until it can be
    sure they are not the start of one.
    """

    def __init__(self, blocks: Mapping[str, str]) -> None:
        self._blocks = dict(blocks)
        self._used: set[str] = set()
        self._buffer = ""

    def feed(self, delta: str) -> str:
        self._buffer += delta
        out, self._buffer = self._consume(self._buffer, hold_tail=True)
        return out

    def flush(self) -> str:
        out, self._buffer = self._consume(self._buffer, hold_tail=False)
        return out

    def unused(self) -> list[str]:
        """Blocks whose marker never arrived, so the caller can append them."""
        return [
            block for name, block in self._blocks.items()
            if name not in self._used and block
        ]

    def _consume(self, text: str, *, hold_tail: bool) -> tuple[str, str]:
        out: list[str] = []
        position = 0
        for match in _MARKER_RE.finditer(text):
            out.append(text[position : match.start()])
            name = match.group(1)
            if name in self._blocks and name not in self._used:
                out.append(self._blocks[name])
                self._used.add(name)
            # A duplicated marker, or one naming no block, is dropped rather
            # than shown to the member.
            position = match.end()

        remainder = text[position:]
        if hold_tail:
            keep = min(len(remainder), _MAX_MARKER)
            cut = len(remainder) - keep
            return "".join(out) + remainder[:cut], remainder[cut:]
        return "".join(out) + remainder, ""
