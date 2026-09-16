"""The investigation: search, read what came back, decide what to do next.

Offline: `call_with_tools` is scripted and the tools are faked, because what is
under test is the control flow -- when the loop stops, what it keeps, and what
it does when a budget runs out -- not whether the model is clever.
"""

from __future__ import annotations

from pension_chat.agent import loop as loop_mod
from pension_chat.agent.llm import ToolCall, ToolTurn


def _turn(*calls: tuple[str, dict], content: str = "") -> ToolTurn:
    parsed = tuple(
        ToolCall(id=f"c{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls)
    )
    raw: dict = {"role": "assistant"}
    if parsed:
        raw["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}}
            for c in parsed
        ]
    if content:
        raw["content"] = content
    return ToolTurn(calls=parsed, content=content, raw_message=raw)


def _script(monkeypatch, turns: list[ToolTurn], calls_seen: list | None = None):
    sequence = iter(turns)

    async def fake_call(**kwargs):
        if calls_seen is not None:
            calls_seen.append(kwargs)
        try:
            return next(sequence)
        except StopIteration:
            return _turn(content="done")

    monkeypatch.setattr(loop_mod, "call_with_tools", fake_call)


def _tools(monkeypatch, result: str = "a section", update: dict | None = None):
    ran: list[tuple[str, dict]] = []

    async def fake_run(name, arguments, state):
        ran.append((name, arguments))
        return result, (update or {})

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    return ran


async def test_a_loop_that_searches_then_answers(monkeypatch):
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")])
    ran = _tools(monkeypatch)

    out = await loop_mod.investigate({"question": "what is Article 11?", "messages": []})

    # Two searches now: the prefetch before the first model call, and the one
    # the model asked for. Both are the loop searching; the count is incidental.
    assert [name for name, _ in ran] == ["search_statute", "search_statute"]
    assert any(m.get("role") == "tool" for m in out["loop_messages"])


async def test_tool_results_accumulate_into_state(monkeypatch):
    """The key the pipeline writes, so nothing downstream has to change."""
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")])
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "text": "t"}]})

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert [s["section"] for s in out["retrieved_sections"]] == ["11-134"]


async def test_sections_accumulate_across_iterations_without_duplicating(monkeypatch):
    _script(
        monkeypatch,
        [
            _turn(("search_statute", {"queries": ["a"]})),
            _turn(("search_statute", {"queries": ["b"]})),
            _turn(content="Here."),
        ],
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "text": "t"}]})

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert [s["section"] for s in out["retrieved_sections"]] == ["11-134"], "same section twice is once"


async def test_the_total_budget_ends_the_turn(monkeypatch):
    """A model that never stops calling must still produce an answer."""
    _script(monkeypatch, [_turn(("get_section", {"section": "11-134"})) for _ in range(60)])
    ran = _tools(monkeypatch)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert len(ran) <= loop_mod.BUDGETS["total"]
    assert out["loop_exhausted"] is True


async def test_an_exhausted_sub_budget_removes_only_that_tool(monkeypatch):
    """The rule most likely to be implemented as "end the turn".

    A loop out of searches can still read a section it already knows about,
    follow a cross-reference, or answer. Ending the turn throws all of that
    away over one spent resource.
    """
    seen: list[dict] = []
    _script(
        monkeypatch,
        [_turn(("search_statute", {"queries": ["x"]})) for _ in range(loop_mod.BUDGETS["search_statute"] + 1)]
        + [_turn(content="Here.")],
        calls_seen=seen,
    )
    _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    offered = [{t["function"]["name"] for t in kwargs["tools"]} for kwargs in seen]
    assert "search_statute" not in offered[-1], "the spent tool is withdrawn"
    assert "get_section" in offered[-1], "the others stay"


async def test_a_spent_tool_is_explained_not_silently_dropped(monkeypatch):
    seen: list[dict] = []
    _script(
        monkeypatch,
        [_turn(("search_statute", {"queries": ["x"]})) for _ in range(loop_mod.BUDGETS["search_statute"] + 1)]
        + [_turn(content="Here.")],
        calls_seen=seen,
    )
    _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    last = seen[-1]["messages"]
    assert any("search_statute" in str(m.get("content", "")) and "no more" in str(m.get("content", "")).lower()
               for m in last), "the model is told why the tool vanished"


async def test_unread_cross_references_are_reported_on_exhaustion(monkeypatch):
    """Exhaustion is not a licence to answer.

    A figure cannot be fabricated -- compute_benefit has no number without
    inputs -- but prose can still conclude on law that was never read. The gap
    is computed, not judged: what the evidence cites, minus what was read.
    """
    _script(monkeypatch, [_turn(("get_section", {"section": "11-134"})) for _ in range(60)])
    _tools(
        monkeypatch,
        update={
            "retrieved_sections": [
                {"section": "11-134", "text": "t", "referenced_sections": ["11-216", "11-134"]}
            ]
        },
    )

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert out["unread_references"] == ["11-216"], "cited but never read; itself does not count"


async def test_a_malformed_tool_call_does_not_end_the_turn(monkeypatch):
    """`call_with_tools` gives {} for broken JSON. The tool reports what it needed."""
    _script(monkeypatch, [_turn(("search_statute", {})), _turn(content="Here.")])
    ran = _tools(monkeypatch, result="search_statute needs at least one query.")

    out = await loop_mod.investigate({"question": "q", "messages": []})

    # The prefetch searches first with real arguments; the model's own call is
    # the one with the broken JSON, and it is the one under test.
    assert ("search_statute", {}) in ran
    assert out["loop_exhausted"] is False


async def test_a_loop_that_answers_without_reading_anything_is_sent_back(monkeypatch):
    """The failure this guard exists for, caught on a live turn.

    Asked "what is Article 11?" with a member on file, the loop called no tools
    and answered from memory: "Article 11 governs the Municipal Employees'
    Annuity and Benefit Fund". Article 11 is the Laborers' fund. The guardrail
    passed it -- there were no citations to verify and no figures to trace --
    so nothing anywhere caught a confident, wrong answer about the law.

    A model that has read nothing has nothing to be right from. It gets one
    push back; if it insists, `compose` is told it consulted nothing.
    """
    seen: list[dict] = []
    _script(monkeypatch, [_turn(content="Article 11 is the Municipal fund."),
                          _turn(("search_statute", {"queries": ["Article 11"]})),
                          _turn(content="Article 11 is the Laborers' fund.")], calls_seen=seen)
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-101", "text": "t"}]})

    out = await loop_mod.investigate({"question": "what is Article 11?", "messages": []})

    assert ran, "it must be sent back to read something"
    assert [s["section"] for s in out["retrieved_sections"]] == ["11-101"]


async def test_the_push_back_happens_once_and_then_it_is_let_go(monkeypatch):
    """A model that insists is not argued with -- that would spend the budget
    on a fight. It is let through, and `compose` says what it did not read.

    It is no longer let through *unsearched*, though: a turn that ran no search
    gets one run for it on the member's own words. Arguing twice is a fight;
    looking once is the step `retrieve_policy` never made optional."""
    _script(monkeypatch, [_turn(content="I am sure."), _turn(content="Still sure.")])
    ran = _tools(monkeypatch)  # no update, so nothing is ever found

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert [name for name, _ in ran] == ["search_statute"], "looked once, argued once"
    assert out["consulted_nothing"] is True, "and still found nothing"


async def test_an_outline_alone_does_not_count_as_having_read_the_code(monkeypatch):
    """Headings are a map, not the territory.

    The first version of this guard asked whether any *tool* had run. The loop
    then called `outline`, stopped, and described Article 11 from memory with
    nothing citable behind it -- `outline` returns headings and writes no
    sections, exactly so that a heading cannot be mistaken for statute text.
    """
    _script(
        monkeypatch,
        [_turn(("outline", {"article": "11"})), _turn(content="Article 11 is..."),
         _turn(("get_section", {"section": "11-101"})), _turn(content="Article 11 is...")],
    )
    calls: list[str] = []

    async def fake_run(name, arguments, state):
        calls.append(name)
        if name == "get_section":
            return "text", {"retrieved_sections": [{"section": "11-101", "text": "t"}]}
        return "headings", {}

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)

    out = await loop_mod.investigate({"question": "what is Article 11?", "messages": []})

    assert "get_section" in calls, "an outline is not enough to answer from"
    assert out["consulted_nothing"] is False


async def test_the_evidence_is_completed_before_the_answer_is_written(monkeypatch):
    """The step the loop dropped, which the pipeline had all along.

    `resolve_recipes` pulls a recipe's cross-referenced sections in explicitly,
    because "an answer that names Section 11-216 without its text in front of it
    is citing from memory, which verification rejects outright". The loop
    gathered evidence that leaned on sections it never read, and then cited them
    -- 11-216, 11-160.1, 11-124 -- and the guardrail threw each answer away.

    Two prompt-level attempts did not stop it, because it was never a prompt
    problem: the evidence was incomplete.
    """
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")])
    fetched: list[str] = []

    async def fake_run(name, arguments, state):
        if name == "get_section":
            fetched.append(arguments["section"])
            return "text", {"retrieved_sections": [{"section": arguments["section"], "text": "t"}]}
        return "found", {
            "retrieved_sections": [
                {"section": "11-134", "text": "t", "referenced_sections": ["11-216"]}
            ]
        }

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert fetched == ["11-216"], "the section the evidence leans on is read before answering"
    assert {s["section"] for s in out["retrieved_sections"]} == {"11-134", "11-216"}
    assert out["unread_references"] == [], "nothing is left dangling"


async def test_completion_prefers_what_the_most_sections_lean_on(monkeypatch):
    """Bounded, so one section naming a dozen others cannot flood the answer.

    Ranked by how many of the gathered sections depend on each target -- the
    same weighting Phase 1 gave `_follow_references`, for the same reason.
    """
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")])
    fetched: list[str] = []

    async def fake_run(name, arguments, state):
        if name == "get_section":
            fetched.append(arguments["section"])
            return "text", {"retrieved_sections": [{"section": arguments["section"], "text": "t"}]}
        return "found", {
            "retrieved_sections": [
                {"section": "11-130", "text": "t", "referenced_sections": ["11-216", "11-169"]},
                {"section": "11-134", "text": "t", "referenced_sections": ["11-216", "11-131"]},
                {"section": "11-133", "text": "t", "referenced_sections": ["11-216"]},
            ]
        }

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert fetched[0] == "11-216", "three sections lean on it; the others one each"
    assert len(fetched) <= loop_mod.MAX_COMPLETION_FETCHES
    assert out["unread_references"] == [] or "11-216" not in out["unread_references"]


async def test_completion_does_not_run_when_the_budget_is_gone(monkeypatch):
    """An exhausted loop has already spent what it had. Completing evidence is
    worth a few calls, not an overrun.

    Asked as a self-description so the prefetch is skipped: a prefetched turn
    stops after one round and never reaches exhaustion, which would make this
    test about the cap rather than about the budget."""
    _script(monkeypatch, [_turn(("get_section", {"section": "11-134"})) for _ in range(60)])
    fetched: list[str] = []

    async def fake_run(name, arguments, state):
        fetched.append(arguments.get("section", "?"))
        return "text", {
            "retrieved_sections": [
                {"section": "11-134", "text": "t", "referenced_sections": ["11-216"]}
            ]
        }

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)

    out = await loop_mod.investigate(
        {"question": "i am a park employee, what happens?", "messages": []}
    )

    assert out["loop_exhausted"] is True
    assert out["unread_references"] == ["11-216"], "reported instead, so compose can say so"


async def test_a_tool_sees_what_earlier_tools_found(monkeypatch):
    """compute_benefit derives its recipe from the statute the loop gathered.

    The loop kept its sections in a local and handed `run_tool` the turn's
    original state, so a calculation ran against an empty corpus and reported
    'no calculation ran' -- with the sections sitting right there in the loop.
    """
    # Both in one round. A prefetched turn gets one tool round, and what this
    # is about is that the second call sees what the first found -- which the
    # loop threads through `working` within a round as well as between them.
    _script(
        monkeypatch,
        [
            _turn(
                ("search_statute", {"queries": ["annuity"]}),
                ("compute_benefit", {"benefit": "retirement_annuity"}),
            ),
            _turn(content="Here."),
        ],
    )
    saw: list[list] = []

    async def fake_run(name, arguments, state):
        if name == "compute_benefit":
            saw.append([s["section"] for s in (state.get("retrieved_sections") or [])])
            return "figure", {}
        return "found", {"retrieved_sections": [{"section": "11-134", "text": "t"}]}

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)

    await loop_mod.investigate({"question": "q", "messages": [], "retrieved_sections": []})

    assert saw == [["11-134"]], "the calculation must see what the search found"


async def test_a_live_premise_is_re_stated_every_turn(monkeypatch):
    """A follow-up exploring a supposition carries none of its words.

    "and at 60?" is still a question about being a Chicago police officer, so
    the frame has to be in front of the model on every turn it is live --
    otherwise the premise quietly reverts to the record the turn after it was
    set up, and the member gets their own fund's law under somebody else's job.
    """
    context = loop_mod._context(
        {
            "question": "and at 60?",
            "hypothetical_frame": {"premise": "a Chicago police officer", "article": "5"},
        }
    )
    assert "a Chicago police officer" in context
    assert "Article 5" in context
    assert "conditional" in context


async def test_a_premise_that_was_never_placed_says_so(monkeypatch):
    """L08's original failure, in one line of context: the frame existed with
    `article: None`, so retrieval stayed on Article 11 and answered a question
    about somebody else's law with the member's own."""
    context = loop_mod._context(
        {"question": "q", "hypothetical_frame": {"premise": "a dog walker", "article": None}}
    )
    assert "never placed in an Article" in context


async def test_the_loop_can_reach_coverage(monkeypatch):
    """Coverage is a tool here, not a stage. The pipeline settles it in a node
    before retrieval; the loop has no such node, and the model reading the
    question is the one who knows a supposition when it sees one."""
    names = {t["function"]["name"] for t in loop_mod.LOOP_TOOLS}
    assert "check_coverage" in names
    assert names <= set(loop_mod.BUDGETS), "every tool offered must have a budget"


async def test_a_premise_confines_what_the_answer_may_rest_on(monkeypatch):
    """Asked what a Chicago police officer would get, the loop read Article 3 --
    police *outside* Chicago -- while ruling it out, and then cited it.

    Reading it was right; citing it is the exact harm the case exists for, one
    fund's law answering another fund's question with a real citation on it. The
    pipeline cannot make this mistake because its retrieval filter is the
    determination. The loop has no filter, so the determination is applied to
    what it gathered: under a premise naming an Article, the evidence is that
    Article and the Articles that govern every member. The rest was
    investigation, not evidence.
    """
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["police annuity"]})),
                          _turn(content="Found it.")])
    _tools(
        monkeypatch,
        update={
            "retrieved_sections": [
                {"section": "5-132", "article": "5"},
                {"section": "3-111", "article": "3"},
                {"section": "1-160", "article": "1"},
                {"section": "11-134", "article": "11"},
            ]
        },
    )

    out = await loop_mod.investigate(
        {
            "question": "if I were a Chicago police officer, what would I get?",
            "hypothetical_frame": {"premise": "a Chicago police officer", "article": "5"},
        }
    )
    kept = [s["section"] for s in out["retrieved_sections"]]
    assert "5-132" in kept, "the Article the premise named"
    assert "1-160" in kept, "Tier 2 governs every member, whichever fund they are in"
    assert "3-111" not in kept and "11-134" not in kept, "other funds' law"


async def test_without_a_premise_nothing_is_dropped(monkeypatch):
    """A question comparing two funds genuinely rests on both, and no premise
    has committed the answer to either."""
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="ok")])
    _tools(
        monkeypatch,
        update={
            "retrieved_sections": [
                {"section": "11-134", "article": "11"},
                {"section": "12-133", "article": "12"},
            ]
        },
    )
    out = await loop_mod.investigate({"question": "how do Articles 11 and 12 differ?"})
    assert len(out["retrieved_sections"]) == 2


async def test_a_premise_that_named_no_article_confines_nothing(monkeypatch):
    """There is nothing to confine to. Dropping everything would leave the
    answer resting on nothing at all."""
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="ok")])
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "article": "11"}]})
    out = await loop_mod.investigate(
        {"question": "q", "hypothetical_frame": {"premise": "a dog walker", "article": None}}
    )
    assert [s["section"] for s in out["retrieved_sections"]] == ["11-134"]


async def test_a_figure_already_given_is_in_front_of_the_model(monkeypatch):
    """R03, and it is CRITICAL.

    Turn 2 of "how much pension will i get" / "actually sorry it's more like 23
    years" spent one check_coverage call and never computed: the model read a
    correction to a *quantity* as a statement about who the member is, because
    nothing in front of it said a figure was standing or what that figure
    rested on. The pipeline does not need the model for this -- `_needs_
    calculation` routes on `stale_figures` deterministically -- so the loop's
    model has to be told what the pipeline's edge already knew.
    """
    context = loop_mod._context(
        {
            "question": "actually sorry it's more like 23 years, i forgot the first two",
            "member_facts": {"service_years": "21.5"},
            "baseline": {
                "turn": 1,
                "annual_amount": "32126.78",
                "section": "11-134",
                "rests_on": ["service_years", "age_at_annuity_start"],
            },
        }
    )
    assert "32126.78" in context or "32,126.78" in context
    assert "11-134" in context
    assert "service_years" in context
    assert "contradict" in context.lower() or "correct" in context.lower()


async def test_no_earlier_figure_means_no_such_line(monkeypatch):
    context = loop_mod._context({"question": "q", "member_facts": {"service_years": "21.5"}})
    assert "stands from" not in context.lower()


async def test_compose_reports_the_rung_the_evidence_supports(monkeypatch):
    """`select_rung` runs inside `prepare_answer`, which the loop does not use,
    so every loop answer reported rung 0 -- including R07's, whose content is
    right and does disclose its assumption."""
    out = await loop_mod.compose(
        {
            "question": "q",
            "loop_messages": [],
            "calculation_result": {"retirement_annuity": {"base": {"ok": True}}},
            "assumptions": {
                "withdrawal_date": {
                    "value": "2026-09-13",
                    "source": "assumed",
                    "basis": "the date you stop working, taken as today",
                }
            },
        }
    )
    assert out["answer_rung"] == 2, "a figure resting on an assumed value"


async def test_compose_reports_a_clean_figure_at_its_own_rung(monkeypatch):
    """Rung 1 is a figure every input of which somebody supplied."""
    out = await loop_mod.compose(
        {
            "question": "q",
            "loop_messages": [],
            "calculation_result": {"retirement_annuity": {"base": {"ok": True}}},
        }
    )
    assert out["answer_rung"] == 1


# ── Binding the member's reply ────────────────────────────────────


_ASKED = {
    "employee_record": {"article_number": 11},
    "turn_index": 1,
    "clarifications_asked": 1,
    "open_question": {
        "about": "coverage",
        "described_as": "a park employee",
        "asked_turn": 0,
        "options": [
            {"article": "12", "section": "12-101", "test": "board of park commissioners"},
            {"article": "11", "section": None, "test": None, "from_record": True},
        ],
    },
}


def test_the_members_own_answer_is_bound_before_the_model_runs():
    """G32, and it is CRITICAL.

    The member restated their coverage in the statute's own literal words --
    "employee of a board of park commissioners" IS the Section 12-101 test --
    and the loop had nothing to bind it to, so the verdict stayed on the record
    and the same Article 11 figure came back. A question whose answer does not
    stick is worse than no question: it costs a turn and teaches the member
    they are not being heard.

    Deterministic on purpose. `bind_reply` is pure Python reading an Article
    number, a section number, or one option's distinguishing words; leaving it
    to the model puts the member's own answer at the mercy of a re-reading.
    """
    bound = loop_mod._bind_open_question(
        {**_ASKED, "question": "i am an employee of a board of park commissioners"}
    )
    assert bound["coverage"]["article"] == "12"
    assert bound["coverage"]["basis"] == "member_confirmed"
    assert bound["coverage"]["conflict"] is False
    assert bound["open_question"] is None
    assert bound["candidate_articles"] == ["12"]


def test_a_reply_that_settles_nothing_leaves_the_question_open():
    """G33: asking about an Article is not answering about one."""
    assert loop_mod._bind_open_question(
        {**_ASKED, "question": "what type of employees come under Article 12?"}
    ) == {}


def test_a_stale_question_is_not_bound_against():
    """An open question that never expires is a trap: six turns on, somebody
    mentioning Article 12 in passing would be filed into that fund on the
    strength of a remark."""
    assert loop_mod._bind_open_question(
        {**_ASKED, "turn_index": 9, "question": "i am an employee of a board of park commissioners"}
    ) == {}


def test_nothing_asked_means_nothing_to_bind():
    assert loop_mod._bind_open_question({"question": "what is Article 11?"}) == {}


async def test_a_turn_that_says_nothing_about_the_member_still_knows_their_article(monkeypatch):
    """fund_smoke, all eighteen funds, 0/18 against the pipeline's 13/18 -- and
    every one on the same line: `coverage article None`.

    `assess_coverage` sets coverage from the record even on its fast exit, so
    the pipeline always has a verdict. The loop only had one when the model
    called check_coverage, which it does only when the member describes
    themselves -- and "roughly what would my pension be if I retired at the end
    of this year?" describes nobody. So the ordinary turn, which is nearly every
    turn, ran with no coverage at all.

    It mostly worked by luck: `_governing_article` falls back to the record, so
    recipes resolved. What it broke was everything that reads `coverage` --
    the answer's own account of which fund it is speaking for, and `memory`,
    which refuses to remember an answer it cannot place in an Article.
    """
    _script(monkeypatch, [_turn(content="Here.")])
    _tools(monkeypatch)

    out = await loop_mod.investigate(
        {
            "question": "Roughly what would my pension be if I retired at the end of this year?",
            "employee_record": {"article_number": 5},
            "turn_index": 0,
        }
    )
    assert out["coverage"]["article"] == "5"
    assert out["coverage"]["basis"] == "record"
    assert out["coverage"]["conflict"] is False


async def test_an_established_verdict_is_not_overwritten_by_the_record(monkeypatch):
    """A member who confirmed Article 12 does not revert to Article 11 on their
    next quiet turn."""
    _script(monkeypatch, [_turn(content="Here.")])
    _tools(monkeypatch)
    settled = {
        "article": "12",
        "record_article": "11",
        "basis": "member_confirmed",
        "conflict": False,
    }
    out = await loop_mod.investigate(
        {
            "question": "and what about at 60?",
            "employee_record": {"article_number": 11},
            "coverage": settled,
            "turn_index": 3,
        }
    )
    assert out.get("coverage", settled)["article"] == "12"


async def test_an_anonymous_member_gets_no_invented_article(monkeypatch):
    _script(monkeypatch, [_turn(content="Here.")])
    _tools(monkeypatch)
    out = await loop_mod.investigate({"question": "what is a refund?", "turn_index": 0})
    assert out["coverage"]["article"] is None
    assert out["coverage"]["basis"] == "unresolved"


def test_no_tool_can_be_called_instead_of_reading():
    """The lesson `find_provision` cost, kept as a guard.

    It was added to fix one case and withdrawn the same day: the full suite,
    run on the loop for the first time afterwards, found it had caused or
    contributed to eight failures including five CRITICAL ones. It handed the
    model a section number **with no text**, and the model cited it -- and it
    *satisfied*, so turns stopped at "which provision" without computing.

    Neither harm yielded to prose. The shape was the problem: a tool that can
    be called instead of reading will be. Every tool the loop is offered must
    either put statute text in front of it, compute through the engine, or
    settle coverage -- none of which can stand in for having read the law.
    """
    offered = {t["function"]["name"] for t in loop_mod.LOOP_TOOLS}
    assert offered == {
        "search_statute",
        "get_section",
        "outline",
        "related_sections",
        "compute_benefit",
        "check_coverage",
    }
    assert "find_provision" not in offered


async def test_a_turn_that_only_browsed_still_searches_before_answering(monkeypatch):
    """Five of ten full-suite failures were "did not cite 11-134", and the
    spend on each was `{outline: 1, get_section: N}` -- no search at all.

    The loop reads Article 11's outline, fetches the sections whose *headings*
    sound relevant, and answers. Section 11-134 is called "Amount of annuity";
    11-130 is "Annuities - Present employees and future entrants", which reads
    like the better match and is not. And there is no way out of that
    cul-de-sac: 11-127 through 11-131 cite **nothing**, so
    `_complete_the_evidence` has no edge to follow.

    `retrieve_policy` always runs a search. That is the one thing the pipeline
    does structurally that the loop can skip entirely -- so it stops being
    optional, exactly as completing the evidence did.
    """
    _script(monkeypatch, [_turn(("outline", {"article": "11"})), _turn(content="Found it.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-130"}]})

    await loop_mod.investigate(
        {"question": "which provision sets the amount of my annuity?", "turn_index": 0}
    )
    assert any(name == "search_statute" for name, _ in ran), [n for n, _ in ran]
    query = next(args for name, args in ran if name == "search_statute")["queries"]
    assert any("annuity" in q.lower() for q in query), query


async def test_a_turn_that_searched_is_left_alone(monkeypatch):
    """One search is enough to have looked. This adds the missing step, not a
    second opinion on a turn that took it."""
    _script(
        monkeypatch,
        [_turn(("search_statute", {"queries": ["annuity amount"], "why": "x"})),
         _turn(content="Found it.")],
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134"}]})
    out = await loop_mod.investigate({"question": "how much?", "turn_index": 0})
    # The subject is the backstop, which adds a search to a turn that took none.
    # A prefetched turn has already taken one, so the count moved; what must
    # still hold is that the backstop did not fire.
    assert not any(
        "answered without searching" in str(m.get("content") or "")
        for m in out["loop_messages"]
    )


async def test_an_exhausted_loop_does_not_search_again(monkeypatch):
    """Worth a call, not an overrun -- the same bound completion carries."""
    _script(monkeypatch, [_turn(("outline", {"article": "11"}))] * 30)
    ran = _tools(monkeypatch)
    # A self-description skips the prefetch, so this turn reaches exhaustion
    # with no search behind it -- which is the only state in which the backstop
    # has anything to add, and therefore the only one worth testing.
    out = await loop_mod.investigate(
        {"question": "i am a park employee, what happens?", "turn_index": 0}
    )
    assert out["loop_exhausted"] is True
    assert not any(name == "search_statute" for name, _ in ran)


# ── Leaving a supposition ──────────────────────────────────────────
#
# G41, and it is CRITICAL. The member supposed a park employee, explored it for
# a turn, and then said "Now go back to my actual record. What is my pension
# really?" -- and the loop answered the supposition again, resolving 12-133
# against an Article 11 member, with every record-derived input starved because
# the frame it never left says those facts belong to another fund.
#
# `assess_coverage` releases the frame on an edge, before retrieval, using
# `coverage.leaves_hypothetical`. The loop has no such edge: `check_coverage`
# creates a frame and nothing anywhere clears one. So the release joins the two
# deterministic steps `investigate` already runs before any model sees the turn
# -- binding an answered question, and defaulting coverage to the record -- for
# the same reason both of those are there: a member who has said which
# conversation they are in must not have to say it twice.

_SUPPOSING: dict = {
    "question": "",
    "turn_index": 1,
    "employee_record": {"article_number": 11},
    "coverage": {
        "article": "12",
        "record_article": "11",
        "basis": "hypothetical",
        "turn": 0,
    },
    "hypothetical_frame": {
        "premise": "a park employee",
        "article": "12",
        "entered_turn": 0,
        "last_used_turn": 0,
    },
}


def test_the_member_can_step_out_of_a_supposition():
    left = loop_mod._leave_hypothetical(
        {**_SUPPOSING, "question": "Now go back to my actual record. What is my pension really?"}
    )
    assert left["hypothetical_frame"] is None
    assert left["coverage"]["basis"] == "record"
    assert left["coverage"]["article"] == "11"
    assert left["candidate_articles"] == ["11"]


def test_asserting_who_they_are_also_leaves_the_supposition():
    """Supposing a job and then stating one is stepping out of the first.

    The same rule `assess_coverage` applies: an assertion is not a follow-up
    inside the premise, it is a new determination, and leaving the frame up
    would confine the new one's evidence to the old one's Article.
    """
    left = loop_mod._leave_hypothetical(
        {**_SUPPOSING, "question": "i am a laborer for the city"}
    )
    assert left["hypothetical_frame"] is None


def test_a_follow_up_inside_the_premise_does_not_leave_it():
    """"and what if I had 30 years of service?" carries none of the premise's
    words and is still a question about being a park employee. The frame holds
    by default and is left explicitly; guessing that a supposition has been
    "fully answered" strands the member mid-exploration."""
    assert loop_mod._leave_hypothetical(
        {**_SUPPOSING, "question": "and what if I had 30 years of service?"}
    ) == {}


def test_there_is_nothing_to_leave_without_a_premise():
    assert loop_mod._leave_hypothetical(
        {"question": "actually, what is my pension really?", "turn_index": 1}
    ) == {}


# ── The forced search reads the member's own law ───────────────────
#
# G30 and G01, both CRITICAL, and F02 which passed only because fund_smoke's
# key does not look. Every one of them spent `{outline: 1, get_section: 1}`, so
# the fallback search ran -- and it ran *unfiltered*, on the member's own
# question, which pulled a slice of the whole Code into the citable set. G30's
# answer then named Article 11 correctly on Section 11-101 and volunteered
# Section 7-109 to say it could not establish whether that provision confirmed
# anything; F02 cited Section 4-109 to an Article 2 member the same way. A gap
# reported with another fund's section number on it is still another fund's law
# in an answer about this member.
#
# `retrieve_policy` is article-filtered -- that filter *is* the determination --
# and the fallback exists precisely because it is the one structural thing the
# loop can skip. Skipping the filter with it was the half that got missed.


async def test_the_forced_search_is_filtered_to_the_governing_article(monkeypatch):
    _script(monkeypatch, [_turn(("outline", {"article": "11"})), _turn(content="Found it.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-130"}]})

    await loop_mod.investigate(
        {
            "question": "determine my governing Article and my current pension status",
            "turn_index": 0,
            "employee_record": {"article_number": 11},
        }
    )
    args = next(args for name, args in ran if name == "search_statute")
    assert args["articles"] == ["11", *loop_mod.CROSS_CUTTING_ARTICLES]


async def test_naming_an_article_moves_the_forced_search_to_it(monkeypatch):
    """L06. "What does Article 4 provide for police officers?" is a question
    about the law, not about this member, and filtering it to their own fund
    answers a question nobody asked. The pipeline resolves this the same way and
    for the same reason: naming a fund Article *moves* the filter."""
    _script(monkeypatch, [_turn(("outline", {"article": "4"})), _turn(content="Found it.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "4-109"}]})

    await loop_mod.investigate(
        {
            "question": "what does Article 4 provide for police officers?",
            "turn_index": 0,
            "employee_record": {"article_number": 11},
        }
    )
    args = next(args for name, args in ran if name == "search_statute")
    assert args["articles"] == ["4", *loop_mod.CROSS_CUTTING_ARTICLES]


async def test_a_premise_moves_the_forced_search_too(monkeypatch):
    """Under a supposition the question is about the supposed fund, and
    `_confined_to_premise` would drop everything else from the evidence
    afterwards anyway -- so searching the member's own Article spends a call to
    gather what is about to be set aside."""
    _script(monkeypatch, [_turn(("outline", {"article": "12"})), _turn(content="Found it.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "12-133"}]})

    await loop_mod.investigate(
        {
            "question": "and what if I had 30 years of service?",
            "turn_index": 1,
            "employee_record": {"article_number": 11},
            "hypothetical_frame": {"premise": "a park employee", "article": "12"},
        }
    )
    args = next(args for name, args in ran if name == "search_statute")
    assert args["articles"] == ["12", *loop_mod.CROSS_CUTTING_ARTICLES]


async def test_an_anonymous_member_with_no_article_searches_the_whole_code(monkeypatch):
    """Nothing has said which fund, so narrowing to one would be inventing the
    determination rather than applying it."""
    _script(monkeypatch, [_turn(("outline", {"article": "11"})), _turn(content="Found it.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-130"}]})

    await loop_mod.investigate({"question": "how are pensions calculated?", "turn_index": 0})
    args = next(args for name, args in ran if name == "search_statute")
    assert not args.get("articles")


async def test_a_fund_that_takes_its_benefits_from_another_searches_both(monkeypatch):
    """F10. Section 10-103 gives Forest Preserve District employees "the same
    annuities, refunds and benefits ... as are made and allowed for employees of
    the county" -- employees of the county being Article 9 -- so an Article 10
    figure worked under Section 9-134 is right, and `fund_smoke`'s rule is true
    of every fund but this one.

    Filtering the fallback to Article 10 alone would hide the law the figure
    comes from on any turn the model did not search for itself. Declared in
    `funds.py` with the section that establishes it and never inferred from a
    cross-reference: an Article whose text merely *mentions* another is not
    incorporating it, and guessing would re-admit the borrowed-formula failure
    the suite exists to catch.
    """
    _script(monkeypatch, [_turn(("outline", {"article": "10"})), _turn(content="Found it.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "10-103"}]})

    await loop_mod.investigate(
        {
            "question": "what would my pension be if I retired at the end of this year?",
            "turn_index": 0,
            "employee_record": {"article_number": 10},
        }
    )
    args = next(args for name, args in ran if name == "search_statute")
    assert args["articles"] == ["10", "9", *loop_mod.CROSS_CUTTING_ARTICLES]


async def test_the_settled_provision_is_read_before_the_answer(monkeypatch):
    """G30's thirteenth turn, and the pipeline fixed this exact turn by pinning.

    "Re-run the applicability analysis without changing my facts" names no
    section, no benefit and no age, so retrieval has nothing to lock onto. A
    long conversation drifts away from its own subject the moment a turn is
    phrased without statutory vocabulary -- `retrieve_policy`'s own comment
    names this turn and the six sections it came back with, Section 11-134 not
    among them.

    The loop is told a figure is standing and what section it rests on, which is
    not enough: a section it has not *read* this turn is one verification will
    not let it cite, so the answer talks around the provision the whole
    conversation is about. Reading it is the fix, and it is the same step
    `_complete_the_evidence` already performs for a cross-reference the
    gathered text leans on.
    """
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Done.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-130"}]})

    await loop_mod.investigate(
        {
            "question": "I think the pathway you picked was wrong. Re-run the analysis.",
            "turn_index": 12,
            "employee_record": {"article_number": 11},
            "selected_provision": {"formula_type": "retirement_annuity", "section": "11-134"},
        }
    )
    fetched = [args.get("section") for name, args in ran if name == "get_section"]
    assert "11-134" in fetched, fetched


async def test_a_settled_provision_already_read_is_not_fetched_twice(monkeypatch):
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Done.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134"}]})

    await loop_mod.investigate(
        {
            "question": "and at 60?",
            "turn_index": 3,
            "employee_record": {"article_number": 11},
            "selected_provision": {"formula_type": "retirement_annuity", "section": "11-134"},
        }
    )
    assert [args.get("section") for name, args in ran if name == "get_section"] == []


async def test_nothing_settled_means_nothing_pinned(monkeypatch):
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Done.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-130"}]})

    await loop_mod.investigate(
        {"question": "what is Article 11?", "turn_index": 0, "employee_record": {"article_number": 11}}
    )
    assert [args.get("section") for name, args in ran if name == "get_section"] == []


async def test_the_settled_provision_is_read_even_when_the_budget_is_gone(monkeypatch):
    """G30 turn 13 again, failing in the full suite after passing alone.

    The pin was gated on `not exhausted`, copied from `_complete_the_evidence`
    — and the two are different in kind. Completion makes up to three
    *speculative* fetches for cross-references that might matter, and skipping
    those when the budget is gone is right. This makes exactly one *known*
    fetch, for the provision the answer has to rest on, and skipping it is
    backwards: a turn that spent its whole budget is the one most likely to have
    drifted away from its own subject.
    """
    _script(monkeypatch, [_turn(*[("search_statute", {"queries": ["x"]})] * 20)])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-130"}]})

    await loop_mod.investigate(
        {
            "question": "I think the pathway you picked was wrong.",
            "turn_index": 12,
            "employee_record": {"article_number": 11},
            "selected_provision": {"formula_type": "retirement_annuity", "section": "11-134"},
        }
    )
    fetched = [args.get("section") for name, args in ran if name == "get_section"]
    assert "11-134" in fetched, fetched


async def test_the_completion_fetches_are_made_at_once(monkeypatch):
    """Three cross-references the evidence leans on, read in one wait, not three.

    Each is a known section id and none of them decides whether another is
    worth reading, so reading them one after another spent three round trips of
    the member's time to learn nothing between them. What has to stay ordered is
    which sections are chosen, not when their text arrives.
    """
    import asyncio

    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")])
    in_flight = 0
    peak = 0

    async def fake_run(name, arguments, state):
        nonlocal in_flight, peak
        if name == "get_section":
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return "text", {
                "retrieved_sections": [{"section": arguments["section"], "text": "t"}]
            }
        return "found", {
            "retrieved_sections": [
                {
                    "section": "11-134",
                    "text": "t",
                    "referenced_sections": ["11-216", "11-169", "11-161"],
                }
            ]
        }

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert peak > 1, "the completion fetches were made one at a time"
    assert {s["section"] for s in out["retrieved_sections"]} == {
        "11-134",
        "11-216",
        "11-169",
        "11-161",
    }


async def test_a_completion_fetch_that_fails_does_not_lose_the_others(monkeypatch):
    """One unreachable section must not take the turn with it."""
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")])

    async def fake_run(name, arguments, state):
        if name == "get_section":
            if arguments["section"] == "11-169":
                raise RuntimeError("search is unreachable")
            return "text", {
                "retrieved_sections": [{"section": arguments["section"], "text": "t"}]
            }
        return "found", {
            "retrieved_sections": [
                {
                    "section": "11-134",
                    "text": "t",
                    "referenced_sections": ["11-216", "11-169"],
                }
            ]
        }

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert "11-216" in {s["section"] for s in out["retrieved_sections"]}


async def test_the_engine_provision_lookup_does_not_wait_for_the_completion_fetches(
    monkeypatch,
):
    """The registry knows which provision governs without reading any section.

    `_engine_provisions` asks the registry, which needs nothing the completion
    step gathers -- so the two waits are one wait.

    The budget carries a third wait that is serial by design: the prefetch
    closes the evidence before the first model call, and the model cannot
    decide without it. 0.2s of what is measured here is that.
    """
    import asyncio
    import time

    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")])

    async def slow_provisions(state):
        await asyncio.sleep(0.2)
        return []

    async def fake_run(name, arguments, state):
        if name == "get_section":
            await asyncio.sleep(0.2)
            return "text", {
                "retrieved_sections": [{"section": arguments["section"], "text": "t"}]
            }
        return "found", {
            "retrieved_sections": [
                {"section": "11-134", "text": "t", "referenced_sections": ["11-216"]}
            ]
        }

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    monkeypatch.setattr(loop_mod, "_engine_provisions", slow_provisions)

    started = time.perf_counter()
    await loop_mod.investigate({"question": "q", "messages": []})
    elapsed = time.perf_counter() - started

    # 0.2s prefetch closure, then provisions and completion overlapping in
    # 0.2s rather than queueing for 0.4s.
    assert elapsed < 0.55, f"the registry lookup waited for the fetches ({elapsed:.2f}s)"
