# Verification: a consequence proportional to the failure

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop a sound answer being withdrawn in full because one sentence in it
cited the wrong section.

**Architecture:** Each failure code carries a consequence — retract the answer,
excise the span, or regenerate. Excision runs in code with no model call: drop
the offending sentences, re-verify what is left, and publish that. An untraceable
figure still retracts everything, because it is the one failure that harms a
member.

**Tech Stack:** Python 3.12, pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-rag-first-agent-design.md` §8.

## Global Constraints

- **A figure that does not trace to engine output retracts the whole answer.**
  Not negotiable, and no excision path may weaken it.
- **An unknown failure code retracts.** Same rule as `ledger.rank`: what is not
  recognised sorts to the worst outcome, so a new check added without a
  consequence fails safe rather than silently becoming excisable.
- **Excision costs no model call.** If it needed one it would be repair, which
  already exists.
- **What is left must pass verification on its own.** Excision that publishes
  unverified text is worse than the retraction it replaces.
- Offline-testable throughout.

## The design question this was blocked on

§8 recorded the ladder as unbuildable: "the per-block downgrade ladder above
depends on blocks carrying registers, which §1 dissolved — it needs redesigning
against claim provenance before it can be built."

**The unit is the sentence, and the locator already exists.** Registers were
only ever needed to know which claim class a span belonged to — and the failure
itself already knows that: `UNVERIFIED_CITATION` is about a citation,
`UNSUPPORTED_COVERAGE` is about a coverage claim. What was missing was *where*,
and `guardrails._sentence_around(text, index)` has been in the file the whole
time, used by the coverage check to judge attribution in context. The streaming
gate settled the same question the same way when it chose a sentence boundary.

So there is no register-shaped hole. Each check records the span it objected to,
and the consequence table says what to do with it.

---

### Task 1: A failure knows what it costs

**Files:**
- Modify: `src/pension_chat/agent/guardrails.py`
- Test: `tests/test_downgrade_ladder.py` (create)

**Interfaces:**
- Produces: `guardrails.RETRACT/EXCISE/REPAIR`; `CONSEQUENCE: dict[str, str]`;
  `GuardrailReport.fail(code, message, spans=())`;
  `GuardrailReport.consequence -> str`.

- [ ] **Step 1: Write the failing test**

```python
"""What a failure costs, decided by what kind of failure it is.

Today every hard failure costs the same thing: the whole answer, replaced with
"I have taken that answer back -- sorry." That is right for a figure nobody can
trace and wrong for a sentence that named the wrong section beside four that
named the right one.
"""

from __future__ import annotations

from pension_chat.agent.guardrails import (
    CONSEQUENCE,
    EXCISE,
    REPAIR,
    RETRACT,
    GuardrailReport,
)


def test_an_untraceable_figure_still_retracts_everything():
    """The one failure that harms a member."""
    assert CONSEQUENCE["UNVERIFIED_FIGURE"] == RETRACT


def test_a_bad_citation_costs_its_own_sentence():
    assert CONSEQUENCE["UNVERIFIED_CITATION"] == EXCISE
    assert CONSEQUENCE["UNSUPPORTED_COVERAGE"] == EXCISE


def test_a_missing_disclosure_is_regenerated_not_cut():
    """Nothing in the answer is wrong -- a sentence is missing. Cutting text
    cannot add one."""
    assert CONSEQUENCE["ASSUMPTION_NOT_DISCLOSED"] == REPAIR
    assert CONSEQUENCE["UNMARKED_HYPOTHETICAL"] == REPAIR


def test_an_unrecognised_code_retracts():
    """A check added without a consequence must fail safe. The alternative is
    a new failure silently becoming excisable."""
    report = GuardrailReport()
    report.fail("SOMETHING_NEW", "...")
    assert report.consequence == RETRACT


def test_a_clean_report_costs_nothing():
    assert GuardrailReport().consequence == ""


def test_the_worst_failure_decides():
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "...", spans=["a."])
    report.fail("UNVERIFIED_FIGURE", "...")
    assert report.consequence == RETRACT


def test_repair_outranks_excision():
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "...", spans=["a."])
    report.fail("ASSUMPTION_NOT_DISCLOSED", "...")
    assert report.consequence == REPAIR


def test_an_excisable_failure_with_no_span_cannot_be_excised():
    """Knowing a sentence is wrong is not knowing which sentence."""
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "...")
    assert report.consequence == RETRACT
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_downgrade_ladder.py -q`
Expected: FAIL, `ImportError: cannot import name 'CONSEQUENCE'`.

- [ ] **Step 3: Add the table and the property**

```python
#: What a failure costs. Ordered worst first by `_COST`, because a report
#: carrying several failures pays the worst one.
RETRACT = "retract"
EXCISE = "excise"
REPAIR = "repair"

_COST = {RETRACT: 0, REPAIR: 1, EXCISE: 2}

#: A consequence per failure code. Anything absent retracts: a check added
#: without an entry here must fail safe rather than quietly become excisable.
CONSEQUENCE: dict[str, str] = {
    "EMPTY_ANSWER": RETRACT,
    # The one failure that harms a member, so it is the one that costs
    # everything. A figure nobody can trace is not improved by deleting the
    # sentence around it.
    "UNVERIFIED_FIGURE": RETRACT,
    # A sentence naming a section nobody retrieved, beside four naming
    # sections that were. Withdrawing all five is how a sound explanation of
    # teacher pensions became "I have taken that answer back -- sorry."
    "UNVERIFIED_CITATION": EXCISE,
    "UNSUPPORTED_COVERAGE": EXCISE,
    # Nothing here is wrong; something is missing. Cutting text cannot add a
    # disclosure, and cannot make a flat statement conditional.
    "ASSUMPTION_NOT_DISCLOSED": REPAIR,
    "UNMARKED_HYPOTHETICAL": REPAIR,
}
```

On `GuardrailReport`, `fail` gains `spans: Sequence[str] = ()` stored on the
failure dict, and:

```python
    @property
    def consequence(self) -> str:
        """The worst thing this report's failures cost, "" when it passed.

        An excisable failure that recorded no span is not excisable: knowing a
        sentence is wrong is not knowing which sentence, so it retracts.
        """
        worst = ""
        for failure in self.hard_failures:
            cost = CONSEQUENCE.get(failure["code"], RETRACT)
            if cost == EXCISE and not failure.get("spans"):
                cost = RETRACT
            if not worst or _COST[cost] < _COST[worst]:
                worst = cost
        return worst
```

Add `spans` to the failure dict in `fail`, and leave `to_dict` carrying it.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_downgrade_ladder.py tests/test_guardrails.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Guardrails: a failure carries what it costs"
```

---

### Task 2: The excisable checks record where they objected

**Files:**
- Modify: `src/pension_chat/agent/guardrails.py` (`verify_answer`, `_verify_coverage`)
- Test: `tests/test_downgrade_ladder.py`

**Interfaces:**
- Consumes: `fail(..., spans=...)` from Task 1.
- Produces: `UNVERIFIED_CITATION` and `UNSUPPORTED_COVERAGE` failures carry
  `spans` — verbatim substrings of the answer.

**Why:** a span must be findable in the answer exactly as written, because
excision removes it by string match. `_verify_coverage` works on `plain`, which
has quoted spans stripped, so a sentence located there may not appear verbatim
in the answer — in which case it records no span and the failure retracts.

- [ ] **Step 1: Write the failing test**

```python
"""Where the answer went wrong, not just that it did.

A span is a verbatim substring of the answer. It has to be: excision removes it
by string match, and a span that does not appear is a cut that would silently do
nothing -- publishing the bad sentence under a passing report.
"""

from __future__ import annotations

from pension_chat.agent.guardrails import verify_answer

SECTIONS = [{"section": "11-134", "text": "The annuity is computed as follows."}]


def test_a_bad_citation_records_the_sentence_it_is_in():
    answer = (
        "Your annuity is computed under [40 ILCS 5/11-134]. "
        "The service rule lives in [40 ILCS 5/7-109]. "
        "That is the whole of it."
    )
    report = verify_answer(answer, retrieved_sections=SECTIONS, check_numbers=False)

    spans = report.hard_failures[0]["spans"]
    assert len(spans) == 1
    assert "7-109" in spans[0]
    assert "11-134" not in spans[0], "the good sentence must survive"
    assert spans[0] in answer, "a span must be verbatim, or excision cuts nothing"


def test_two_bad_citations_in_one_sentence_record_it_once():
    answer = "Sections [40 ILCS 5/7-109] and [40 ILCS 5/7-110] apply to you."
    report = verify_answer(answer, retrieved_sections=SECTIONS, check_numbers=False)
    assert len(report.hard_failures[0]["spans"]) == 1


def test_a_clean_answer_records_no_spans():
    report = verify_answer(
        "Your annuity is computed under [40 ILCS 5/11-134].",
        retrieved_sections=SECTIONS,
        check_numbers=False,
    )
    assert report.passed
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_downgrade_ladder.py -q`
Expected: FAIL, `KeyError: 'spans'` or an empty list.

- [ ] **Step 3: Record the spans**

In `verify_answer`, replace the bare `report.fail("UNVERIFIED_CITATION", ...)`
with one that locates each unverified citation's sentence:

```python
    if report.unverified_citations:
        spans: list[str] = []
        for section in report.unverified_citations:
            for match in re.finditer(re.escape(section), answer):
                sentence = _sentence_around(answer, match.start()).strip()
                # Verbatim or not at all: excision removes a span by string
                # match, so one that does not appear would cut nothing and
                # publish the sentence it objected to.
                if sentence and sentence in answer and sentence not in spans:
                    spans.append(sentence)
        report.fail("UNVERIFIED_CITATION", "...", spans=spans)
```

In `_verify_coverage`, the asserting sentence is already computed as
`sentence = _sentence_around(plain, asserted.start())`. Pass
`spans=[sentence.strip()] if sentence.strip() in answer else []` to both
`UNSUPPORTED_COVERAGE` failures — the first one has no sentence in scope, so
compute it the same way there.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_downgrade_ladder.py tests/test_guardrails.py tests/test_coverage.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Guardrails: an excisable failure records the sentence it objected to"
```

---

### Task 3: Excision, and re-verifying what is left

**Files:**
- Modify: `src/pension_chat/agent/guardrails.py` (`excise`)
- Modify: `src/pension_chat/agent/graph.py` (the post-generation path)
- Test: `tests/test_downgrade_ladder.py`, `tests/test_excision.py` (create)

**Interfaces:**
- Consumes: `report.consequence`, `failure["spans"]`.
- Produces: `guardrails.excise(answer: str, spans: Sequence[str]) -> str`.

**Why:** this is the whole point — and it costs no model call, so the common
failure case gets faster as well as less destructive.

- [ ] **Step 1: Write the failing test**

```python
"""Cut the sentence, keep the answer -- and only if what is left stands up.

Excision publishes text that was already streamed minus a span. If the
remainder does not verify on its own, publishing it is worse than the
retraction it replaces, so the old path takes over.
"""

from __future__ import annotations

from pension_chat.agent.guardrails import excise


def test_a_span_is_removed_and_the_rest_closes_up():
    answer = "One. Two. Three."
    assert excise(answer, ["Two."]) == "One. Three."


def test_removing_everything_leaves_nothing():
    """The caller checks for this and falls through; excise does not pretend."""
    assert excise("Only this.", ["Only this."]) == ""


def test_a_span_that_is_not_there_changes_nothing():
    assert excise("One. Two.", ["Three."]) == "One. Two."


def test_paragraph_structure_survives():
    answer = "Intro.\n\nOne. Bad. Two.\n\nEnd."
    assert excise(answer, ["Bad."]) == "Intro.\n\nOne. Two.\n\nEnd."


def test_an_emptied_paragraph_does_not_leave_a_hole():
    answer = "Intro.\n\nBad.\n\nEnd."
    assert excise(answer, ["Bad."]) == "Intro.\n\nEnd."
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_excision.py -q`
Expected: FAIL, `excise` not defined.

- [ ] **Step 3: Write `excise`**

```python
def excise(answer: str, spans: Sequence[str]) -> str:
    """``answer`` with each span removed and the whitespace closed up.

    Longest span first, so a sentence containing another is not left with a
    fragment of it. Paragraph breaks survive; a paragraph emptied entirely
    goes with its break, or the answer keeps a hole where a claim used to be.
    """
    text = answer
    for span in sorted(spans, key=len, reverse=True):
        if span:
            text = text.replace(span, "")
    # Collapse the runs the cut left behind, without touching paragraph breaks.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
```

- [ ] **Step 4: Wire it into the post-generation path**

In `graph.py`, before the repair attempt:

```python
    # A consequence proportional to the failure. Cutting the sentence that
    # named a section nobody retrieved keeps the four beside it that named
    # sections that were -- and costs no model call, unlike repair.
    if not report.passed and report.consequence == guardrails.EXCISE:
        spans = [
            span
            for failure in report.hard_failures
            for span in (failure.get("spans") or [])
        ]
        trimmed = guardrails.excise(answer, spans)
        # What is left has to stand on its own. Publishing an unverified
        # remainder would be worse than the retraction this replaces.
        if trimmed and len(trimmed.split()) >= _MIN_EXCISED_WORDS:
            retry = _verify(state, trimmed, settings)
            if retry.passed:
                answer = trimmed
                report = retry
                state["final_answer"] = answer
                state["guardrail_passed"] = True
                state["guardrail_report"] = report.to_dict()
                yield {
                    "type": "replace",
                    "text": answer,
                    "reason": {"excised": len(spans)},
                }
```

with, near the other module constants:

```python
#: Below this, what excision left is a fragment rather than an answer, and the
#: member is better served by the retraction telling them what happened.
_MIN_EXCISED_WORDS = 25
```

The existing `if not report.passed:` repair block follows unchanged, so a
failed excision falls through to repair and then to the fallback exactly as
before.

- [ ] **Step 5: Test the wiring**

Add to `tests/test_excision.py` a test driving `run_agent_stream` with a
scripted answer citing one retrieved and one unretrieved section, asserting:
the `replace` event fires with `reason["excised"] == 1`, the surviving text
still contains the good citation, and no `repair_answer` model call was made.
Add its opposite: an answer whose only sentence is bad falls through to the
fallback and still reads "I have taken that answer back".

Run: `uv run pytest tests/test_excision.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "Verification: cut the sentence, keep the answer"
```

---

### Task 4: The record-attribution line becomes a block

**Files:**
- Modify: `src/pension_chat/agent/nodes.py` (`ANSWER_SYSTEM`, the prose)
- Modify: `src/pension_chat/agent/loop.py` (`_attribution`, its compose block)
- Test: `tests/test_attribution_block.py` (create)

**Why:** §8 names this outright. "I'm going by your record, which places you in
Article 11 — tell me if that's not right" is prose in the answer prompt with a
prose condition, asking the model to police itself — the exact pattern
`CLAUDE.md` records as having failed twice already ("a paragraph in a tool
schema is a warning, not a mechanism").

- [ ] **Step 1: Write the failing test**

```python
"""Whether to say "I'm going by your record" is a fact about state.

The prompt asked the model to work out both the condition and the wording:
"Where an answer rests on what their file says rather than on anything they
told you, say so." A condition in prose is a request; this is the same
sentence rendered by code that reads the coverage basis, like every other
block in `compose`.
"""

from __future__ import annotations

from pension_chat.agent.loop import _attribution


def test_an_answer_resting_on_the_file_says_so():
    text = _attribution(
        {"coverage": {"basis": "record", "article": "11", "record_article": "11"}}
    )
    assert "your record" in text
    assert "Article 11" in text


def test_a_member_who_confirmed_is_not_told_what_their_file_says():
    """They said it. Attributing it to the file reads as not having listened."""
    assert _attribution({"coverage": {"basis": "member_confirmed", "article": "11"}}) == ""


def test_a_statutory_match_is_not_the_file_speaking():
    assert _attribution({"coverage": {"basis": "statutory_match", "article": "12"}}) == ""


def test_a_supposition_is_not_attributed_to_the_record():
    assert _attribution({"coverage": {"basis": "hypothetical", "article": "5"}}) == ""


def test_no_coverage_is_nothing_to_attribute():
    assert _attribution({}) == ""
    assert _attribution({"coverage": None}) == ""
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_attribution_block.py -q`
Expected: FAIL, `_attribution` not defined.

- [ ] **Step 3: Write the block and delete the prose**

Add `_attribution(state)` to `loop.py` beside `_assumed` and `_branched`,
firing only when `coverage["basis"] == "record"`. Append it to `compose`'s
messages the same way, instructing the model to include the line verbatim.

Delete the "One exception, and it is not chattiness" paragraph from
`ANSWER_SYSTEM` in `nodes.py`.

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src/pension_chat/agent/guardrails.py`
Expected: PASS. `ruff` at its pre-existing 37 and no more.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Answer: whether the file is speaking is a fact, not a judgement call"
```

---

## Verification

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check .` — 37, the pre-existing count, and no more
- [ ] Confirm by reading `CONSEQUENCE` that `UNVERIFIED_FIGURE` is still
      `RETRACT`. It is the one line in this plan that must not have moved.
- [ ] Update §8 with what shipped and what the ladder's unit turned out to be.

**Not in this plan, deliberately:** falling back to *investigation* on a
streaming cut. §8 lists it as still deferred, and it means re-entering the tool
loop mid-stream — a latency cost and a control-flow change that wants its own
design, not a step at the end of this one.
