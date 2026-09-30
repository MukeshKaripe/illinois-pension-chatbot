"""An eval run leaves the formula registry exactly as it found it.

Reuse counters feed the reviewer's view of which recipes are load-bearing, and
`verify_freshness` can mark a row stale for production; neither may move
because an eval ran. `registry_writes_enabled` (REGISTRY_WRITES_ENABLED) gates
every UPDATE the registry makes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pension_chat.agent import registry


@pytest.mark.asyncio
async def test_no_update_reaches_the_database_when_writes_are_off(monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(registry, "async_session_factory", lambda: opened.append(1))
    monkeypatch.setattr(registry, "get_settings", lambda: SimpleNamespace(registry_writes_enabled=False))
    await registry._mark(SimpleNamespace(id="r1"), reuse_count=2)
    await registry._mark_by_formula_id("f1", contested_count=1)
    assert opened == []


def test_the_setting_exists_and_defaults_on() -> None:
    from pension_chat.config import Settings

    assert Settings.model_fields["registry_writes_enabled"].default is True
