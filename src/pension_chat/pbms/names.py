"""Which employees a message names: active members' first and last names, held in memory.

A message cannot be sent to SQL Server to ask whether it contains a name, so
the directory of names -- and nothing else -- is loaded once and matched here,
at no database cost; PBMS is asked only when a name actually matches (spec §3.4).

Matching is deterministic and case-insensitive. A name is a run of 2-4
whitespace-separated tokens (names may hold spaces, `_`, `'` and `.`), written
"FIRST LAST" or "LAST, FIRST", with surrounding punctuation and a possessive
"'s" tolerated. A token only ever matches whole: "XKA_LS" is not "KA_LS".
"""
from __future__ import annotations

import re
from collections.abc import Iterable

# Trimmed from token edges. `_`, `'` and `.` inside a token stay: names hold them.
_EDGE = ".,;:!?\"()[]{}<>*`“”‘’"
_POSSESSIVE = re.compile(r"(?:'|’)S$")
_MAX_TOKENS = 4


def _norm(part: str) -> str:
    return " ".join(part.split()).upper()


class NameDirectory:
    def __init__(self, pairs: Iterable[tuple[str, str]] = ()) -> None:
        self._by_full: dict[str, tuple[str, str]] = {}
        for first, last in pairs:
            first, last = (first or "").strip(), (last or "").strip()
            if first and last:
                self._by_full.setdefault(f"{_norm(first)} {_norm(last)}", (first, last))

    def __len__(self) -> int:
        return len(self._by_full)

    def names_in(self, text: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, bool]] = []          # (token, followed by a comma)
        for raw in (text or "").split():
            token = raw.strip(_EDGE).upper()
            if token:
                tokens.append((token, raw.rstrip(".!?;:)\"”").endswith(",")))
        # Forward ("FIRST LAST") matches first; a reversed "LAST, FIRST" only
        # where it overlaps none of them -- in "A B, C D and E F" the comma
        # separates two names, and "B, C" is not a third.
        forward: list[tuple[int, int, tuple[str, str]]] = []
        reverse: list[tuple[int, int, tuple[str, str]]] = []
        for i in range(len(tokens)):
            for j in range(i + 1, min(i + _MAX_TOKENS, len(tokens))):
                pair, reversed_ = self._match(tokens[i:j + 1])
                if pair is not None:
                    (reverse if reversed_ else forward).append((i, j, pair))
        taken = {k for i, j, _ in forward for k in range(i, j + 1)}
        hits = forward + [(i, j, p) for i, j, p in reverse if not taken & set(range(i, j + 1))]
        found: list[tuple[str, str]] = []
        for _, _, pair in sorted(hits, key=lambda h: h[0]):
            if pair not in found:
                found.append(pair)
        return found

    def _match(self, run: list[tuple[str, bool]]) -> tuple[tuple[str, str] | None, bool]:
        """The name a run of tokens spells, and whether it was written LAST, FIRST."""
        words = [t for t, _ in run]
        variants = [words]
        if _POSSESSIVE.search(words[-1]):
            variants.append(words[:-1] + [_POSSESSIVE.sub("", words[-1])])
        for variant in variants:
            if pair := self._by_full.get(" ".join(variant)):
                return pair, False
            # "LAST, FIRST": the comma marks where the last name ends.
            for k in range(1, len(variant)):
                if run[k - 1][1]:
                    last, first = " ".join(variant[:k]), " ".join(variant[k:])
                    if pair := self._by_full.get(f"{first} {last}"):
                        return pair, True
        return None, False
