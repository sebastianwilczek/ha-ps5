"""Repair issues raised by the integration."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .api.const import NPSSO_URL
from .const import (
    CONF_CONSOLE_NAME,
    CONF_NPSSO_EXPIRES_AT,
    DOMAIN,
    ISSUE_API_CHANGED,
    ISSUE_CONSOLE_NOT_FOUND,
    ISSUE_NPSSO_EXPIRING,
    NPSSO_WARNING,
)


def npsso_issue_id(entry: ConfigEntry) -> str:
    return f"{ISSUE_NPSSO_EXPIRING}_{entry.entry_id}"


def console_issue_id(entry: ConfigEntry) -> str:
    return f"{ISSUE_CONSOLE_NOT_FOUND}_{entry.entry_id}"


def api_changed_issue_id(operation: str) -> str:
    return f"{ISSUE_API_CHANGED}_{operation}"


@callback
def async_check_npsso_expiry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create or delete the npsso_expiring issue."""
    expires_at = entry.data.get(CONF_NPSSO_EXPIRES_AT)
    if expires_at is None:
        ir.async_delete_issue(hass, DOMAIN, npsso_issue_id(entry))
        return
    expires = datetime.fromtimestamp(expires_at, UTC)
    if expires - datetime.now(UTC) >= NPSSO_WARNING:
        ir.async_delete_issue(hass, DOMAIN, npsso_issue_id(entry))
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        npsso_issue_id(entry),
        data={"entry_id": entry.entry_id},
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_NPSSO_EXPIRING,
        translation_placeholders={
            "console": entry.title,
            "expires": expires.date().isoformat(),
            "npsso_url": NPSSO_URL,
        },
    )


@callback
def async_delete_npsso_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, npsso_issue_id(entry))


@callback
def async_create_console_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        console_issue_id(entry),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_CONSOLE_NOT_FOUND,
        translation_placeholders={"console": entry.data[CONF_CONSOLE_NAME]},
    )


@callback
def async_delete_console_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, console_issue_id(entry))


@callback
def async_create_api_changed_issue(hass: HomeAssistant, operation: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        api_changed_issue_id(operation),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_API_CHANGED,
        translation_placeholders={"operation": operation},
    )
