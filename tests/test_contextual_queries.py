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
