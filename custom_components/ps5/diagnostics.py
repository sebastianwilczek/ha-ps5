"""Diagnostics for the PS5 integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCOUNT_ID,
    CONF_CLIENT_DUID,
    CONF_NPSSO,
    CONF_REFRESH_TOKEN,
)
from .coordinator import PS5ConfigEntry

TO_REDACT = {
    CONF_NPSSO,
    CONF_REFRESH_TOKEN,
    CONF_CLIENT_DUID,
    CONF_ACCOUNT_ID,
    "duid",
    "access_token",
    "id_token",
    "Authorization",
    "authorization",
    "unique_id",
}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PS5ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (secrets redacted)."""
    runtime = entry.runtime_data
    status = runtime.status
    library = runtime.library
    client = runtime.client
    data = status.data
    status_info: dict[str, Any] = {
        "update_interval_s": status.update_interval.total_seconds()
        if status.update_interval
        else None,
        "last_update_success": status.last_update_success,
        "last_success": _iso(status.last_success),
        "consecutive_failures": status.failures,
        "last_error": status.last_error,
    }
    if data is not None:
        status_info |= {
            "console_name": data.console.name,
            "installed_titles": len(data.console.installed),
            "installed_bytes": data.console.installed_bytes,
            "queue": [
                {
                    "entitlement_id": item.entitlement_id,
                    "title": item.title,
                    "queue_status": item.status,
                    "reason_code": item.reason_code,
                }
                for item in data.queue
            ],
            "progress": {
                eid: {
                    "status": p.status,
                    "downloaded_bytes": p.downloaded_bytes,
                    "total_bytes": p.total_bytes,
                    "remaining_s": p.remaining_s,
                }
                for eid, p in data.progress.items()
            },
        }
    library_data = library.data
    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "version": entry.version,
                "data": dict(entry.data),
            },
            "status": status_info,
            "library": {
                "titles": len(library_data.titles) if library_data else 0,
                "downloadable": len(library_data.downloadable) if library_data else 0,
                "fetched_at": _iso(library_data.fetched_at) if library_data else None,
                "update_interval_s": library.update_interval.total_seconds()
                if library.update_interval
                else None,
                "last_update_success": library.last_update_success,
            },
            "limiter": runtime.limiter.snapshot(),
            "client": {
                "console_duid_age_s": client.console_duid_age(),
                "disabled_operations": sorted(client.disabled_operations),
                "last_error_per_operation": dict(client.last_errors),
            },
        },
        TO_REDACT,
    )
