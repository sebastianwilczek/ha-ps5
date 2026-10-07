"""Tests for polling, download events, failures and repair issues."""

from __future__ import annotations

import copy
from datetime import timedelta
import time
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ps5.const import DOMAIN
from tests.common import (
    EID_DISPATCH,
    EID_ETHAN,
    EID_JAZZPUNK,
    FakeResponse,
    FakeSony,
    load_fixture,
    progress_response,
    queue_item,
    queue_response,
)
from tests.conftest import entry_data

ETHAN = queue_item(EID_ETHAN, "The Vanishing of Ethan Carter")
JAZZPUNK = queue_item(EID_JAZZPUNK, "Jazzpunk: Director's Cut")
DISPATCH = queue_item(EID_DISPATCH, "Dispatch")
EID_GEN5 = "EP0000-PPSA90005_00-GEN0000000000005"  # in the generated library, not installed
GEN5 = queue_item(EID_GEN5, "Game 005")


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def devices_with(*title_ids: str, name: str = "PS5") -> dict[str, Any]:
    data = load_fixture("get_user_devices.json")
    console = data["data"]["deviceStorageDetailsRetrieve"][0]
    console["deviceName"] = name
    games = console["deviceStorageDetails"]["installedGames"]
    for title_id in title_ids:
        game = copy.deepcopy(games[0])
        game["titleId"] = title_id
        game["name"] = f"Installed {title_id}"
        games.append(game)
    return data


@pytest.fixture
def events(init_integration: MockConfigEntry) -> list[tuple[str, dict[str, Any]]]:
    captured: list[tuple[str, dict[str, Any]]] = []
    init_integration.runtime_data.status.async_add_event_listener(
        lambda event_type, data: captured.append((event_type, data))
    )
    return captured


async def test_idle_and_active_polling(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
    events: list,
) -> None:
    status = init_integration.runtime_data.status
    assert status.update_interval == timedelta(minutes=10)
    mock_sony.reset()

    # Nothing happens before 10 minutes in idle mode (HA aligns timers to the second).
    await tick(hass, freezer, 590)
    assert mock_sony.ops() == []

    mock_sony.set("GetRemoteDownloadList", queue_response(ETHAN, JAZZPUNK))
    await tick(hass, freezer, 10)
    assert mock_sony.ops() == [
        "getUserDevices",
        "GetRemoteDownloadList",
        "downloadProgress",
        "downloadProgress",
    ]
    assert status.update_interval == timedelta(seconds=60)
    assert [e[0] for e in events] == ["started", "started"]
    assert events[0][1] == {
        "entitlement_id": EID_ETHAN,
        "title_id": "CUSA03048_00",
        "title": "The Vanishing of Ethan Carter",
        "platform": None,
    }
    assert hass.states.get("binary_sensor.ps5_downloading").state == "on"

    # Active: queue + one progress per item, getUserDevices only every 9 minutes.
    mock_sony.reset()
    for _ in range(8):
        await tick(hass, freezer, 60)
    assert mock_sony.ops().count("getUserDevices") == 0
    assert mock_sony.ops().count("GetRemoteDownloadList") == 8
    assert mock_sony.ops().count("downloadProgress") == 16
    mock_sony.reset()
    await tick(hass, freezer, 60)  # ciphertext now 9 minutes old
    assert mock_sony.ops() == [
        "getUserDevices",
        "GetRemoteDownloadList",
        "downloadProgress",
        "downloadProgress",
    ]

    # Queue empties: back to idle immediately.
    mock_sony.set("GetRemoteDownloadList", load_fixture("remote_download_list_empty.json"))
    mock_sony.reset()
    await tick(hass, freezer, 60)
    assert mock_sony.ops() == ["GetRemoteDownloadList", "getUserDevices"]
    assert status.update_interval == timedelta(minutes=10)
    assert hass.states.get("binary_sensor.ps5_downloading").state == "off"


async def test_completed_and_cancelled(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
    events: list,
) -> None:
    mock_sony.set("GetRemoteDownloadList", queue_response(ETHAN, JAZZPUNK, GEN5))
    mock_sony.set(
        "downloadProgress",
        lambda req: {
            EID_ETHAN: load_fixture("download_progress_playable.json"),  # 100 %
            EID_JAZZPUNK: progress_response(downloaded=10, total=100),
            EID_GEN5: progress_response(downloaded=20, total=100),
        }[req.variables["entitlementId"]],
    )
    await tick(hass, freezer, 600)
    assert [e[0] for e in events] == ["started"] * 3
    events.clear()

    # All three disappear; Jazzpunk is installed now, Game 005 is not.
    mock_sony.set("GetRemoteDownloadList", load_fixture("remote_download_list_empty.json"))
    mock_sony.set("getUserDevices", devices_with("CUSA05228_00"))
    mock_sony.reset()
    await tick(hass, freezer, 60)
    assert mock_sony.ops() == ["GetRemoteDownloadList", "getUserDevices"]
    outcome = {data["entitlement_id"]: event_type for event_type, data in events}
    assert outcome == {
        EID_ETHAN: "completed",  # bytes rule
        EID_JAZZPUNK: "completed",  # installed rule (titleId from the entitlementId)
        EID_GEN5: "cancelled",  # neither
    }
    gen5 = next(data for _type, data in events if data["entitlement_id"] == EID_GEN5)
    assert gen5 == {
        "entitlement_id": EID_GEN5,
        "title_id": "PPSA90005_00",
        "title": "Game 005",
        "platform": "PS5",
    }


async def test_cancelled_when_neither_rule_matches(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
    events: list,
) -> None:
    mock_sony.set("GetRemoteDownloadList", queue_response(JAZZPUNK))
    mock_sony.set("downloadProgress", progress_response(downloaded=99, total=100))
    await tick(hass, freezer, 600)
    mock_sony.set("GetRemoteDownloadList", load_fixture("remote_download_list_empty.json"))
    await tick(hass, freezer, 60)
    assert events[-1] == (
        "cancelled",
        {
            "entitlement_id": EID_JAZZPUNK,
            "title_id": "CUSA05228_00",
            "title": "Jazzpunk: Director's Cut",
            "platform": None,
        },
    )
    state = hass.states.get("event.ps5_download")
    assert state.attributes["event_type"] == "cancelled"
    assert state.attributes["entitlement_id"] == EID_JAZZPUNK


async def test_installed_rule_uses_library_title_id(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
    events: list,
) -> None:
    """A library title's titleId and platform are used (Dispatch, PPSA27158_00)."""
    without_dispatch = load_fixture("get_user_devices.json")
    details = without_dispatch["data"]["deviceStorageDetailsRetrieve"][0]["deviceStorageDetails"]
    details["installedGames"] = [
        game for game in details["installedGames"] if game["titleId"] != "PPSA27158_00"
    ]
    mock_sony.set("getUserDevices", without_dispatch)
    mock_sony.set("GetRemoteDownloadList", queue_response(DISPATCH))
    mock_sony.set("downloadProgress", load_fixture("download_progress_retrieving.json"))
    await tick(hass, freezer, 600)
    mock_sony.set("GetRemoteDownloadList", load_fixture("remote_download_list_empty.json"))
    mock_sony.set("getUserDevices", load_fixture("get_user_devices.json"))
    await tick(hass, freezer, 60)
    assert events[-1] == (
        "completed",
        {
            "entitlement_id": EID_DISPATCH,
            "title_id": "PPSA27158_00",
            "title": "Dispatch",
            "platform": "PS5",
        },
    )


async def test_no_events_on_first_update(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.set("GetRemoteDownloadList", queue_response(ETHAN))
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("event.ps5_download").state == STATE_UNKNOWN
    assert mock_config_entry.runtime_data.status.update_interval == timedelta(seconds=60)


async def test_retrieving_and_progress_choice(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
) -> None:
    mock_sony.set("GetRemoteDownloadList", queue_response(ETHAN, JAZZPUNK, DISPATCH))
    progress = {
        EID_ETHAN: load_fixture("download_progress_retrieving.json"),
        EID_JAZZPUNK: progress_response(downloaded=25, total=200, remaining=120),
        EID_DISPATCH: progress_response(downloaded=1, total=3, remaining=5),
    }
    mock_sony.set("downloadProgress", lambda req: progress[req.variables["entitlementId"]])
    await tick(hass, freezer, 600)

    state = hass.states.get("sensor.ps5_download_progress")
    assert state.state == "12.5"  # first transferring item in queue order
    assert state.attributes["entitlement_id"] == EID_JAZZPUNK
    assert state.attributes["downloaded_bytes"] == 25
    assert state.attributes["total_bytes"] == 200
    assert hass.states.get("sensor.ps5_download_time_remaining").state == "120"

    queue = hass.states.get("sensor.ps5_download_queue")
    assert queue.state == "3"
    first = queue.attributes["items"][0]
    assert first["progress_status"] == "RETRIEVING"
    assert first["percent"] is None
    assert first["downloaded_bytes"] is None
    assert first["queue_status"] == "NOTSTARTED"
    third = queue.attributes["items"][2]
    assert third["title_id"] == "PPSA27158_00"
    assert third["platform"] == "PS5"
    assert third["percent"] == 33.3

    # Nothing transferring: unknown.
    progress[EID_JAZZPUNK] = load_fixture("download_progress_retrieving.json")
    progress[EID_DISPATCH] = progress_response(status="SOMETHINGNEW")
    await tick(hass, freezer, 60)
    assert hass.states.get("sensor.ps5_download_progress").state == STATE_UNKNOWN
    assert hass.states.get("sensor.ps5_download_time_remaining").state == STATE_UNKNOWN


async def test_three_failures_and_backoff(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
) -> None:
    status = init_integration.runtime_data.status
    mock_sony.set("GetRemoteDownloadList", queue_response(ETHAN))
    await tick(hass, freezer, 600)
    assert status.update_interval == timedelta(seconds=60)

    mock_sony.set("GetRemoteDownloadList", FakeResponse(503))
    await tick(hass, freezer, 60)
    assert status.failures == 1
    assert status.update_interval == timedelta(minutes=1)
    assert hass.states.get("sensor.ps5_download_queue").state == "1"
    await tick(hass, freezer, 60)
    assert status.update_interval == timedelta(minutes=2)
    assert hass.states.get("sensor.ps5_download_queue").state == "1"
    await tick(hass, freezer, 120)
    assert status.failures == 3
    assert status.update_interval == timedelta(minutes=4)
    assert hass.states.get("sensor.ps5_download_queue").state == STATE_UNAVAILABLE
    for expected in (8, 16, 32, 60, 60):
        await tick(hass, freezer, status.update_interval.total_seconds())
        assert status.update_interval == timedelta(minutes=expected)

    mock_sony.set("GetRemoteDownloadList", load_fixture("remote_download_list_empty.json"))
    await tick(hass, freezer, 3600)
    assert status.failures == 0
    assert status.update_interval == timedelta(minutes=10)
    assert hass.states.get("sensor.ps5_download_queue").state == "0"


async def test_idle_backoff_never_faster_than_idle(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
) -> None:
    status = init_integration.runtime_data.status
    mock_sony.set("getUserDevices", TimeoutError())
    await tick(hass, freezer, 600)
    assert status.failures == 1
    assert status.update_interval == timedelta(minutes=10)


async def test_rate_limited(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
    caplog: pytest.LogCaptureFixture,
) -> None:
    status = init_integration.runtime_data.status
    mock_sony.queue("getUserDevices", FakeResponse(429, headers={"Retry-After": "1800"}))
    await tick(hass, freezer, 600)
    assert status.update_interval == timedelta(seconds=1800)
    assert caplog.text.count("rate limited") == 1
    assert init_integration.runtime_data.limiter.pause_remaining > 0
    mock_sony.reset()
    # A library refresh during the pause sends nothing.
    await init_integration.runtime_data.library.async_refresh()
    assert mock_sony.requests == []
    await tick(hass, freezer, 1800)
    assert status.failures == 0
    assert mock_sony.ops() == ["getUserDevices", "GetRemoteDownloadList"]


async def test_persisted_query_not_found_issue(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
) -> None:
    mock_sony.queue(
        "GetRemoteDownloadList",
        {"errors": [{"message": "PersistedQueryNotFound"}]},
    )
    await tick(hass, freezer, 600)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "api_changed_GetRemoteDownloadList")
    assert issue is not None
    assert issue.translation_placeholders == {"operation": "GetRemoteDownloadList"}
    mock_sony.reset()
    await tick(hass, freezer, 600)
    assert "GetRemoteDownloadList" not in mock_sony.ops()  # disabled until restart


async def test_console_not_found_issue(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
) -> None:
    mock_sony.set("getUserDevices", devices_with(name="Renamed"))
    await tick(hass, freezer, 600)
    issue_id = f"console_not_found_{init_integration.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert hass.states.get("sensor.ps5_installed_titles").state == STATE_UNAVAILABLE
    mock_sony.set("getUserDevices", devices_with())
    await tick(hass, freezer, 600)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get("sensor.ps5_installed_titles").state == "5"


async def test_npsso_expiring_issue(hass: HomeAssistant, mock_sony: FakeSony) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PS5",
        unique_id="1234567890123456789",
        data=entry_data(npsso_expires_at=time.time() + 6 * 24 * 3600),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"npsso_expiring_{entry.entry_id}")
    assert issue is not None
    assert issue.is_fixable
    assert issue.severity is ir.IssueSeverity.WARNING


async def test_no_npsso_issue_without_expiry(hass: HomeAssistant, mock_sony: FakeSony) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="PS5", unique_id="x", data=entry_data(npsso_expires_at=None)
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not ir.async_get(hass).issues
