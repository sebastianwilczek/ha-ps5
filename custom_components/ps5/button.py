"""Buttons of the PS5 integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SIGNAL_SELECTION_CHANGED
from .coordinator import LibraryCoordinator, PS5ConfigEntry, StatusCoordinator
from .entity import PS5Entity
from .services import async_cancel_download, async_start_download

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PS5ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_add_entities(
        [
            PS5DownloadSelectedButton(runtime.library, entry),
            PS5CancelDownloadButton(runtime.status, entry),
            PS5RefreshLibraryButton(runtime.library, entry),
        ]
    )


class PS5DownloadSelectedButton(PS5Entity[LibraryCoordinator], ButtonEntity):
    """Starts the download of the game chosen in the select."""

    def __init__(self, coordinator: LibraryCoordinator, entry: PS5ConfigEntry) -> None:
        super().__init__(coordinator, entry, "download_selected")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SELECTION_CHANGED.format(self._entry.entry_id),
                self._handle_selection,
            )
        )

    @callback
    def _handle_selection(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        selected = self._entry.runtime_data.selected_entitlement_id
        return (
            super().available
            and selected is not None
            and selected in self.coordinator.data.by_entitlement
        )

    async def async_press(self) -> None:
        selected = self._entry.runtime_data.selected_entitlement_id
        if selected is not None:
            await async_start_download(self.hass, self._entry, selected)


class PS5CancelDownloadButton(PS5Entity[StatusCoordinator], ButtonEntity):
    """Cancels the transferring download, else the first queued one."""

    def __init__(self, coordinator: StatusCoordinator, entry: PS5ConfigEntry) -> None:
        super().__init__(coordinator, entry, "cancel_download")

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data.queue)

    async def async_press(self) -> None:
        data = self.coordinator.data
        active = data.active_item()
        target = active[0] if active else (data.queue[0] if data.queue else None)
        if target is not None:
            await async_cancel_download(self.hass, self._entry, target.entitlement_id)


class PS5RefreshLibraryButton(PS5Entity[LibraryCoordinator], ButtonEntity):
    """Refreshes the purchased library now."""

    def __init__(self, coordinator: LibraryCoordinator, entry: PS5ConfigEntry) -> None:
        super().__init__(coordinator, entry, "refresh_library")

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
