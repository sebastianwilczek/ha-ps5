"""Tests for the config flow."""

from __future__ import annotations

import copy
import re
import time

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ps5.const import (
    CONF_ACCOUNT_ID,
    CONF_CLIENT_DUID,
    CONF_CONSOLE_NAME,
    CONF_NPSSO,
    CONF_NPSSO_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN_EXPIRES_AT,
    DOMAIN,
)
from tests.common import (
    ACCOUNT_ID,
    CLIENT_DUID,
    NPSSO,
    REFRESH_TOKEN,
    FakeResponse,
    FakeSony,
    authorize_redirect,
    load_fixture,
    token_response,
)
from tests.conftest import entry_data

NEW_NPSSO = "M" * 64


def two_consoles(second_name: str = "Bedroom") -> dict:
    data = load_fixture("get_user_devices.json")
    devices = data["data"]["deviceStorageDetailsRetrieve"]
    second = copy.deepcopy(devices[0])
    second["deviceName"] = second_name
    devices.append(second)
    return data


async def start_user_flow(hass: HomeAssistant, npsso: str) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["description_placeholders"] == {
        "npsso_url": "https://ca.account.sony.com/api/v1/ssocookie"
    }
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NPSSO: npsso})


async def test_user_flow_bare_npsso(hass: HomeAssistant, mock_sony: FakeSony) -> None:
    result = await start_user_flow(hass, NPSSO)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "PS5"
    data = result["data"]
    assert re.fullmatch(r"0000000700090100[0-9a-f]{64}", data[CONF_CLIENT_DUID])
    assert data[CONF_NPSSO] == NPSSO
    assert data[CONF_NPSSO_EXPIRES_AT] is None
    assert data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN
    assert data[CONF_REFRESH_TOKEN_EXPIRES_AT] == pytest.approx(time.time() + 863999, abs=5)
    assert data[CONF_ACCOUNT_ID] == ACCOUNT_ID
    assert data[CONF_CONSOLE_NAME] == "PS5"
    assert result["result"].unique_id == ACCOUNT_ID
    assert mock_sony.ops()[:3] == ["authorize", "token", "getUserDevices"]
    # The same client duid is used for authorize and token.
    assert data[CONF_CLIENT_DUID] in mock_sony.requests[0].url
    assert data[CONF_CLIENT_DUID] in mock_sony.requests[1].data
    # A cookie-less session was used and released.
    assert mock_sony.closed


async def test_user_flow_json_npsso(hass: HomeAssistant, mock_sony: FakeSony) -> None:
    raw = '{"npsso":"' + NPSSO + '","expires_in":5183985}'
    result = await start_user_flow(hass, raw)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NPSSO] == NPSSO
    assert result["data"][CONF_NPSSO_EXPIRES_AT] == pytest.approx(time.time() + 5183985, abs=5)


@pytest.mark.parametrize(
    ("npsso", "setup", "error"),
    [
        ("too-short", None, "invalid_npsso"),
        (NPSSO, ("authorize", authorize_redirect(code=None)), "invalid_npsso"),
        (NPSSO, ("authorize", FakeResponse(503)), "cannot_connect"),
        (NPSSO, ("token", TimeoutError()), "cannot_connect"),
        (NPSSO, ("getUserDevices", {"errors": [{"message": "x"}]}), "unknown"),
        (NPSSO, ("getUserDevices", ValueError("boom")), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_sony: FakeSony, npsso: str, setup, error: str
) -> None:
    if setup:
        mock_sony.queue(*setup)
    result = await start_user_flow(hass, npsso)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    # The form can be submitted again and succeeds.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NPSSO: NPSSO})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_no_ps5(hass: HomeAssistant, mock_sony: FakeSony) -> None:
    data = load_fixture("get_user_devices.json")
    data["data"]["deviceStorageDetailsRetrieve"] = data["data"]["deviceStorageDetailsRetrieve"][1:]
    mock_sony.queue("getUserDevices", data)
    result = await start_user_flow(hass, NPSSO)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_ps5"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_sony: FakeSony, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await start_user_flow(hass, NPSSO)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_select_console(hass: HomeAssistant, mock_sony: FakeSony) -> None:
    mock_sony.set("getUserDevices", two_consoles())
    result = await start_user_flow(hass, NPSSO)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_console"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"console": "Bedroom"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bedroom"
    assert result["data"][CONF_CONSOLE_NAME] == "Bedroom"


async def test_user_flow_duplicate_names(hass: HomeAssistant, mock_sony: FakeSony) -> None:
    mock_sony.set("getUserDevices", two_consoles(second_name="PS5"))
    result = await start_user_flow(hass, NPSSO)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "duplicate_console_names"


async def test_reauth(
    hass: HomeAssistant, mock_sony: FakeSony, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"npsso_expiring_{mock_config_entry.entry_id}",
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="npsso_expiring",
    )
    mock_sony.set("token", lambda req: token_response(refresh_token="rt-new"))
    mock_sony.set("refresh", lambda req: token_response(refresh_token="rt-new"))
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NPSSO: '{"npsso":"' + NEW_NPSSO + '","expires_in":5183985}'}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    data = mock_config_entry.data
    assert data[CONF_NPSSO] == NEW_NPSSO
    assert data[CONF_NPSSO_EXPIRES_AT] == pytest.approx(time.time() + 5183985, abs=5)
    assert data[CONF_REFRESH_TOKEN] == "rt-new"
    assert data[CONF_CLIENT_DUID] == CLIENT_DUID  # kept
    assert CLIENT_DUID in mock_sony.calls("authorize")[0].url
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"npsso_expiring_{mock_config_entry.entry_id}")
        is None
    )


async def test_reauth_wrong_account(
    hass: HomeAssistant, mock_sony: FakeSony, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    mock_sony.set("token", lambda req: token_response(account_id="999"))
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NPSSO: NEW_NPSSO}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert mock_config_entry.data[CONF_NPSSO] == NPSSO


async def test_reauth_invalid_npsso(
    hass: HomeAssistant, mock_sony: FakeSony, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    mock_sony.queue("authorize", authorize_redirect(code=None))
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NPSSO: NEW_NPSSO}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_npsso"}


async def test_reconfigure_renamed_console(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_sony: FakeSony
) -> None:
    entry = init_integration
    renamed = load_fixture("get_user_devices.json")
    renamed["data"]["deviceStorageDetailsRetrieve"][0]["deviceName"] = "Living room"
    mock_sony.set("getUserDevices", renamed)
    mock_sony.reset()
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert mock_sony.ops() == ["getUserDevices"]  # current tokens, no new login
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"console": "Living room"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_CONSOLE_NAME] == "Living room"
    assert entry.title == "Living room"
    ent_reg = er.async_get(hass)
    entity = ent_reg.async_get("sensor.ps5_installed_titles")
    assert entity.unique_id == f"{ACCOUNT_ID}_Living room_installed_titles"
    assert dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{ACCOUNT_ID}_Living room"), entry.entry_id
    )
    assert hass.states.get("sensor.ps5_installed_titles").state == "5"


async def test_reconfigure_not_loaded(hass: HomeAssistant, mock_sony: FakeSony) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Old", unique_id=ACCOUNT_ID, data=entry_data(console_name="Old")
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"console_not_found_{entry.entry_id}")
    mock_sony.reset()
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    assert mock_sony.ops() == ["refresh", "getUserDevices"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"console": "PS5"})
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_CONSOLE_NAME] == "PS5"


async def test_reconfigure_duplicate_names(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.set("getUserDevices", two_consoles(second_name="PS5"))
    result = await init_integration.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "duplicate_console_names"
