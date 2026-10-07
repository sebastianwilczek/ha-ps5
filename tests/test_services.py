"""Tests for the actions and the write rules."""

from __future__ import annotations

from datetime import timedelta
import logging

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ps5.const import DOMAIN
from tests.common import (
    ACCOUNT_ID,
    CONSOLE_DUID,
    EID_DISPATCH,
    EID_ETHAN,
    FakeResponse,
    FakeSony,
    authorize_redirect,
    queue_item,
    queue_response,
)

EID_GEN7 = "EP0000-PPSA90007_00-GEN0000000000007"


def device_id(hass: HomeAssistant) -> str:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{ACCOUNT_ID}_PS5"), entry.entry_id
    )
    assert device is not None
    return device.id


async def call(hass: HomeAssistant, service: str, **data) -> None:
    return await hass.services.async_call(
        DOMAIN, service, {"device_id": device_id(hass), **data}, blocking=True
    )


async def test_start_download(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
) -> None:
    mock_sony.reset()
    await call(hass, "start_download", entitlement_id=EID_GEN7)
    assert mock_sony.ops() == ["initiateDownload"]
    (req,) = mock_sony.calls("initiateDownload")
    assert req.variables == {
        "scheduleRequests": {
            "downloadSchedules": [
                {"duid": CONSOLE_DUID, "entitlementId": EID_GEN7, "platform": "PS5"}
            ]
        }
    }
    # A status refresh follows 5 s later and switches to active mode.
    mock_sony.set("GetRemoteDownloadList", queue_response(queue_item(EID_GEN7, "Game 007")))
    freezer.tick(timedelta(seconds=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_sony.ops() == ["initiateDownload"]
    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_sony.ops()[1:] == ["getUserDevices", "GetRemoteDownloadList", "downloadProgress"]
    assert init_integration.runtime_data.status.update_interval == timedelta(seconds=60)


async def test_start_download_refreshes_stale_ciphertext(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
) -> None:
    freezer.tick(timedelta(minutes=9))
    mock_sony.reset()
    await call(hass, "start_download", entitlement_id=EID_GEN7)
    assert mock_sony.ops() == ["getUserDevices", "initiateDownload"]


async def test_start_download_validation(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.reset()
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "start_download", entitlement_id="EP0000-NOPE00000_00-X")
    assert err.value.translation_key == "not_downloadable"
    assert mock_sony.requests == []


async def test_start_download_installed_warns(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        await call(hass, "start_download", entitlement_id=EID_DISPATCH)
    assert "already installed" in caplog.text
    assert len(mock_sony.calls("initiateDownload")) == 1


@pytest.mark.parametrize(
    ("response", "key"),
    [
        (
            {
                "data": {
                    "remoteDownloadSchedule": [
                        {"entitlementId": EID_GEN7, "errorCode": "E1", "reasonCode": "R9"}
                    ]
                }
            },
            "write_rejected",
        ),
        (FakeResponse(500), "request_failed"),
        (TimeoutError(), "request_failed"),
        ({"errors": [{"message": "Something odd"}]}, "api_error"),
        ({"errors": [{"message": "PersistedQueryNotFound"}]}, "api_changed"),
        (FakeResponse(429, headers={"Retry-After": "60"}), "rate_limited"),
    ],
)
async def test_start_download_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_sony: FakeSony,
    response,
    key: str,
) -> None:
    mock_sony.queue("initiateDownload", response)
    mock_sony.reset()
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "start_download", entitlement_id=EID_GEN7)
    assert err.value.translation_key == key
    if key == "write_rejected":
        assert err.value.translation_placeholders == {"error_code": "E1", "reason_code": "R9"}
    assert mock_sony.ops() == ["initiateDownload"]  # never retried


async def test_write_with_invalid_npsso_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.queue("initiateDownload", FakeResponse(401), FakeResponse(401))
    mock_sony.queue("authorize", authorize_redirect(code=None))
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "start_download", entitlement_id=EID_GEN7)
    assert err.value.translation_key == "auth_failed"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert len(mock_sony.calls("initiateDownload")) == 2


async def test_cancel_download(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_sony: FakeSony
) -> None:
    mock_sony.set("GetRemoteDownloadList", queue_response(queue_item(EID_ETHAN, "Ethan")))
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    mock_sony.reset()
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "cancel_download", entitlement_id=EID_GEN7)
    assert err.value.translation_key == "not_in_queue"
    await call(hass, "cancel_download", entitlement_id=EID_ETHAN)
    assert mock_sony.ops() == ["remoteDownloadCancel"]
    assert (
        mock_sony.calls("remoteDownloadCancel")[0].variables["updateRequests"]["downloadUpdates"][
            0
        ]["entitlementId"]
        == EID_ETHAN
    )


async def test_get_library(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    result = await hass.services.async_call(
        DOMAIN, "get_library", {"device_id": device_id(hass)}, blocking=True, return_response=True
    )
    assert len(result["titles"]) == 218
    assert result["titles"][0] == {
        "name": "Dispatch",
        "entitlement_id": EID_DISPATCH,
        "title_id": "PPSA27158_00",
        "platform": "PS5",
        "image": "https://image.api.playstation.com/sgst/prod/00/PPSA27158_00/app/info/21/fi_2ebce38520689cc4aeec31a6096743ae5098b4adbd92da7b07b4e041aaed8eed/icon0.png",
        "installed": True,
    }
    ps4 = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {"device_id": device_id(hass), "platform": "PS4"},
        blocking=True,
        return_response=True,
    )
    assert [t["entitlement_id"] for t in ps4["titles"]] == ["EP6690-CUSA28307_00-4596512634892904"]
    installed = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {"device_id": device_id(hass), "installed": True},
        blocking=True,
        return_response=True,
    )
    assert [t["name"] for t in installed["titles"]] == ["Dispatch"]
    not_installed = await hass.services.async_call(
        DOMAIN,
        "get_library",
        {"device_id": device_id(hass), "installed": False},
        blocking=True,
        return_response=True,
    )
    assert len(not_installed["titles"]) == 217


async def test_invalid_device(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "start_download",
            {"device_id": "nope", "entitlement_id": EID_GEN7},
            blocking=True,
        )
    assert err.value.translation_key == "invalid_device"
