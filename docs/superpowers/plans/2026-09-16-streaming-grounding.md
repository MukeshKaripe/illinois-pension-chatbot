# Streaming Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch an ungrounded citation at the moment it becomes checkable — mid-stream, one sentence in — instead of four seconds later when the whole answer is retracted.

**Architecture:** A new `agent/grounding.py` holds a pure sentence-buffering gate. `run_agent_stream` feeds streamed text through it before yielding tokens; a sentence naming a section that was never retrieved halts the stream and forces the existing repair-or-fallback path. Separately, the `pushed_back` invariant gains the seam it needs to survive prefetch, which lands in a later plan.

**Tech Stack:** Python 3.12, pytest, `uv`. No new dependencies. Every test offline.

**Spec:** `docs/superpowers/specs/2026-09-16-rag-first-agent-design.md` — §8 (Verification) and §13 step 3.

## Global Constraints

- Run everything through `uv`: `uv run pytest`, `uv run ruff check .`.
- Every test in this plan is offline. No test may touch Azure OpenAI, Azure AI Search or Postgres.
- `uv run ruff check <files touched>` must pass before each commit, and the full suite must stay green.
- The gate may not make a **model call**, a **network call**, or an **await**. It is a pure function over text, because it runs between every delta and the browser.
- Do not change what `verify_answer` checks. This plan changes *when* one of its checks first runs, not what passes it.

## Sequencing note

The spec's §13 lists subject-scoped retrieval as step 2 and this as step 3. That order is wrong: §5 moves the park-employee protection out of the retrieval filter and into the claim check, so doing retrieval first opens a window where neither layer holds it. This plan is step 3 and runs first. Retrieval scope follows it.

---

### Task 1: The streaming gate

**Files:**
- Create: `src/pension_chat/agent/grounding.py`
- Test: `tests/test_grounding.py`

**Interfaces:**
- Consumes: `_CITATION` from `pension_chat.agent.guardrails` (module-level compiled regex, group 1 is the bare section id such as `11-134`).
- Produces:
  - `class StreamGate` with:
    - `__init__(self, available: set[str]) -> None`
    - `feed(self, text: str) -> tuple[str, str | None]` — returns `(text safe to emit now, the section id that broke it or None)`. Once broken, every later `feed` returns `("", <same section>)`.
    - `flush(self) -> tuple[str, str | None]` — the held remainder at end of stream, under the same rule.
    - `broken: str | None` — the offending section, or None.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_grounding.py`:

```python
"""Tests for catching an ungrounded claim while it is still being written.

Verification runs after generation, so an answer citing a section nobody
retrieved streamed in full and was replaced seconds later -- the member read a
sentence about their pension that the Code does not support. A citation is
checkable the instant it appears: set membership, no model call. Holding each
sentence until its citations clear costs a fraction of a second and bounds the
damage to one sentence that never left the server.
"""

from __future__ import annotations

from pension_chat.agent.grounding import StreamGate

AVAILABLE = {"11-134", "11-216"}


def test_text_without_citations_flows_sentence_by_sentence():
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("Your pension depends on two things. ")
    assert out == "Your pension depends on two things. "
    assert broken is None


def test_a_partial_sentence_is_held_until_it_completes():
    """Held, not dropped -- and released the moment the full stop arrives."""
    gate = StreamGate(AVAILABLE)
    assert gate.feed("Your pension is based") == ("", None)
    assert gate.feed(" on your service.") == ("Your pension is based on your service.", None)


def test_a_sentence_citing_a_retrieved_section_is_released():
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("The annuity is fixed at withdrawal [40 ILCS 5/11-134]. ")
    assert "11-134" in out
    assert broken is None


def test_a_sentence_citing_an_unretrieved_section_is_never_emitted():
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("Article 4 gives you 50% of salary [40 ILCS 5/4-109]. ")
    assert out == ""
    assert broken == "4-109"


def test_once_broken_the_gate_stays_shut():
    """The rest of the answer rests on the sentence that failed."""
    gate = StreamGate(AVAILABLE)
    gate.feed("Article 4 gives you 50% [40 ILCS 5/4-109]. ")
    assert gate.feed("And then some more text. ") == ("", "4-109")
    assert gate.flush() == ("", "4-109")
    assert gate.broken == "4-109"


def test_good_sentences_before_a_bad_one_are_kept():
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed(
        "Your annuity is fixed at withdrawal [40 ILCS 5/11-134]. "
        "Article 4 pays 50% [40 ILCS 5/4-109]. "
    )
    assert "11-134" in out
    assert "4-109" not in out
    assert broken == "4-109"


def test_flush_releases_a_clean_trailing_sentence_without_a_full_stop():
    gate = StreamGate(AVAILABLE)
    gate.feed("Your service counts under Section 11-216")
    assert gate.flush() == ("Your service counts under Section 11-216", None)


def test_flush_withholds_a_trailing_sentence_that_does_not_clear():
    gate = StreamGate(AVAILABLE)
    gate.feed("See [40 ILCS 5/4-109]")
    out, broken = gate.flush()
    assert out == ""
    assert broken == "4-109"


def test_a_citation_split_across_deltas_is_not_judged_early():
    """Tokens arrive mid-citation. Judging '[40 ILCS 5/11-1' would reject a
    section that is about to be fine."""
    gate = StreamGate(AVAILABLE)
    assert gate.feed("The rule is [40 ILCS 5/11-") == ("", None)
    out, broken = gate.feed("134]. ")
    assert "11-134" in out
    assert broken is None


def test_a_bare_mention_is_not_gated():
    """Only bracketed citations are gated here. A bare mention is held to a
    weaker standard by `verify_answer` on purpose -- Article 11 is written in
    cross-references, and naming the provision a retrieved section sends you to
    is reading the page rather than recalling it."""
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("Service is governed by Section 4-109. ")
    assert out == "Service is governed by Section 4-109. "
    assert broken is None


def test_newlines_end_a_sentence():
    """Blocks and list items arrive without full stops."""
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("- 2.40% for each year [40 ILCS 5/11-134]\n")
    assert "11-134" in out
    assert broken is None


def test_an_empty_feed_is_harmless():
    gate = StreamGate(AVAILABLE)
    assert gate.feed("") == ("", None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_grounding.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'pension_chat.agent.grounding'`

- [ ] **Step 3: Write the module**

Create `src/pension_chat/agent/grounding.py`:

```python
"""Catching an ungrounded claim while it is still being written.

`verify_answer` runs after generation, which is the right place for it: holding
the whole answer back to check it would put the model's full generation time
onto time-to-first-token. The cost is that an answer citing a section nobody
retrieved streams in full and is replaced seconds later, and in between the
member reads a sentence about their pension that the Code does not support.

A bracketed citation does not need generation to finish to be checkable. It is
set membership against the sections already retrieved -- no model call, no
await, microseconds. So each sentence is held until its citations clear, and one
that does not clear never leaves the server.

**Only bracketed citations are gated.** A bare mention is held to a weaker
standard by `verify_answer` deliberately: Article 11 is written in
cross-references, so naming the provision a retrieved section sends you to is
reading the page rather than recalling from memory. Gating those here would stop
the assistant describing a cross-reference at all.

**Once broken, the gate stays shut.** What follows a failed sentence was written
on top of it.
"""

from __future__ import annotations

import re

from pension_chat.agent.guardrails import _CITATION

#: A sentence ends at a full stop, a question or exclamation mark followed by
#: whitespace, or at a newline. Lists and rendered blocks arrive without full
#: stops, so a newline has to count.
_BOUNDARY = re.compile(r"(?<=[.!?])\s|\n")

#: The opening of a citation, so a delta that stops mid-citation is held rather
#: than judged. "[40 ILCS 5/11-" names no section yet.
_OPEN_CITATION = re.compile(r"40\s*ILCS\s*5?/?\s*\d{0,2}-?\d{0,3}\.?\d{0,2}$", re.IGNORECASE)


class StreamGate:
    """Releases streamed text one settled sentence at a time."""

    def __init__(self, available: set[str]) -> None:
        self._available = {str(s) for s in available}
        self._held = ""
        self.broken: str | None = None

    def feed(self, text: str) -> tuple[str, str | None]:
        """Take the next chunk; return what may be shown and what broke."""
        if self.broken is not None:
            return "", self.broken
        self._held += text or ""
        return self._release(final=False)

    def flush(self) -> tuple[str, str | None]:
        """End of stream: judge whatever is left as a sentence in its own right."""
        if self.broken is not None:
            return "", self.broken
        return self._release(final=True)

    def _release(self, *, final: bool) -> tuple[str, str | None]:
        released: list[str] = []
        while self._held:
            match = _BOUNDARY.search(self._held)
            if match:
                sentence, rest = self._held[: match.end()], self._held[match.end() :]
            elif final:
                sentence, rest = self._held, ""
            else:
                # An incomplete sentence, or one whose citation is still
                # arriving, waits for more text.
                break
            offender = self._offender(sentence)
            if offender is not None:
                self.broken = offender
                self._held = ""
                return "".join(released), offender
            released.append(sentence)
            self._held = rest
        if not final and _OPEN_CITATION.search(self._held):
            # Nothing to do -- the partial citation is already held -- but say
            # so, because this is the case the gate is easiest to get wrong on.
            pass
        return "".join(released), None

    def _offender(self, sentence: str) -> str | None:
        for section in _CITATION.findall(sentence):
            if str(section) not in self._available:
                return str(section)
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_grounding.py -q`
Expected: 12 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/pension_chat/agent/grounding.py tests/test_grounding.py
git add src/pension_chat/agent/grounding.py tests/test_grounding.py
git commit -m "Grounding: judge a citation when it appears, not when the answer ends"
```

---

### Task 2: Gate the answer stream

**Files:**
- Modify: `src/pension_chat/agent/graph.py` — the token loop in `run_agent_stream()`, and the verification that follows it
- Test: `tests/test_stream_grounding.py` (create)

**Interfaces:**
- Consumes: `StreamGate` from `agent.grounding`.
- Produces: `run_agent_stream` emits no token belonging to a sentence whose bracketed citation was never retrieved. When the gate breaks, the turn's `GuardrailReport` carries a hard failure with code `STREAM_UNGROUNDED`, so the existing repair-then-fallback path runs.

- [ ] **Step 1: Write the failing test**

Create `tests/test_stream_grounding.py`:

```python
"""The stream stops at the first sentence the evidence does not support.

Without this, an ungrounded answer streams in full and is replaced at `done`.
The member reads it in the meantime. With it, at most one sentence is composed
and none of it is sent.
"""

from __future__ import annotations

from pension_chat.agent.grounding import StreamGate


def _drive(gate: StreamGate, deltas: list[str]) -> tuple[str, str | None]:
    """What a consumer of the gate actually sees across a whole stream."""
    shown = []
    for delta in deltas:
        text, broken = gate.feed(delta)
        if text:
            shown.append(text)
        if broken:
            return "".join(shown), broken
    tail, broken = gate.flush()
    if tail:
        shown.append(tail)
    return "".join(shown), broken


def test_a_clean_answer_streams_whole():
    gate = StreamGate({"11-134"})
    shown, broken = _drive(
        gate,
        ["Your annuity ", "is fixed at withdrawal ", "[40 ILCS 5/11-134]. ", "That is the rule."],
    )
    assert broken is None
    assert shown == (
        "Your annuity is fixed at withdrawal [40 ILCS 5/11-134]. That is the rule."
    )


def test_the_member_never_sees_the_ungrounded_sentence():
    gate = StreamGate({"11-134"})
    shown, broken = _drive(
        gate,
        [
            "Your annuity is fixed at withdrawal [40 ILCS 5/11-134]. ",
            "As a firefighter you would get 50% ",
            "[40 ILCS 5/4-109]. ",
            "So retiring now is better.",
        ],
    )
    assert broken == "4-109"
    assert "4-109" not in shown
    assert "firefighter" not in shown
    assert "retiring now is better" not in shown
    assert "11-134" in shown


def test_what_was_already_sound_is_kept():
    """The answer is cut, not discarded -- the repair path rewrites from here."""
    gate = StreamGate({"11-134"})
    shown, _ = _drive(
        gate, ["First point [40 ILCS 5/11-134]. ", "Second point [40 ILCS 5/9-134]. "]
    )
    assert shown.strip() == "First point [40 ILCS 5/11-134]."
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_stream_grounding.py -q`
Expected: PASS for the first test, FAIL for the others only if `StreamGate` is wrong — this task's test exercises the gate through a consumer, so if Task 1 is correct these pass immediately. That is intended: this file pins the *contract* Task 2's wiring must preserve, and Step 3 is what makes the wiring use it.

- [ ] **Step 3: Wire the gate into the stream**

In `src/pension_chat/agent/graph.py`, add the import beside the other agent imports:

```python
from pension_chat.agent.grounding import StreamGate
```

Replace the token loop. The existing block is:

```python
    chunks: list[str] = []
    first_token_at: float | None = None
    substituter = MarkerSubstituter(state.get("answer_blocks") or {})
    try:
        async for delta in stream_llm(
            node="generate_answer",
            messages=messages,
            reasoning_effort=settings.reasoning_effort_answer,
        ):
            if first_token_at is None:
                first_token_at = time.perf_counter() - started
            text = substituter.feed(delta)
            if text:
                chunks.append(text)
                yield {"type": "token", "text": text}
```

with:

```python
    chunks: list[str] = []
    first_token_at: float | None = None
    substituter = MarkerSubstituter(state.get("answer_blocks") or {})
    # A citation is checkable the instant it appears -- set membership against
    # what was retrieved, no model call. Holding each sentence until its
    # citations clear costs a fraction of a second and means an ungrounded
    # claim is never sent, rather than being sent and replaced at `done`.
    gate = StreamGate(
        {
            str(section.get("section"))
            for section in (state.get("retrieved_sections") or [])
            if section.get("section")
        }
        | {str(section) for section in (state.get("cited_sections") or {})}
    )
    ungrounded: str | None = None
    try:
        async for delta in stream_llm(
            node="generate_answer",
            messages=messages,
            reasoning_effort=settings.reasoning_effort_answer,
        ):
            text, broken = gate.feed(substituter.feed(delta))
            if text:
                if first_token_at is None:
                    first_token_at = time.perf_counter() - started
                chunks.append(text)
                yield {"type": "token", "text": text}
            if broken:
                ungrounded = broken
                break
```

Immediately after the `except Exception` block that follows that loop, replace the existing flush of the substituter:

```python
    if tail := substituter.flush():
        chunks.append(tail)
        yield {"type": "token", "text": tail}
```

with:

```python
    if ungrounded is None:
        tail, broken = gate.feed(substituter.flush())
        if tail:
            chunks.append(tail)
            yield {"type": "token", "text": tail}
        if broken:
            ungrounded = broken
        else:
            tail, broken = gate.flush()
            if tail:
                chunks.append(tail)
                yield {"type": "token", "text": tail}
            if broken:
                ungrounded = broken
```

Then, immediately after `report = _verify(state, answer, settings)`, add:

```python
    if ungrounded is not None:
        # The stream was cut at the sentence that named this, so the text now
        # in `answer` no longer contains it and would verify clean. Failing
        # explicitly is what sends this turn down the repair-then-fallback
        # path rather than delivering a silently truncated answer.
        report.fail(
            "STREAM_UNGROUNDED",
            f"The answer began citing 40 ILCS 5/{ungrounded}, which was not among "
            "the sections retrieved for it. The stream was stopped there.",
        )
        state["guardrail_passed"] = False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_stream_grounding.py tests/test_grounding.py tests/test_guardrails.py -q`
Expected: all pass

- [ ] **Step 5: Run the whole offline suite**

Run: `uv run pytest tests/ -q`
Expected: no new failures. `tests/test_degradation.py` and `tests/test_capture.py` exercise the replace-and-fallback path; they are the ones to read if anything moves.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/pension_chat/agent/graph.py tests/test_stream_grounding.py
git add src/pension_chat/agent/graph.py tests/test_stream_grounding.py
git commit -m "Stream: an ungrounded sentence is never sent, only never finished"
```

---

### Task 3: The seam that keeps `pushed_back` alive under prefetch

**Files:**
- Modify: `src/pension_chat/agent/loop.py` — `investigate()`
- Test: `tests/test_loop_pushback.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `investigate()` tracks `prefetched: set[str]` — section ids handed to the loop before its first model call — and pushes back when the model declares itself done having read nothing **beyond** those. Today `prefetched` is always empty, so behaviour is unchanged; the later prefetch plan populates it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_loop_pushback.py`:

```python
"""The guard against answering from memory, and the seam that keeps it.

The loop pushes back once when the model declares itself done having read
nothing. That guard exists because the loop once answered from memory that
Article 11 is the Municipal Employees' fund -- it is the Laborers' fund -- and
verification passed it, because an answer with no citations has nothing to
verify.

A later plan hands the loop statute text before its first model call. That would
make `sections` non-empty on every turn and silently retire the guard. The
invariant has to be "read nothing it was not handed", not "read nothing".
"""

from __future__ import annotations

from pension_chat.agent.loop import _read_beyond_prefetch


def test_nothing_read_at_all_is_a_pushback():
    assert _read_beyond_prefetch(sections=[], prefetched=set()) == []


def test_reading_something_unprompted_is_not():
    sections = [{"section": "11-134"}]
    assert _read_beyond_prefetch(sections=sections, prefetched=set()) == ["11-134"]


def test_being_handed_a_section_is_not_the_same_as_reading_one():
    """The case the seam exists for: prefetch fills `sections`, the model reads
    nothing more, and the guard must still fire."""
    sections = [{"section": "11-134"}]
    assert _read_beyond_prefetch(sections=sections, prefetched={"11-134"}) == []


def test_reading_past_what_was_handed_over_counts():
    sections = [{"section": "11-134"}, {"section": "11-216"}]
    assert _read_beyond_prefetch(sections=sections, prefetched={"11-134"}) == ["11-216"]


def test_a_section_with_no_id_is_ignored_rather_than_counted():
    assert _read_beyond_prefetch(sections=[{"text": "..."}], prefetched=set()) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_loop_pushback.py -q`
Expected: `ImportError: cannot import name '_read_beyond_prefetch' from 'pension_chat.agent.loop'`

- [ ] **Step 3: Add the helper and use it**

In `src/pension_chat/agent/loop.py`, add this function immediately above `async def investigate(`:

```python
def _read_beyond_prefetch(
    *, sections: list[dict[str, Any]], prefetched: set[str]
) -> list[str]:
    """Section ids this loop found for itself, rather than being handed.

    The pushback guard asks whether the model read anything before declaring
    itself done. Once a later turn's evidence is prefetched before the first
    model call, `sections` is never empty and that question stops being the one
    worth asking -- a model that ignored everything it was given looks exactly
    like one that went and found it.
    """
    return [
        str(section["section"])
        for section in sections
        if section.get("section") and str(section["section"]) not in prefetched
    ]
```

In `investigate()`, immediately after `pushed_back = False`, add:

```python
    # Section ids handed to the loop before its first model call. Empty until
    # the prefetch plan lands; the guard below reads it so that plan cannot
    # retire the guard by accident.
    prefetched: set[str] = set()
```

and change the pushback condition from:

```python
            if not sections and not pushed_back:
```

to:

```python
            if not _read_beyond_prefetch(sections=sections, prefetched=prefetched) and (
                not pushed_back
            ):
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop_pushback.py tests/test_loop.py -q`
Expected: all pass

- [ ] **Step 5: Run the whole offline suite, lint, commit**

```bash
uv run pytest tests/ -q
uv run ruff check src/pension_chat/agent/loop.py tests/test_loop_pushback.py
git add src/pension_chat/agent/loop.py tests/test_loop_pushback.py
git commit -m "Loop: the pushback guard asks what was read, not what is present"
```

---

## Self-Review

**Spec coverage.** This implements §8's incremental verification and the `pushed_back` invariant, which together are §13 step 3. §8's downgrade ladder — per-block verdicts, `estimate` downgrading to `requirements` — is **not** here: it depends on blocks carrying registers, which the superseding spec dissolved, so it needs redesigning against claim provenance before it can be planned. That is the one piece of §8 left outstanding and it is named here rather than silently skipped.

**A latency cost, accepted deliberately.** Holding each sentence until it completes delays the first token by roughly one sentence of generation — at 50–100 tokens/s and ~25 tokens a sentence, about 0.25–0.5s. Against the measured 3.84s fast path that is near 10%. It is worth it: the alternative is sending a legal claim the Code does not support and taking it back. **This must be measured** once the prefetch path lands, and the KPI it moves is time-to-first-grounded-token, which is the one the spec says to optimise.

**Type consistency.** `StreamGate.feed` and `.flush` both return `tuple[str, str | None]`, used that way in Tasks 1 and 2. `_read_beyond_prefetch` returns `list[str]` and is used only for its truthiness in Task 3, which is why the tests assert on the list rather than on a bool.

**One risk.** Task 2 changes when `first_token_at` is recorded — it now marks the first token *released* rather than the first delta *received*. That is deliberate and matches the TFGT definition in §10, but it means the timings key is no longer comparable with runs recorded before this change. Anything comparing against the 7.98s and 3.84s baselines must note the definition moved.
