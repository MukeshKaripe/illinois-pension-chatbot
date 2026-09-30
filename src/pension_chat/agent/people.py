"""Who a conversation is about: the people chosen in it, and who each turn concerns (spec §4).

Pure and deterministic -- no model and no database. The people are the
conversation's own list (`sessions.subjects`, most recent first, at most
MAX_PEOPLE); each is keyed by an internal MEAB ID (`ref`) that is never shown.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

MAX_PEOPLE = 4
# More candidates than this and a choice starts with the department.
MAX_LISTED = 12


def norm_name(text: str | None) -> str:
    return " ".join((text or "").split()).upper()


def labels(subjects: list[dict[str, Any]]) -> dict[str, str]:
    """How each person is named to the model and in tables: their name, plus the
    department only when two people in context share that name."""
    counts = Counter(norm_name(s.get("display_name")) for s in subjects)
    out: dict[str, str] = {}
    for s in subjects:
        name = s.get("display_name") or "Employee"
        if counts[norm_name(name)] > 1:
            name = f"{name} ({s.get('department') or s.get('employer') or 'no department on file'})"
        out[s["ref"]] = name
    return out
