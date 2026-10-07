"""Download event entity of the PS5 integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_CANCELLED, EVENT_COMPLETED, EVENT_STARTED
from .coordinator import PS5ConfigEntry, StatusCoordinator
from .entity import PS5Entity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PS5ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PS5DownloadEvent(entry.runtime_data.status, entry)])


class PS5DownloadEvent(PS5Entity[StatusCoordinator], EventEntity):
    """Fires when a download starts, completes or is cancelled."""

    _attr_event_types = [EVENT_STARTED, EVENT_COMPLETED, EVENT_CANCELLED]

    def __init__(self, coordinator: StatusCoordinator, entry: PS5ConfigEntry) -> None:
        super().__init__(coordinator, entry, "download")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_event_listener(self._handle_download_event))

    @callback
    def _handle_download_event(self, event_type: str, data: dict[str, Any]) -> None:
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
