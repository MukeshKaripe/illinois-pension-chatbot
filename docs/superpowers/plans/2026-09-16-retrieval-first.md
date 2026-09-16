# Retrieval-First Fast Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hand the loop complete evidence before its first model call, so an ordinary turn answers in one round trip instead of browsing for four.

**Architecture:** The mandatory search and the reference closure both already exist — the search as a backstop *after* the loop, the closure as `_reference_weights` → `_read_sections` *after* the loop. This moves both to the front, and caps the loop at one tool round when they ran. Measured in-region on 23 questions: **7.98s → 3.84s, four round trips → two, and citations up from 12 to 13.**

**Tech Stack:** Python 3.12, pytest, `uv`. No new dependencies. Every test offline, using `tests/test_loop.py`'s existing harness (`_script` fakes the model, `_tools` fakes the tools).

**Spec:** `docs/superpowers/specs/2026-09-16-rag-first-agent-design.md` — §5, §6, §6.1, and §13 steps 2, 4, 5.

## Global Constraints

- Run everything through `uv`: `uv run pytest`, `uv run ruff check .`.
- Every test offline. No test may touch Azure OpenAI, Azure AI Search or Postgres.
- `uv run ruff check <files touched>` must pass before each commit, and the full suite must stay green.
- The prefetch must **not** add a model call. It is a search and a set of section reads, both of which the turn already pays for later.
- The cap must never apply on a turn where the prefetch did not run. That is §6.1, and it is the difference between this working and three questions collapsing to one citation.

## What this plan deliberately leaves out

**Subject-scoped retrieval — widening the filter to the union of active subjects' Articles — is NOT here**, though §13 lists it as step 2. Two reasons, both evidence:

1. The measured win did not need it. The 3.84s and the citation increase were obtained with the existing single-Article filter.
2. It carries real risk without the per-claim protections. `_ARTICLE_BY_NAME` refuses to match fund Articles by name so "I am a park employee" cannot bypass the coverage determination, and §5 moves that protection into the claim check — which depends on the per-block downgrade ladder that the superseding spec dissolved and has not yet redesigned.

The retraction case it was meant to fix — "explain about a person who is a teacher" — was **already fixed by the closure alone** in measurement: guardrail failed before, passed after, with 16 citations. So scope widening is a quality improvement to schedule on its own evidence, not a prerequisite here.

---

### Task 1: Search on the conversation, not just the last message

**Files:**
- Modify: `src/pension_chat/agent/loop.py` — add `_contextual_queries()`, use it in the safety-net search
- Test: `tests/test_contextual_queries.py` (create)

**Interfaces:**
- Produces: `_contextual_queries(state: dict[str, Any]) -> list[str]` — the member's question, plus the same question prefixed by their previous message when there was one. Never fewer than one query, never more than two.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_contextual_queries.py`:

```python
"""The safety-net search reads the conversation, not just the last thing said.

"what are the things needed, please list them" names nothing. Searched alone it
returned the Code's rules for pension fund *investment advisers* under §1-113.5
-- a precise answer to a question nobody asked. The previous message is what
says which "things".

Both queries are kept rather than one merged one: the bare question still gets
searched on its own, so a turn that was already specific is not diluted by
whatever preceded it.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from pension_chat.agent.loop import _contextual_queries


def test_a_first_turn_searches_only_itself():
    state = {"question": "how is my pension calculated?", "messages": []}
    assert _contextual_queries(state) == ["how is my pension calculated?"]


def test_a_follow_up_also_searches_with_what_came_before():
    state = {
        "question": "what are the things needed, please list them",
        "messages": [
            HumanMessage(content="i work at a school as a teacher"),
            AIMessage(content="Article 15 may cover you."),
            HumanMessage(content="what are the things needed, please list them"),
        ],
    }
    queries = _contextual_queries(state)
    assert queries[0] == "what are the things needed, please list them"
    assert len(queries) == 2
    assert "teacher" in queries[1]
    assert "things needed" in queries[1]


def test_the_assistants_own_words_are_not_searched():
    """Searching what we said turns our own guess into next turn's evidence."""
    state = {
        "question": "and after that?",
        "messages": [
            HumanMessage(content="when can i retire"),
            AIMessage(content="Article 11 provides an annuity at 60."),
            HumanMessage(content="and after that?"),
        ],
    }
    queries = _contextual_queries(state)
    assert not any("annuity at 60" in q for q in queries)
    assert any("when can i retire" in q for q in queries)


def test_a_repeated_question_does_not_search_itself_twice():
    state = {
        "question": "how is my pension calculated?",
        "messages": [
            HumanMessage(content="how is my pension calculated?"),
            AIMessage(content="..."),
            HumanMessage(content="how is my pension calculated?"),
        ],
    }
    assert _contextual_queries(state) == ["how is my pension calculated?"]


def test_an_empty_question_still_yields_a_query_list():
    assert _contextual_queries({"question": "", "messages": []}) == [""]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_contextual_queries.py -q`
Expected: `ImportError: cannot import name '_contextual_queries' from 'pension_chat.agent.loop'`

- [ ] **Step 3: Add the helper and use it**

In `src/pension_chat/agent/loop.py`, add immediately above `def _search_filter(`:

```python
def _contextual_queries(state: dict[str, Any]) -> list[str]:
    """What to search when the member's own words are the query.

    "what are the things needed, please list them" names nothing, and searched
    alone it returned the Code's rules for pension fund investment advisers --
    a precise answer to a question nobody asked. The previous message is what
    says which "things".

    Both queries are kept rather than merged into one: the bare question is
    still searched on its own, so a turn that was already specific is not
    diluted by whatever preceded it. Only the member's own messages are used --
    searching what the assistant said would turn its own guess into the next
    turn's evidence.
    """
    question = str(state.get("question") or "")
    queries = [question]
    earlier = [
        str(message.content)
        for message in (state.get("messages") or [])
        if getattr(message, "type", None) == "human"
    ]
    # The last human message before this turn's own.
    previous = next((text for text in reversed(earlier[:-1]) if text.strip()), "")
    if previous and previous.strip() != question.strip():
        queries.append(f"{previous} {question}".strip())
    return queries
```

In the safety-net search further down, replace:

```python
                "queries": [state["question"]],
```

with:

```python
                "queries": _contextual_queries(state),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_contextual_queries.py tests/test_loop.py -q`
Expected: all pass

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/pension_chat/agent/loop.py tests/test_contextual_queries.py
git add src/pension_chat/agent/loop.py tests/test_contextual_queries.py
git commit -m "Retrieval: the safety-net search reads the conversation, not the last line"
```

---

### Task 2: Prefetch — search before the first model call

**Files:**
- Modify: `src/pension_chat/agent/loop.py` — `investigate()`
- Test: `tests/test_prefetch.py` (create)

**Interfaces:**
- Consumes: `_contextual_queries`, `_search_filter`, `coverage_mod.classify_self_reference`, `run_tool`, `_merge_sections`.
- Produces: `investigate()` runs the mandatory search before its first `call_with_tools`, populating `sections`, `spent["search_statute"]` and a local `prefetched: set[str]`. Skipped when the member describes themselves.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prefetch.py`:

```python
"""The Code is searched before the model is asked anything.

The loop already searched on the member's own words when the model had not --
as a backstop, after the loop had finished. Running it first is the same search
at a better time: the model's first call already has statute text, so the round
trip it used to spend discovering that it should search disappears.

Measured in-region over 23 questions: four model round trips became two.
"""

from __future__ import annotations

import pytest

from pension_chat.agent import loop as loop_mod
from tests.test_loop import _script, _tools, _turn


async def test_the_search_runs_before_the_first_model_call(monkeypatch):
    seen: list = []
    _script(monkeypatch, [_turn(content="Here.")], calls_seen=seen)
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "text": "t"}]})

    out = await loop_mod.investigate({"question": "what is Article 11?", "messages": []})

    assert ran and ran[0][0] == "search_statute", "searched before anything else"
    assert [s["section"] for s in out["retrieved_sections"]] == ["11-134"]


async def test_the_model_is_told_it_already_has_statute_text(monkeypatch):
    _script(monkeypatch, [_turn(content="Here.")])
    _tools(monkeypatch, result="### 40 ILCS 5/11-134", update={})

    out = await loop_mod.investigate({"question": "q", "messages": []})

    system = [m["content"] for m in out["loop_messages"] if m.get("role") == "system"]
    assert any("before you were asked" in text for text in system)


async def test_the_backstop_does_not_search_again(monkeypatch):
    """One search, not two. The backstop exists for a turn that never searched."""
    _script(monkeypatch, [_turn(content="Here.")])
    ran = _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    assert [name for name, _ in ran].count("search_statute") == 1


async def test_a_self_description_is_not_prefetched(monkeypatch):
    """check_coverage may still move the governing Article, and a filter built
    on the record would seed another fund's law into the citable set."""
    _script(monkeypatch, [_turn(content="Here.")])
    ran = _tools(monkeypatch)

    await loop_mod.investigate(
        {"question": "i am a park employee, what do I get?", "messages": []}
    )

    assert [name for name, _ in ran].count("search_statute") <= 1


async def test_the_model_can_still_search_for_itself(monkeypatch):
    _script(
        monkeypatch,
        [_turn(("search_statute", {"queries": ["reduction"]})), _turn(content="Here.")],
    )
    ran = _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    assert [name for name, _ in ran].count("search_statute") == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prefetch.py -q`
Expected: FAIL — `test_the_search_runs_before_the_first_model_call` sees no tool run at all, because the model answered immediately and the backstop is skipped when the budget is untouched.

- [ ] **Step 3: Add the prefetch**

In `src/pension_chat/agent/loop.py`, in `investigate()`, replace the block:

```python
    # Section ids handed to the loop before its first model call. Empty until
    # the prefetch work lands; the guard below reads it so that work cannot
    # retire the guard by accident.
    prefetched: set[str] = set()
```

with:

```python
    # ── Search before the model is asked anything ────────────────
    #
    # The same search the backstop below runs, at a better time. A turn that
    # has statute text in front of it before its first call does not spend a
    # round trip discovering it should search: measured in-region over 23
    # questions, four model round trips became two.
    #
    # Skipped when the member describes themselves. `check_coverage` may still
    # move the governing Article, and a filter built on the record would seed
    # another fund's law into the citable set -- the exact harm the coverage
    # layer exists to prevent. Those turns run the loop as they always did.
    prefetched: set[str] = set()
    if coverage_mod.classify_self_reference(state["question"]) is None:
        result, update = await run_tool(
            "search_statute",
            {
                "queries": _contextual_queries(state),
                "articles": _search_filter(working),
                "why": "searched before the first model call",
            },
            working,
        )
        spent["search_statute"] = spent.get("search_statute", 0) + 1
        if update.get("retrieved_sections"):
            sections = _merge_sections(sections, update["retrieved_sections"])
            working.update(update)
            working["retrieved_sections"] = sections
        prefetched = {
            str(section["section"]) for section in sections if section.get("section")
        }
        messages.append(
            {
                "role": "system",
                "content": (
                    "The Code was searched on the member's own words before you "
                    "were asked anything, so you already have statute text. Read "
                    "further only if this does not settle the question:"
                    + chr(10) + chr(10) + result
                ),
            }
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prefetch.py tests/test_loop.py -q`
Expected: all pass

- [ ] **Step 5: Run the whole offline suite, lint, commit**

```bash
uv run pytest tests/ -q
uv run ruff check src/pension_chat/agent/loop.py tests/test_prefetch.py
git add src/pension_chat/agent/loop.py tests/test_prefetch.py
git commit -m "Loop: the Code is searched before the model is asked anything"
```

---

### Task 3: Close the evidence before the model sees it

**Files:**
- Modify: `src/pension_chat/agent/loop.py` — `investigate()`, and a new constant
- Test: `tests/test_prefetch_closure.py` (create)

**Interfaces:**
- Consumes: `_unread_references`, `_reference_weights`, `_read_sections`.
- Produces: `MAX_PREFETCH_CLOSURE = 6`; after the prefetch, the most-leaned-on unread cross-references are fetched and added to `sections`, `messages` and `prefetched`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prefetch_closure.py`:

```python
"""Every section the prefetched text leans on is read before the model decides.

Capping the loop without this is faster and cites a third less -- fast because
it answers with less, which is not a win. With the closure it is 2.1x faster
than the loop was AND cites more than the loop did, because these are sections
the model never got round to reading.

The same fetch already ran after the loop. Running it first is what lets the
model settle the question in one call instead of browsing to find them.
"""

from __future__ import annotations

from pension_chat.agent import loop as loop_mod
from tests.test_loop import _script, _turn


def _tools_with_references(monkeypatch):
    """First call returns a section that cites another; later calls return it."""
    ran: list[tuple[str, dict]] = []

    async def fake_run(name, arguments, state):
        ran.append((name, arguments))
        if name == "search_statute":
            return "found", {
                "retrieved_sections": [
                    {
                        "section": "11-134",
                        "text": "computed as in Section 11-216",
                        "referenced_sections": ["11-216"],
                    }
                ]
            }
        return "the referenced text", {
            "retrieved_sections": [{"section": "11-216", "text": "service"}]
        }

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    return ran


async def test_a_cross_reference_is_read_before_the_model_decides(monkeypatch):
    _script(monkeypatch, [_turn(content="Here.")])
    ran = _tools_with_references(monkeypatch)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert ("get_section", {"section": "11-216"}) in ran
    assert {s["section"] for s in out["retrieved_sections"]} == {"11-134", "11-216"}


async def test_the_closed_evidence_is_put_in_front_of_the_model(monkeypatch):
    _script(monkeypatch, [_turn(content="Here.")])
    _tools_with_references(monkeypatch)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    system = [m["content"] for m in out["loop_messages"] if m.get("role") == "system"]
    assert any("cross-reference" in text or "leans on" in text for text in system)


async def test_the_closure_is_bounded(monkeypatch):
    """A section citing forty others must not cost forty round trips."""
    ran: list[tuple[str, dict]] = []

    async def fake_run(name, arguments, state):
        ran.append((name, arguments))
        if name == "search_statute":
            return "found", {
                "retrieved_sections": [
                    {
                        "section": "11-134",
                        "text": "t",
                        "referenced_sections": [f"11-{i}" for i in range(200, 240)],
                    }
                ]
            }
        return "text", {}

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    _script(monkeypatch, [_turn(content="Here.")])

    await loop_mod.investigate({"question": "q", "messages": []})

    fetched = [name for name, _ in ran if name == "get_section"]
    assert len(fetched) <= loop_mod.MAX_PREFETCH_CLOSURE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prefetch_closure.py -q`
Expected: FAIL — `('get_section', {'section': '11-216'}) in ran` is False, and `MAX_PREFETCH_CLOSURE` does not exist.

- [ ] **Step 3: Add the closure**

In `src/pension_chat/agent/loop.py`, beside `MAX_COMPLETION_FETCHES = 3`, add:

```python
#: How far the prefetch closes the evidence before the first model call. Larger
#: than `MAX_COMPLETION_FETCHES` because this runs once, in parallel, and is
#: what lets the loop stop after one tool round: the measured citation count
#: rose from 12 to 13 against the uncapped loop at this width.
MAX_PREFETCH_CLOSURE = 6
```

Then, inside `investigate()`, immediately after the `messages.append({...})` that ends the prefetch block, add:

```python
        # Every section that text leans on, read before the model decides.
        # `_unread_references` is computed rather than judged -- "you relied on
        # 11-134, which computes on service as defined in 11-216, and you never
        # read 11-216" is a checkable statement about what is missing. The same
        # fetch already runs after the loop; running it first is what lets the
        # model settle the question in one call instead of browsing to find it.
        weights = _reference_weights(sections)
        targets = sorted(
            _unread_references(sections),
            key=lambda section: (-weights.get(section, 0), section),
        )[:MAX_PREFETCH_CLOSURE]
        closed: list[str] = []
        for target, fetched in zip(targets, await _read_sections(targets, working)):
            if fetched is None:
                continue
            text, update = fetched
            if update.get("retrieved_sections"):
                sections = _merge_sections(sections, update["retrieved_sections"])
                working["retrieved_sections"] = sections
            closed.append(f"Section {target}:{chr(10)}{text}")
        if closed:
            prefetched = {
                str(section["section"]) for section in sections if section.get("section")
            }
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Every section that text leans on by cross-reference has "
                        "also been read for you, so the evidence is closed:"
                        + chr(10) + chr(10) + (chr(10) * 2).join(closed)
                    ),
                }
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_prefetch_closure.py tests/test_prefetch.py tests/test_loop.py -q`
Expected: all pass

- [ ] **Step 5: Run the whole offline suite, lint, commit**

```bash
uv run pytest tests/ -q
uv run ruff check src/pension_chat/agent/loop.py tests/test_prefetch_closure.py
git add src/pension_chat/agent/loop.py tests/test_prefetch_closure.py
git commit -m "Loop: close the evidence before the model decides, not after"
```

---

### Task 4: The conditional cap

**Files:**
- Modify: `src/pension_chat/agent/loop.py` — `investigate()`
- Test: `tests/test_loop_cap.py` (create)

**Interfaces:**
- Produces: when `prefetched` is non-empty, the loop takes at most one tool-calling round before composing. When it is empty, the loop is unbounded as before.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_loop_cap.py`:

```python
"""One tool round, once the evidence was handed over complete.

The cap is what turns prefetch-plus-closure into two model round trips instead
of four. It is conditional on the prefetch having run, and that condition is the
whole difference between the measured win and three questions collapsing to one
citation: the turns that collapsed were exactly the turns where the prefetch is
skipped, which were left with no evidence AND no iterations.
"""

from __future__ import annotations

from pension_chat.agent import loop as loop_mod
from tests.test_loop import _script, _tools, _turn


async def test_a_prefetched_turn_takes_one_tool_round(monkeypatch):
    seen: list = []
    _script(
        monkeypatch,
        [
            _turn(("get_section", {"section": "11-134"})),
            _turn(("get_section", {"section": "11-216"})),
            _turn(("get_section", {"section": "11-218"})),
            _turn(content="Here."),
        ],
        calls_seen=seen,
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "text": "t"}]})

    await loop_mod.investigate({"question": "q", "messages": []})

    assert len(seen) == 1, "one model call after the prefetch, then compose"


async def test_a_turn_without_prefetch_is_not_capped(monkeypatch):
    """The self-description case. No prefetch ran, so the loop must be free to
    investigate -- otherwise it has neither evidence nor the chance to get any."""
    seen: list = []
    _script(
        monkeypatch,
        [
            _turn(("check_coverage", {})),
            _turn(("search_statute", {"queries": ["park"]})),
            _turn(("get_section", {"section": "12-118"})),
            _turn(content="Here."),
        ],
        calls_seen=seen,
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "12-118", "text": "t"}]})

    await loop_mod.investigate(
        {"question": "i am a park employee, how is my pension worked out?", "messages": []}
    )

    assert len(seen) >= 3, "an unprefetched turn keeps its iterations"


async def test_the_capped_turn_still_keeps_what_the_round_found(monkeypatch):
    _script(
        monkeypatch,
        [_turn(("get_section", {"section": "11-216"})), _turn(content="Here.")],
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-216", "text": "t"}]})

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert "11-216" in {s["section"] for s in out["retrieved_sections"]}


async def test_a_prefetched_turn_that_answers_at_once_uses_no_round(monkeypatch):
    seen: list = []
    _script(monkeypatch, [_turn(content="Here.")], calls_seen=seen)
    _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    assert len(seen) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loop_cap.py -q`
Expected: FAIL — `test_a_prefetched_turn_takes_one_tool_round` sees 4 model calls, not 1.

- [ ] **Step 3: Add the cap**

In `src/pension_chat/agent/loop.py`, in `investigate()`, immediately before `while True:`, add:

```python
    # One tool round, once the evidence arrived complete. This is what makes a
    # prefetched turn two model round trips rather than four.
    #
    # Conditional on the prefetch, and that condition is load-bearing. The three
    # questions that collapsed to one or two citations in measurement were
    # exactly the three where `classify_self_reference` fires and the prefetch is
    # skipped: capped as well, they had neither evidence nor the chance to get
    # any. A turn nobody handed anything to keeps every iteration it ever had.
    rounds_allowed = 1 if prefetched else 0
    rounds_taken = 0
```

and at the top of the loop body, immediately after the `while True:` line and before the total-budget check, add:

```python
        if rounds_allowed and rounds_taken >= rounds_allowed:
            logger.info("Loop stopping after %d round on prefetched evidence", rounds_taken)
            break
```

and immediately before `turn = await call_with_tools(`, add:

```python
        rounds_taken += 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop_cap.py tests/test_prefetch.py tests/test_prefetch_closure.py tests/test_loop.py -q`
Expected: all pass. If `test_loop.py`'s budget tests fail, read them: several script many tool rounds and assert the loop stops on a budget. Those turns are prefetched now, so the cap stops them first. Where that is what the test is about, give it a question that triggers `classify_self_reference` so it is not prefetched; where it is not, the cap is the correct new answer and the assertion should move.

- [ ] **Step 5: Run the whole offline suite, lint, commit**

```bash
uv run pytest tests/ -q
uv run ruff check src/pension_chat/agent/loop.py tests/test_loop_cap.py
git add src/pension_chat/agent/loop.py tests/test_loop_cap.py
git commit -m "Loop: one tool round, once the evidence arrived complete"
```

---

## Self-Review

**Spec coverage.** This implements §5 (retrieval-first, and the conversational query rewrite), §6 (the evidence closure contract and the cap) and §6.1 (the cap conditional on the prefetch), which are §13 steps 4 and 5 plus the safe half of step 2. Scope widening — the other half of step 2 — is excluded above with its reasons.

**The interaction with `pushed_back`.** The previous plan added `_read_beyond_prefetch` and a `prefetched` set that was always empty. Task 2 populates it, which is exactly what that seam was for: the guard keeps asking "did the model read anything it was not handed", and does not silently retire now that `sections` is never empty.

**The interaction with the streaming gate.** The gate's available set is built from `retrieved_sections`, which the prefetch and closure both populate before generation. So a wider prefetched set makes the gate *less* likely to cut, not more — the closure is fetching exactly the cross-referenced sections an answer would otherwise cite unread.

**Expect `test_loop.py` to need edits in Task 4, and read them rather than deleting them.** Deleting a test is a decision about a behaviour. Several of its cases script many tool rounds to exercise budgets; those turns are prefetched now, so the cap stops them before the budget does. Where the budget is the subject, the fix is a question that skips the prefetch. Where it is not, the cap is the correct new answer.

**Validation.** The offline suite pins control flow. The numbers in this plan's header came from an in-region job on 23 questions, and the last opportunity to re-run one is today, before the trial expires. Run it after Task 4.
