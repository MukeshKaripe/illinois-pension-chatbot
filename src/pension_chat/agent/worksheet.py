"""The conversation's working sheet: what is known, what was computed from it,
and what is still open.

This is a view over durable state keys that already exist -- `assumptions`,
`baseline`, `scenario_set`, `open_question` -- not a thirteenth key alongside
them. Two keys would drift; one accessor cannot.

The load-bearing field is `Fact.source`. A figure may rest on a value from the
member's record, from something they said, or from a premise they asked us to
suppose, and it may rest on a value we filled in ourselves so long as the answer
says so. It may never rest on a value that is on file but not usable under the
premise being explored -- that is `withheld`, and it exists because starving a
record fact and reporting it as merely *missing* makes the assistant ask a
member for an age it already knows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SOURCE_RECORD = "record"
SOURCE_STATED = "stated"
SOURCE_HYPOTHETICAL = "hypothetical"
SOURCE_ASSUMED = "assumed"
SOURCE_WITHHELD = "withheld"

#: Sources a computed figure is allowed to rest on. `withheld` is deliberately
#: absent: the value is known, and using it anyway is the bug this exists to stop.
USABLE_SOURCES = frozenset(
    {SOURCE_RECORD, SOURCE_STATED, SOURCE_HYPOTHETICAL, SOURCE_ASSUMED}
)


@dataclass
class Fact:
    """One value the conversation holds, and where it came from."""

    name: str
    value: Any
    source: str
    basis: str = ""
    turn: int = 0
    asked: bool = False


@dataclass
class Figure:
    """One computed result, and the facts it was computed from."""

    label: str
    result: dict[str, Any]
    provision: dict[str, Any]
    rests_on: tuple[str, ...]
    turn: int
    stale: bool = False


@dataclass
class OpenItem:
    """Something the conversation still needs settled."""

    what: str
    why: str
    asked_turn: int
    bound_to: str | None = None


@dataclass
class WorkSheet:
    """Facts, figures and open items for one conversation."""

    facts: dict[str, Fact] = field(default_factory=dict)
    figures: dict[str, Figure] = field(default_factory=dict)
    open_items: list[OpenItem] = field(default_factory=list)
    turn: int = 0

    # ── Construction ─────────────────────────────────────────────

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> WorkSheet:
        """Build the sheet from the durable keys the checkpointer carries.

        `member_facts` is the record and arrives as `record`. `assumptions`
        overlays it, because a value the member stated or a premise they
        supposed outranks the file.
        """
        sheet = cls(turn=int(state.get("turn_index") or 0))

        for name, value in (state.get("member_facts") or {}).items():
            if value is not None:
                sheet.facts[name] = Fact(name, value, SOURCE_RECORD, turn=sheet.turn)

        for name, entry in (state.get("assumptions") or {}).items():
            if not isinstance(entry, Mapping):
                continue
            sheet.facts[name] = Fact(
                name=name,
                value=entry.get("value"),
                source=str(entry.get("source") or SOURCE_STATED),
                basis=str(entry.get("basis") or ""),
                turn=int(entry.get("turn") or 0),
                asked=bool(entry.get("asked")),
            )

        baseline = state.get("baseline")
        if isinstance(baseline, Mapping) and baseline.get("result") is not None:
            sheet.figures["now"] = _figure_from(baseline, "now")
        for label, entry in (state.get("scenario_set") or {}).items():
            if isinstance(entry, Mapping) and entry.get("result") is not None:
                sheet.figures[label] = _figure_from(entry, label)

        open_question = state.get("open_question")
        if isinstance(open_question, Mapping) and open_question.get("what"):
            sheet.open_items.append(
                OpenItem(
                    what=str(open_question.get("what")),
                    why=str(open_question.get("why") or ""),
                    asked_turn=int(open_question.get("asked_turn") or 0),
                    bound_to=open_question.get("bound_to"),
                )
            )
        return sheet

    def to_state_update(self) -> dict[str, Any]:
        """The state update that persists this sheet.

        Only non-record facts are written back: the record is re-read from the
        database every turn, and storing a copy would let a stale one win.
        """
        assumptions = {
            name: {
                "value": fact.value,
                "source": fact.source,
                "basis": fact.basis,
                "turn": fact.turn,
                "asked": fact.asked,
            }
            for name, fact in self.facts.items()
            if fact.source != SOURCE_RECORD
        }
        update: dict[str, Any] = {"assumptions": assumptions}

        if "now" in self.figures:
            update["baseline"] = _figure_to_dict(self.figures["now"])
        scenarios = {
            label: _figure_to_dict(figure)
            for label, figure in self.figures.items()
            if label != "now"
        }
        if scenarios:
            update["scenario_set"] = scenarios
        if self.open_items:
            item = self.open_items[0]
            update["open_question"] = {
                "what": item.what,
                "why": item.why,
                "asked_turn": item.asked_turn,
                "bound_to": item.bound_to,
            }
        return update

    # ── Facts ────────────────────────────────────────────────────

    def record_fact(
        self,
        name: str,
        value: Any,
        source: str,
        basis: str = "",
        turn: int = 0,
    ) -> None:
        """Record a value under an explicit provenance."""
        self.facts[name] = Fact(name, value, source, basis=basis, turn=turn or self.turn)

    def fact(self, name: str) -> Fact | None:
        return self.facts.get(name)

    def knows(self, name: str) -> bool:
        """Whether the conversation holds this value at all, usable or not."""
        return name in self.facts and self.facts[name].value is not None

    def usable_values(self) -> dict[str, Any]:
        """Values a figure may be computed from."""
        return {
            name: fact.value
            for name, fact in self.facts.items()
            if fact.source in USABLE_SOURCES and fact.value is not None
        }

    def assumed_facts(self) -> dict[str, Fact]:
        return {
            name: fact
            for name, fact in self.facts.items()
            if fact.source == SOURCE_ASSUMED
        }

    # ── Figures and dependencies ─────────────────────────────────

    def record_figure(
        self,
        label: str,
        result: dict[str, Any],
        provision: dict[str, Any],
        rests_on: tuple[str, ...],
        turn: int,
    ) -> None:
        self.figures[label] = Figure(label, result, provision, tuple(rests_on), turn)

    def apply_revisions(self, revised: Mapping[str, Any], turn: int) -> set[str]:
        """Apply values the member corrected, and return the figures they invalidate.

        This is what makes "actually it's more like 23 years" recompute. The
        member does not have to use a word the system recognises; they have to
        contradict something a figure was built on.
        """
        changed: set[str] = set()
        for name, value in revised.items():
            if value is None:
                continue
            existing = self.facts.get(name)
            if existing is not None and str(existing.value) == str(value):
                continue
            self.record_fact(name, value, SOURCE_STATED, turn=turn)
            changed.add(name)

        stale: set[str] = set()
        for label, figure in self.figures.items():
            if changed.intersection(figure.rests_on):
                figure.stale = True
                stale.add(label)
        return stale

    def stale_figures(self) -> list[str]:
        return [label for label, figure in self.figures.items() if figure.stale]

    # ── Asking ───────────────────────────────────────────────────

    def filter_asks(self, asks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Drop asks for values already held; mark the ones held but unusable.

        Asking a member for their age when it is on their file reads as not
        having listened, and it happened on three of eight measured cases.
        """
        kept: list[dict[str, Any]] = []
        for ask in asks:
            name = str(ask.get("name") or "")
            fact = self.facts.get(name)
            if fact is not None and fact.value is not None:
                if fact.source in USABLE_SOURCES:
                    continue
                item = dict(ask)
                item["withheld"] = True
                item["withheld_basis"] = fact.basis
                kept.append(item)
                continue
            kept.append(dict(ask))
        return kept


def _figure_from(entry: Mapping[str, Any], label: str) -> Figure:
    return Figure(
        label=label,
        result=dict(entry.get("result") or {}),
        provision=dict(entry.get("provision") or {}),
        rests_on=tuple(entry.get("rests_on") or ()),
        turn=int(entry.get("turn") or 0),
        stale=bool(entry.get("stale")),
    )


def _figure_to_dict(figure: Figure) -> dict[str, Any]:
    return {
        "result": figure.result,
        "provision": figure.provision,
        "rests_on": list(figure.rests_on),
        "turn": figure.turn,
        "stale": figure.stale,
    }
