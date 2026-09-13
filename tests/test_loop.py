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

    assert [name for name, _ in ran] == ["search_statute"]
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

    assert ran == [("search_statute", {})]
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
    on a fight. It is let through, and `compose` says what it did not read."""
    _script(monkeypatch, [_turn(content="I am sure."), _turn(content="Still sure.")])
    ran = _tools(monkeypatch)  # no update, so nothing is ever read

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert ran == []
    assert out["consulted_nothing"] is True


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
    worth a few calls, not an overrun."""
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

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert out["loop_exhausted"] is True
    assert out["unread_references"] == ["11-216"], "reported instead, so compose can say so"


async def test_a_tool_sees_what_earlier_tools_found(monkeypatch):
    """compute_benefit derives its recipe from the statute the loop gathered.

    The loop kept its sections in a local and handed `run_tool` the turn's
    original state, so a calculation ran against an empty corpus and reported
    'no calculation ran' -- with the sections sitting right there in the loop.
    """
    _script(
        monkeypatch,
        [
            _turn(("search_statute", {"queries": ["annuity"]})),
            _turn(("compute_benefit", {"benefit": "retirement_annuity"})),
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
