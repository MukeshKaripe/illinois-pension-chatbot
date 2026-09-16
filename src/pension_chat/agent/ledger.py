"""What this conversation has established, and how it knows it.

The member's own words are the highest-provenance fact class in this design.
`guardrails` traces a figure to "the member's own messages" as a source distinct
from their record, and `coverage` treats the record and their word as two voices
precisely so that one can contradict the other. Yet it was the only fact class
thrown away at the end of every turn: `input_sources` is not in `DURABLE_KEYS`,
so a member who gave their service in turn two was asked for it again in turn
four.

What is kept here is the classification `nodes._input_origins` already makes --
in code, at resolution time, deliberately not reported by the model, because
"the calculation card shows these to the member, so a guess here is a false
claim about their own figure".

**Precedence, not overwrite.** A value the member stated outranks one read off
their record, which outranks one derived from another, which outranks one the
Code states, which outranks one we filled in. Re-reading the file every turn
therefore cannot quietly undo a correction. Equal rank replaces, because a
member restating a value is still the member speaking and the later statement is
the current one.

The two middle ranks are the ones that stop the hedging. A value derived from
what this member said and a value cited to the section that states it are both
*better* than a gap, and neither is an assumption -- so neither is capped, and
neither is disclosed as something we guessed at.

**Per subject.** Facts are held under the subject they are about, and nothing
reads across. That is the same rule `_available_facts` enforces when it starves
record facts under a supposition, applied to a conversation that can hold more
than one person in view at once.
"""

from __future__ import annotations

from typing import Any

from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_DERIVED,
    SOURCE_RECORD,
    SOURCE_STATED,
    SOURCE_STATUTORY,
)

#: The subject a conversation is about unless it says otherwise.
SELF = "me"

#: Best first. A source not in this tuple ranks below every source in it, so an
#: unrecognised label can never displace a known one.
PRECEDENCE: tuple[str, ...] = (
    SOURCE_STATED,
    SOURCE_RECORD,
    SOURCE_DERIVED,
    SOURCE_STATUTORY,
    SOURCE_ASSUMED,
)


def rank(source: str) -> int:
    """Where ``source`` sits in the precedence order; unknown sorts last."""
    try:
        return PRECEDENCE.index(source)
    except ValueError:
        return len(PRECEDENCE)


def outranks(new: str, old: str) -> bool:
    """Does a ``new``-sourced value replace an ``old``-sourced one?

    Equal rank replaces. The member correcting themselves is still the member,
    and a record re-read this turn is no staler than the one read last turn.
    """
    return rank(new) <= rank(old)


def remember(
    ledger: dict[str, Any] | None,
    subject: str,
    values: dict[str, Any],
    source: str,
    turn: int,
) -> dict[str, Any]:
    """The ledger update recording ``values`` for ``subject``.

    Returns a whole-subject dict because the state reducer merges at the top
    level only: handing it ``{subject: {one_key: ...}}`` would drop every other
    fact that subject holds. An empty dict when nothing was worth recording,
    which merges to nothing rather than clearing the key.
    """
    held = dict((ledger or {}).get(subject) or {})
    kept: dict[str, Any] = {}
    for name, value in (values or {}).items():
        if value is None:
            # Extraction reports what it could not find as null. An absence is
            # not a fact, and recording it would outrank the record.
            continue
        entry = held.get(name)
        if entry is not None and not outranks(source, str(entry.get("source") or "")):
            continue
        kept[name] = {"value": value, "source": source, "turn": turn}
    if not kept:
        return {}
    return {subject: {**held, **kept}}


def recall(ledger: dict[str, Any] | None, subject: str = SELF) -> dict[str, Any]:
    """Flat ``name -> value`` for ``subject``."""
    held = (ledger or {}).get(subject) or {}
    return {name: entry["value"] for name, entry in held.items()}


def provenance(ledger: dict[str, Any] | None, subject: str = SELF) -> dict[str, str]:
    """Flat ``name -> source`` for ``subject``."""
    held = (ledger or {}).get(subject) or {}
    return {name: str(entry.get("source") or "") for name, entry in held.items()}


def stated_only(ledger: dict[str, Any] | None, subject: str = SELF) -> dict[str, Any]:
    """Flat ``name -> value``, restricted to what the member supplied themselves.

    The one thing that survives the record being starved. A figure is computable
    exactly when no input is borrowed from a record filed under another Article
    -- and a value the member typed is not borrowed from any record.
    """
    held = (ledger or {}).get(subject) or {}
    return {
        name: entry["value"]
        for name, entry in held.items()
        if entry.get("source") == SOURCE_STATED
    }
