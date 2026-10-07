"""Sensors of the PS5 integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    LibraryData,
    PS5ConfigEntry,
    StatusCoordinator,
    StatusData,
    resolve_platform,
    resolve_title_id,
)
from .entity import PS5Entity

PARALLEL_UPDATES = 0


def installed_titles(data: StatusData) -> list[dict[str, Any]]:
    return [
        {
            "name": title.name,
            "title_id": title.title_id,
            "platforms": list(title.platforms),
            "size_bytes": title.size_bytes,
            "image": title.image,
        }
        for title in sorted(data.console.installed, key=lambda t: (t.name.casefold(), t.title_id))
    ]


def queue_items(data: StatusData, library: LibraryData | None) -> list[dict[str, Any]]:
    items = []
    for item in data.queue:
        progress = data.progress.get(item.entitlement_id)
        items.append(
            {
                "entitlement_id": item.entitlement_id,
                "title_id": resolve_title_id(item.entitlement_id, library),
                "title": item.title,
                "platform": resolve_platform(item.entitlement_id, library),
                "queue_status": item.status,
                "progress_status": progress.status if progress else None,
                "percent": progress.percent if progress else None,
                "downloaded_bytes": progress.downloaded_bytes if progress else None,
                "total_bytes": progress.total_bytes if progress else None,
                "remaining_s": progress.remaining_s if progress else None,
                "image": item.image,
            }
        )
    return items


def _progress_attributes(data: StatusData, library: LibraryData | None) -> dict[str, Any]:
    if (active := data.active_item()) is None:
        return {
            "title": None,
            "entitlement_id": None,
            "downloaded_bytes": None,
            "total_bytes": None,
        }
    item, progress = active
    return {
        "title": item.title,
        "entitlement_id": item.entitlement_id,
        "downloaded_bytes": progress.downloaded_bytes,
        "total_bytes": progress.total_bytes,
    }


def _active_percent(data: StatusData) -> float | None:
    active = data.active_item()
    return active[1].percent if active else None


def _active_remaining(data: StatusData) -> int | None:
    active = data.active_item()
    return active[1].remaining_s if active else None


@dataclass(frozen=True, kw_only=True)
class PS5SensorEntityDescription(SensorEntityDescription):
    """Describes a PS5 sensor."""

    value_fn: Callable[[StatusData], Any]
    attributes_fn: Callable[[StatusData, LibraryData | None], dict[str, Any]] | None = None


SENSORS: tuple[PS5SensorEntityDescription, ...] = (
    PS5SensorEntityDescription(
        key="installed_titles",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.console.installed),
        attributes_fn=lambda data, _library: {"titles": installed_titles(data)},
    ),
    PS5SensorEntityDescription(
        key="storage_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.console.installed_bytes,
    ),
    PS5SensorEntityDescription(
        key="download_queue",
        value_fn=lambda data: len(data.queue),
        attributes_fn=lambda data, library: {"items": queue_items(data, library)},
    ),
    PS5SensorEntityDescription(
        key="download_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_active_percent,
        attributes_fn=_progress_attributes,
    ),
    PS5SensorEntityDescription(
        key="download_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=_active_remaining,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PS5ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    status = entry.runtime_data.status
    async_add_entities(
        SENSOR_CLASSES.get(description.key, PS5Sensor)(status, entry, description)
        for description in SENSORS
    )


class PS5Sensor(PS5Entity[StatusCoordinator], SensorEntity):
    """A sensor fed by the status coordinator."""

    entity_description: PS5SensorEntityDescription

    def __init__(
        self,
        coordinator: StatusCoordinator,
        entry: PS5ConfigEntry,
        description: PS5SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(
            self.coordinator.data, self.coordinator.library.data
        )


class PS5InstalledTitlesSensor(PS5Sensor):
    """Installed titles; the title list is not recorded."""

    _unrecorded_attributes = frozenset({"titles"})


class PS5DownloadQueueSensor(PS5Sensor):
    """Download queue; the item list is not recorded."""

    _unrecorded_attributes = frozenset({"items"})


SENSOR_CLASSES: dict[str, type[PS5Sensor]] = {
    "installed_titles": PS5InstalledTitlesSensor,
    "download_queue": PS5DownloadQueueSensor,
}
