"""PbmsClient runs registry statements only, safely, on a small pool."""
from __future__ import annotations

import pytest

from pension_chat.pbms.connection import PbmsClient, PbmsUnavailable, UnknownQuery, connection_string
from pension_chat.records import RecordUnavailable

SECRET = "pa}ss-w0rd-XYZ"


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description = conn, None

    def execute(self, sql, args):
        self.conn.log.append((sql, args, self.conn.timeout))    # the timeout in force for this statement
        if self.conn.fail:
            raise self.conn.fail
        self.description = [("MEAB_Id",), ("First_Name",)]

    def fetchall(self):
        return [("M-000001", "AB_CD")]

    def close(self):
        pass


class FakeConn:
    def __init__(self, fail=None):
        self.log, self.fail, self.timeout, self.closed, self.setup = [], fail, None, False, []

    def cursor(self):
        return FakeCursor(self)

    def execute(self, sql):
        self.setup.append(sql)

    def close(self):
        self.closed = True


def _client(*conns):
    made, opened = list(conns), []

    def connect(conn_str, timeout):
        conn = made.pop(0)
        opened.append(conn)
        return conn

    return PbmsClient(connection_string(server="s", database="d", user="u", password=SECRET),
                      connect=connect), opened


async def test_a_registry_statement_returns_rows_as_dicts() -> None:
    client, opened = _client(FakeConn())
    rows = await client.fetch("resolve_meab_id", meab_id="M-000001")
    assert rows == [{"MEAB_Id": "M-000001", "First_Name": "AB_CD"}]
    assert opened[0].setup == ["SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"]
    assert opened[0].log[0][2] == 5


async def test_a_statement_with_its_own_timeout_uses_it_then_restores_the_default() -> None:
    client, opened = _client(FakeConn())
    await client.fetch("name_directory")
    assert opened[0].log[0][2] == 30
    assert opened[0].timeout == 5


async def test_an_unknown_name_never_reaches_the_driver() -> None:
    client, opened = _client(FakeConn())
    with pytest.raises(UnknownQuery):
        await client.fetch("SELECT * FROM dbo.T_Person_Info")
    assert opened == []


async def test_a_missing_parameter_is_refused() -> None:
    client, _ = _client(FakeConn())
    with pytest.raises(TypeError):
        await client.fetch("resolve_name", first_name="AB_CD")


async def test_a_driver_failure_is_unavailable_and_carries_no_secret() -> None:
    boom = Exception("08001", f"login failed for DRIVER=...;PWD={SECRET}")
    client, _ = _client(FakeConn(fail=boom))
    with pytest.raises(PbmsUnavailable) as caught:
        await client.fetch("record", meab_id="M-000001")
    assert isinstance(caught.value, RecordUnavailable)
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert SECRET not in repr(client)


async def test_a_dropped_pooled_connection_is_retried_once_on_a_fresh_one() -> None:
    client, opened = _client(FakeConn(), FakeConn())
    await client.fetch("record", meab_id="M-000001")          # opens #1 and returns it to the pool
    opened[0].fail = Exception("08S01", "communication link failure")
    rows = await client.fetch("record", meab_id="M-000001")   # #1 fails, #2 answers
    assert rows and opened[0].closed and len(opened) == 2


def test_the_connection_string_is_read_only_and_named() -> None:
    cs = connection_string(server="tcp:h,2022", database="PbsMeabDev", user="sa", password=SECRET)
    assert "ApplicationIntent=ReadOnly" in cs and "APP=pension-chat-readonly" in cs
    assert "PWD={pa}}ss-w0rd-XYZ}" in cs
