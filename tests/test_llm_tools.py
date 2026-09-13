"""One model call that may ask for tools, parsed into something the loop can use.

Everything here is offline: the Azure client is replaced, because what is being
tested is the parsing, not the network. The one thing a live probe established
-- that this deployment returns `finish_reason: tool_calls` with well-formed
arguments -- is not something a unit test can or should assert.
"""

from __future__ import annotations

from pension_chat.agent import llm


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Message:
    def __init__(self, content=None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message, finish_reason) -> None:
        self.message = message
        self.finish_reason = finish_reason


class _Response:
    def __init__(self, message, finish_reason="stop") -> None:
        self.choices = [_Choice(message, finish_reason)]
        self.usage = None


def _client(response, seen: dict | None = None):
    class _Completions:
        async def create(self, **kwargs):
            if seen is not None:
                seen.update(kwargs)
            return response

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

        def with_options(self, **kwargs):
            return self

    return _Client()


async def test_a_tool_call_is_parsed_with_its_arguments(monkeypatch):
    response = _Response(
        _Message(
            tool_calls=[
                _Call("c1", "search_statute", '{"queries": ["early retirement"], "why": "x"}')
            ]
        ),
        finish_reason="tool_calls",
    )
    monkeypatch.setattr(llm, "get_llm", lambda: _client(response))

    turn = await llm.call_with_tools(node="t", messages=[], tools=[])

    assert len(turn.calls) == 1
    assert turn.calls[0].name == "search_statute"
    assert turn.calls[0].arguments == {"queries": ["early retirement"], "why": "x"}
    assert turn.calls[0].id == "c1"
    assert not turn.done


async def test_no_tool_calls_means_the_model_is_done(monkeypatch):
    monkeypatch.setattr(
        llm, "get_llm", lambda: _client(_Response(_Message(content="Here is the answer.")))
    )

    turn = await llm.call_with_tools(node="t", messages=[], tools=[])

    assert turn.calls == ()
    assert turn.content == "Here is the answer."
    assert turn.done


async def test_malformed_arguments_do_not_kill_the_turn(monkeypatch):
    """A model that sends broken JSON has still told you which tool it wants.

    Raising here would end the conversation over a recoverable mistake; the
    loop can run the tool with nothing and let it report what it needed.
    """
    response = _Response(
        _Message(tool_calls=[_Call("c1", "search_statute", "{not json")]),
        finish_reason="tool_calls",
    )
    monkeypatch.setattr(llm, "get_llm", lambda: _client(response))

    turn = await llm.call_with_tools(node="t", messages=[], tools=[])

    assert turn.calls[0].name == "search_statute"
    assert turn.calls[0].arguments == {}


async def test_the_assistant_message_comes_back_appendable(monkeypatch):
    """The API rejects a `role: "tool"` result whose call is not in the history."""
    response = _Response(
        _Message(tool_calls=[_Call("c1", "outline", '{"article": 11}')]),
        finish_reason="tool_calls",
    )
    monkeypatch.setattr(llm, "get_llm", lambda: _client(response))

    turn = await llm.call_with_tools(node="t", messages=[], tools=[])

    assert turn.raw_message["role"] == "assistant"
    assert turn.raw_message["tool_calls"][0]["id"] == "c1"
    assert turn.raw_message["tool_calls"][0]["function"]["name"] == "outline"


async def test_tools_are_only_offered_when_there_are_some(monkeypatch):
    """`tool_choice` with an empty tool list is a 400, not a no-op."""
    seen: dict = {}
    monkeypatch.setattr(
        llm, "get_llm", lambda: _client(_Response(_Message(content="done")), seen)
    )

    await llm.call_with_tools(node="t", messages=[], tools=[])
    assert "tools" not in seen and "tool_choice" not in seen

    seen.clear()
    await llm.call_with_tools(
        node="t",
        messages=[],
        tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
    )
    assert seen["tool_choice"] == "auto"
