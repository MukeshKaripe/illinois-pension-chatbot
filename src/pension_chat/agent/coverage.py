"""Which Article covers this member, and on what evidence.

Every other determination in this system rests on this one. The Article picks
the retrieval filter, which picks the statute text, which picks the recipe,
which produces the figure. And until now it was the one determination made by a
database column, with no evidence behind it and no way to be contradicted.

The failure that motivated this module: a member wrote "i am a park employee,
how is my pension calculated?" and was told *"As a park employee covered by
Article 11, your pension is calculated under Section 11-134(a)"*, with a figure.
Park employees are generally Article 12; the record said 11. The assistant
welded the member's description to the record's column and produced a causal
claim the Pension Code does not support. Nothing caught it, because verification
polices figures and citations and coverage was neither.

Two turns later the member restated their coverage in the statute's own literal
words -- "employee of a board of park commissioners", which *is* the Section
12-101 test -- and got the Article 11 figure again. There was no state key that
could hold what they had just established.

So coverage becomes an evidenced determination, and it follows the rule the rest
of the architecture already follows (``calculation/applicability.py``): **the
model states the condition, code decides whether it is met.** The model does the
semantic work -- mapping "a worker who works at the park" onto a statutory test,
which no keyword table can do -- but it works from coverage sections actually
retrieved, and it emits candidates carrying the section and test they rest on,
never a bare Article number. ``adjudicate`` is pure Python and makes the call.

The other half is grammatical rather than semantic. These three sentences are
different acts and each is a distinct bug when handled as another:

    "I am a park employee"            asserted   -- evidence; check it, ask on conflict
    "if I were a park employee"       stipulated -- a premise; adopt it, never ask
    "I think I'm a park employee"     doubted    -- *they* are unsure; ask

A stipulated premise also has a lifetime. "and what if I had 30 years?" is still
inside it, and it holds until the member steps out. `assumptions` already
carries `source: hypothetical` for the numbers going *into* a formula; this is
the same idea for the facts that choose the formula.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pension_chat.agent.llm import complete
from pension_chat.agent.retrieval import CROSS_CUTTING_ARTICLES
from pension_chat.config import get_settings

logger = logging.getLogger("pension_chat.agent.coverage")

# ── The three acts ────────────────────────────────────────────────

ASSERTED = "asserted"
STIPULATED = "stipulated"
DOUBTED = "doubted"

# What a self-reference is about. Coverage goes to the corpus; a claim about
# employment status is checked against the record directly, since the record
# holds the answer and the Code does not.
COVERAGE = "coverage"
STATUS = "status"

# How coverage came to be believed, most to least established.
BASIS_CONFIRMED = "member_confirmed"
BASIS_STATUTORY = "statutory_match"
BASIS_RECORD = "record"
BASIS_HYPOTHETICAL = "hypothetical"
BASIS_UNRESOLVED = "unresolved"

# The two bases under which an answer may state coverage as a fact about the
# member. Read by the guardrail, so it lives here with the vocabulary it checks.
ESTABLISHED_BASES = frozenset({BASIS_CONFIRMED, BASIS_STATUTORY})

# Articles that govern the members of other Articles can never be the answer to
# "which Article covers you", because nobody is a member of one.
#
# This is not hypothetical once they are indexed. Both define an "Employee" in
# terms broad enough to match almost any description — Section 20-104 is "any
# person in the service of an employer" — so they compete for coverage against
# the funds on their own retrieved text, and a park employee can be routed to a
# reciprocal act that administers no annuity. They stay fully retrievable and
# citable; this excludes them from being a *destination*, not from being read.
NON_MEMBERSHIP_ARTICLES = frozenset(CROSS_CUTTING_ARTICLES)


@dataclass
class SelfReference:
    """Something the member said about who they are."""

    text: str
    act: str
    kind: str = COVERAGE

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "act": self.act, "kind": self.kind}


# ── Detecting a self-reference ────────────────────────────────────

# The frames people actually use to say what they do. An article ("a", "an",
# "the") is required on the copula frame and that is load-bearing: without it
# "I'm not sure if I'm a park employee" matches at the *first* "I'm" and the
# description becomes "not sure if I'm a park employee", which is both wrong and
# unclassifiable. Requiring the article makes the first match land on the second
# clause, leaving "I'm not sure if" in front of it where the governor test can
# see it.
_COVERAGE_FRAME = re.compile(
    r"""
    (?:
        \bi\s*(?:'m|’m|\ am|\ was|\ were|\ might\ be|\ may\ be|\ could\ be|\ would\ be)
        \s+(?:a|an|the)\s+(?P<a>[^,.;?!\n]{2,90})
      | \bi\s+work(?:ed|s)?\s+(?:at|for|in|as)\s+(?P<b>[^,.;?!\n]{2,90})
      | \bi\s*(?:'m|’m|\ am|\ was|\ were)\s+employed\s+(?:by|as|at|with|in)\s+(?P<c>[^,.;?!\n]{2,90})
      | \bmy\s+job\s+is\s+(?:a|an|the)?\s*(?P<d>[^,.;?!\n]{2,90})
      | \bas\s+(?:a|an)\s+(?P<e>[^,.;?!\n]{2,90}?)\s*,\s*(?:how|what|when|do|does|can|will|would|am|is|i)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Asking outright whether they are covered. This is a self-reference in
# interrogative form, and it is always doubt however confidently it is phrased:
# "Am I a park employee?" is the member putting the question to *you*, and
# answering it as though they had asserted it inverts who is telling whom.
_ASKS_ABOUT_SELF = re.compile(
    r"\b(?:do\s+i\s+count\s+as|am\s+i|would\s+i\s+be|do\s+i\s+qualify\s+as|"
    r"does\s+that\s+make\s+me)\s+(?:a|an|the)\s+(?P<q>[^,.;?!\n]{2,90})",
    re.IGNORECASE,
)

# Employment status, which the record holds and the Code does not. Kept separate
# because it is settled by looking at the row, not by reading the statute.
_STATUS_FRAME = re.compile(
    r"\bi\s*(?:'m|’m|\ am|\ was)\s+(?P<s>retired|retiring|no longer working|"
    r"on (?:a |an )?(?:leave of absence|unpaid leave|leave|disability)|"
    r"still working|no longer employed|out on disability)\b"
    r"|\bi\s+(?:have\s+)?(?:retired|resigned|left (?:the )?service|stopped working)\b",
    re.IGNORECASE,
)

# Governors that turn a description into a supposition. "If I were" is not a
# hedged "I am" -- it is a different speech act, and asking someone to confirm a
# premise they have just stipulated is the same deafness as ignoring one they
# asserted, wearing the opposite coat.
_STIPULATED_GOVERNOR = re.compile(
    r"\b(?:if|suppose|supposing|assume|assuming|imagine|pretend|hypothetical\w*|"
    r"say|let'?s\ say|what\ if|for\ the\ sake\ of|in\ the\ event)\b",
    re.IGNORECASE | re.VERBOSE,
)

# Governors that say the member does not know. An uncertainty marker is not a
# hedge to be stripped before parsing; it is the member telling you the question
# is open, which makes it the clearest case in the system for asking.
_DOUBTED_GOVERNOR = re.compile(
    r"\b(?:i\s+think|i\s+believe|i\s+guess|i\s+assume\s+i|i'?m\s+not\s+sure|"
    r"not\s+sure\s+if|maybe|might\s+be|may\s+be|possibly|i\s+wonder|"
    r"is\s+it\s+right\s+that|am\s+i\s+(?:right|correct)|do\s+i\s+count\s+as)\b",
    re.IGNORECASE,
)

# Sentence boundary, so a governor in a previous sentence cannot reach forward:
# "I am a park employee. What if I retire at 60?" is an assertion followed by a
# question about timing, not a supposition about coverage.
_SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+|\n+")

# Leaving a supposition behind. Deliberately the same vocabulary G13 already
# tests for hypothetical *values* ("now go back to my actual record"), because
# members do not use one set of words for numbers and another for facts.
_LEAVES_FRAME = re.compile(
    r"\b(?:actually|in\s+reality|in\s+my\s+case|really|truly|"
    r"(?:back|go\s+back|going\s+back)\s+to\s+my\s+(?:actual|real|own)|"
    r"my\s+(?:actual|real|own)\s+(?:record|situation|case|job|position|employer)|"
    r"forget\s+(?:the|that)\s+hypothetical|never\s+mind\s+(?:the|that))\b",
    re.IGNORECASE,
)


def _governor(question: str, start: int, frame: str = "") -> str:
    """Which act the frame at ``start`` is performing.

    Only the text before it *within its own sentence* counts.
    """
    prefix = question[:start]
    parts = _SENTENCE_SPLIT.split(prefix)
    clause = parts[-1] if parts else prefix
    # The frame's own opening counts too: the doubt in "I might be a park
    # employee" sits inside the frame rather than in front of it.
    clause = f"{clause} {' '.join(frame.split()[:4])}"

    # Doubt is checked first. "I'm not sure if I'd be a park employee" carries
    # both an "if" and a "not sure"; the member is telling you they do not know,
    # and that is the fact worth acting on.
    if _DOUBTED_GOVERNOR.search(clause):
        return DOUBTED
    if _STIPULATED_GOVERNOR.search(clause):
        return STIPULATED
    return ASSERTED


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip(" ,.;:?!")
    # Strip a trailing interrogative that the frame's greedy tail swallowed —
    # "I'm a park employee what is my pension" is a description and a question
    # run together.
    #
    # Relative pronouns are deliberately absent from this list. They read as
    # question words and are not: "a worker who works at the park" is one
    # description, and stripping at "who" left it as "worker", which matches
    # every Article in the Code and none of them well. That phrasing is the
    # whole reason a keyword table was rejected, so losing it here would have
    # given the corpus the one input it cannot do anything with.
    text = re.sub(
        r"\s+(?:how|what|when|why|does|do|can|will|would)\b.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip(" ,.;:?!")


def classify_self_reference(question: str) -> SelfReference | None:
    """What the member said about who they are, and which act they performed.

    Returns ``None`` when they said nothing about themselves, which is the
    common case and the one that must cost nothing.
    """
    if not question:
        return None

    # Checked first: an interrogative beats every other reading of the same
    # words. "Am I a park employee?" would otherwise match the plain copula
    # frame and be read as someone telling you that they are.
    asking = _ASKS_ABOUT_SELF.search(question)
    if asking:
        described = _clean(asking.group("q"))
        if described:
            return SelfReference(text=described, act=DOUBTED, kind=COVERAGE)

    status = _STATUS_FRAME.search(question)
    coverage = _COVERAGE_FRAME.search(question)

    # Coverage wins a tie: it decides which law applies, and status only decides
    # a fact within it.
    if coverage and (not status or coverage.start() <= status.start()):
        described = _clean(
            next(
                (g for g in coverage.groupdict().values() if g),
                "",
            )
        )
        if not described:
            return None
        return SelfReference(
            text=described,
            act=_governor(question, coverage.start(), coverage.group(0)),
            kind=COVERAGE,
        )

    if status:
        described = _clean(status.group("s") or status.group(0))
        return SelfReference(
            text=described,
            act=_governor(question, status.start(), status.group(0)),
            kind=STATUS,
        )

    return None


def leaves_hypothetical(question: str) -> bool:
    """True when the member is stepping out of a supposition."""
    return bool(_LEAVES_FRAME.search(question or ""))


# ── Asking the Code who it covers ─────────────────────────────────

_ASSESS_SYSTEM = """You match how a person describes their job against the coverage
tests in the Illinois Pension Code.

You are shown how a member described themselves, and the sections of the Code
that say who each Article covers. Decide which Articles could cover someone who
describes themselves that way.

Rules:
- Work ONLY from the sections shown. Never use knowledge of the Pension Code
  from anywhere else.
- Every candidate must name a section that appears below and quote the test from
  it. A candidate you cannot ground in one of these sections is not a candidate.
- Match on substance, not wording. "A worker who works at the park", "park
  employee" and "employee of a board of park commissioners" describe the same
  person; the member does not know the statute's vocabulary and is not required
  to.
- Return every Article whose test the description could satisfy. Two candidates
  is a real and useful answer -- it says the description alone does not settle
  it, which is what you were asked.
- "settled_by" names the ONE fact that would separate the candidates: usually
  which employer pays them. Write it as something a member could answer in a few
  words, not as a statutory element.
- If the description matches nothing shown, return an empty list. That is a
  normal answer, not a failure.

Respond with JSON only:
{"candidates": [{"article": "12", "section": "12-101",
                 "test": "the words from the section that they would have to satisfy",
                 "settled_by": "which employer they work for"}]}"""


@dataclass
class CoverageReport:
    """Candidate Articles for a description, each grounded in a section."""

    candidates: list[dict[str, Any]] = field(default_factory=list)
    considered_sections: list[str] = field(default_factory=list)

    @property
    def articles(self) -> list[str]:
        seen: list[str] = []
        for candidate in self.candidates:
            article = str(candidate.get("article") or "")
            if article and article not in seen:
                seen.append(article)
        return seen


async def assess(
    description: str,
    sections: list[Any],
    *,
    usage: Any | None = None,
) -> CoverageReport:
    """Match a self-description against the Code's own coverage tests.

    ``sections`` are ``RetrievedSection``s from ``retrieval.retrieve_coverage``.
    A candidate naming a section that is not among them is dropped, exactly as
    an answer citing an unretrieved section is rejected: the model may do the
    semantic work, but it may not introduce an Article it never read the
    coverage test for. That is the whole difference between this and the
    hallucinated bridge it replaces.
    """
    # Retrieval order is evidence and is kept. Two members can describe
    # themselves identically and belong to different funds -- "park employee"
    # returns the same candidates for an Article 11 member and an Article 12 one
    # -- so which candidate the corpus ranked *first* on their own words is the
    # only thing that separates a description supporting the record from one
    # pointing away from it.
    order = {
        getattr(s, "section", ""): rank
        for rank, s in enumerate(sections)
        if getattr(s, "section", "")
    }
    available = set(order)
    if not description or not available:
        return CoverageReport()

    settings = get_settings()
    corpus = "\n\n".join(
        f"### 40 ILCS 5/{s.section} — {getattr(s, 'title', '')}\n{s.text}"
        for s in sections
    )

    try:
        raw = await complete(
            node="assess_coverage",
            messages=[
                {"role": "system", "content": _ASSESS_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"The member described themselves as: {description}\n\n"
                        f"Coverage sections:\n{corpus}"
                    ),
                },
            ],
            usage=usage,
            reasoning_effort=settings.reasoning_effort_coverage,
            json_mode=True,
        )
        parsed = json.loads(raw or "{}")
    except Exception as exc:
        # A coverage assessment that fails must not sink the turn. Falling back
        # to no candidates means falling back to the record, which is exactly
        # today's behaviour -- minus the licence to present it as established.
        logger.warning("Coverage assessment failed for %r: %s", description, exc)
        return CoverageReport(considered_sections=sorted(available))

    grounded: list[dict[str, Any]] = []
    for candidate in parsed.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        section = str(candidate.get("section") or "").strip()
        article = str(candidate.get("article") or "").strip()
        if not article or section not in available:
            logger.info(
                "Dropping ungrounded coverage candidate: article=%r section=%r",
                article,
                section,
            )
            continue
        if article in NON_MEMBERSHIP_ARTICLES:
            logger.info(
                "Dropping non-membership coverage candidate: article=%r section=%r",
                article,
                section,
            )
            continue
        grounded.append(
            {
                "article": article,
                "section": section,
                "test": str(candidate.get("test") or "").strip(),
                "settled_by": str(candidate.get("settled_by") or "").strip(),
                "rank": order.get(section, len(order)),
            }
        )

    # Best-supported first, so `articles` reads in the order the member's own
    # words earned rather than the order the model happened to emit them.
    grounded.sort(key=lambda c: c["rank"])
    return CoverageReport(candidates=grounded, considered_sections=sorted(available))


# ── Deciding, in code ─────────────────────────────────────────────


def adjudicate(
    reference: SelfReference | None,
    report: CoverageReport | None,
    record_article: str | None,
    *,
    turn: int = 0,
) -> dict[str, Any]:
    """Decide what this conversation now believes about coverage, and why.

    Pure. Every branch is a rule someone can read and disagree with, which is
    the point -- this is the determination the whole answer rests on.
    """
    candidates = list(report.candidates) if report else []
    articles = report.articles if report else []
    base: dict[str, Any] = {
        "record_article": record_article,
        "candidates": candidates,
        "turn": turn,
        "described_as": reference.text if reference else None,
    }

    # A stipulated premise is adopted whole. The member is not claiming to be
    # anything, so there is nothing to check and nothing to ask about.
    if reference is not None and reference.act == STIPULATED:
        return {
            **base,
            "article": articles[0] if articles else None,
            "basis": BASIS_HYPOTHETICAL,
            "conflict": False,
        }

    # They told you they do not know. Everything else here is the system being
    # unsure; this is the member being unsure, which outranks it.
    if reference is not None and reference.act == DOUBTED:
        return {
            **base,
            "article": record_article,
            "basis": BASIS_RECORD,
            "conflict": True,
            "why": "doubted",
        }

    if not record_article:
        # Nobody to be wrong about. One candidate settles it; several or none
        # leave it open, and with no record there is nothing to fall back to.
        return {
            **base,
            "article": articles[0] if len(articles) == 1 else None,
            "basis": BASIS_STATUTORY if len(articles) == 1 else BASIS_UNRESOLVED,
            "conflict": len(articles) != 1,
            "why": "anonymous",
        }

    if not candidates:
        # The description matched no coverage test. Fall back to the record --
        # but `basis: record` is what stops the answer presenting their job as
        # the reason they are in that Article.
        return {**base, "article": record_article, "basis": BASIS_RECORD, "conflict": False}

    if articles[0] == record_article:
        # The Article their own words support best is the one they are filed
        # under. This is the good case, and the only one where an answer may say
        # "as a park employee covered by Article 12" -- because now the Code
        # says so too.
        #
        # Judged on the *best-supported* candidate rather than on the candidate
        # list being exactly [record]. Coverage tests overlap by design: "park
        # employee" matches Section 11-110 ("any person employed by the board")
        # as well as Section 12-118, so two members who describe themselves
        # identically produce an identical candidate list and differ only in
        # their file. Requiring a single candidate would mean an Article 12 park
        # employee gets interrogated about being a park employee -- and being
        # asked to confirm something you just said, which your own record
        # already agrees with, is the same failure as not being listened to.
        return {
            **base,
            "article": record_article,
            "basis": BASIS_STATUTORY,
            "conflict": False,
        }

    # Their words point somewhere their file does not. Whether the record's
    # Article appears further down the list changes what to say about it, not
    # whether to ask.
    return {
        **base,
        "article": record_article,
        "basis": BASIS_RECORD,
        "conflict": True,
        "why": "elsewhere" if record_article not in articles else "outranked",
    }


def open_question_for(coverage: dict[str, Any]) -> dict[str, Any]:
    """The question to put to the member, and what an answer would settle."""
    candidates = coverage.get("candidates") or []
    record_article = coverage.get("record_article")
    options = []
    for candidate in candidates:
        options.append(
            {
                "article": candidate.get("article"),
                "section": candidate.get("section"),
                "test": candidate.get("test"),
            }
        )
    if record_article and record_article not in [o["article"] for o in options]:
        # The record's own Article belongs in the question even though the
        # coverage search did not surface a test for it — otherwise the member
        # is asked to choose between possibilities without being told where
        # their file already places them. Flagged, so the answer describes it as
        # what the record says rather than as another statutory candidate.
        options.append(
            {
                "article": record_article,
                "section": None,
                "test": None,
                "from_record": True,
            }
        )
    return {
        "about": "coverage",
        "described_as": coverage.get("described_as"),
        "options": options,
        "asked_turn": coverage.get("turn", 0),
        "settled_by": next(
            (c.get("settled_by") for c in candidates if c.get("settled_by")), None
        ),
    }


# How long an unanswered question stays live. A member may answer a turn late --
# they often ask something else first -- but an open question that never expires
# is a trap: six turns on, someone mentioning "Article 12" in passing would be
# read as finally answering it, and filed into that fund on the strength of a
# remark.
OPEN_QUESTION_TURNS = 2


def question_is_live(open_question: dict[str, Any] | None, turn: int) -> bool:
    """Whether an unanswered question is still recent enough to bind against."""
    if not open_question:
        return False
    asked = open_question.get("asked_turn")
    if asked is None:
        return True
    return turn - int(asked) <= OPEN_QUESTION_TURNS


# ── Binding the member's reply ────────────────────────────────────

# A reply that puts the question back rather than answering it: asking what an
# Article provides, or saying in so many words that they do not know.
_ASKS_BACK = re.compile(
    r"\b(?:what (?:type|kind|sort)s? of|which employees|who (?:comes?|falls?) under|"
    r"is (?:that|it|this) (?:right|correct|true)|am i (?:right|correct)|"
    r"i thought|i don'?t know|not sure|no idea|can you (?:tell|explain)|"
    r"what does .{0,20}(?:say|cover|mean)|how do i (?:know|find out)|"
    r"what should i|which one|"
    # Challenging the previous answer is not answering the previous question.
    r"why did you|why do you|you said|you told me|what do you mean|"
    r"that'?s (?:not|wrong)|i never said|"
    # Reported speech. "When I said I was a park employee, why did you…" is the
    # member recounting what they said, not saying it again — and the recounted
    # clause parses identically to a fresh assertion.
    r"(?:when |after )?i (?:said|told you|asked|mentioned)|i already (?:said|told))\b",
    re.IGNORECASE,
)

# Anything the member put in quotation marks. Almost always the assistant's own
# words being quoted back, and never the member's claim about themselves.
_QUOTED = re.compile(r"[\"“”'']{1,2}[^\"“”]{6,600}?[\"“”'']{1,2}", re.DOTALL)

_AFFIRM = re.compile(r"\b(?:yes|yeah|yep|correct|right|that'?s right|exactly|true)\b", re.I)
_DENY = re.compile(r"\b(?:no|nope|not|incorrect|wrong|neither)\b", re.I)
_ARTICLE_IN_REPLY = re.compile(r"\bart(?:icle)?\.?\s*(\d{1,2})\b", re.I)


def _asks_rather_than_answers(reply: str) -> bool:
    """Is this reply a question back, rather than an answer?

    An asserted self-reference always counts as an answer, however the sentence
    around it is punctuated: "I am an employee of a board of park commissioners,
    how is my pension calculated?" ends in a question mark and is nonetheless
    the member telling you what they are.
    """
    # Checked before the classifier, not after. "So when I said I was a park
    # employee, why did you…" contains a clause that parses exactly like a fresh
    # assertion, and taking it as one confirms the member into the Article they
    # are objecting to.
    if _ASKS_BACK.search(reply):
        return True
    reference = classify_self_reference(reply)
    if reference is not None:
        return reference.act != ASSERTED
    return False


def bind_reply(reply: str, open_question: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve an open coverage question against the member's next message.

    This is the turn-4 fix. The member answered -- in the statute's own words --
    and nothing captured it, so the next turn asked again and computed the same
    wrong figure. A question whose answer does not stick is worse than no
    question: it costs a turn and teaches the member they are not being heard.

    Returns the coverage update, or ``None`` when the reply did not settle it.
    """
    if not reply or not open_question or open_question.get("about") != "coverage":
        return None

    # What the member quoted is not what the member said. The third turn of the
    # reported conversation was the member quoting the assistant's own wrong
    # sentence back at it — "why did you say 'As a park employee covered by
    # Article 11...'" — and reading the Article number out of that quotation
    # confirmed them into the very Article they were objecting to.
    reply = _QUOTED.sub(" ", reply)

    # Asking about an Article is not answering about one. "I thought park
    # employees came under Article 12, is that right, and what type of employees
    # come under Article 12?" names the Article twice and settles nothing — the
    # member is putting the question back, and reading it as a confirmation
    # would record them into a fund on the strength of their own uncertainty.
    if _asks_rather_than_answers(reply):
        return None

    options = open_question.get("options") or []
    if not options:
        return None
    articles = [str(o.get("article")) for o in options if o.get("article")]

    def settled(article: str, how: str) -> dict[str, Any]:
        return {
            "article": article,
            "basis": BASIS_CONFIRMED,
            "conflict": False,
            "confirmed_by": how,
        }

    # They named the Article outright.
    named = [m.group(1) for m in _ARTICLE_IN_REPLY.finditer(reply)]
    for article in named:
        if article in articles:
            return settled(article, "named the Article")

    # They named a section, which implies its Article.
    for match in re.finditer(r"\b(\d{1,2})-\d{1,3}(?:\.\d{1,2})?\b", reply):
        if match.group(1) in articles:
            return settled(match.group(1), "named a section")

    # They used one option's own distinguishing words. Scored on the rarer terms
    # of each test, so "park" separates 12-101 from 11-110 while "employee",
    # which every test contains, separates nothing.
    scores: dict[str, int] = {}
    lowered = reply.lower()
    for option in options:
        article = str(option.get("article") or "")
        terms = _distinguishing_terms(option, options)
        hit = sum(1 for term in terms if term in lowered)
        if hit:
            scores[article] = max(scores.get(article, 0), hit)
    if len(scores) == 1:
        article = next(iter(scores))
        return settled(article, "described the employer")
    if len(scores) > 1:
        best = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        if best[0][1] > best[1][1]:
            return settled(best[0][0], "described the employer")

    # A bare yes/no only settles a question that had exactly two options, and
    # only when it is not also a denial ("no, the park district" is handled
    # above by its own words).
    if len(articles) == 2 and _AFFIRM.search(reply) and not _DENY.search(reply):
        return settled(articles[0], "confirmed")

    return None


_STOPWORDS = frozenset(
    """a an the of or and to in for by any person employee employees service
    position such this that with who whose shall means their his her its is are
    was were been being not been under upon which as at on from""".split()
)


def _distinguishing_terms(option: dict[str, Any], options: list[dict[str, Any]]) -> list[str]:
    """Words in this option's test that no other option's test uses."""
    def words(entry: dict[str, Any]) -> set[str]:
        text = f"{entry.get('test') or ''}".lower()
        return {w for w in re.findall(r"[a-z]{4,}", text) if w not in _STOPWORDS}

    mine = words(option)
    others: set[str] = set()
    for other in options:
        if other is not option:
            others |= words(other)
    return sorted(mine - others)
