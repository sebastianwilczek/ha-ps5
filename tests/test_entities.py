"""Tests for the select, buttons and sensor attributes."""

from __future__ import annotations

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.ps5.api.models import LibraryTitle
from custom_components.ps5.select import build_options
from tests.common import (
    EID_ETHAN,
    EID_JAZZPUNK,
    FakeSony,
    progress_response,
    queue_item,
    queue_response,
)

EID_GEN7 = "EP0000-PPSA90007_00-GEN0000000000007"


def lib(eid: str, name: str, platform: str = "PS5") -> LibraryTitle:
    return LibraryTitle(eid, eid.split("-")[1], name, platform, None, True, False, True)


def test_option_naming_and_collisions() -> None:
    options = build_options(
        [
            lib("EP0000-PPSA00002_00-AAAAAAAA123456", "zeta"),
            lib("EP0000-CUSA00001_00-AAAAAAAAAAAAAA", "Alpha", "PS4"),
            lib("EP0000-PPSA00001_00-AAAAAAAABBBBBB", "Alpha"),
            lib("EP0000-PPSA00003_00-AAAAAAAACCCCCC", "Alpha"),
        ]
    )
    assert list(options) == [
        "Alpha (PS4)",
        "Alpha (PS5) [BBBBBB]",
        "Alpha (PS5) [CCCCCC]",
        "zeta (PS5)",
    ]
    assert options["Alpha (PS5) [CCCCCC]"] == "EP0000-PPSA00003_00-AAAAAAAACCCCCC"


async def test_select_and_download_button(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_sony: FakeSony
) -> None:
    state = hass.states.get("select.ps5_game")
    options = state.attributes["options"]
    assert len(options) == 218
    assert options[:3] == [
        "Catlateral Damage: Remeowstered (PS4)",
        "Catlateral Damage: Remeowstered (PS5)",
        "Dispatch (PS5)",
    ]
    assert hass.states.get("button.ps5_download_selected_game").state == STATE_UNAVAILABLE

    mock_sony.reset()
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.ps5_game", "option": "Game 007 (PS5)"},
        blocking=True,
    )
    assert mock_sony.requests == []  # selecting makes no API call
    assert hass.states.get("select.ps5_game").state == "Game 007 (PS5)"
    assert hass.states.get("button.ps5_download_selected_game").state != STATE_UNAVAILABLE

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.ps5_download_selected_game"}, blocking=True
    )
    assert mock_sony.ops() == ["initiateDownload"]
    assert (
        mock_sony.calls("initiateDownload")[0].variables["scheduleRequests"]["downloadSchedules"][
            0
        ]["entitlementId"]
        == EID_GEN7
    )


async def test_select_restores_choice(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("select.ps5_game", "Game 007 (PS5)"),
                {"entitlement_id": EID_GEN7},
            )
        ],
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.ps5_game").state == "Game 007 (PS5)"
    assert mock_config_entry.runtime_data.selected_entitlement_id == EID_GEN7
    assert hass.states.get("button.ps5_download_selected_game").state != STATE_UNAVAILABLE


async def test_cancel_button(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.set(
        "GetRemoteDownloadList",
        queue_response(queue_item(EID_ETHAN, "Ethan"), queue_item(EID_JAZZPUNK, "Jazzpunk")),
    )
    mock_sony.set(
        "downloadProgress",
        lambda req: progress_response(
            status="transferring"
            if req.variables["entitlementId"] == EID_JAZZPUNK
            else "RETRIEVING"
        ),
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.ps5_cancel_download"}, blocking=True
    )
    (req,) = mock_sony.calls("remoteDownloadCancel")
    # The transferring item, not the first one.
    assert req.variables["updateRequests"]["downloadUpdates"][0]["entitlementId"] == EID_JAZZPUNK


async def test_cancel_button_first_item_and_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.set(
        "GetRemoteDownloadList",
        queue_response(queue_item(EID_ETHAN, "Ethan"), queue_item(EID_JAZZPUNK, "Jazzpunk")),
    )
    mock_sony.set("downloadProgress", progress_response(status="RETRIEVING"))
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.ps5_cancel_download"}, blocking=True
    )
    (req,) = mock_sony.calls("remoteDownloadCancel")
    assert req.variables["updateRequests"]["downloadUpdates"][0]["entitlementId"] == EID_ETHAN


async def test_cancel_button_unavailable_when_idle(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    assert hass.states.get("button.ps5_cancel_download").state == STATE_UNAVAILABLE


async def test_refresh_library_button(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.reset()
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.ps5_refresh_library"}, blocking=True
    )
    await hass.async_block_till_done()
    assert mock_sony.ops() == ["getPurchasedGameList"] * 10


async def test_sensor_attributes(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    installed = hass.states.get("sensor.ps5_installed_titles")
    assert installed.state == "5"
    assert installed.attributes["state_class"] == "measurement"
    names = [t["name"] for t in installed.attributes["titles"]]
    assert names == sorted(names, key=str.casefold)
    assert installed.attributes["titles"][0] == {
        "name": "Dispatch",
        "title_id": "PPSA27158_00",
        "platforms": ["PS5"],
        "size_bytes": 14991622144,
        "image": "https://image.api.playstation.com/vulcan/ap/rnd/202508/0621/4fb935ea5e75490740d6726da42d05d953cb50a693188174.png",
    }
    storage = hass.states.get("sensor.ps5_storage_used")
    assert storage.attributes["unit_of_measurement"] == "GB"
    assert storage.state == "74.804494336"
    assert hass.states.get("sensor.ps5_download_queue").attributes["items"] == []


async def test_unrecorded_attributes(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN

    component = hass.data["entity_components"][SENSOR_DOMAIN]
    by_id = {entity.entity_id: entity for entity in component.entities}
    for entity_id, attribute in (
        ("sensor.ps5_installed_titles", "titles"),
        ("sensor.ps5_download_queue", "items"),
    ):
        unrecorded = by_id[entity_id]._Entity__combined_unrecorded_attributes
        assert attribute in unrecorded
