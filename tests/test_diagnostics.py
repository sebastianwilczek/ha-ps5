"""Tests for diagnostics and the repair flow."""

from __future__ import annotations

import json
import time

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.ps5.const import DOMAIN
from tests.common import (
    ACCOUNT_ID,
    CLIENT_DUID,
    CONSOLE_DUID,
    NPSSO,
    REFRESH_TOKEN,
    FakeSony,
    queue_item,
    queue_response,
)
from tests.conftest import entry_data


async def test_diagnostics_redaction(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    mock_sony: FakeSony,
) -> None:
    mock_sony.set("GetRemoteDownloadList", queue_response(queue_item("EP0-CUSA1_00-X", "X")))
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    access_token = mock_sony.requests[-1].headers["Authorization"].removeprefix("Bearer ")

    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)
    dumped = json.dumps(result)
    for secret in (NPSSO, REFRESH_TOKEN, CLIENT_DUID, ACCOUNT_ID, CONSOLE_DUID, access_token):
        assert secret not in dumped
    assert "U2FsdGVkX1" not in dumped
    assert result["entry"]["data"]["npsso"] == "**REDACTED**"
    assert result["status"]["installed_titles"] == 5
    assert result["status"]["queue"][0]["entitlement_id"] == "EP0-CUSA1_00-X"
    assert result["status"]["update_interval_s"] == 60
    assert result["library"]["titles"] == 218
    assert result["limiter"]["total_requests"] > 0
    assert result["client"]["disabled_operations"] == []


async def test_npsso_repair_flow_starts_reauth(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, mock_sony: FakeSony
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PS5",
        unique_id=ACCOUNT_ID,
        data=entry_data(npsso_expires_at=time.time() + 24 * 3600),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "repairs", {})
    client = await hass_client()
    resp = await client.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": f"npsso_expiring_{entry.entry_id}"},
    )
    assert resp.status == 200
    flow = await resp.json()
    assert flow["step_id"] == "confirm"
    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert resp.status == 200
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
