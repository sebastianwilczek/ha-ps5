"""Binary sensor of the PS5 integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PS5ConfigEntry, StatusCoordinator
from .entity import PS5Entity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PS5ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PS5DownloadingBinarySensor(entry.runtime_data.status, entry)])


class PS5DownloadingBinarySensor(PS5Entity[StatusCoordinator], BinarySensorEntity):
    """On while the download queue is not empty."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: StatusCoordinator, entry: PS5ConfigEntry) -> None:
        super().__init__(coordinator, entry, "downloading")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.queue)
