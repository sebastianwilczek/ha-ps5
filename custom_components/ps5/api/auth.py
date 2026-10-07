"""Authentication: NPSSO -> authorization code -> access/refresh tokens."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aiohttp
from yarl import URL

from .const import (
    ACCESS_TOKEN_REFRESH_MARGIN,
    BASIC_AUTH,
    CLIENT_DUID_PREFIX,
    NPSSO_LENGTH,
    REFRESH_TOKEN_REAUTH_MARGIN,
    REQUEST_TIMEOUT,
)
from .encoding import (
    build_authorize_url,
    build_refresh_body,
    build_token_body,
    build_token_url,
)
from .errors import (
    ApiError,
    AuthRejected,
    NpssoInvalid,
    PsnError,
    RateLimited,
    TransientError,
)
from .limiter import RateLimiter
from .transport import parse_retry_after

_LOGGER = logging.getLogger(__name__)

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


def _wall_clock() -> float:
    return time.time()


def generate_client_duid() -> str:
    """Return a new client duid (generated once per config entry)."""
    return CLIENT_DUID_PREFIX + secrets.token_hex(32)


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying its signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as err:
        raise ApiError("Malformed JWT") from err
    if not isinstance(decoded, dict):
        raise ApiError("Malformed JWT")
    return decoded


def extract_code(location: str | None) -> str | None:
    """Extract the `code` query parameter from a redirect Location."""
    if not location:
        return None
    parts = urlsplit(location)
    for query in (parts.query, parts.fragment):
        if codes := parse_qs(query).get("code"):
            return codes[0] or None
    return None


def parse_npsso_input(raw: str, now: float) -> tuple[str, float | None]:
    """Parse user input: a bare NPSSO or the JSON shown at NPSSO_URL.

    Returns (npsso, expires_at). Raises NpssoInvalid if the value is unusable.
    """
    text = raw.strip()
    expires_at: float | None = None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as err:
            raise NpssoInvalid("Malformed JSON") from err
        if not isinstance(data, dict) or not isinstance(data.get("npsso"), str):
            raise NpssoInvalid("JSON without npsso")
        text = data["npsso"].strip()
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
            expires_at = now + expires_in
    if len(text) != NPSSO_LENGTH or any(c.isspace() for c in text):
        raise NpssoInvalid("NPSSO must be 64 characters")
    return text, expires_at


def parse_rotated_npsso(set_cookies: list[str], now: float) -> tuple[str, float | None] | None:
    """Return a new (npsso, expires_at) if Sony set an npsso cookie."""
    for header in set_cookies:
        if not header.startswith("npsso="):
            continue
        parts = [part.strip() for part in header.split(";")]
        value = parts[0][len("npsso=") :]
        if len(value) != NPSSO_LENGTH:
            continue
        expires_at: float | None = None
        for attr in parts[1:]:
            name, _, attr_value = attr.partition("=")
            name = name.strip().lower()
            if name == "max-age":
                with suppress(ValueError):
                    expires_at = now + int(attr_value)
                break
            if name == "expires":
                with suppress(TypeError, ValueError):
                    expires_at = parsedate_to_datetime(attr_value).timestamp()
        return value, expires_at
    return None


@dataclass(frozen=True, slots=True)
class TokenSet:
    """Tokens returned by the token endpoint."""

    access_token: str
    access_expires_at: float
    refresh_token: str
    refresh_expires_at: float
    account_id: str

    def __repr__(self) -> str:
        return "TokenSet(**REDACTED**)"


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Result of authorize + token."""

    tokens: TokenSet
    rotated_npsso: tuple[str, float | None] | None


@dataclass(frozen=True, slots=True)
class Credentials:
    """Persisted credentials of one config entry."""

    npsso: str
    npsso_expires_at: float | None
    refresh_token: str | None
    refresh_token_expires_at: float | None

    def __repr__(self) -> str:
        return "Credentials(**REDACTED**)"


class AuthClient:
    """Low-level calls to Sony's authorization server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        limiter: RateLimiter | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._session = session
        self._limiter = limiter
        self._clock = clock or _wall_clock

    async def _acquire(self) -> None:
        if self._limiter is not None:
            await self._limiter.acquire()

    async def authorize(
        self, npsso: str, client_duid: str
    ) -> tuple[str, tuple[str, float | None] | None]:
        """Exchange the NPSSO for an authorization code."""
        await self._acquire()
        url = build_authorize_url(client_duid)
        try:
            async with self._session.request(
                "GET",
                URL(url, encoded=True),
                headers={"Cookie": f"npsso={npsso}"},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                location = resp.headers.get("Location")
                set_cookies = list(resp.headers.getall("Set-Cookie", []))
                retry_after = resp.headers.get("Retry-After")
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TransientError(f"authorize: {type(err).__name__}") from err
        if status == 429:
            self._pause(retry_after)
            raise RateLimited(parse_retry_after(retry_after))
        if status >= 500:
            raise TransientError(f"authorize: HTTP {status}")
        rotated = parse_rotated_npsso(set_cookies, self._clock())
        if rotated is not None:
            _LOGGER.debug("NPSSO rotated")
        code = extract_code(location)
        if code is None:
            raise NpssoInvalid("No authorization code returned")
        return code, rotated

    def _pause(self, retry_after: str | None) -> None:
        if self._limiter is not None:
            self._limiter.pause(parse_retry_after(retry_after))

    async def _token_request(self, body: str, what: str) -> TokenSet:
        await self._acquire()
        try:
            async with self._session.request(
                "POST",
                URL(build_token_url(), encoded=True),
                headers={"Content-Type": FORM_CONTENT_TYPE, "Authorization": BASIC_AUTH},
                data=body,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                retry_after = resp.headers.get("Retry-After")
                payload: Any = None
                if status == 200:
                    try:
                        payload = await resp.json(content_type=None)
                    except ValueError as err:
                        raise ApiError(f"{what}: invalid JSON") from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TransientError(f"{what}: {type(err).__name__}") from err
        if status in (400, 401):
            raise AuthRejected(status)
        if status == 429:
            self._pause(retry_after)
            raise RateLimited(parse_retry_after(retry_after))
        if status >= 500:
            raise TransientError(f"{what}: HTTP {status}")
        if status != 200:
            raise ApiError(f"{what}: HTTP {status}")
        return self._parse_tokens(payload, what)

    def _parse_tokens(self, payload: Any, what: str) -> TokenSet:
        if not isinstance(payload, dict):
            raise ApiError(f"{what}: unexpected response")
        try:
            access_token = payload["access_token"]
            refresh_token = payload["refresh_token"]
            expires_in = float(payload["expires_in"])
            refresh_expires_in = float(payload["refresh_token_expires_in"])
        except (KeyError, TypeError, ValueError) as err:
            raise ApiError(f"{what}: missing token fields") from err
        claims = decode_jwt_payload(access_token)
        account_id = claims.get("account_id")
        if account_id is None:
            raise ApiError(f"{what}: access token without account_id")
        now = self._clock()
        exp = claims.get("exp")
        access_expires_at = float(exp) if isinstance(exp, (int, float)) else now + expires_in
        return TokenSet(
            access_token=access_token,
            access_expires_at=access_expires_at,
            refresh_token=refresh_token,
            refresh_expires_at=now + refresh_expires_in,
            account_id=str(account_id),
        )

    async def exchange_code(self, code: str, client_duid: str) -> TokenSet:
        """Exchange an authorization code for tokens."""
        return await self._token_request(build_token_body(code, client_duid), "token")

    async def refresh(self, refresh_token: str, client_duid: str) -> TokenSet:
        """Get a new access token with the refresh token."""
        return await self._token_request(build_refresh_body(refresh_token, client_duid), "refresh")

    async def login(self, npsso: str, client_duid: str) -> LoginResult:
        """Authorize with the NPSSO and exchange the code for tokens."""
        code, rotated = await self.authorize(npsso, client_duid)
        try:
            tokens = await self.exchange_code(code, client_duid)
        except AuthRejected as err:
            # The code we just received was refused: not an NPSSO problem.
            raise ApiError(str(err)) from err
        return LoginResult(tokens=tokens, rotated_npsso=rotated)


class TokenManager:
    """Keeps a valid access token, refreshing and re-authenticating silently.

    Raises NpssoInvalid when the stored NPSSO no longer works; the caller maps
    that to a re-auth flow.
    """

    def __init__(
        self,
        auth: AuthClient,
        client_duid: str,
        credentials: Credentials,
        *,
        on_credentials_changed: Callable[[Credentials], None] | None = None,
        tokens: TokenSet | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._auth = auth
        self._client_duid = client_duid
        self._credentials = credentials
        self._on_changed = on_credentials_changed
        self._clock = clock or _wall_clock
        self._lock = asyncio.Lock()
        self._access_token: str | None = tokens.access_token if tokens else None
        self._access_expires_at: float = tokens.access_expires_at if tokens else 0.0
        self.account_id: str | None = tokens.account_id if tokens else None

    @property
    def credentials(self) -> Credentials:
        return self._credentials

    @property
    def access_expires_at(self) -> float:
        return self._access_expires_at

    def _access_valid(self) -> bool:
        return self._access_token is not None and (
            self._access_expires_at - self._clock() > ACCESS_TOKEN_REFRESH_MARGIN
        )

    async def async_get_access_token(self) -> str:
        """Return an access token with more than 5 minutes left."""
        if self._access_valid():
            assert self._access_token is not None
            return self._access_token
        async with self._lock:
            if self._access_valid():
                assert self._access_token is not None
                return self._access_token
            return await self._renew()

    async def async_refresh_after_rejection(self, rejected_token: str) -> str:
        """Get a new access token after `rejected_token` was answered with 401."""
        async with self._lock:
            if self._access_token is not None and self._access_token != rejected_token:
                # Someone else already renewed it.
                return self._access_token
            self._access_token = None
            return await self._renew()

    async def async_reauthenticate(self) -> str:
        """Run a silent re-auth with the stored NPSSO."""
        async with self._lock:
            return await self._silent_reauth()

    async def _renew(self) -> str:
        creds = self._credentials
        if (
            creds.refresh_token is None
            or creds.refresh_token_expires_at is None
            or creds.refresh_token_expires_at - self._clock() < REFRESH_TOKEN_REAUTH_MARGIN
        ):
            return await self._silent_reauth()
        try:
            tokens = await self._auth.refresh(creds.refresh_token, self._client_duid)
        except AuthRejected:
            _LOGGER.debug("Refresh rejected, re-authenticating with the NPSSO")
            return await self._silent_reauth()
        self._apply(tokens)
        if tokens.refresh_token != creds.refresh_token:
            self._store(
                replace(
                    creds,
                    refresh_token=tokens.refresh_token,
                    refresh_token_expires_at=tokens.refresh_expires_at,
                )
            )
        assert self._access_token is not None
        return self._access_token

    async def _silent_reauth(self) -> str:
        creds = self._credentials
        result = await self._auth.login(creds.npsso, self._client_duid)
        if self.account_id is not None and result.tokens.account_id != self.account_id:
            raise NpssoInvalid("NPSSO belongs to another account")
        self._apply(result.tokens)
        npsso, npsso_expires_at = creds.npsso, creds.npsso_expires_at
        if result.rotated_npsso is not None:
            npsso, rotated_expiry = result.rotated_npsso
            npsso_expires_at = rotated_expiry if rotated_expiry is not None else npsso_expires_at
        self._store(
            Credentials(
                npsso=npsso,
                npsso_expires_at=npsso_expires_at,
                refresh_token=result.tokens.refresh_token,
                refresh_token_expires_at=result.tokens.refresh_expires_at,
            )
        )
        assert self._access_token is not None
        return self._access_token

    def _apply(self, tokens: TokenSet) -> None:
        self._access_token = tokens.access_token
        self._access_expires_at = tokens.access_expires_at
        self.account_id = tokens.account_id

    def _store(self, credentials: Credentials) -> None:
        self._credentials = credentials
        if self._on_changed is not None:
            self._on_changed(credentials)


__all__ = [
    "AuthClient",
    "Credentials",
    "LoginResult",
    "PsnError",
    "TokenManager",
    "TokenSet",
    "decode_jwt_payload",
    "extract_code",
    "generate_client_duid",
    "parse_npsso_input",
    "parse_rotated_npsso",
]
