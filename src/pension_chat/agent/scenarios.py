"""Retirement-timing scenarios, found without a model call.

Two of these three functions exist because a model cannot do their job, not
because a model is expensive.

``extract_scenarios`` resolves *offsets*. Turn understanding runs concurrently
with the record fetch, so it does not know the member's age and cannot turn
"wait 2 more years" into an absolute retirement age -- it returns a null age and
the scenario dies. The offset is detected here instead and deliberately survives
unresolved until `_resolve_retirement_age` adds it to the member's real age at
execution time.

``_corroborated`` guards against the opposite failure. Whatever plans a turn is
shown the conversation so far, and that context leaks: scenarios from an earlier
turn come back attached to a question that did not ask for them. A generative
planner has more scope for this than the regex one it replaced, not less, so the
guard outlived the planner it was written for.
"""

from __future__ import annotations

import re
from typing import Any

Scenario = dict[str, Any]
#: What makes two scenarios the same one: a kind and the value it names.
ScenarioKey = tuple[str, Any]

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_COUNT = r"(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"

# Relative offsets: "wait 2 more years", "in three years", "5 years from now".
_OFFSET_PATTERNS = [
    re.compile(rf"\b(?:wait(?:ing)?|after|in)\s+(?:another\s+)?{_COUNT}\s+(?:more\s+)?years?\b", re.I),
    re.compile(rf"\b{_COUNT}\s+(?:more\s+)?years?\s+from\s+now\b", re.I),
]
# Absolute ages: "at age 60", "when I reach 62", "at 57, at 59 and at 60".
# A list of ages in one breath: "at ages 57, 59 and 60". Written as separate
# single-age patterns this matched nothing at all (the plural "ages" was not
# covered) and, once it was, matched only the first -- so a member nine turns
# into a comparison asking for three more got none of them.
_AGE_LIST = re.compile(
    r"\bages?\s+(\d{2}(?:\s*(?:,|and|or|/|through|to)\s*(?:ages?\s+)?\d{2})+)", re.I
)

_AGE_PATTERNS = [
    re.compile(r"\b(?:at|until|to|reach(?:ing|es)?|turn(?:ing|s)?)\s+ages?\s+(\d{2})\b", re.I),
    re.compile(r"\b(?:reach(?:ing|es)?|turn(?:ing|s)?)\s+(\d{2})\b", re.I),
    re.compile(r"\bat\s+(\d{2})\b", re.I),
]
_RETIRE_NOW = re.compile(r"\b(?:retire\s+now|right\s+now|retire\s+today|if\s+i\s+retire\s+now)\b", re.I)

# Ages outside this band are not retirement ages — the guard stops "at 21.5
# years of service" or a dollar figure being read as one.
_PLAUSIBLE_AGE = range(45, 76)

# The planner is shown the conversation so far, because a follow-up question is
# often unreadable without it. That context leaked: after a turn asking "what if
# I waited three more years", the NEXT question — about a claimed 50%-of-salary
# formula, with no timing request in it — still came back with a three-year
# comparison the member had not asked for again.
#
# A word-level "does this mention timing" gate does not fix it: "21.5 years of
# service" is not a request to compare retirement dates, and neither is "56
# years old". What distinguishes a live scenario from a remembered one is
# whether its target appears in the message in front of us, so that is what is
# checked. A planner scenario survives only if the current message names the age
# or the offset it is about.
_NOW_CUE = re.compile(r"\b(?:now|today|immediately|currently|right\s+away)\b", re.I)


def corroborated(scenario: Scenario, question: str) -> bool:
    """True if the current message names the age or offset this scenario is for."""
    age = scenario.get("retirement_age")
    offset = scenario.get("years_from_now")

    if offset == 0 or (age is None and offset is None and _NOW_CUE.search(question)):
        return bool(_RETIRE_NOW.search(question) or _NOW_CUE.search(question))

    target = age if age is not None else offset
    if target is None:
        # Nothing concrete to corroborate. Kept only when the message is clearly
        # asking about timing at all, so it can be reported as unresolvable
        # rather than silently answered or silently dropped.
        return bool(_NOW_CUE.search(question)) or any(
            p.search(question) for p in _OFFSET_PATTERNS + _AGE_PATTERNS
        )

    word = next((w for w, n in _NUMBER_WORDS.items() if n == target), None)
    # The number must stand alone. Excluding a preceding "$" or digit keeps
    # "$72,000" from corroborating a retirement age of 72, and "(?!,\d)" rejects
    # thousands grouping without rejecting the comma in "at 57, at 59 and at 60".
    digits = re.compile(rf"(?<![\d.$]){target}(?![\d.]|,\d)")
    return bool(digits.search(question)) or (
        word is not None and re.search(rf"\b{word}\b", question, re.I) is not None
    )


def _as_count(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def extract_scenarios(question: str) -> list[Scenario]:
    """Find retirement-timing scenarios in the question, without a model call.

    The planner can also emit scenarios, but it runs concurrently with the
    record fetch and therefore does not know the member's age — so it cannot
    turn "wait 2 more years" into an absolute retirement age and returns null.
    Detecting the offset here, and resolving it against the real age at
    execution time, removes the model from a step it has no data for.
    """
    text = question or ""
    scenarios: list[Scenario] = []
    seen: set[ScenarioKey] = set()

    def add(entry: Scenario, key: ScenarioKey) -> None:
        if key not in seen:
            seen.add(key)
            scenarios.append(entry)

    if _RETIRE_NOW.search(text):
        add({"label": "retire now", "years_from_now": 0}, ("offset", 0))

    for pattern in _OFFSET_PATTERNS:
        for match in pattern.finditer(text):
            count = _as_count(match.group(1))
            if count is not None and 0 < count <= 40:
                label = "in 1 year" if count == 1 else f"in {count} years"
                add({"label": label, "years_from_now": count}, ("offset", count))

    for match in _AGE_LIST.finditer(text):
        for value in re.findall(r"\d{2}", match.group(1)):
            age = int(value)
            if age in _PLAUSIBLE_AGE:
                add({"label": f"at age {age}", "retirement_age": age}, ("age", age))

    for pattern in _AGE_PATTERNS:
        for match in pattern.finditer(text):
            age = int(match.group(1))
            if age in _PLAUSIBLE_AGE:
                add({"label": f"at age {age}", "retirement_age": age}, ("age", age))

    return scenarios


def merge_scenarios(
    deterministic: list[Scenario], planned: list[Scenario]
) -> list[Scenario]:
    """Union the regex-found scenarios with the planner's, preferring resolved ones."""
    merged: list[Scenario] = []
    seen: set[ScenarioKey] = set()

    for entry in list(deterministic) + list(planned):
        if not isinstance(entry, dict):
            continue
        age = entry.get("retirement_age")
        offset = entry.get("years_from_now")
        if age is None and offset is None:
            # An unresolvable planner scenario ("in 2 years", age null) is kept
            # only if nothing already covers it, so it can be reported rather
            # than silently dropped.
            key: ScenarioKey = ("unresolved", str(entry.get("label", "")).lower())
        else:
            key = ("age", age) if age is not None else ("offset", offset)
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)

    # A resolvable scenario supersedes an unresolvable one describing the same
    # thing, so drop the leftovers if anything concrete was found.
    concrete = [s for s in merged if s.get("retirement_age") is not None or s.get("years_from_now") is not None]
    return concrete if concrete else merged
