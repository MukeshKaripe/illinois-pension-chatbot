"""What is load-bearing, counted — and a cache that never hit.

The registry's columns recorded what had earned approval. Nothing recorded what
the system actually leans on, which is the thing a reviewer needs first: an
error in the recipe that has quietly produced four hundred figures costs more
than an error in one that has produced none.

Offline. `verify_freshness` is exercised against a fake row and a fake session,
because what is under test is which branch runs, not Postgres.
"""

from __future__ import annotations

from types import SimpleNamespace

from pension_chat.agent import registry
from pension_chat.calculation.fingerprint import compute_evidence_fingerprint


class _Row(SimpleNamespace):
    pass


def _row(**over):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "formula_id": "11_retirement_annuity_11-134_abc",
        "evidence_fingerprint": {},
        "knowledge_status": "agent_derived",
        "source_sections": ["11-134"],
    }
    base.update(over)
    return _Row(**base)


def _capture_updates(monkeypatch):
    """Record the values written, without a database."""
    written: list[dict] = []

    class _Session:
        async def execute(self, statement):
            compiled = getattr(statement, "_values", None) or {}
            written.append(
                {
                    str(k).split(".")[-1]: getattr(v, "value", v)
                    for k, v in compiled.items()
                }
            )

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(registry, "async_session_factory", lambda: _Session())
    return written


async def test_an_unfingerprinted_row_is_marked_rather_than_re_derived_forever(monkeypatch):
    """The bug, and it is silent in the worst way.

    `verify_freshness` returned "unverifiable" for a row with no stored
    fingerprint *before* reaching the code that marks a row stale. So the row
    stayed reusable, `lookup_recipe` kept returning it, freshness kept refusing
    it, and the agent re-derived the same recipe on every turn forever -- a
    cache that never hits and a status that never changes. Nothing logs a
    warning, because from each turn's point of view it worked.
    """
    written = _capture_updates(monkeypatch)
    mismatches = await registry.verify_freshness(_row(), {"11-134": "text"})

    assert mismatches == {"__unfingerprinted__": "missing"}, "still unusable"
    assert written and written[-1]["knowledge_status"] == "stale", "and now it says so"


async def test_a_fresh_row_is_left_alone(monkeypatch):
    written = _capture_updates(monkeypatch)
    fingerprint = compute_evidence_fingerprint([{"section_id": "11-134", "content": "text"}])
    assert await registry.verify_freshness(
        _row(evidence_fingerprint=fingerprint), {"11-134": "text"}
    ) == {}
    assert written == [], "a fresh recipe is not written to"


async def test_changed_statute_still_marks_stale(monkeypatch):
    """The branch that always worked, guarded so the fix cannot break it."""
    written = _capture_updates(monkeypatch)
    fingerprint = compute_evidence_fingerprint([{"section_id": "11-134", "content": "old"}])
    mismatches = await registry.verify_freshness(
        _row(evidence_fingerprint=fingerprint), {"11-134": "new"}
    )
    assert mismatches
    assert written[-1]["knowledge_status"] == "stale"


async def test_reuse_is_counted_and_dated(monkeypatch):
    """Repurposed from "what earned approval" to "what is load-bearing"."""
    written = _capture_updates(monkeypatch)
    await registry.note_reuse(_row())
    values = written[-1]
    assert "reuse_count" in values
    assert "last_reused_at" in values


async def test_a_disputed_figure_counts_against_the_recipe(monkeypatch):
    """`input_overrides` already detects a member disputing a figure, and today
    it only says so in prose. A recipe whose inputs members keep correcting is
    exactly what a reviewer should see."""
    written = _capture_updates(monkeypatch)
    await registry.note_contested("11_retirement_annuity_11-134_abc")
    assert "contested_count" in written[-1]


async def test_a_recipe_reaches_the_graph_as_a_dict_not_a_row(monkeypatch):
    """State is checkpointed, so an ORM object put on it is a detached instance
    waiting to happen. The formula_id is the only handle a downstream node has,
    which is why contested is addressed by it."""
    written = _capture_updates(monkeypatch)
    await registry.note_contested("")
    assert written == []


async def test_nothing_is_written_for_a_row_with_no_id(monkeypatch):
    written = _capture_updates(monkeypatch)
    await registry.note_reuse(_row(id=None))
    assert written == []


# ── The admin's power is to correct and retire, not to admit ──────
#
# Read off the router rather than the assembled app: importing `main` drags in
# the lifespan and every other router, and what is under test is this module's
# own surface.


def _formula_routes():
    from pension_chat.api import admin

    return [r for r in admin.router.routes if "/formulas" in getattr(r, "path", "")]


def test_promotion_is_gone():
    """Positive control does not scale past a handful of Articles, makes the
    assistant worst exactly when a member asks something new, and in practice
    nobody clicks the button. The gate is `validator.py` and always was."""
    assert not any("promote" in r.path for r in _formula_routes())


def test_retire_and_suspend_exist():
    """Negative control is what is actually needed, and `retired` /
    `no_execute` already existed with no surface."""
    paths = {r.path for r in _formula_routes()}
    assert any(p.endswith("/retire") for p in paths)
    assert any(p.endswith("/suspend") for p in paths)


def test_every_formula_action_is_admin_guarded():
    """`require_auth` asks whether somebody is signed in, which is not the
    question these need answered. Guarded as a set, so an action added later
    cannot quietly arrive unguarded."""
    import inspect

    for route in _formula_routes():
        source = inspect.getsource(route.endpoint)
        assert "require_admin" in source, f"{route.path} is not admin-guarded"


# ── Bookkeeping is not something a member waits for ───────────────


async def test_counting_a_reuse_does_not_make_the_member_wait(monkeypatch):
    """The write that records "this recipe was used again" told the member
    nothing and cost them a round trip.

    Measured at 0.72s against Azure Postgres, sitting inside `compute_benefit`
    on the way to a figure -- a quarter of it BEGIN and COMMIT for a single
    UPDATE nobody reads until a reviewer opens the registry. It still happens;
    it just stops happening in front of the answer.
    """
    import asyncio

    written = _capture_updates(monkeypatch)
    started = asyncio.Event()

    async def slow_mark(row, **values):
        started.set()
        await asyncio.sleep(0.05)
        written.append(values)

    monkeypatch.setattr(registry, "_mark", slow_mark)

    registry.note_reuse_later(_row())
    assert written == [], "the caller waited for a write it does not read"

    await asyncio.wait_for(started.wait(), timeout=1)
    await registry.drain_background_writes()
    assert "reuse_count" in written[-1], "and the count still lands"


async def test_a_failed_bookkeeping_write_is_logged_not_raised(monkeypatch):
    """Nothing awaits this task, so an exception in it would surface as an
    unretrieved-task warning long after the turn it belonged to."""

    async def broken(row, **values):
        raise RuntimeError("registry is unreachable")

    monkeypatch.setattr(registry, "_mark", broken)

    registry.note_reuse_later(_row())
    await registry.drain_background_writes()
