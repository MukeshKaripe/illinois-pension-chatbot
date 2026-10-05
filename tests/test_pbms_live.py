"""On demand only: `uv run pytest -m pbms`. Reads PBMS live; asserts shapes and counts; prints nothing."""
from __future__ import annotations

import random
from datetime import date

import pytest

pytestmark = pytest.mark.pbms


async def test_names_from_the_directory_resolve_to_readable_records() -> None:
    from pension_chat.config import get_settings
    from pension_chat.pbms import build_record_provider

    provider = build_record_provider(get_settings().model_copy(update={"record_source": "pbms"}))
    assert provider is not None
    try:
        assert await provider.load_names() > 1000
        pairs = random.sample(list(provider._names._by_full.values()), 2)
        text = f"Compare {' '.join(pairs[0])} with {' '.join(pairs[1])} at 62"
        assert provider.names_in(text) == pairs
        for first, last in pairs:
            matches = await provider.find_by_name(first, last)
            assert matches and all(m.status == "active" for m in matches)
            record = await provider.get_record(matches[0].key)
            history = await provider.get_salary_history(matches[0].key)
            assert record is not None and record.source == "PBMS" and record.tier in (None, 1, 2, 3)
            assert all(h.year < date.today().year and h.annual_salary > 0 for h in history)
    finally:
        await provider.close()
