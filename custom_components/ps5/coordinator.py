"""Data update coordinators for the PS5 integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.client import PsnClient
from .api.errors import (
    ConsoleNotFound,
    NpssoInvalid,
    PersistedQueryNotFound,
    PsnError,
    RateLimited,
)
from .api.limiter import RateLimiter
from .api.models import (
    Console,
    LibraryTitle,
    Progress,
    QueueItem,
    title_id_from_entitlement,
)
from .const import (
    ACTIVE_INTERVAL,
    DOMAIN,
    EVENT_CANCELLED,
    EVENT_COMPLETED,
    EVENT_STARTED,
    FAILURES_BEFORE_UNAVAILABLE,
    IDLE_INTERVAL,
    LIBRARY_INTERVAL,
    LIBRARY_RETRY_INTERVAL,
    LIBRARY_STORAGE_VERSION,
    MAX_BACKOFF,
    REFRESH_DELAY_AFTER_WRITE,
)
from .issues import (
    async_check_npsso_expiry,
    async_create_api_changed_issue,
    async_create_console_issue,
    async_delete_console_issue,
)

_LOGGER = logging.getLogger(__name__)

type PS5ConfigEntry = ConfigEntry[PS5RuntimeData]
type DownloadEventListener = Callable[[str, dict[str, Any]], None]


@dataclass
class PS5RuntimeData:
    """Runtime data of one config entry."""

    client: PsnClient
    limiter: RateLimiter
    status: StatusCoordinator
    library: LibraryCoordinator
    selected_entitlement_id: str | None = None


# --------------------------------------------------------------------------
# Library


@dataclass
class LibraryData:
    """The purchased library."""

    titles: list[LibraryTitle]
    fetched_at: datetime | None
    by_entitlement: dict[str, LibraryTitle] = field(init=False)

    def __post_init__(self) -> None:
        self.by_entitlement = {title.entitlement_id: title for title in self.titles}

    @property
    def downloadable(self) -> list[LibraryTitle]:
        return [title for title in self.titles if title.downloadable]

    def as_store(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "titles": [title.as_dict() for title in self.titles],
        }

    @classmethod
    def from_store(cls, raw: Any) -> LibraryData | None:
        if not isinstance(raw, dict):
            return None
        try:
            fetched_at = (
                dt_util.parse_datetime(raw["fetched_at"]) if raw.get("fetched_at") else None
            )
            titles = [LibraryTitle.from_dict(item) for item in raw["titles"]]
        except KeyError, TypeError, ValueError:
            return None
        return cls(titles=titles, fetched_at=fetched_at)


class LibraryCoordinator(DataUpdateCoordinator[LibraryData]):
    """Keeps the purchased library, cached across restarts."""

    config_entry: PS5ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: PsnClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} library",
            update_interval=LIBRARY_INTERVAL,
        )
        self.client = client
        self._store: Store[dict[str, Any]] = Store(
            hass, LIBRARY_STORAGE_VERSION, library_storage_key(entry.entry_id)
        )
        self.last_success: datetime | None = None

    async def async_initialize(self) -> None:
        """Load the cache; refresh now only if it is missing or 12 h old."""
        cached = LibraryData.from_store(await self._store.async_load())
        self.data = cached or LibraryData(titles=[], fetched_at=None)
        age = dt_util.utcnow() - cached.fetched_at if cached and cached.fetched_at else None
        if age is not None and timedelta(0) <= age < LIBRARY_INTERVAL:
            self.update_interval = LIBRARY_INTERVAL - age
            return
        self.config_entry.async_create_background_task(
            self.hass, self.async_refresh(), f"{DOMAIN} library refresh"
        )

    async def _async_update_data(self) -> LibraryData:
        try:
            titles = await self.client.get_library()
        except NpssoInvalid as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PsnError as err:
            if isinstance(err, PersistedQueryNotFound):
                async_create_api_changed_issue(self.hass, err.operation)
            _LOGGER.warning("Library refresh failed, keeping the previous data: %s", err)
            interval = LIBRARY_RETRY_INTERVAL
            if isinstance(err, RateLimited):
                interval = max(interval, timedelta(seconds=err.retry_after))
            self.update_interval = interval
            return self.data
        data = LibraryData(titles=titles, fetched_at=dt_util.utcnow())
        await self._store.async_save(data.as_store())
        self.update_interval = LIBRARY_INTERVAL
        self.last_success = dt_util.utcnow()
        return data

    async def async_remove_cache(self) -> None:
        await self._store.async_remove()


def library_storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.library.{entry_id}"


# --------------------------------------------------------------------------
# Status


@dataclass
class StatusData:
    """Console, installed titles and download queue."""

    console: Console
    queue: list[QueueItem]
    progress: dict[str, Progress]
    last_progress: dict[str, Progress]

    @property
    def installed_title_ids(self) -> set[str]:
        return self.console.installed_title_ids

    def active_item(self) -> tuple[QueueItem, Progress] | None:
        """Return the first item (queue order) whose progress is `transferring`."""
        for item in self.queue:
            progress = self.progress.get(item.entitlement_id)
            if progress is not None and progress.is_transferring:
                return item, progress
        return None


def resolve_title_id(entitlement_id: str, library: LibraryData | None) -> str | None:
    """Return a queue item's titleId: library first, else from the entitlementId."""
    title = library.by_entitlement.get(entitlement_id) if library is not None else None
    if title is not None and title.title_id:
        return title.title_id
    return title_id_from_entitlement(entitlement_id)


def resolve_platform(entitlement_id: str, library: LibraryData | None) -> str | None:
    if library is not None and (title := library.by_entitlement.get(entitlement_id)):
        return title.platform
    return None


class StatusCoordinator(DataUpdateCoordinator[StatusData]):
    """Polls the console and the download queue (idle 10 min, active 60 s)."""

    config_entry: PS5ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PsnClient,
        library: LibraryCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} status",
            update_interval=IDLE_INTERVAL,
        )
        self.client = client
        self.library = library
        self.failures = 0
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self._console: Console | None = None
        self._tracked: dict[str, QueueItem] | None = None
        self._last_progress: dict[str, Progress] = {}
        self._event_listeners: list[DownloadEventListener] = []
        self._cancel_delayed_refresh: CALLBACK_TYPE | None = None
        self._rate_limit_logged = False

    # -- events -----------------------------------------------------------

    @callback
    def async_add_event_listener(self, listener: DownloadEventListener) -> CALLBACK_TYPE:
        self._event_listeners.append(listener)

        @callback
        def remove() -> None:
            self._event_listeners.remove(listener)

        return remove

    @callback
    def _fire(self, event_type: str, item: QueueItem) -> None:
        library = self.library.data
        title = library.by_entitlement.get(item.entitlement_id) if library else None
        data = {
            "entitlement_id": item.entitlement_id,
            "title_id": resolve_title_id(item.entitlement_id, library),
            "title": item.title or (title.name if title else None),
            "platform": resolve_platform(item.entitlement_id, library),
        }
        _LOGGER.debug("Download %s: %s", event_type, item.entitlement_id)
        for listener in list(self._event_listeners):
            listener(event_type, data)

    # -- refresh after writes ---------------------------------------------

    @callback
    def async_schedule_refresh_after_write(self) -> None:
        """Refresh 5 s after a successful write."""
        if self._cancel_delayed_refresh is not None:
            self._cancel_delayed_refresh()
        self._cancel_delayed_refresh = async_call_later(
            self.hass,
            REFRESH_DELAY_AFTER_WRITE,
            HassJob(self._async_delayed_refresh, cancel_on_shutdown=True),
        )

    async def _async_delayed_refresh(self, _now: datetime) -> None:
        self._cancel_delayed_refresh = None
        await self.async_refresh()

    async def async_shutdown(self) -> None:
        if self._cancel_delayed_refresh is not None:
            self._cancel_delayed_refresh()
            self._cancel_delayed_refresh = None
        await super().async_shutdown()

    # -- update -----------------------------------------------------------

    async def _async_update_data(self) -> StatusData:
        try:
            data, events = await self._async_fetch()
        except NpssoInvalid as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ConsoleNotFound as err:
            async_create_console_issue(self.hass, self.config_entry)
            self.last_error = type(err).__name__
            raise UpdateFailed(str(err)) from err
        except PsnError as err:
            return self._handle_failure(err)

        self.failures = 0
        self.last_error = None
        self._rate_limit_logged = False
        self.last_success = dt_util.utcnow()
        self.update_interval = ACTIVE_INTERVAL if data.queue else IDLE_INTERVAL
        async_delete_console_issue(self.hass, self.config_entry)
        async_check_npsso_expiry(self.hass, self.config_entry)
        for event_type, item in events:
            self._fire(event_type, item)
        return data

    def _normal_interval(self) -> timedelta:
        return ACTIVE_INTERVAL if self.data is not None and self.data.queue else IDLE_INTERVAL

    def _handle_failure(self, err: PsnError) -> StatusData:
        self.failures += 1
        self.last_error = type(err).__name__
        backoff = min(MAX_BACKOFF, timedelta(minutes=2 ** (self.failures - 1)))
        interval = max(self._normal_interval(), backoff)
        if isinstance(err, PersistedQueryNotFound):
            async_create_api_changed_issue(self.hass, err.operation)
        if isinstance(err, RateLimited):
            interval = max(interval, timedelta(seconds=err.retry_after))
            if not self._rate_limit_logged:
                self._rate_limit_logged = True
                _LOGGER.warning(
                    "Sony rate limited the integration; pausing for %.0f s", err.retry_after
                )
        self.update_interval = interval
        if self.data is None or self.failures >= FAILURES_BEFORE_UNAVAILABLE:
            raise UpdateFailed(f"{type(err).__name__}: {err}") from err
        _LOGGER.debug("Update failed (%s), keeping the last data: %s", self.failures, err)
        return self.data

    async def _async_fetch(self) -> tuple[StatusData, list[tuple[str, QueueItem]]]:
        client = self.client
        console = self._console
        active = self.data is not None and bool(self.data.queue)
        devices_fetched = False
        if console is None or not active or not client.console_duid_fresh():
            console = await client.get_console()
            devices_fetched = True

        queue = await client.get_queue()
        current = {item.entitlement_id: item for item in queue}
        tracked = self._tracked
        disappeared = [] if tracked is None else [eid for eid in tracked if eid not in current]
        if disappeared and not devices_fetched:
            # Update installed titles to tell completed from cancelled.
            console = await client.get_console()

        progress: dict[str, Progress] = {}
        for item in queue:
            progress[item.entitlement_id] = await client.get_progress(item.entitlement_id)

        # Everything was fetched: commit the new state.
        last_progress: dict[str, Progress] = {}
        for eid, new in progress.items():
            previous = self._last_progress.get(eid)
            # Keep the last reading with sizes when a reading without sizes follows.
            last_progress[eid] = (
                previous
                if new.total_bytes is None
                and previous is not None
                and previous.total_bytes is not None
                else new
            )

        events: list[tuple[str, QueueItem]] = []
        if tracked is not None:
            events.extend(
                (EVENT_STARTED, item) for eid, item in current.items() if eid not in tracked
            )
            installed = console.installed_title_ids
            for eid in disappeared:
                known = self._last_progress.get(eid)
                title_id = resolve_title_id(eid, self.library.data)
                completed = (known is not None and known.is_complete_by_size) or (
                    title_id is not None and title_id in installed
                )
                events.append((EVENT_COMPLETED if completed else EVENT_CANCELLED, tracked[eid]))

        self._console = console
        self._tracked = current
        self._last_progress = last_progress
        return (
            StatusData(
                console=console,
                queue=queue,
                progress=progress,
                last_progress=dict(last_progress),
            ),
            events,
        )
