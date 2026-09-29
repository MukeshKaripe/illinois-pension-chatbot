"""Which sections of other Articles a corpus needs: the ones its own text cites.

A section number alone does not say which law it belongs to. Article 8 cites
"Section 3-101 of the Code of Civil Procedure"; the parser records "3-101", and
40 ILCS 5/3-101 is the police pension fund — a different law under the same
number. So a reference counts as the Pension Code's unless *every* place it is
cited says "of the <some other> Code/Act". "Of this Code" and "of the Illinois
Pension Code" are the Pension Code.
"""

from __future__ import annotations

import re

from pension_chat.ingestion.parser import ParsedSection

#: What follows a section number when the citation is to another Act.
_OTHER_ACT_TAIL_RE = re.compile(
    r"^(?:\([a-z0-9-]+\))*\s+of\s+the\s+(?!Illinois Pension Code)(?=[A-Z])[^.;,]{0,40}?\b(?:Code|Act)\b"
)


def _cited_only_in_other_acts(ref: str, text: str) -> bool:
    tails = [text[m.end():] for m in re.finditer(re.escape(ref) + r"(?!\d)", text)]
    return bool(tails) and all(_OTHER_ACT_TAIL_RE.match(tail) for tail in tails)


def _outside_references(sections: list[ParsedSection], own_articles: set[int]) -> list[tuple[str, bool]]:
    """(reference, is it another Act's) for every citation outside `own_articles`."""
    found = []
    for section in sections:
        for ref in section.referenced_sections:
            prefix = ref.split("-", 1)[0]
            if not prefix.isdigit() or int(prefix) in own_articles:
                continue
            found.append((ref, _cited_only_in_other_acts(ref, section.full_text)))
    return found


def one_hop_references(sections: list[ParsedSection], own_articles: set[int]) -> dict[int, set[str]]:
    refs: dict[int, set[str]] = {}
    for ref, other_act in _outside_references(sections, own_articles):
        if not other_act:
            refs.setdefault(int(ref.split("-", 1)[0]), set()).add(ref)
    return refs


def other_act_references(sections: list[ParsedSection], own_articles: set[int]) -> set[str]:
    """Section numbers cited as another Act's — reported, never ingested."""
    pension = {ref for refs in one_hop_references(sections, own_articles).values() for ref in refs}
    return {ref for ref, other_act in _outside_references(sections, own_articles) if other_act} - pension
