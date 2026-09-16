"""The ladder: stated -> record -> derived -> statutory_parameter -> assumed.

Ranks 3 and 4 are where the agent used to give up. A retirement date worked out
from an age the member gave is not an assumption, and an Article's own normal
retirement age is citable rather than guessed -- so neither belongs at the
bottom of the ladder with the values nobody supplied.
"""

from __future__ import annotations

from pension_chat.agent import ledger
from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_DERIVED,
    SOURCE_RECORD,
    SOURCE_STATED,
    SOURCE_STATUTORY,
    USABLE_SOURCES,
)


def test_the_ladder_runs_from_the_member_to_the_code_to_the_gap():
    assert ledger.PRECEDENCE == (
        SOURCE_STATED,
        SOURCE_RECORD,
        SOURCE_DERIVED,
        SOURCE_STATUTORY,
        SOURCE_ASSUMED,
    )


def test_the_code_outranks_a_guess():
    """A cited age from the Article beats one we filled in, and loses to one
    worked out from what the member actually told us."""
    assert ledger.outranks(SOURCE_STATUTORY, SOURCE_ASSUMED)
    assert not ledger.outranks(SOURCE_STATUTORY, SOURCE_DERIVED)


def test_a_figure_may_rest_on_a_statutory_parameter():
    assert SOURCE_STATUTORY in USABLE_SOURCES


def test_an_unknown_source_still_sorts_last():
    assert ledger.rank("something-new") > ledger.rank(SOURCE_ASSUMED)
