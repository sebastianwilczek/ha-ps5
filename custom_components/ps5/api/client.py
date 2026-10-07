"""High-level PlayStation client for one account and one console."""

from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import Any

from .auth import TokenManager
from .const import (
    CONSOLE_DUID_MAX_AGE,
    LIBRARY_MAX_PAGES,
    LIBRARY_PAGE_SIZE,
    OP_DOWNLOAD_PROGRESS,
    OP_GET_USER_DEVICES,
    OP_INITIATE_DOWNLOAD,
    OP_PURCHASED_GAME_LIST,
    OP_REMOTE_DOWNLOAD_CANCEL,
    OP_REMOTE_DOWNLOAD_LIST,
)
from .errors import AuthExpired, ConsoleNotFound, PersistedQueryNotFound, PsnError
from .models import (
    KNOWN_PROGRESS_STATUSES,
    Console,
    LibraryPage,
    LibraryTitle,
    Progress,
    QueueItem,
)
from .operations import OPERATIONS, Operation
from .transport import GraphQLTransport

_LOGGER = logging.getLogger(__name__)


def _monotonic() -> float:
    return time.monotonic()


class PsnClient:
    """Typed calls with the 401 rule, disabled operations and duid caching."""

    def __init__(
        self,
        transport: GraphQLTransport,
        tokens: TokenManager,
        console_name: str | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._transport = transport
        self._tokens = tokens
        self.console_name = console_name
        self._clock = clock or _monotonic
        self._console_duid: str | None = None
        self._console_duid_at: float | None = None
        self._seen_unknown_statuses: set[str] = set()
        self.disabled_operations: set[str] = set()
        self.last_errors: dict[str, str] = {}

    # -- plumbing ---------------------------------------------------------

    async def _call(self, name: str, *args: Any) -> Any:
        op = OPERATIONS[name]
        if name in self.disabled_operations:
            raise PersistedQueryNotFound(name)
        variables = op.build_variables(*args)
        try:
            data = await self._execute_with_auth(op, variables)
            result = op.parse(data)
        except PersistedQueryNotFound:
            self.disabled_operations.add(name)
            self.last_errors[name] = PersistedQueryNotFound.__name__
            raise
        except PsnError as err:
            self.last_errors[name] = type(err).__name__
            raise
        self.last_errors.pop(name, None)
        return result

    async def _execute_with_auth(self, op: Operation, variables: dict[str, Any]) -> dict[str, Any]:
        token = await self._tokens.async_get_access_token()
        try:
            return await self._execute(op, variables, token)
        except AuthExpired:
            _LOGGER.debug("%s answered 401, refreshing the access token", op.name)
            token = await self._tokens.async_refresh_after_rejection(token)
        try:
            return await self._execute(op, variables, token)
        except AuthExpired:
            # Rejected twice: re-authenticate for the next call, never retry again.
            _LOGGER.debug("%s answered 401 twice, re-authenticating", op.name)
            await self._tokens.async_reauthenticate()
            raise

    async def _execute(
        self, op: Operation, variables: dict[str, Any], token: str
    ) -> dict[str, Any]:
        return await self._transport.execute(
            op.name, op.sha256_hash, variables, write=op.write, access_token=token
        )

    # -- consoles ---------------------------------------------------------

    def console_duid_age(self) -> float | None:
        """Seconds since the cached console duid was fetched, or None."""
        if self._console_duid_at is None:
            return None
        return self._clock() - self._console_duid_at

    def console_duid_fresh(self) -> bool:
        age = self.console_duid_age()
        return self._console_duid is not None and age is not None and age < CONSOLE_DUID_MAX_AGE

    async def get_consoles(self) -> list[Console]:
        """Return the account's PS5 consoles (console filter applied)."""
        consoles = [c for c in await self._call(OP_GET_USER_DEVICES) if c.is_supported_ps5]
        if self.console_name is not None:
            for console in consoles:
                if console.name == self.console_name:
                    self._console_duid = console.duid
                    self._console_duid_at = self._clock()
                    break
        return consoles

    async def get_console(self) -> Console:
        """Return the selected console, refreshing its duid."""
        for console in await self.get_consoles():
            if console.name == self.console_name:
                return console
        self._console_duid = None
        self._console_duid_at = None
        raise ConsoleNotFound(self.console_name or "")

    async def ensure_console_duid(self) -> str:
        """Return a console duid that is less than 9 minutes old."""
        if not self.console_duid_fresh():
            await self.get_console()
        assert self._console_duid is not None
        return self._console_duid

    # -- reads ------------------------------------------------------------

    async def get_library_page(self, start: int) -> LibraryPage:
        return await self._call(OP_PURCHASED_GAME_LIST, start)

    async def get_library(self) -> list[LibraryTitle]:
        """Return the whole purchased library."""
        titles: list[LibraryTitle] = []
        for page_number in range(LIBRARY_MAX_PAGES):
            page = await self.get_library_page(page_number * LIBRARY_PAGE_SIZE)
            titles.extend(page.titles)
            if page.is_last:
                return titles
        _LOGGER.warning(
            "Library paging stopped after %s pages without reaching the last page",
            LIBRARY_MAX_PAGES,
        )
        return titles

    async def get_queue(self) -> list[QueueItem]:
        duid = await self.ensure_console_duid()
        return await self._call(OP_REMOTE_DOWNLOAD_LIST, duid)

    async def get_progress(self, entitlement_id: str) -> Progress:
        duid = await self.ensure_console_duid()
        progress: Progress = await self._call(OP_DOWNLOAD_PROGRESS, duid, entitlement_id)
        status = progress.normalized_status
        if (
            status is not None
            and status not in KNOWN_PROGRESS_STATUSES
            and status not in self._seen_unknown_statuses
        ):
            # Unknown statuses are kept raw and treated as pending.
            self._seen_unknown_statuses.add(status)
            _LOGGER.debug("Unknown download progress status: %s", progress.status)
        return progress

    # -- writes (never retried, except once after a 401) -------------------

    async def start_download(self, entitlement_id: str) -> None:
        duid = await self.ensure_console_duid()
        await self._call(OP_INITIATE_DOWNLOAD, duid, entitlement_id)

    async def cancel_download(self, entitlement_id: str) -> None:
        duid = await self.ensure_console_duid()
        await self._call(OP_REMOTE_DOWNLOAD_CANCEL, duid, entitlement_id)
