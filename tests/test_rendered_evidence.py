"""What reached the model, subsection by subsection -- not what was retrieved."""
from __future__ import annotations

from pension_chat.agent import instrument
from pension_chat.agent.retrieval import _assemble, merge_variants_with_ids
from pension_chat.agent.tools import _render


def _c(sub: str, body: str) -> dict:
    return {"id": f"8-129_{sub.strip('()')}", "section": "8-129", "article": "8",
            "section_title": "Age and service annuity", "subsection_id": sub, "content": body}


def test_merge_with_ids_names_the_subsections_each_part_carries() -> None:
    parts = merge_variants_with_ids([_c("(b)", "(b) two"), _c("(a)", "(a) one")])
    assert parts == [("(a) one", ["(a)"]), ("(b) two", ["(b)"])]


def test_assemble_records_spans_for_fully_included_parts_only() -> None:
    section = _assemble("8-129", [_c("(a)", "a" * 500), _c("(b)", "b" * 3000)], 1.0, char_budget=1500)
    ids = [sid for sid, _ in section.spans]
    assert ids == ["(a)"]                   # (b) was cut, so it is not evidence
    assert section.truncated


def test_render_reports_what_it_rendered() -> None:
    section = _assemble("8-129", [_c("(a)", "a" * 500), _c("(b)", "b" * 500)], 1.0, char_budget=10_000)
    inst = instrument.begin()
    _render([section.to_dict()], budget=700)
    assert inst.rendered == {"8-129": ["(a)"]}


def test_render_without_an_instrument_is_unchanged() -> None:
    section = _assemble("8-129", [_c("(a)", "(a) one")], 1.0, char_budget=10_000)
    assert "(a) one" in _render([section.to_dict()], budget=10_000)
