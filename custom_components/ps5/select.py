"""Game selection for the download button."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoredExtraData, RestoreEntity

from .api.models import LibraryTitle
from .const import SIGNAL_SELECTION_CHANGED
from .coordinator import LibraryCoordinator, PS5ConfigEntry
from .entity import PS5Entity

PARALLEL_UPDATES = 0


def build_options(titles: list[LibraryTitle]) -> dict[str, str]:
    """Return {option label: entitlementId} for downloadable titles, sorted by name."""
    base = {title.entitlement_id: f"{title.name} ({title.platform})" for title in titles}
    counts = Counter(base.values())
    labeled = []
    for title in titles:
        label = base[title.entitlement_id]
        if counts[label] > 1:
            label = f"{label} [{title.entitlement_id[-6:]}]"
        labeled.append((title.name.casefold(), label, title.entitlement_id))
    labeled.sort()
    return {label: entitlement_id for _name, label, entitlement_id in labeled}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PS5ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PS5GameSelect(entry.runtime_data.library, entry)])


class PS5GameSelect(PS5Entity[LibraryCoordinator], SelectEntity, RestoreEntity):
    """Choose a downloadable game; selecting only stores the choice."""

    def __init__(self, coordinator: LibraryCoordinator, entry: PS5ConfigEntry) -> None:
        super().__init__(coordinator, entry, "game")
        self._options: dict[str, str] = {}
        self._update_options()

    @callback
    def _update_options(self) -> None:
        self._options = build_options(self.coordinator.data.downloadable)
        self._attr_options = list(self._options)

    @property
    def _selected(self) -> str | None:
        return self._entry.runtime_data.selected_entitlement_id

    def _set_selected(self, entitlement_id: str | None) -> None:
        self._entry.runtime_data.selected_entitlement_id = entitlement_id
        async_dispatcher_send(self.hass, SIGNAL_SELECTION_CHANGED.format(self._entry.entry_id))

    @property
    def current_option(self) -> str | None:
        selected = self._selected
        if selected is None:
            return None
        for label, entitlement_id in self._options.items():
            if entitlement_id == selected:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        self._set_selected(self._options[option])
        self.async_write_ha_state()

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return RestoredExtraData({"entitlement_id": self._selected})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_extra_data()) is not None:
            restored: Any = last.as_dict().get("entitlement_id")
            if isinstance(restored, str) and self._selected is None:
                self._set_selected(restored)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_options()
        super()._handle_coordinator_update()
