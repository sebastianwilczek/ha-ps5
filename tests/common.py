"""Shared test helpers: fixtures and a fake Sony session (no network)."""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from multidict import CIMultiDict

from custom_components.ps5.api.const import AUTHZ_BASE, GRAPHQL_URL

FIXTURES = Path(__file__).parent / "fixtures"

ACCOUNT_ID = "1234567890123456789"
CONSOLE_DUID = (
    "U2FsdGVkX1+FAKEconsoleA/"
    "0000000000000000000000000000000000000000000000000000000000000000000000000="
)
CLIENT_DUID = "0000000700090100" + "a" * 64
NPSSO = "N" * 64
REFRESH_TOKEN = "fake-refresh-token-0000"
AUTH_CODE = "v3.FAKECODE"

EID_DISPATCH = "HP7386-PPSA27158_00-0022337374597692"
EID_ETHAN = "EP1210-CUSA03048_00-ETHANCARTERPS4EU"
EID_JAZZPUNK = "EP0459-CUSA05228_00-JAZZPUNK00000001"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_jwt(claims: dict[str, Any]) -> str:
    def enc(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{enc({'alg': 'RS256', 'typ': 'JWT'})}.{enc(claims)}.c2lnbmF0dXJl"


def token_response(
    account_id: str = ACCOUNT_ID,
    refresh_token: str = REFRESH_TOKEN,
    refresh_expires_in: int = 863999,
    now: float | None = None,
    **extra_claims: Any,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    access = make_jwt(
        {
            "account_id": account_id,
            "device_type": "WEB APPS",
            "exp": int(now) + 3600,
            "iat": int(now),
            "grant_type": "authorization_code",
            **extra_claims,
        }
    )
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": 3599,
        "scope": "psn:mobile.v2.core psn:clientapp",
        "id_token": make_jwt({"sub": "x"}),
        "refresh_token": refresh_token,
        "refresh_token_expires_in": refresh_expires_in,
    }


def generated_title(index: int, platform: str = "PS5") -> dict[str, Any]:
    template = load_fixture("purchased_game_list_page0.json")["data"]["purchasedTitlesRetrieve"][
        "games"
    ][0]
    title = copy.deepcopy(template)
    prefix = "PPSA" if platform == "PS5" else "CUSA"
    title_id = f"{prefix}9{index:04d}_00"
    title["entitlementId"] = f"EP0000-{title_id}-GEN{index:013d}"
    title["productId"] = title["entitlementId"]
    title["titleId"] = title_id
    title["name"] = f"Game {index:03d}"
    title["platform"] = platform
    return title


def library_pages(total: int = 218, page_size: int = 24) -> list[dict[str, Any]]:
    """Build a paged library: the 3 real entries first, then generated ones."""
    first = load_fixture("purchased_game_list_page0.json")
    games = first["data"]["purchasedTitlesRetrieve"]["games"]
    games = games + [generated_title(i) for i in range(total - len(games))]
    pages = []
    for offset in range(0, total, page_size):
        chunk = games[offset : offset + page_size]
        is_last = offset + page_size >= total
        pages.append(
            {
                "data": {
                    "purchasedTitlesRetrieve": {
                        "__typename": "GameList",
                        "games": chunk,
                        "pageInfo": {
                            "__typename": "PageInfo",
                            "isLast": is_last,
                            "offset": offset,
                            "size": len(chunk),
                            "totalCount": total,
                        },
                    }
                }
            }
        )
    return pages


def queue_response(*items: dict[str, Any]) -> dict[str, Any]:
    return {
        "data": {
            "remoteDownloadStatusesRetrieve": {
                "__typename": "RemoteDownloadStatuses",
                "downloadStatuses": list(items),
            }
        }
    }


def queue_item(entitlement_id: str, title: str, status: str = "NOTSTARTED") -> dict[str, Any]:
    return {
        "__typename": "RemoteDownloadItemStatus",
        "entitlementId": entitlement_id,
        "media": {"__typename": "Media", "type": "IMAGE", "url": "https://example.invalid/i.png"},
        "platform": "PS5",
        "reasonCode": None,
        "status": status,
        "title": title,
    }


def progress_response(
    status: str = "transferring",
    downloaded: int | None = 50,
    total: int | None = 100,
    remaining: int | None = 30,
) -> dict[str, Any]:
    return {
        "data": {
            "downloadProgressRetrieve": {
                "__typename": "CanDownloadProgressDetail",
                "commandId": "00000000-0000-0000-0000-000000000000",
                "downloadedSize": downloaded,
                "playableSize": total,
                "pollInterval": 10,
                "remainingTime": remaining,
                "status": status,
                "totalSize": total,
            }
        }
    }


class FakeResponse:
    """Minimal stand-in for aiohttp.ClientResponse."""

    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        headers: dict[str, str] | list[tuple[str, str]] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = CIMultiDict(headers or {})

    async def json(self, content_type: Any = None) -> Any:
        if self._body is None:
            raise ValueError("no body")
        if isinstance(self._body, str):
            return json.loads(self._body)
        return copy.deepcopy(self._body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class _RaisingContext:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> Any:
        raise self._exc

    async def __aexit__(self, *args: Any) -> None:
        return None


@dataclass
class RecordedRequest:
    key: str
    method: str
    url: str
    headers: dict[str, str]
    data: str | None
    kwargs: dict[str, Any]

    @property
    def variables(self) -> dict[str, Any]:
        if self.method == "GET":
            return json.loads(parse_qs(urlsplit(self.url).query)["variables"][0])
        assert self.data is not None
        return json.loads(self.data)["variables"]


def authorize_redirect(code: str | None = AUTH_CODE, set_cookie: str | None = None) -> FakeResponse:
    location = "com.scee.psxandroid.scecompcall://redirect/"
    if code is not None:
        location += f"?code={code}&cid=00000000-0000-0000-0000-000000000000"
    headers: list[tuple[str, str]] = [("Location", location)]
    if set_cookie is not None:
        headers.append(("Set-Cookie", set_cookie))
    return FakeResponse(302, None, headers)


Spec = Any  # FakeResponse | dict | BaseException | Callable[[RecordedRequest], Spec]


class FakeSony:
    """A fake aiohttp session answering like Sony, recording every request."""

    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self._queued: dict[str, list[Spec]] = {}
        self.pages = library_pages()
        self.defaults: dict[str, Spec] = {
            "authorize": lambda req: authorize_redirect(),
            "token": lambda req: token_response(),
            "refresh": lambda req: token_response(),
            "getUserDevices": load_fixture("get_user_devices.json"),
            "getPurchasedGameList": self._library_page,
            "GetRemoteDownloadList": load_fixture("remote_download_list_empty.json"),
            "downloadProgress": load_fixture("download_progress_transferring.json"),
            "initiateDownload": load_fixture("initiate_download.json"),
            "remoteDownloadCancel": load_fixture("remote_download_cancel.json"),
        }
        self.closed = False

    def _library_page(self, req: RecordedRequest) -> dict[str, Any]:
        start = req.variables["start"]
        return self.pages[start // 24]

    def set(self, key: str, spec: Spec) -> None:
        self.defaults[key] = spec

    def queue(self, key: str, *specs: Spec) -> None:
        self._queued.setdefault(key, []).extend(specs)

    def calls(self, key: str | None = None) -> list[RecordedRequest]:
        return [r for r in self.requests if key is None or r.key == key]

    def ops(self) -> list[str]:
        return [r.key for r in self.requests]

    def reset(self) -> None:
        self.requests.clear()

    @staticmethod
    def _key(method: str, url: str, data: str | None) -> str:
        if url.startswith(f"{AUTHZ_BASE}/authorize"):
            return "authorize"
        if url.startswith(f"{AUTHZ_BASE}/token"):
            return "refresh" if data and "grant_type=refresh_token" in data else "token"
        if url.startswith(GRAPHQL_URL):
            if method == "GET":
                return parse_qs(urlsplit(url).query)["operationName"][0]
            assert data is not None
            return json.loads(data)["operationName"]
        raise AssertionError(f"Unexpected URL {url}")

    def request(
        self, method: str, url: Any, *, headers: Any = None, data: Any = None, **kwargs: Any
    ) -> Any:
        url_str = str(url)
        key = self._key(method, url_str, data)
        req = RecordedRequest(key, method, url_str, dict(headers or {}), data, kwargs)
        self.requests.append(req)
        queued = self._queued.get(key)
        spec = queued.pop(0) if queued else self.defaults[key]
        while callable(spec) and not isinstance(spec, (FakeResponse, BaseException)):
            spec = spec(req)
        if isinstance(spec, BaseException):
            return _RaisingContext(spec)
        if isinstance(spec, FakeResponse):
            return spec
        return FakeResponse(200, spec)

    def detach(self) -> None:
        self.closed = True

    async def close(self) -> None:
        self.closed = True
