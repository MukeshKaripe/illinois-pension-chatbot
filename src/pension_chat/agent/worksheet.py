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
from decimal import Decimal, InvalidOperation
from typing import Any

from pension_chat.agent.facts import FRAMING_DEFAULTS, aliases_of
from pension_chat.calculation.assumption_policy import rule_for

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

#: Sources that must never be written into durable state. `record` because the
#: file is re-read every turn and a stored copy could go stale; `withheld`
#: because it is a per-turn *view* of a record fact rather than a fact, and
#: persisting it would overlay the record with an unusable copy of itself for
#: the rest of the conversation.
_NOT_DURABLE = frozenset({SOURCE_RECORD, SOURCE_WITHHELD})


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
    def from_state(
        cls, state: Mapping[str, Any], *, record_usable: bool = True
    ) -> WorkSheet:
        """Build the sheet from the durable keys the checkpointer carries.

        `member_facts` is the record and arrives as `record`. `assumptions`
        overlays it, because a value the member stated or a premise they
        supposed outranks the file.

        `record_usable` is False when the conversation is reading an Article
        the member's record is not filed under: the record is on file but not
        a legitimate basis for a figure under this premise, so its facts are
        recorded as `SOURCE_WITHHELD` rather than `SOURCE_RECORD` -- known,
        but not usable, and so still worth asking about rather than silently
        dropping the ask. The caller decides this; the sheet has no opinion of
        its own about which Article governs.
        """
        sheet = cls(turn=int(state.get("turn_index") or 0))

        record_source = SOURCE_RECORD if record_usable else SOURCE_WITHHELD
        record_basis = (
            ""
            if record_usable
            else "on file, but not usable while this conversation is reading another Article"
        )
        for name, value in (state.get("member_facts") or {}).items():
            if value is None:
                continue
            # A framing default arrives in `member_facts` beside the record's
            # own values, but it is not one of them and must not inherit their
            # provenance: the facts layer fills `withdrawal_date` with today
            # so a date_diff has something to subtract, and calling that a
            # record fact is how a figure resting on "you stop working today"
            # came out at rung 1 with no assumptions block and nothing in the
            # answer's own voice saying which retirement date it used.
            #
            # Labelled here rather than left out of `derive_facts` on purpose.
            # The value is still needed -- applicability predicates and the
            # interpreter both read it -- so the defect is the label alone, and
            # dropping the key to fix a label would starve the predicates.
            if name in FRAMING_DEFAULTS:
                rule = rule_for(name)
                sheet.facts[name] = Fact(
                    name,
                    value,
                    SOURCE_ASSUMED,
                    basis=rule.basis if rule else "",
                    turn=sheet.turn,
                )
                continue
            sheet.facts[name] = Fact(
                name, value, record_source, basis=record_basis, turn=sheet.turn
            )

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
        if isinstance(baseline, Mapping) and _holds_a_figure(baseline):
            sheet.figures["now"] = _figure_from(baseline, "now")
        for label, entry in (state.get("scenario_set") or {}).items():
            if isinstance(entry, Mapping) and _holds_a_figure(entry):
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

        `withheld` is excluded for a sharper reason. It is not a fact at all --
        it is a *per-turn view* of a record fact, saying "known, but not usable
        under the premise this turn is exploring". Persisting it puts a copy of
        the record into `assumptions`, which `from_state` overlays back on top
        of `member_facts` on every later turn; the withheld copy then wins
        forever and `usable_values()` stays empty for the rest of the
        conversation. One stipulated-premise turn would otherwise poison the
        record permanently.
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
            if fact.source not in _NOT_DURABLE
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
            if existing is not None and _same_value(existing.value, value):
                continue
            self.record_fact(name, value, SOURCE_STATED, turn=turn)
            changed.add(name)

        # A figure declares its dependencies under the names its *recipe* chose,
        # and the member corrects a value under the name the conversation used
        # for it. "23 years" revises `service_years`; the recipe consumed
        # `creditable_service_years`. Same quantity, so the same figure is
        # stale -- and without the alias expansion the design's own worked
        # example ("a revision to service_years invalidates every figure that
        # used it") does not fire on Article 11's own recipe.
        touched: set[str] = set()
        for name in changed:
            touched |= aliases_of(name)

        stale: set[str] = set()
        for label, figure in self.figures.items():
            if touched.intersection(figure.rests_on):
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
            item = dict(ask)
            if fact is not None and fact.asked:
                item["already_asked"] = True
            kept.append(item)
        return kept


def _same_value(a: Any, b: Any) -> bool:
    """Whether two values are the same, compared numerically where both parse.

    `str(a) == str(b)` reads "21.50" and "21.5" as different, so a member
    correcting 21.5 to 21.50 would be recorded as revising nothing, and a
    figure carrying `Decimal("21.500")` would be marked stale on a turn that
    changed nothing at all.
    """
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError, TypeError):
        return str(a) == str(b)


#: Keys on a persisted figure that describe the figure rather than being part
#: of it. Everything else in the entry is the result.
_FIGURE_META = frozenset(
    {"turn", "stale", "rests_on", "result", "provision", "formula_type",
     "section", "subsection", "formula_id", "selection_basis"}
)

#: Keys that identify the provision a figure was computed under.
_PROVISION_KEYS = ("section", "subsection", "formula_type", "formula_id", "selection_basis")

#: What a persisted figure must carry to be a figure at all.
_AMOUNT_KEYS = ("annual_amount", "monthly_amount")


def _holds_a_figure(entry: Mapping[str, Any]) -> bool:
    """Whether a durable `baseline`/`scenario_set` entry actually names a figure.

    Two shapes reach here and both are real. `graph.turn_record` writes the
    amounts **flat** -- `annual_amount`, `monthly_amount`, `inputs_used` -- and
    that is what production persists; a nested `{"result": {...}}` also occurs,
    because that is what this module used to write back. Reading only the
    nested one is why the sheet held no figures in production at all, which
    made every downstream staleness check dead code.
    """
    if entry.get("result") is not None:
        return True
    return any(entry.get(key) is not None for key in _AMOUNT_KEYS)


def _figure_from(entry: Mapping[str, Any], label: str) -> Figure:
    nested = entry.get("result")
    if isinstance(nested, Mapping):
        result = dict(nested)
    else:
        result = {k: v for k, v in entry.items() if k not in _FIGURE_META}

    provision = entry.get("provision")
    if isinstance(provision, Mapping) and provision:
        where = dict(provision)
    else:
        where = {k: entry[k] for k in _PROVISION_KEYS if entry.get(k) is not None}

    # `rests_on` is the dependency set that makes recompute structural. When it
    # was not written down, the inputs the figure was computed from say the same
    # thing -- the interpreter reports them as `inputs_used`, and `turn_record`
    # carries them through.
    declared = entry.get("rests_on")
    if declared:
        rests_on = tuple(str(name) for name in declared)
    else:
        used = result.get("inputs_used") or entry.get("inputs_used") or {}
        rests_on = tuple(str(name) for name in used) if isinstance(used, Mapping) else ()

    return Figure(
        label=label,
        result=result,
        provision=where,
        rests_on=rests_on,
        turn=int(entry.get("turn") or 0),
        stale=bool(entry.get("stale")),
    )


def _figure_to_dict(figure: Figure) -> dict[str, Any]:
    """Persist a figure in the flat shape `graph.turn_record` writes.

    One shape, not two. `graph._verify` reads `baseline["turn"]` and
    `scenario_set[label]["annual_amount"]` directly off the durable keys, so a
    nested round trip here would leave those readers looking at the wrong
    level on any turn that wrote the sheet back without recalculating.
    """
    persisted: dict[str, Any] = {
        **figure.result,
        **figure.provision,
        "rests_on": list(figure.rests_on),
        "turn": figure.turn,
    }
    if figure.stale:
        persisted["stale"] = True
    return persisted
