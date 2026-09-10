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

from pension_chat.agent.facts import aliases_of
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


def _parameter_amount(name: str, entry: Any) -> str:
    """One statutory constant, as the statute states it.

    A parameter arrives either bare or as ``{"value": ..., "source": {...}}`` --
    the interpreter attaches the section each constant was read from, and
    `_present` carries that through. Reading the wrapper as the value printed
    the whole dict into the table.

    A rate or cap is stored as the fraction the arithmetic uses (0.024, 0.0025,
    0.8) and read by a member as a percentage. Rendering the fraction as money
    turned Article 11's 2.40% accrual rate into "$0.02".
    """
    value = entry.get("value") if isinstance(entry, Mapping) else entry
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return _amount(value, name)
    if number != 0 and abs(number) < 1:
        return f"{number * 100:,.2f}%"
    return _amount(value, name)


#: A step whose name ends in one of these published a ratio, not an amount.
_FACTOR_SUFFIXES = ("_factor", "_fraction", "_ratio", "_rate", "_percentage")


def _step_amount(name: str, value: Any) -> str:
    """One named intermediate value, as the engine published it.

    Steps are keyed by the recipe's own ``result_name``, so there is no
    declared type to consult. A name ending in "_factor" is the one shape that
    would otherwise read as money: Article 11's ``age_reduction_factor`` of
    0.89 is a multiplier, not 89 cents.
    """
    if name.endswith(_FACTOR_SUFFIXES):
        try:
            return f"{Decimal(str(value)).normalize():f}"
        except (InvalidOperation, ValueError, TypeError):
            return str(value)
    return _amount(value, name)


def render_worked_steps(result: Mapping[str, Any]) -> str:
    """The two-column table showing how one figure was reached.

    Two shapes reach here and both are real. `nodes._present` renames the
    interpreter's ``parameters_used`` to ``statutory_values_used`` and rewrites
    each step as ``{"name", "value"}``; the interpreter's own output keeps
    ``parameters_used`` and adds ``operation``. Reading only for
    ``statutory_values_used`` entries as bare values, and for steps keyed
    ``label``/``amount``, printed a dict in one column and ``None`` in the
    other -- in the block whose whole purpose is that a table can never be
    mistranscribed.
    """
    rows: list[tuple[str, str]] = []
    for name, value in (result.get("inputs_used") or {}).items():
        rows.append((_label(name), _amount(value, name)))
    parameters = result.get("statutory_values_used")
    if parameters is None:
        parameters = result.get("parameters_used")
    for name, entry in (parameters or {}).items():
        rows.append((_label(name), _parameter_amount(name, entry)))
    for step in result.get("steps") or []:
        if step.get("name"):
            name = str(step["name"])
            label, value = _label(name), step.get("value")
        else:
            # A step carrying a prose label rather than a field name: use it as
            # written rather than putting it through the field-name formatter.
            name = label = str(step.get("label") or "")
            value = step.get("amount")
        rows.append((label, _step_amount(name, value)))

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
    """What was filled in, and on whose say-so. Empty when nothing was.

    One line per *quantity*, not per input name. `withdrawal_date` and
    `annuity_start_date` are one choice wearing two names -- the ask side
    already collapses them in `_dedupe_missing_inputs` -- and disclosing it
    twice reads as two separate guesses about two separate things.
    """
    assumed = sheet.assumed_facts()
    if not assumed:
        return ""
    lines: list[str] = []
    covered: set[str] = set()
    for name, fact in assumed.items():
        if name in covered:
            continue
        covered |= aliases_of(name)
        lines.append(f"- {fact.basis or fact.name}")
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
