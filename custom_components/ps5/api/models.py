"""Data models parsed from Sony's responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import PS4_PLACEHOLDER_DUID

PROGRESS_TRANSFERRING = "transferring"
PROGRESS_RETRIEVING = "retrieving"
PROGRESS_PLAYABLE = "playable"
KNOWN_PROGRESS_STATUSES = {PROGRESS_TRANSFERRING, PROGRESS_RETRIEVING, PROGRESS_PLAYABLE}


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _media_url(value: Any) -> str | None:
    if isinstance(value, dict):
        return _str_or_none(value.get("url"))
    return None


def title_id_from_entitlement(entitlement_id: str) -> str | None:
    """Return the titleId part of an entitlementId (`<prefix>-<titleId>-<label>`)."""
    parts = entitlement_id.split("-")
    return parts[1] if len(parts) > 1 and parts[1] else None


@dataclass(frozen=True, slots=True)
class InstalledTitle:
    """A title installed on the console (games and apps alike)."""

    title_id: str
    name: str
    platforms: tuple[str, ...]
    size_bytes: int | None
    image: str | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> InstalledTitle | None:
        title_id = _str_or_none(raw.get("titleId"))
        if not title_id:
            return None
        size = raw.get("installationSize")
        platforms = raw.get("targetPlatforms")
        return cls(
            title_id=title_id,
            name=_str_or_none(raw.get("name")) or title_id,
            platforms=tuple(p for p in platforms if isinstance(p, str))
            if isinstance(platforms, list)
            else (),
            size_bytes=_int_or_none(size.get("bytes")) if isinstance(size, dict) else None,
            image=_media_url(raw.get("media")),
        )


@dataclass(frozen=True, slots=True)
class Console:
    """A console from getUserDevices."""

    name: str
    platform: str | None
    duid: str
    has_storage_details: bool
    installed: tuple[InstalledTitle, ...]

    @property
    def is_supported_ps5(self) -> bool:
        """Return True if this entry is a real PS5 (console filter)."""
        return (
            self.platform == "PS5"
            and self.duid != PS4_PLACEHOLDER_DUID
            and self.has_storage_details
        )

    @property
    def installed_title_ids(self) -> set[str]:
        return {title.title_id for title in self.installed}

    @property
    def installed_bytes(self) -> int:
        return sum(title.size_bytes or 0 for title in self.installed)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Console | None:
        duid = _str_or_none(raw.get("duid"))
        if not duid:
            return None
        details = raw.get("deviceStorageDetails")
        installed: list[InstalledTitle] = []
        if isinstance(details, dict):
            for game in details.get("installedGames") or []:
                if isinstance(game, dict) and (title := InstalledTitle.from_api(game)):
                    installed.append(title)
        platform = _str_or_none(raw.get("devicePlatform"))
        return cls(
            # A console without a name is shown (and selected) by its platform.
            name=_str_or_none(raw.get("deviceName")) or platform or "Console",
            platform=platform,
            duid=duid,
            has_storage_details=details is not None,
            installed=tuple(installed),
        )

    def __repr__(self) -> str:
        # Never expose the duid in reprs (logs, tracebacks).
        return (
            f"Console(name={self.name!r}, platform={self.platform!r}, "
            f"installed={len(self.installed)})"
        )


@dataclass(frozen=True, slots=True)
class LibraryTitle:
    """A purchased title."""

    entitlement_id: str
    title_id: str | None
    name: str
    platform: str | None
    image: str | None
    is_downloadable: bool
    is_pre_order: bool
    is_active: bool

    @property
    def downloadable(self) -> bool:
        """Return True if this title can be sent to the console."""
        return self.is_downloadable and not self.is_pre_order and self.platform in ("PS4", "PS5")

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> LibraryTitle | None:
        entitlement_id = _str_or_none(raw.get("entitlementId"))
        if not entitlement_id:
            return None
        return cls(
            entitlement_id=entitlement_id,
            title_id=_str_or_none(raw.get("titleId")) or title_id_from_entitlement(entitlement_id),
            name=_str_or_none(raw.get("name")) or entitlement_id,
            platform=_str_or_none(raw.get("platform")),
            image=_media_url(raw.get("image")),
            is_downloadable=raw.get("isDownloadable") is True,
            is_pre_order=raw.get("isPreOrder") is True,
            is_active=raw.get("isActive") is True,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "entitlement_id": self.entitlement_id,
            "title_id": self.title_id,
            "name": self.name,
            "platform": self.platform,
            "image": self.image,
            "is_downloadable": self.is_downloadable,
            "is_pre_order": self.is_pre_order,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LibraryTitle:
        return cls(
            entitlement_id=raw["entitlement_id"],
            title_id=raw.get("title_id"),
            name=raw.get("name") or raw["entitlement_id"],
            platform=raw.get("platform"),
            image=raw.get("image"),
            is_downloadable=bool(raw.get("is_downloadable")),
            is_pre_order=bool(raw.get("is_pre_order")),
            is_active=bool(raw.get("is_active")),
        )


@dataclass(frozen=True, slots=True)
class LibraryPage:
    """One page of getPurchasedGameList."""

    titles: tuple[LibraryTitle, ...]
    is_last: bool
    total_count: int | None


@dataclass(frozen=True, slots=True)
class QueueItem:
    """An item of the remote download queue."""

    entitlement_id: str
    title: str | None
    platform: str | None
    status: str | None
    reason_code: str | None
    image: str | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> QueueItem | None:
        entitlement_id = _str_or_none(raw.get("entitlementId"))
        if not entitlement_id:
            return None
        reason = raw.get("reasonCode")
        return cls(
            entitlement_id=entitlement_id,
            title=_str_or_none(raw.get("title")),
            platform=_str_or_none(raw.get("platform")),
            status=_str_or_none(raw.get("status")),
            reason_code=None if reason is None else str(reason),
            image=_media_url(raw.get("media")),
        )


@dataclass(frozen=True, slots=True)
class Progress:
    """Download progress of one queue item."""

    status: str | None
    downloaded_bytes: int | None
    total_bytes: int | None
    playable_bytes: int | None
    remaining_s: int | None
    poll_interval_s: int | None

    @property
    def normalized_status(self) -> str | None:
        return self.status.lower() if self.status else None

    @property
    def is_transferring(self) -> bool:
        return self.normalized_status == PROGRESS_TRANSFERRING

    @property
    def is_complete_by_size(self) -> bool:
        """Return True if all bytes have been downloaded."""
        return (
            self.downloaded_bytes is not None
            and self.total_bytes is not None
            and self.downloaded_bytes == self.total_bytes
        )

    @property
    def percent(self) -> float | None:
        if self.downloaded_bytes is None or not self.total_bytes:
            return None
        return round(self.downloaded_bytes / self.total_bytes * 100, 1)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Progress:
        return cls(
            status=_str_or_none(raw.get("status")),
            downloaded_bytes=_int_or_none(raw.get("downloadedSize")),
            total_bytes=_int_or_none(raw.get("totalSize")),
            playable_bytes=_int_or_none(raw.get("playableSize")),
            remaining_s=_int_or_none(raw.get("remainingTime")),
            poll_interval_s=_int_or_none(raw.get("pollInterval")),
        )
