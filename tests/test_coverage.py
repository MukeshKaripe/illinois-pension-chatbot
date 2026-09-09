"""Coverage: whose Article is it, and on what evidence.

These cover the layer added after a member wrote "i am a park employee" and was
told "As a park employee covered by Article 11, your pension is calculated under
Section 11-134(a)" — a causal claim welded out of their description and a column
on their record, followed by a figure. Park employees are generally Article 12.

Everything here is deterministic and needs no network. The one model call in the
path (``coverage.assess``) is exercised through its grounding rule, which is the
part that has to hold: a candidate the model did not read the coverage test for
is dropped, so the semantic freedom that makes "a worker who works at the park"
work cannot also reintroduce the invented bridge.
"""

from __future__ import annotations

import re

import pytest

from pension_chat.agent import coverage as cov
from pension_chat.agent.guardrails import verify_answer

# ── The three acts ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "question,expected",
    [
        # Asserted: they are telling you something about themselves.
        ("i am a park employee, how is my pension calculated?", cov.ASSERTED),
        ("I am an employee of a board of park commissioners", cov.ASSERTED),
        ("I work for the park district", cov.ASSERTED),
        ("I'm employed by the Chicago Park District", cov.ASSERTED),
        ("my job is groundskeeper at a city park", cov.ASSERTED),
        ("As a park employee, how is my pension calculated?", cov.ASSERTED),
        # Stipulated: a premise, not a claim. Asking them to confirm it is the
        # same deafness as ignoring one they asserted.
        ("if I were a park employee, how would my pension work?", cov.STIPULATED),
        ("Suppose I work for the park district, what then?", cov.STIPULATED),
        ("assume I am a park employee", cov.STIPULATED),
        ("what if I were an employee of a board of park commissioners?", cov.STIPULATED),
        ("hypothetically, I'm a park employee", cov.STIPULATED),
        ("let's say I work at a park", cov.STIPULATED),
        # Doubted: they have told you the question is open, which makes it the
        # clearest case in the system for asking.
        ("I think I'm a park employee", cov.DOUBTED),
        ("I'm not sure if I'm a park employee", cov.DOUBTED),
        ("I might be an employee of a board of park commissioners", cov.DOUBTED),
        ("Am I right that I am a park employee?", cov.DOUBTED),
        # Interrogative self-reference: the member is putting the question
        # to you, however plainly it is phrased.
        ("Am I a park employee?", cov.DOUBTED),
        ("Do I count as an employee of a board of park commissioners?", cov.DOUBTED),
        ("Would I be a park employee for pension purposes?", cov.DOUBTED),
    ],
)
def test_the_act_is_read_correctly(question, expected):
    reference = cov.classify_self_reference(question)
    assert reference is not None, question
    assert reference.act == expected


def test_a_governor_does_not_reach_across_a_sentence():
    """"I am a park employee. What if I retire at 60?" is an assertion.

    The "what if" governs the retirement age, not the coverage. Without the
    sentence boundary every assertion followed by a hypothetical question would
    be silently downgraded to a supposition.
    """
    reference = cov.classify_self_reference(
        "I am a park employee. What if I retire at 60?"
    )
    assert reference.act == cov.ASSERTED


def test_a_free_description_survives_intact():
    """The phrasing that ruled out a keyword table in the first place.

    "A worker who works at the park" has to reach the corpus whole. An earlier
    version stripped at "who" — reading it as a question word — and passed on
    "worker", which matches every Article in the Code and none of them usefully.
    """
    reference = cov.classify_self_reference(
        "I am a worker who works at the park, what is my pension?"
    )
    assert reference.text == "worker who works at the park"


def test_a_question_about_nobody_in_particular_is_not_a_self_reference():
    for question in (
        "how is my pension calculated?",
        "What does Section 11-134 say?",
        "what type of employees come under Article 12?",
        "Now do the same at age 59.",
    ):
        assert cov.classify_self_reference(question) is None, question


def test_employment_status_is_read_separately_from_coverage():
    reference = cov.classify_self_reference("I am retired")
    assert reference.kind == cov.STATUS
    assert cov.classify_self_reference("I am a park employee").kind == cov.COVERAGE


def test_leaving_a_supposition():
    assert cov.leaves_hypothetical("Now go back to my actual record")
    assert cov.leaves_hypothetical("actually, in my case I work for the city")
    assert not cov.leaves_hypothetical("and at 60?")
    assert not cov.leaves_hypothetical("what if I had 30 years instead?")


# Every governor in the two pattern families must have a sentence proving it
# fires. Guarded the way `test_planning.py` guards `_CALCULATION_SIGNALS`, and
# for the same reason: three alternatives in that pattern were dead for as long
# as they had existed, because a trailing `\b` could never match after them, and
# reading the regex did not reveal it.
_WORKED_EXAMPLES = {
    cov._STIPULATED_GOVERNOR: [
        "if I were a park employee",
        "suppose I work at the park",
        "supposing I am a park employee",
        "assume I am a park employee",
        "assuming I work for the park district",
        "imagine I am a park employee",
        "pretend I am a park employee",
        "hypothetically I am a park employee",
        "hypothetical: I work at a park",
        "say I am a park employee",
        "let's say I work at a park",
        "what if I were a park employee",
        "for the sake of argument I am a park employee",
        "in the event I am a park employee",
    ],
    cov._DOUBTED_GOVERNOR: [
        "I think I am a park employee",
        "I believe I am a park employee",
        "I guess I am a park employee",
        "I assume I am a park employee",
        "I'm not sure I am a park employee",
        "not sure if I am a park employee",
        "maybe I am a park employee",
        "I might be a park employee",
        "I may be a park employee",
        "possibly I am a park employee",
        "I wonder if I am a park employee",
        "is it right that I am a park employee",
        "am I right that I am a park employee",
        "do I count as a park employee",
        "am I correct that I am a park employee",
    ],
}


def _alternatives(pattern: re.Pattern) -> set[str]:
    """The literal alternatives inside a `(?:a|b|c)` group, normalised."""
    body = re.sub(r"^.*?\(\?:", "", pattern.pattern, count=1, flags=re.DOTALL)
    body = body.rsplit(")", 1)[0]
    found = set()
    for alternative in body.split("|"):
        cleaned = alternative.strip()
        cleaned = cleaned.replace("\\b", "").replace("\\ ", " ").replace("\\s+", " ")
        cleaned = re.sub(r"\\w\*|\(\?:|\)|\?", "", cleaned).strip()
        if cleaned:
            found.add(cleaned)
    return found


@pytest.mark.parametrize("pattern", list(_WORKED_EXAMPLES))
def test_every_governor_alternative_has_a_worked_example(pattern):
    covered = " ".join(_WORKED_EXAMPLES[pattern]).lower()
    unproven = [
        alternative
        for alternative in _alternatives(pattern)
        if not re.search(alternative.replace(" ", r"\s*").replace("'", "'?"), covered)
    ]
    assert not unproven, (
        f"No sentence proves these alternatives ever fire: {sorted(unproven)}. "
        "Add one to _WORKED_EXAMPLES, or delete the alternative."
    )


@pytest.mark.parametrize(
    "pattern,sentences", [(p, s) for p, s in _WORKED_EXAMPLES.items()]
)
def test_every_worked_example_actually_classifies(pattern, sentences):
    expected = cov.STIPULATED if pattern is cov._STIPULATED_GOVERNOR else cov.DOUBTED
    for sentence in sentences:
        reference = cov.classify_self_reference(sentence)
        assert reference is not None, sentence
        assert reference.act == expected, sentence


# ── Grounding ─────────────────────────────────────────────────────


class _Section:
    def __init__(self, section, text="text", title=""):
        self.section = section
        self.text = text
        self.title = title


@pytest.mark.asyncio
async def test_a_candidate_the_model_did_not_read_is_dropped(monkeypatch):
    """The rule that keeps semantic freedom from rebuilding the original bug.

    The model may map any phrasing onto a statutory test, but it may not name an
    Article whose coverage section it was never shown. That is the difference
    between reasoning from the Code and asserting from memory — and asserting
    from memory is precisely what produced "as a park employee covered by
    Article 11".
    """
    async def fake_complete(**_kwargs):
        return (
            '{"candidates": ['
            '{"article": "12", "section": "12-101", "test": "park commissioners"},'
            '{"article": "8", "section": "8-113", "test": "invented from memory"}'
            "]}"
        )

    monkeypatch.setattr(cov, "complete", fake_complete)
    report = await cov.assess("park employee", [_Section("12-101"), _Section("11-110")])

    assert report.articles == ["12"]
    assert all(c["section"] in {"12-101", "11-110"} for c in report.candidates)


@pytest.mark.asyncio
async def test_an_assessment_failure_falls_back_to_the_record(monkeypatch):
    async def boom(**_kwargs):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(cov, "complete", boom)
    report = await cov.assess("park employee", [_Section("12-101")])
    assert report.candidates == []

    # No candidates means the record stands — today's behaviour, minus the
    # licence to present it as established.
    verdict = cov.adjudicate(
        cov.SelfReference("park employee", cov.ASSERTED), report, "11"
    )
    assert verdict["basis"] == cov.BASIS_RECORD
    assert verdict["conflict"] is False


# ── Adjudication ──────────────────────────────────────────────────


def _report(*articles):
    """Candidates in the order the corpus ranked them, best first."""
    return cov.CoverageReport(
        candidates=[
            {
                "article": a,
                "section": f"{a}-10{i}",
                "test": f"test for {a}",
                "settled_by": "which employer they work for",
                "rank": i,
            }
            for i, a in enumerate(articles)
        ]
    )


def test_a_supposition_is_adopted_and_never_asked_about():
    verdict = cov.adjudicate(
        cov.SelfReference("park employee", cov.STIPULATED), _report("12"), "11"
    )
    assert verdict["basis"] == cov.BASIS_HYPOTHETICAL
    assert verdict["conflict"] is False
    assert verdict["article"] == "12"


def test_doubt_is_always_a_question_even_when_it_agrees_with_the_record():
    """They said they do not know. That outranks the system's own confidence."""
    verdict = cov.adjudicate(
        cov.SelfReference("park employee", cov.DOUBTED), _report("11"), "11"
    )
    assert verdict["conflict"] is True
    assert verdict["why"] == "doubted"


def test_a_description_that_agrees_with_the_record_is_the_good_case():
    verdict = cov.adjudicate(
        cov.SelfReference("park employee", cov.ASSERTED), _report("12"), "12"
    )
    assert verdict["basis"] == cov.BASIS_STATUTORY
    assert verdict["conflict"] is False


def test_a_description_pointing_elsewhere_is_a_question():
    """The reported conversation, turn one."""
    verdict = cov.adjudicate(
        cov.SelfReference("park employee", cov.ASSERTED), _report("12"), "11"
    )
    assert verdict["conflict"] is True
    assert verdict["why"] == "elsewhere"
    assert verdict["basis"] == cov.BASIS_RECORD


def test_the_record_is_a_prior_and_a_better_supported_article_outranks_it():
    """Their words fit two Articles, and their file is the weaker of the two."""
    verdict = cov.adjudicate(
        cov.SelfReference("park employee", cov.ASSERTED), _report("12", "11"), "11"
    )
    assert verdict["conflict"] is True
    assert verdict["why"] == "outranked"


def test_identical_words_from_two_members_resolve_differently():
    """The pair that forced the rule to be about ranking rather than membership.

    Coverage tests overlap by design — "park employee" matches Section 11-110
    ("any person employed by the board") as much as Section 12-118 — so an
    Article 11 park employee and an Article 12 one produce the *same* candidate
    list and differ only in their file. Asking "is the record among the
    candidates?" cannot separate them; asking "is the record the best-supported
    one?" can.

    Getting this wrong in the safe direction is not harmless: it interrogates an
    Article 12 park employee about being a park employee, which is the same
    failure to listen as the bug this whole layer exists to fix, just wearing
    the careful coat.
    """
    words = cov.SelfReference("park employee", cov.ASSERTED)
    candidates = _report("12", "12", "11")  # what the corpus actually returns

    filed_under_11 = cov.adjudicate(words, candidates, "11")
    assert filed_under_11["conflict"] is True, "must ask the Article 11 member"

    filed_under_12 = cov.adjudicate(words, candidates, "12")
    assert filed_under_12["conflict"] is False, "must not ask the Article 12 member"
    assert filed_under_12["basis"] == cov.BASIS_STATUTORY


def test_a_description_matching_nothing_falls_back_without_a_question():
    verdict = cov.adjudicate(
        cov.SelfReference("night shift", cov.ASSERTED), cov.CoverageReport(), "11"
    )
    assert verdict["basis"] == cov.BASIS_RECORD
    assert verdict["conflict"] is False
    # But it is remembered, which is what stops the answer presenting their job
    # as the reason they are in that Article.
    assert verdict["described_as"] == "night shift"


def test_an_anonymous_member_with_one_match_is_settled_by_the_statute():
    verdict = cov.adjudicate(
        cov.SelfReference("park employee", cov.ASSERTED), _report("12"), None
    )
    assert verdict["basis"] == cov.BASIS_STATUTORY
    assert verdict["conflict"] is False


def test_an_anonymous_member_with_no_match_is_unresolved():
    verdict = cov.adjudicate(
        cov.SelfReference("something", cov.ASSERTED), cov.CoverageReport(), None
    )
    assert verdict["basis"] == cov.BASIS_UNRESOLVED
    assert verdict["conflict"] is True


# ── Binding the reply ─────────────────────────────────────────────


def _asked():
    return cov.open_question_for(
        cov.adjudicate(
            cov.SelfReference("park employee", cov.ASSERTED),
            cov.CoverageReport(
                candidates=[
                    {
                        "article": "12",
                        "section": "12-101",
                        "test": "employees of a board of park commissioners",
                        "settled_by": "which employer pays them",
                    }
                ]
            ),
            "11",
        )
    )


@pytest.mark.parametrize(
    "reply,article",
    [
        ("Article 12", "12"),
        ("I think section 12-101 is the one", "12"),
        ("yes, I work for the board of park commissioners", "12"),
        ("i am an employee of a board of park commissioners", "12"),
    ],
)
def test_a_reply_that_settles_it_is_bound(reply, article):
    bound = cov.bind_reply(reply, _asked())
    assert bound is not None, reply
    assert bound["article"] == article
    assert bound["basis"] == cov.BASIS_CONFIRMED


def test_the_turn_that_was_lost():
    """Turn four of the reported conversation.

    The member answered in the statute's own literal words — "employee of a
    board of park commissioners" *is* the Section 12-101 test — and the
    assistant computed the Article 11 figure again. There was nowhere to put it.
    """
    bound = cov.bind_reply(
        "i am an employee of a board of park commissioners, how is my pension "
        "calculated?",
        _asked(),
    )
    assert bound["article"] == "12"
    assert bound["basis"] == cov.BASIS_CONFIRMED


def test_a_reply_that_settles_nothing_is_not_forced():
    for reply in ("I don't know", "what do you mean?", "tell me more"):
        assert cov.bind_reply(reply, _asked()) is None, reply


def test_nothing_binds_without_an_open_question():
    assert cov.bind_reply("Article 12", None) is None


# ── The guardrail ─────────────────────────────────────────────────

_SECTIONS = [{"section": "11-134", "text": "Sec. 11-134. Minimum annuity."}]


def _verify(answer, coverage):
    return verify_answer(
        answer,
        retrieved_sections=_SECTIONS,
        check_numbers=False,
        coverage=coverage,
    )


# The sentence this whole layer exists to stop, verbatim.
_TURN_ONE = (
    "As a park employee covered by Article 11, your pension is calculated under "
    "Section 11-134(a). [40 ILCS 5/11-134]"
)


def test_the_original_sentence_is_now_a_hard_failure():
    report = _verify(_TURN_ONE, {"basis": cov.BASIS_RECORD, "described_as": "park employee"})
    assert not report.passed
    assert [f["code"] for f in report.hard_failures] == ["UNSUPPORTED_COVERAGE"]


@pytest.mark.parametrize("basis", sorted(cov.ESTABLISHED_BASES))
def test_the_same_sentence_is_fine_once_the_evidence_supports_it(basis):
    assert _verify(_TURN_ONE, {"basis": basis, "described_as": "park employee"}).passed


def test_describing_who_an_article_covers_is_always_allowed():
    """The distinction the check turns on.

    Saying what Article 12 provides is a statement about the law. Telling this
    member it covers *them* is a statement about them, and only the second one
    needs evidence about them.
    """
    report = _verify(
        "Article 12 covers employees of a board of park commissioners. Your file "
        "records you under Article 11. [40 ILCS 5/11-134]",
        {"basis": cov.BASIS_RECORD, "described_as": "park employee"},
    )
    assert report.passed


def test_a_supposition_answered_as_fact_is_a_hard_failure():
    report = _verify(
        "Your pension is calculated under Section 11-134(a). [40 ILCS 5/11-134]",
        {"basis": cov.BASIS_HYPOTHETICAL, "described_as": "park employee"},
    )
    assert not report.passed
    assert [f["code"] for f in report.hard_failures] == ["UNMARKED_HYPOTHETICAL"]


def test_a_supposition_answered_conditionally_passes():
    report = _verify(
        "If you were an employee of a board of park commissioners, Article 12 "
        "would apply instead. [40 ILCS 5/11-134]",
        {"basis": cov.BASIS_HYPOTHETICAL, "described_as": "park employee"},
    )
    assert report.passed


def test_answers_with_no_coverage_state_are_unaffected():
    """Every existing caller passes no coverage, and must keep passing."""
    assert _verify(_TURN_ONE, None).passed


@pytest.mark.parametrize(
    "reply",
    [
        # Turn two of the reported conversation. It names Article 12 twice and
        # settles nothing: the member is putting the question back, and reading
        # it as a confirmation would file them into a fund on the strength of
        # their own uncertainty.
        "i thought park employee's came under article 12, is it correct, if not "
        "what type of employees come under article 12?",
        "what type of employees come under Article 12?",
        "is that right? I'm not sure",
        "I don't know, how do I find out?",
        "which one applies to me?",
    ],
)
def test_asking_about_an_article_is_not_answering_about_one(reply):
    assert cov.bind_reply(reply, _asked()) is None, reply


def test_an_assertion_binds_even_when_the_sentence_is_a_question():
    """Turn four ends in a question mark and is still an answer.

    "I am an employee of a board of park commissioners, how is my pension
    calculated?" tells you what they are and asks something else in the same
    breath. Punctuation does not decide this; the act does.
    """
    bound = cov.bind_reply(
        "i am an employee of a board of park commissioners, how is my pension "
        "calculated?",
        _asked(),
    )
    assert bound is not None
    assert bound["article"] == "12"


def test_a_quoted_sentence_is_not_the_members_own_words():
    """Turn three of the reported conversation.

    The member quoted the assistant's wrong sentence back at it to object to it.
    Reading the Article number out of that quotation confirmed them into the
    very Article they were objecting to — and then the next turn, having "an
    answer", stopped asking.
    """
    challenge = (
        'so when i said i was a park employee why did you say this "As a park '
        'employee covered by Article 11, your pension is calculated under '
        'Section 11-134(a)?"'
    )
    assert cov.bind_reply(challenge, _asked()) is None


def test_saying_where_a_belief_came_from_is_allowed():
    """Attribution is the whole difference, and it must not be punished.

    "Your record places you under Article 11" is a true statement about the file
    and is precisely what the answer is instructed to say. If verification
    withdrew it, the honest phrasing would be the one that gets rejected.
    """
    for answer in (
        "Your record places you under Article 11. [40 ILCS 5/11-134]",
        "According to your file you are covered by Article 11, though Article 12 "
        "covers employees of a board of park commissioners. [40 ILCS 5/11-134]",
        "Your pension is currently recorded under Article 11. [40 ILCS 5/11-134]",
    ):
        report = _verify(answer, {"basis": cov.BASIS_RECORD, "described_as": "park employee"})
        assert report.passed, answer


@pytest.mark.asyncio
async def test_candidates_are_ordered_by_the_evidence_behind_them(monkeypatch):
    """Retrieval order is evidence, and it has to reach the adjudicator.

    The model emits candidates in whatever order it likes. What separates a
    description supporting the record from one pointing away is which coverage
    test the corpus ranked first on the member's own words — so that ordering is
    reimposed here rather than trusted from the model.
    """
    async def fake_complete(**_kwargs):
        # Deliberately worst-first, to prove the order is not taken on trust.
        return (
            '{"candidates": ['
            '{"article": "11", "section": "11-110", "test": "employed by the board"},'
            '{"article": "12", "section": "12-118", "test": "board of park commissioners"}'
            "]}"
        )

    monkeypatch.setattr(cov, "complete", fake_complete)
    # Retrieval put 12-118 first, which is what "park employee" actually returns.
    report = await cov.assess(
        "park employee", [_Section("12-118"), _Section("11-110")]
    )

    assert report.articles == ["12", "11"]
    assert [c["section"] for c in report.candidates] == ["12-118", "11-110"]


def test_quoting_a_wrong_sentence_in_order_to_disown_it_is_allowed():
    """The turn whose entire subject is the sentence that must not be asserted.

    A member asks "why did you say 'As a park employee covered by Article 11'?"
    An answer that cannot repeat those words cannot explain them — and this
    check would otherwise withdraw the assistant's own correction for containing
    the error it was correcting.
    """
    report = _verify(
        'I should not have said "As a park employee covered by Article 11, your '
        "pension is calculated under Section 11-134(a)”. Your record places "
        "you in Article 11, but your job description alone does not establish "
        "that. [40 ILCS 5/11-134]",
        {"basis": cov.BASIS_RECORD, "described_as": "park employee"},
    )
    assert report.passed


def test_a_quotation_does_not_launder_an_assertion():
    """The other side of it: quoting cannot be a way to smuggle the claim in.

    Stripping quotations removes them from what the answer is taken to assert.
    An answer that also asserts it in its own voice still fails.
    """
    report = _verify(
        'You asked why I said "As a park employee covered by Article 11". You '
        "are covered by Article 11 because you are a park employee. "
        "[40 ILCS 5/11-134]",
        {"basis": cov.BASIS_RECORD, "described_as": "park employee"},
    )
    assert not report.passed
    assert [f["code"] for f in report.hard_failures] == ["UNSUPPORTED_COVERAGE"]


# ── The member's own figures, and when they are off limits ────────


async def _prompt_text(state):
    """The context the answer prompt would carry for this state."""
    from pension_chat.agent.nodes import prepare_answer
    from pension_chat.agent.state import initial_state

    full = initial_state("what would my pension be?", "s1", "e1")
    full.update(state)
    result = await prepare_answer(full)
    # The static system prompt is skipped: its worked example happens to use
    # $69,956.40 too, and asserting over it would pass or fail for the wrong
    # reason. What matters is the per-turn context.
    return "\n".join(m["content"] for m in result["answer_messages"][1:])


_FACTS = {
    "age": 56,
    "service_years": 21.5,
    "final_average_salary": 69956.40,
    "current_annual_salary": 72000.0,
    "article_number": 11,
    "tier": 1,
}


@pytest.mark.asyncio
async def test_a_supposition_does_not_get_the_members_real_salary():
    """The bug the first hypothetical answer actually shipped with.

    It set out the Article 12 formula correctly and then named $69,956.40 — the
    member's *Article 11* salary basis — as the salary it would run on.
    Starving the calculation was not enough: the figures were still in the
    prompt under "what you know about this member", and the model reaches for
    what it is handed. Verification could not catch it either, because a figure
    from the member's own record traces perfectly well; it is simply not the
    right basis, and only the prompt knows that.
    """
    prompt = await _prompt_text(
        {
            "member_facts": dict(_FACTS),
            "coverage": {
                "article": "12",
                "basis": cov.BASIS_HYPOTHETICAL,
                "record_article": "11",
            },
            "hypothetical_frame": {"premise": "park employee", "article": "12"},
        }
    )
    assert "69956" not in prompt.replace(",", "")
    assert "NOT AVAILABLE TO THIS ANSWER" in prompt


@pytest.mark.asyncio
async def test_the_members_figures_are_there_when_the_article_is_their_own():
    """The converse, so the rule above cannot be satisfied by starving always."""
    prompt = await _prompt_text(
        {
            "member_facts": dict(_FACTS),
            "coverage": {
                "article": "11",
                "basis": cov.BASIS_RECORD,
                "record_article": "11",
            },
        }
    )
    assert "69956" in prompt.replace(",", "")


@pytest.mark.parametrize(
    "coverage,expected_empty",
    [
        ({"article": "11", "basis": cov.BASIS_RECORD, "record_article": "11"}, False),
        ({"article": "11", "basis": cov.BASIS_STATUTORY, "record_article": "11"}, False),
        ({"article": "12", "basis": cov.BASIS_HYPOTHETICAL, "record_article": "11"}, True),
        ({"article": "12", "basis": cov.BASIS_CONFIRMED, "record_article": "11"}, True),
        # Supposing the Article they are already in borrows nothing.
        ({"article": "12", "basis": cov.BASIS_HYPOTHETICAL, "record_article": "12"}, False),
        (None, False),
    ],
)
def test_the_facts_rule_is_single_sourced(coverage, expected_empty):
    """One rule, four consumers.

    The member's record is the wrong basis under another Article in four
    different ways at once — as a calculation input, as a fact an applicability
    predicate is tested against, as a figure the answer may quote, and as a
    source verification will accept. They were originally written separately,
    and three of the four were wrong. Reading them all from here is what stops
    that recurring.
    """
    from pension_chat.agent.nodes import _available_facts

    state = {"member_facts": dict(_FACTS), "coverage": coverage}
    assert (_available_facts(state) == {}) is expected_empty


def test_an_unanswered_question_stops_being_live():
    """A member may answer a turn late; they may not answer six turns late.

    An open question that never expires is a trap. Long after the exchange has
    moved on, someone mentioning "Article 12" in passing would be read as
    finally answering it, and filed into that fund on the strength of a remark.
    """
    asked = {**_asked(), "asked_turn": 3}

    assert cov.question_is_live(asked, 3)
    assert cov.question_is_live(asked, 4), "answering a turn late is normal"
    assert cov.question_is_live(asked, 5)
    assert not cov.question_is_live(asked, 6)
    assert not cov.question_is_live(None, 1)


def test_a_question_with_no_recorded_turn_stays_live():
    """Older threads have no `asked_turn`; they must not silently stop binding."""
    assert cov.question_is_live({"about": "coverage", "options": [{"article": "12"}]}, 9)


# ── Rates the statute writes as fractions ─────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("the annuity is reduced 1/4 of 1% for each month", "0.25"),
        ("reduced by 1/2 of 1% per month", "0.5"),
        ("3/4 of 1% for each full month", "0.75"),
        ("an additional 1 1/2% for each year", "1.5"),
    ],
)
def test_a_fraction_of_a_percent_reads_as_its_decimal(text, expected):
    from decimal import Decimal

    from pension_chat.agent.guardrails import _fractional_percentages

    assert Decimal(expected) in _fractional_percentages(text)


def test_a_bare_fraction_is_not_a_percentage():
    """"2/3 of the annuity" is a share of an amount, not a rate."""
    from pension_chat.agent.guardrails import _fractional_percentages

    assert _fractional_percentages("2/3 of the annuity") == set()


def test_restating_a_statutory_fraction_in_decimal_is_not_an_invented_figure():
    """The false positive that withdrew a correct Article 12 answer.

    Article 12 writes the early-retirement reduction as "1/4 of 1%"; Article 11
    writes the identical rule as "0.25%". An answer that restates the fraction
    the way members read rates has introduced nothing — but 0.25 appears nowhere
    in Article 12's text, so numeric provenance rejected it. It could only ever
    bite on Article 12, which is why it surfaced the moment a second Article
    became reachable.
    """
    sections = [
        {
            "section": "12-133",
            "text": (
                "the annuity shall be reduced 1/4 of 1% for each month or "
                "fraction of a month that the employee's age is less than 60."
            ),
        }
    ]
    report = verify_answer(
        "The annuity is reduced by 0.25% for each month before age 60. "
        "[40 ILCS 5/12-133]",
        retrieved_sections=sections,
        check_numbers=True,
    )
    assert report.passed, report.unverified_numbers


def test_an_ordinary_member_is_still_told_which_article_governs_them():
    """The regression this check caused, and the reason it is gated.

    "I am 56 years old with 21.5 years of service. Which retirement provision
    applies to me?" describes no job, so there is no bridge to build — and the
    answer to it is, unavoidably, which Article governs them. Ungated, the check
    fired on `basis: record`, which is every member who never mentioned their
    employer, and withdrew an answer to close to this assistant's whole purpose.
    """
    answer = (
        "You are covered by Article 11, and Section 11-134(a) is the provision "
        "that applies to you. [40 ILCS 5/11-134]"
    )
    assert _verify(answer, {"basis": cov.BASIS_RECORD, "described_as": None}).passed
    assert _verify(answer, {"basis": cov.BASIS_RECORD}).passed

    # But the moment they have described themselves, it is armed again.
    assert not _verify(
        answer, {"basis": cov.BASIS_RECORD, "described_as": "park employee"}
    ).passed
