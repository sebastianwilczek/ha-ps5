"""Base entity for the PS5 integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import CONF_ACCOUNT_ID, CONF_CONSOLE_NAME, DOMAIN, MANUFACTURER, MODEL


def device_identifier(account_id: str, console_name: str) -> str:
    return f"{account_id}_{console_name}"


class PS5Entity[CoordinatorT: DataUpdateCoordinator](CoordinatorEntity[CoordinatorT]):
    """An entity of the selected console."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CoordinatorT, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        account_id = entry.data[CONF_ACCOUNT_ID]
        console_name = entry.data[CONF_CONSOLE_NAME]
        identifier = device_identifier(account_id, console_name)
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{identifier}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=console_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
