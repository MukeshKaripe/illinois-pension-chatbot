"""Which employees a message names -- matched in memory; PBMS is asked only on a hit. Synthetic names."""
from __future__ import annotations

import asyncio

import pytest

from pension_chat.pbms.names import NameDirectory
from pension_chat.pbms.provider import PbmsProvider
from pension_chat.records import NameSearch, RecordUnavailable

D = NameDirectory([("KA_LS", "MA_LP"), ("Qw_Xz", "PL_MR"), ("A _BC", "DE_FG"), ("O'_BR", "NO_LA"),
                   ("A._BC", "XY_ZW"), (" ", "ZZ_ZZ")])


@pytest.mark.parametrize("text, want", [
    ("What would KA_LS MA_LP's pension be at 62?", [("KA_LS", "MA_LP")]),
    ("pension for ka_ls ma_lp", [("KA_LS", "MA_LP")]),
    ("MA_LP, KA_LS -- when can they retire?", [("KA_LS", "MA_LP")]),
    ("is it KA_LS MA_LP?", [("KA_LS", "MA_LP")]),
    ("compare qw_xz pl_mr and KA_LS MA_LP", [("Qw_Xz", "PL_MR"), ("KA_LS", "MA_LP")]),
    ("A _BC DE_FG retiring", [("A _BC", "DE_FG")]),
    ("what about O'_BR NO_LA", [("O'_BR", "NO_LA")]),
    ("A._BC XY_ZW.", [("A._BC", "XY_ZW")]),
    ("XKA_LS MA_LP", []),
    ("KA_LS alone", []),
    ("What is the Tier 2 formula?", []),
    ("", []),
])
def test_names_in_a_message(text, want) -> None:
    assert D.names_in(text) == want


def test_blank_names_are_not_in_the_directory() -> None:
    assert len(D) == 5


class FakeClient:
    def __init__(self):
        self.calls, self.fail = [], False

    async def fetch(self, name, **params):
        self.calls.append((name, params))
        if self.fail:
            raise RecordUnavailable("down")
        if name == "name_directory":
            return [{"First_Name": "KA_LS", "Last_Name": "MA_LP"}]
        if name == "resolve_name":
            return [{"MEAB_Id": f"M-00000{i}", "First_Name": "KA_LS", "Last_Name": "MA_LP", "Birth_Year": 1960 + i,
                     "Employee_Status_Cd": "A", "ER_Type_Cd": "CITY", "Er_Type_Short_Desc": "City",
                     "Department_Short_Desc": "Finance"} for i in range(3)]
        return []

    async def warm(self, connections=4):
        pass

    async def close(self):
        pass


async def test_the_provider_matches_in_memory_and_asks_pbms_only_on_a_hit() -> None:
    client = FakeClient()
    provider = PbmsProvider(client)
    assert isinstance(provider, NameSearch)
    assert provider.names_in("KA_LS MA_LP") == [] and not provider.names_ready
    assert await provider.load_names() == 1
    assert provider.names_in("is KA_LS MA_LP eligible?") == [("KA_LS", "MA_LP")]
    assert [c[0] for c in client.calls] == ["name_directory"]
    matches = await provider.find_by_name("KA_LS", "MA_LP")
    assert [m.birth_year for m in matches] == [1960, 1961, 1962]
    assert client.calls[-1] == ("resolve_name", {"first_name": "KA_LS", "last_name": "MA_LP"})


async def test_a_failed_reload_keeps_the_directory() -> None:
    client = FakeClient()
    provider = PbmsProvider(client)
    await provider.load_names()
    client.fail = True
    with pytest.raises(RecordUnavailable):
        await provider.load_names()
    assert provider.names_in("KA_LS MA_LP") == [("KA_LS", "MA_LP")]


async def test_refresh_retries_soon_after_a_failed_first_load() -> None:
    from pension_chat.pbms import refresh_names_forever

    client = FakeClient()
    client.fail = True
    provider = PbmsProvider(client)
    task = asyncio.create_task(refresh_names_forever(provider, every_s=3600, retry_s=0.01))
    await asyncio.sleep(0.05)
    client.fail = False
    await asyncio.sleep(0.05)
    task.cancel()
    assert provider.names_ready


def test_a_comma_list_is_not_read_as_last_first() -> None:
    # Found live: "A_A B_B, C_C D_D and ..." -- "B_B, C_C" read as LAST, FIRST
    # matched a real person nobody named.
    d = NameDirectory([("VA_FN", "GI_LN"), ("CO_FN", "TW_LN"), ("CO_FN", "GI_LN"), ("RO_FN", "OS_LN")])
    assert d.names_in("compare all three: Va_FN Gi_LN, CO_FN TW_LN and RO_FN OS_LN.") == [
        ("VA_FN", "GI_LN"), ("CO_FN", "TW_LN"), ("RO_FN", "OS_LN")]
    assert d.names_in("GI_LN, CO_FN please") == [("CO_FN", "GI_LN")]        # a real LAST, FIRST still works
