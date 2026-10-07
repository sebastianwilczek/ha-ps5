"""Actions of the PS5 integration and the shared write helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .api.errors import (
    ApiError,
    ConsoleNotFound,
    NpssoInvalid,
    PersistedQueryNotFound,
    PsnError,
    RateLimited,
    WriteRejected,
)
from .const import (
    ATTR_DEVICE_ID,
    ATTR_ENTITLEMENT_ID,
    ATTR_INSTALLED,
    ATTR_PLATFORM,
    DOMAIN,
    SERVICE_CANCEL_DOWNLOAD,
    SERVICE_GET_LIBRARY,
    SERVICE_START_DOWNLOAD,
)
from .coordinator import PS5ConfigEntry
from .issues import async_create_api_changed_issue

_LOGGER = logging.getLogger(__name__)

WRITE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_ENTITLEMENT_ID): cv.string,
    }
)
GET_LIBRARY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_PLATFORM): vol.In(["PS4", "PS5"]),
        vol.Optional(ATTR_INSTALLED): cv.boolean,
    }
)


async def async_start_download(
    hass: HomeAssistant, entry: PS5ConfigEntry, entitlement_id: str
) -> None:
    """Validate and start a remote download."""
    runtime = entry.runtime_data
    title = runtime.library.data.by_entitlement.get(entitlement_id)
    if title is None or not title.downloadable:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_downloadable",
            translation_placeholders={"entitlement_id": entitlement_id},
        )
    status = runtime.status.data
    if status is not None and title.title_id in status.installed_title_ids:
        _LOGGER.warning(
            "%s (%s) is already installed; requesting the download anyway",
            title.name,
            title.title_id,
        )
    await _async_write(hass, entry, runtime.client.start_download, entitlement_id)


async def async_cancel_download(
    hass: HomeAssistant, entry: PS5ConfigEntry, entitlement_id: str
) -> None:
    """Validate and cancel a queued download."""
    runtime = entry.runtime_data
    status = runtime.status.data
    if status is None or entitlement_id not in {item.entitlement_id for item in status.queue}:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_in_queue",
            translation_placeholders={"entitlement_id": entitlement_id},
        )
    await _async_write(hass, entry, runtime.client.cancel_download, entitlement_id)


async def _async_write(
    hass: HomeAssistant,
    entry: PS5ConfigEntry,
    write: Callable[[str], Awaitable[None]],
    entitlement_id: str,
) -> None:
    try:
        await write(entitlement_id)
    except NpssoInvalid as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
    except WriteRejected as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="write_rejected",
            translation_placeholders={
                "error_code": str(err.error_code),
                "reason_code": str(err.reason_code),
            },
        ) from err
    except PersistedQueryNotFound as err:
        async_create_api_changed_issue(hass, err.operation)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="api_changed",
            translation_placeholders={"operation": err.operation},
        ) from err
    except RateLimited as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="rate_limited",
            translation_placeholders={"seconds": f"{err.retry_after:.0f}"},
        ) from err
    except ConsoleNotFound as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="console_not_found",
            translation_placeholders={"console": err.console_name},
        ) from err
    except ApiError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"error": str(err), "details": str(err.errors)},
        ) from err
    except PsnError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="request_failed",
            translation_placeholders={"error": f"{type(err).__name__}: {err}"},
        ) from err
    entry.runtime_data.status.async_schedule_refresh_after_write()


def _entry_from_call(hass: HomeAssistant, call: ServiceCall) -> PS5ConfigEntry:
    device_id = call.data[ATTR_DEVICE_ID]
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                continue
            if entry.state is not ConfigEntryState.LOADED:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="entry_not_loaded"
                )
            return entry
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_device",
        translation_placeholders={"device_id": device_id},
    )


async def _async_handle_start(call: ServiceCall) -> None:
    entry = _entry_from_call(call.hass, call)
    await async_start_download(call.hass, entry, call.data[ATTR_ENTITLEMENT_ID])


async def _async_handle_cancel(call: ServiceCall) -> None:
    entry = _entry_from_call(call.hass, call)
    await async_cancel_download(call.hass, entry, call.data[ATTR_ENTITLEMENT_ID])


async def _async_handle_get_library(call: ServiceCall) -> ServiceResponse:
    entry = _entry_from_call(call.hass, call)
    runtime = entry.runtime_data
    status = runtime.status.data
    installed_ids = status.installed_title_ids if status is not None else set()
    platform = call.data.get(ATTR_PLATFORM)
    installed_filter = call.data.get(ATTR_INSTALLED)
    titles: list[dict[str, Any]] = []
    for title in runtime.library.data.titles:
        installed = title.title_id is not None and title.title_id in installed_ids
        if platform is not None and title.platform != platform:
            continue
        if installed_filter is not None and installed != installed_filter:
            continue
        titles.append(
            {
                "name": title.name,
                "entitlement_id": title.entitlement_id,
                "title_id": title.title_id,
                "platform": title.platform,
                "image": title.image,
                "installed": installed,
            }
        )
    return {"titles": titles}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN, SERVICE_START_DOWNLOAD, _async_handle_start, schema=WRITE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_DOWNLOAD, _async_handle_cancel, schema=WRITE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_LIBRARY,
        _async_handle_get_library,
        schema=GET_LIBRARY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
