"""GraphQL transport: persisted-query GET/POST and error mapping."""

from __future__ import annotations

from email.utils import parsedate_to_datetime
import logging
import time
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    APOLLO_CLIENT_NAME,
    APOLLO_CLIENT_VERSION,
    DEFAULT_RETRY_AFTER,
    GRAPHQL_URL,
    REQUEST_TIMEOUT,
)
from .encoding import build_graphql_get_url, build_graphql_post_body
from .errors import (
    ApiError,
    AuthExpired,
    PersistedQueryNotFound,
    RateLimited,
    TransientError,
)
from .limiter import RateLimiter
from .redact import redact_obj

_LOGGER = logging.getLogger(__name__)

PQNF_MARKERS = ("PersistedQueryNotFound", "PERSISTED_QUERY_NOT_FOUND")


def parse_retry_after(value: str | None) -> float:
    """Parse a Retry-After header (seconds or HTTP date); default 900 s."""
    if not value:
        return float(DEFAULT_RETRY_AFTER)
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except TypeError, ValueError:
            return float(DEFAULT_RETRY_AFTER)
    return seconds if seconds > 0 else float(DEFAULT_RETRY_AFTER)


def graphql_headers(access_token: str) -> dict[str, str]:
    """Return the exact header set of every GraphQL request."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "apollographql-client-name": APOLLO_CLIENT_NAME,
        "apollographql-client-version": APOLLO_CLIENT_VERSION,
    }


def _is_persisted_query_not_found(error: Any) -> bool:
    if not isinstance(error, dict):
        return False
    candidates = [error.get("message")]
    extensions = error.get("extensions")
    if isinstance(extensions, dict):
        candidates.append(extensions.get("code"))
    return any(
        isinstance(text, str) and any(marker in text for marker in PQNF_MARKERS)
        for text in candidates
    )


class GraphQLTransport:
    """Sends persisted GraphQL operations."""

    def __init__(self, session: aiohttp.ClientSession, limiter: RateLimiter) -> None:
        self._session = session
        self._limiter = limiter
        self._seen_error_shapes: set[str] = set()

    async def execute(
        self,
        operation: str,
        sha256_hash: str,
        variables: dict[str, Any],
        *,
        write: bool,
        access_token: str,
    ) -> dict[str, Any]:
        """Send one request and return its `data` object."""
        await self._limiter.acquire(write=write)
        headers = graphql_headers(access_token)
        if write:
            method, url = "POST", GRAPHQL_URL
            body: str | None = build_graphql_post_body(operation, variables, sha256_hash)
        else:
            method, url = "GET", build_graphql_get_url(operation, variables, sha256_hash)
            body = None
        try:
            async with self._session.request(
                method,
                URL(url, encoded=True),
                headers=headers,
                data=body,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                retry_after = resp.headers.get("Retry-After")
                payload: Any = None
                if status == 200 or 400 <= status < 500:
                    try:
                        payload = await resp.json(content_type=None)
                    except ValueError:
                        payload = None
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TransientError(f"{operation}: {type(err).__name__}") from err

        if status == 401:
            raise AuthExpired(operation)
        if status == 429:
            delay = parse_retry_after(retry_after)
            self._limiter.pause(delay)
            raise RateLimited(delay)
        if status >= 500:
            raise TransientError(f"{operation}: HTTP {status}")

        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            if not isinstance(errors, list):
                errors = [errors]
            self._log_error_shape(operation, status, errors)
            if any(_is_persisted_query_not_found(error) for error in errors):
                raise PersistedQueryNotFound(operation)
            raise ApiError(f"{operation}: GraphQL errors", operation, redact_obj(errors))
        if status != 200:
            raise ApiError(f"{operation}: HTTP {status}", operation)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ApiError(f"{operation}: response without data", operation)
        return data

    def _log_error_shape(self, operation: str, status: int, errors: list[Any]) -> None:
        redacted = redact_obj(errors)
        signature = f"{operation}:{status}:" + ",".join(
            sorted(
                str(
                    (e.get("extensions") or {}).get("code")
                    if isinstance(e, dict) and isinstance(e.get("extensions"), dict)
                    else None
                )
                + "/"
                + str(e.get("message") if isinstance(e, dict) else e)
                for e in redacted
            )
        )
        if signature in self._seen_error_shapes:
            return
        self._seen_error_shapes.add(signature)
        _LOGGER.warning(
            "First occurrence of this error from %s (HTTP %s): %s",
            operation,
            status,
            redacted,
        )
