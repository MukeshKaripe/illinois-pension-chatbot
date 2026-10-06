"""Who asked: the host app's user id, kept in each message's audit trail.

v1 has no sign-in of its own; the client's Blazor app knows its user and sends
`X-User-Id`. It is recorded, never trusted for access and never shown to the
model -- an audit field, not an identity.
"""

from __future__ import annotations

from pension_chat.api.sessions import asked_by


def test_the_host_apps_user_is_recorded():
    assert asked_by({"x-user-id": "jdoe@meabf.org"}) == {"type": "asked_by", "user_id": "jdoe@meabf.org"}


def test_no_header_records_nothing():
    assert asked_by({}) is None
    assert asked_by({"x-user-id": "   "}) is None


def test_a_value_that_cannot_be_a_user_id_is_not_stored():
    assert asked_by({"x-user-id": "a" * 300}) is None              # far longer than any login name
    assert asked_by({"x-user-id": "jdoe\nINJECTED"}) is None       # control characters
    assert asked_by({"x-user-id": "  jdoe  "}) == {"type": "asked_by", "user_id": "jdoe"}
