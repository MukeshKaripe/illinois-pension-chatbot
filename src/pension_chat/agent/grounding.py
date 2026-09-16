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
_OPEN_CITATION = re.compile(
    r"40\s*ILCS\s*5?/?\s*\d{0,2}-?\d{0,3}\.?\d{0,2}$", re.IGNORECASE
)


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
        return "".join(released), None

    def _offender(self, sentence: str) -> str | None:
        for section in _CITATION.findall(sentence):
            if str(section) not in self._available:
                return str(section)
        return None
