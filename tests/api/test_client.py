"""Tests for the transport, the operations and the high-level client."""

from __future__ import annotations

import logging

import aiohttp
import pytest

from custom_components.ps5.api.errors import (
    ApiError,
    AuthExpired,
    ConsoleNotFound,
    PersistedQueryNotFound,
    RateLimited,
    TransientError,
    WriteRejected,
)
from custom_components.ps5.api.models import title_id_from_entitlement
from tests.api.helpers import build_stack
from tests.common import (
    CONSOLE_DUID,
    EID_DISPATCH,
    EID_ETHAN,
    EID_JAZZPUNK,
    FakeResponse,
    FakeSony,
    load_fixture,
    progress_response,
    token_response,
)


async def test_console_filter(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    consoles = await stack.client.get_consoles()
    assert [c.name for c in consoles] == ["PS5"]  # the PS4 ("myself") is filtered out
    console = consoles[0]
    assert console.duid == CONSOLE_DUID
    assert len(console.installed) == 5
    assert console.installed_bytes == 14991622144 + 471269376 + 58530660352 + 189267968 + 621674496
    assert "CUSA01369_00" in console.installed_title_ids
    assert "U2FsdGVkX1" not in repr(console)


async def test_console_not_found(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony, console_name="Living room")
    with pytest.raises(ConsoleNotFound):
        await stack.client.get_console()


async def test_console_duid_reused_for_9_minutes(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    await stack.client.get_queue()
    assert fake_sony.ops() == ["refresh", "getUserDevices", "GetRemoteDownloadList"]
    stack.mono.advance(9 * 60 - 1)
    await stack.client.get_progress(EID_JAZZPUNK)
    assert fake_sony.ops()[-1] == "downloadProgress"
    assert fake_sony.calls("getUserDevices").__len__() == 1
    stack.mono.advance(1)  # exactly 9 minutes old: refetch
    await stack.client.get_queue()
    assert len(fake_sony.calls("getUserDevices")) == 2
    assert fake_sony.calls("GetRemoteDownloadList")[-1].variables["duid"] == CONSOLE_DUID


async def test_pagination_stops_on_is_last(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    titles = await stack.client.get_library()
    assert len(titles) == 218
    starts = [r.variables["start"] for r in fake_sony.calls("getPurchasedGameList")]
    assert starts == [0, 24, 48, 72, 96, 120, 144, 168, 192, 216]
    assert all(r.variables["size"] == 24 for r in fake_sony.calls("getPurchasedGameList"))
    assert titles[0].entitlement_id == EID_DISPATCH
    assert titles[1].platform == "PS4"


async def test_pagination_cap(fake_sony: FakeSony, caplog: pytest.LogCaptureFixture) -> None:
    stack = build_stack(fake_sony)
    page = load_fixture("purchased_game_list_page0.json")
    fake_sony.set("getPurchasedGameList", page)  # never isLast
    titles = await stack.client.get_library()
    assert len(fake_sony.calls("getPurchasedGameList")) == 50
    assert len(titles) == 150
    assert "stopped after 50 pages" in caplog.text


@pytest.mark.parametrize("op", ["read", "write"])
async def test_401_refresh_and_single_retry(fake_sony: FakeSony, op: str) -> None:
    stack = build_stack(fake_sony)
    await stack.client.ensure_console_duid()
    key = "GetRemoteDownloadList" if op == "read" else "initiateDownload"
    fake_sony.queue(key, FakeResponse(401))
    fake_sony.queue("refresh", token_response(jti="second"))
    fake_sony.reset()
    if op == "read":
        await stack.client.get_queue()
    else:
        await stack.client.start_download(EID_DISPATCH)
    assert fake_sony.ops() == [key, "refresh", key]
    first, second = fake_sony.calls(key)
    assert first.headers["Authorization"] != second.headers["Authorization"]


@pytest.mark.parametrize("op", ["read", "write"])
async def test_second_401_triggers_reauth_without_retry(fake_sony: FakeSony, op: str) -> None:
    stack = build_stack(fake_sony)
    await stack.client.ensure_console_duid()
    key = "GetRemoteDownloadList" if op == "read" else "remoteDownloadCancel"
    fake_sony.queue(key, FakeResponse(401), FakeResponse(401))
    fake_sony.reset()
    with pytest.raises(AuthExpired):
        if op == "read":
            await stack.client.get_queue()
        else:
            await stack.client.cancel_download(EID_ETHAN)
    assert fake_sony.ops() == [key, "refresh", key, "authorize", "token"]


@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), aiohttp.ClientConnectionError(), FakeResponse(500), FakeResponse(503)],
)
@pytest.mark.parametrize("key", ["initiateDownload", "remoteDownloadCancel"])
async def test_no_write_retry(fake_sony: FakeSony, failure, key: str) -> None:
    stack = build_stack(fake_sony)
    await stack.client.ensure_console_duid()
    fake_sony.queue(key, failure)
    fake_sony.reset()
    with pytest.raises(TransientError):
        if key == "initiateDownload":
            await stack.client.start_download(EID_DISPATCH)
        else:
            await stack.client.cancel_download(EID_ETHAN)
    assert fake_sony.ops() == [key]


async def test_write_rejected(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    fake_sony.set(
        "initiateDownload",
        {
            "data": {
                "remoteDownloadSchedule": [
                    {"entitlementId": EID_DISPATCH, "errorCode": "4097", "reasonCode": "2"}
                ]
            }
        },
    )
    with pytest.raises(WriteRejected) as err:
        await stack.client.start_download(EID_DISPATCH)
    assert (err.value.error_code, err.value.reason_code) == ("4097", "2")
    assert len(fake_sony.calls("initiateDownload")) == 1
    assert stack.client.last_errors["initiateDownload"] == "WriteRejected"


async def test_write_post_has_exact_body(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    await stack.client.cancel_download(EID_ETHAN)
    (req,) = fake_sony.calls("remoteDownloadCancel")
    assert req.method == "POST"
    assert req.variables == {
        "updateRequests": {
            "downloadUpdates": [
                {
                    "duid": CONSOLE_DUID,
                    "entitlementId": EID_ETHAN,
                    "platform": "PS5",
                    "reasonCode": "1",
                    "status": "USERCANCELLED",
                }
            ]
        }
    }


async def test_persisted_query_not_found_disables_operation(
    fake_sony: FakeSony, caplog: pytest.LogCaptureFixture
) -> None:
    stack = build_stack(fake_sony)
    fake_sony.queue(
        "getUserDevices",
        {"errors": [{"message": "PersistedQueryNotFound", "extensions": {"code": "X"}}]},
    )
    with pytest.raises(PersistedQueryNotFound) as err:
        await stack.client.get_consoles()
    assert err.value.operation == "getUserDevices"
    with pytest.raises(PersistedQueryNotFound):
        await stack.client.get_consoles()
    assert len(fake_sony.calls("getUserDevices")) == 1  # disabled until restart
    assert "getUserDevices" in stack.client.disabled_operations


async def test_pqnf_by_extension_code(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    fake_sony.queue(
        "GetRemoteDownloadList",
        FakeResponse(
            400,
            {"errors": [{"message": "nope", "extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"}}]},
        ),
    )
    with pytest.raises(PersistedQueryNotFound):
        await stack.client.get_queue()


async def test_graphql_errors_are_redacted_and_logged_once(
    fake_sony: FakeSony, caplog: pytest.LogCaptureFixture
) -> None:
    stack = build_stack(fake_sony)
    await stack.client.ensure_console_duid()
    error = {"errors": [{"message": f"bad duid {CONSOLE_DUID}", "path": ["x"]}]}
    fake_sony.queue("GetRemoteDownloadList", error, error)
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            with pytest.raises(ApiError) as err:
                await stack.client.get_queue()
    assert CONSOLE_DUID not in str(err.value.errors)
    assert CONSOLE_DUID not in caplog.text
    assert caplog.text.count("First occurrence") == 1


async def test_rate_limited_pauses_everything(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    await stack.client.ensure_console_duid()
    fake_sony.queue("GetRemoteDownloadList", FakeResponse(429, headers={"Retry-After": "120"}))
    with pytest.raises(RateLimited) as err:
        await stack.client.get_queue()
    assert err.value.retry_after == 120
    fake_sony.reset()
    with pytest.raises(RateLimited):
        await stack.client.get_progress(EID_JAZZPUNK)
    assert fake_sony.requests == []


async def test_rate_limited_default_retry_after(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    await stack.client.ensure_console_duid()
    fake_sony.queue("GetRemoteDownloadList", FakeResponse(429))
    with pytest.raises(RateLimited) as err:
        await stack.client.get_queue()
    assert err.value.retry_after == 900


async def test_progress_variants(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    fake_sony.queue(
        "downloadProgress",
        load_fixture("download_progress_transferring.json"),
        load_fixture("download_progress_retrieving.json"),
        load_fixture("download_progress_playable.json"),
        progress_response(status="SOMETHINGNEW"),
    )
    transferring = await stack.client.get_progress(EID_JAZZPUNK)
    assert transferring.is_transferring
    assert transferring.percent == 2.2
    assert transferring.remaining_s == 168
    assert not transferring.is_complete_by_size
    retrieving = await stack.client.get_progress(EID_JAZZPUNK)
    assert retrieving.normalized_status == "retrieving"
    assert retrieving.percent is None
    assert retrieving.downloaded_bytes is None
    assert not retrieving.is_complete_by_size
    playable = await stack.client.get_progress(EID_JAZZPUNK)
    assert playable.is_complete_by_size
    assert playable.percent == 100.0
    unknown = await stack.client.get_progress(EID_JAZZPUNK)
    assert unknown.status == "SOMETHINGNEW"


async def test_queue_parsing(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    fake_sony.set("GetRemoteDownloadList", load_fixture("remote_download_list.json"))
    items = await stack.client.get_queue()
    assert [i.entitlement_id for i in items] == [EID_ETHAN, EID_JAZZPUNK]
    assert items[0].status == "NOTSTARTED"
    assert items[1].title == "Jazzpunk: Director's Cut"


def test_title_id_fallback() -> None:
    assert title_id_from_entitlement(EID_JAZZPUNK) == "CUSA05228_00"
    assert title_id_from_entitlement("garbage") is None


async def test_unknown_progress_status_logged_once(
    fake_sony: FakeSony, caplog: pytest.LogCaptureFixture
) -> None:
    stack = build_stack(fake_sony)
    fake_sony.set("downloadProgress", progress_response(status="Paused"))
    with caplog.at_level(logging.DEBUG, logger="custom_components.ps5.api.client"):
        first = await stack.client.get_progress(EID_JAZZPUNK)
        await stack.client.get_progress(EID_JAZZPUNK)
    assert first.status == "Paused"
    assert not first.is_transferring
    assert caplog.text.count("Unknown download progress status: Paused") == 1
