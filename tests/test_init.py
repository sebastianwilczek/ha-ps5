"""Tests for setup, unload and the library cache."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ps5.const import CONF_REFRESH_TOKEN, DOMAIN
from tests.common import (
    ACCOUNT_ID,
    FakeResponse,
    FakeSony,
    authorize_redirect,
    token_response,
)


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_sony: FakeSony
) -> None:
    entry = init_integration
    assert entry.state is ConfigEntryState.LOADED
    # Status: refresh + getUserDevices + queue; library: 10 pages in the background.
    keys = mock_sony.ops()
    assert keys[:4] == [
        "refresh",
        "getUserDevices",
        "GetRemoteDownloadList",
        "getPurchasedGameList",
    ]
    assert keys.count("getPurchasedGameList") == 10
    assert hass.states.get("sensor.ps5_installed_titles").state == "5"
    assert hass.states.get("sensor.ps5_download_queue").state == "0"
    assert hass.states.get("binary_sensor.ps5_downloading").state == "off"
    entity_registry = er.async_get(hass)
    entity = entity_registry.async_get("sensor.ps5_storage_used")
    assert entity.unique_id == f"{ACCOUNT_ID}_PS5_storage_used"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_invalid_npsso_starts_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.queue("refresh", FakeResponse(400, {"error": "invalid_grant"}))
    mock_sony.queue("authorize", authorize_redirect(code=None))
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


@pytest.mark.parametrize("failure", [FakeResponse(503), TimeoutError()])
async def test_setup_transient_error_retries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_sony: FakeSony, failure
) -> None:
    mock_sony.queue("refresh", failure)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.config_entries.flow.async_progress() == []


async def test_silent_reauth_persists_refresh_token(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.queue("refresh", FakeResponse(401))
    mock_sony.set("token", lambda req: token_response(refresh_token="rt-2"))
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_sony.ops()[:3] == ["refresh", "authorize", "token"]
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == "rt-2"


async def test_library_cache_is_used(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_config_entry: MockConfigEntry,
    mock_sony: FakeSony,
    hass_storage: dict,
) -> None:
    fetched = dt_util.utcnow() - timedelta(hours=11)
    hass_storage[f"{DOMAIN}.library.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.library.{mock_config_entry.entry_id}",
        "data": {
            "fetched_at": fetched.isoformat(),
            "titles": [
                {
                    "entitlement_id": "EP0000-PPSA00001_00-CACHED0000000000",
                    "title_id": "PPSA00001_00",
                    "name": "Cached Game",
                    "platform": "PS5",
                    "image": None,
                    "is_downloadable": True,
                    "is_pre_order": False,
                    "is_active": True,
                }
            ],
        },
    }
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert "getPurchasedGameList" not in mock_sony.ops()
    state = hass.states.get("select.ps5_game")
    assert state.attributes["options"] == ["Cached Game (PS5)"]

    # The cache turns 12 h old one hour later: the library is paged then.
    freezer.tick(timedelta(minutes=50))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert "getPurchasedGameList" not in mock_sony.ops()
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_sony.ops().count("getPurchasedGameList") == 10
    assert len(hass.states.get("select.ps5_game").attributes["options"]) == 218
    stored = hass_storage[f"{DOMAIN}.library.{mock_config_entry.entry_id}"]["data"]
    assert len(stored["titles"]) == 218


async def test_stale_cache_refreshes_at_startup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_sony: FakeSony,
    hass_storage: dict,
) -> None:
    hass_storage[f"{DOMAIN}.library.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.library.{mock_config_entry.entry_id}",
        "data": {
            "fetched_at": (dt_util.utcnow() - timedelta(hours=13)).isoformat(),
            "titles": [],
        },
    }
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_sony.ops().count("getPurchasedGameList") == 10


async def test_library_failure_keeps_previous_data(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
) -> None:
    library = init_integration.runtime_data.library
    assert len(library.data.titles) == 218
    mock_sony.queue("getPurchasedGameList", FakeResponse(502))
    await library.async_refresh()
    assert len(library.data.titles) == 218
    assert library.last_update_success
    assert library.update_interval == timedelta(hours=1)


async def test_remove_entry_deletes_cache(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_storage: dict
) -> None:
    key = f"{DOMAIN}.library.{init_integration.entry_id}"
    assert key in hass_storage
    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
