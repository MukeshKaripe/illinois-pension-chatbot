"""Which sections of other Articles a corpus needs: the ones its own text cites."""

from __future__ import annotations

from pension_chat.ingestion.parser import ParsedSection


def one_hop_references(sections: list[ParsedSection], own_articles: set[int]) -> dict[int, set[str]]:
    refs: dict[int, set[str]] = {}
    for section in sections:
        for ref in section.referenced_sections:
            prefix = ref.split("-", 1)[0]
            if not prefix.isdigit() or int(prefix) in own_articles:
                continue
            refs.setdefault(int(prefix), set()).add(ref)
    return refs
