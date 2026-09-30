"""Who a conversation is about: the people chosen in it, and who each turn concerns (spec §4).

Pure and deterministic -- no model and no database. The people are the
conversation's own list (`sessions.subjects`, most recent first, at most
MAX_PEOPLE); each is keyed by an internal MEAB ID (`ref`) that is never shown.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
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


# A singular personal pronoun: after a turn about several people, it is asked about.
_SINGULAR = re.compile(r"\b(he|she|him|her|his|hers)\b", re.IGNORECASE)


@dataclass
class TurnPeople:
    people: list[str] = field(default_factory=list)                # refs this turn is about
    unfamiliar: list[tuple[str, str]] = field(default_factory=list)  # names to offer a choice for
    which_of: list[str] = field(default_factory=list)              # refs to ask "which of them?"


def decide(question: str, subjects: list[dict[str, Any]], last_people: list[str],
           directory_names: list[tuple[str, str]]) -> TurnPeople:
    """Spec §4.2. Familiar names first; an unfamiliar name is a choice; no name
    continues with the previous turn's people; a singular pronoun after a turn
    about several is asked about; otherwise the most recent person, or nobody."""
    from pension_chat.pbms.names import NameDirectory

    roster_refs = [s["ref"] for s in subjects]
    by_name: dict[str, list[str]] = {}
    for s in subjects:
        by_name.setdefault(norm_name(f"{s.get('first_name') or ''} {s.get('last_name') or ''}"), []).append(s["ref"])

    roster_dir = NameDirectory((s.get("first_name") or "", s.get("last_name") or "") for s in subjects)
    familiar: list[str] = []
    for first, last in roster_dir.names_in(question):
        refs = by_name.get(norm_name(f"{first} {last}"), [])
        if len(refs) > 1:
            return TurnPeople(which_of=refs)
        familiar += [r for r in refs if r not in familiar]
    known = set(by_name)
    unfamiliar = [(f, la) for f, la in directory_names if norm_name(f"{f} {la}") not in known]

    if unfamiliar:
        return TurnPeople(people=familiar[:MAX_PEOPLE], unfamiliar=unfamiliar[:MAX_PEOPLE])
    if familiar:
        return TurnPeople(people=familiar[:MAX_PEOPLE])
    continuing = [r for r in last_people if r in roster_refs]
    if continuing:
        if len(continuing) > 1 and _SINGULAR.search(question):
            return TurnPeople(which_of=continuing)
        return TurnPeople(people=continuing[:MAX_PEOPLE])
    return TurnPeople(people=roster_refs[:1])
