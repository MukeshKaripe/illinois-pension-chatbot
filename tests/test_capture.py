"""Every hard verification failure is a labelled example of the thing this
system exists to prevent, and today it is logged at WARNING and dropped.

Both defects found in Phase 3c needed evidence dumped by hand that a capture
file would have held: the loop citing a section it never read, and a 400 whose
offending message was several calls upstream of where it surfaced.

Offline: the filesystem is a tmp_path and nothing here touches the model.
"""

from __future__ import annotations

import yaml

from pension_chat.agent import capture
from pension_chat.agent.guardrails import GuardrailReport


def _failed() -> GuardrailReport:
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "Section 11-124 was cited but never retrieved.")
    report.unverified_citations = ["11-124"]
    return report


def _state(**over):
    base = {
        "session_id": "s-1",
        "question": "what happens to my wife?",
        "final_answer": "Section 11-124 provides a widow's annuity.",
        "retrieved_sections": [{"section": "11-134"}],
        "messages": [],
    }
    base.update(over)
    return base


def test_a_hard_failure_writes_the_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "_capture_dir", lambda: tmp_path)
    path = capture.record_failure(_state(), _failed())
    assert path is not None and path.exists()

    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["session_id"] == "s-1"
    assert written["turns"][-1]["q"] == "what happens to my wife?"
    assert "11-124" in written["rejected_answer"]


def test_a_passing_answer_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "_capture_dir", lambda: tmp_path)
    assert capture.record_failure(_state(), GuardrailReport()) is None
    assert list(tmp_path.iterdir()) == []


def test_the_capture_names_which_check_failed(tmp_path, monkeypatch):
    """A bare "the answer was rejected" is not a labelled example of anything."""
    monkeypatch.setattr(capture, "_capture_dir", lambda: tmp_path)
    written = yaml.safe_load(
        capture.record_failure(_state(), _failed()).read_text(encoding="utf-8")
    )
    assert [f["code"] for f in written["failed"]] == ["UNVERIFIED_CITATION"]
    assert written["offenders"] == ["11-124"]
    assert written["sections_read"] == ["11-134"], "what it had, beside what it cited"


def test_a_capture_that_throws_does_not_take_the_turn(tmp_path, monkeypatch):
    """The failure has already happened and the fallback is already going out.
    An exception here would replace a degraded answer with none at all."""

    def boom():
        raise OSError("read-only file system")

    monkeypatch.setattr(capture, "_capture_dir", boom)
    assert capture.record_failure(_state(), _failed()) is None


def test_no_member_name_reaches_the_file(tmp_path, monkeypatch):
    """The pilot's members are synthetic. The habit is not."""
    monkeypatch.setattr(capture, "_capture_dir", lambda: tmp_path)
    path = capture.record_failure(
        _state(
            employee_record={
                "first_name": "Marguerite",
                "last_name": "Okonkwo",
                "email": "m.okonkwo@example.org",
                "article_number": 11,
            }
        ),
        _failed(),
    )
    raw = path.read_text(encoding="utf-8")
    assert "Marguerite" not in raw
    assert "Okonkwo" not in raw
    assert "example.org" not in raw


def test_the_conversation_so_far_is_carried(tmp_path, monkeypatch):
    """A rejected answer on turn three is not reproducible from turn three."""
    monkeypatch.setattr(capture, "_capture_dir", lambda: tmp_path)

    class _M:
        def __init__(self, content, type_):
            self.content, self.type = content, type_

    written = yaml.safe_load(
        capture.record_failure(
            _state(messages=[_M("how much will I get?", "human"), _M("About $32,126.78.", "ai")]),
            _failed(),
        ).read_text(encoding="utf-8")
    )
    assert [t["q"] for t in written["turns"] if "q" in t] == [
        "how much will I get?",
        "what happens to my wife?",
    ]
    assert [t["a"] for t in written["turns"] if "a" in t] == ["About $32,126.78."], (
        "the answer that led here, too -- most of why a turn failed is what "
        "came before it"
    )


def test_capture_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "_capture_dir", lambda: tmp_path)
    monkeypatch.setattr(capture, "_enabled", lambda: False)
    assert capture.record_failure(_state(), _failed()) is None
