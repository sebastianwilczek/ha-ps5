"""Golden request tests: what Sony receives must match byte for byte."""

from __future__ import annotations

import json

from aiohttp import ClientSession, DummyCookieJar, web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.ps5.api import encoding, transport as transport_mod
from custom_components.ps5.api.auth import AuthClient
from custom_components.ps5.api.const import HASHES
from custom_components.ps5.api.encoding import (
    build_authorize_url,
    build_graphql_get_url,
    build_graphql_post_body,
    build_refresh_body,
    build_token_body,
)
from custom_components.ps5.api.limiter import RateLimiter
from custom_components.ps5.api.operations import OPERATIONS
from custom_components.ps5.api.transport import GraphQLTransport
from tests.common import CLIENT_DUID, CONSOLE_DUID, EID_DISPATCH, EID_ETHAN, EID_JAZZPUNK

URL_GET_USER_DEVICES = "https://web.np.playstation.com/api/graphql/v1/op?operationName=getUserDevices&variables=%7B%7D&extensions=%7B%22persistedQuery%22%3A%7B%22version%22%3A1%2C%22sha256Hash%22%3A%22e19583f91ba05e86572d7dc6e4a0121a75d2bc23c9fa63c3c62d1eb815769756%22%7D%7D"
URL_PURCHASED_24 = "https://web.np.playstation.com/api/graphql/v1/op?operationName=getPurchasedGameList&variables=%7B%22isActive%22%3Atrue%2C%22platform%22%3A%5B%22ps4%22%2C%22ps5%22%5D%2C%22size%22%3A24%2C%22start%22%3A24%2C%22sortBy%22%3A%22ACTIVE_DATE%22%2C%22sortDirection%22%3A%22desc%22%7D&extensions=%7B%22persistedQuery%22%3A%7B%22version%22%3A1%2C%22sha256Hash%22%3A%22827a423f6a8ddca4107ac01395af2ec0eafd8396fc7fa204aaf9b7ed2eefa168%22%7D%7D"
URL_QUEUE = "https://web.np.playstation.com/api/graphql/v1/op?operationName=GetRemoteDownloadList&variables=%7B%22duid%22%3A%22U2FsdGVkX1%2BFAKEconsoleA%2F0000000000000000000000000000000000000000000000000000000000000000000000000%3D%22%2C%22statusTypes%22%3A%5B%22NOTSTARTED%22%2C%22STARTED%22%2C%22STOPPED%22%2C%22WAITFORDOWNLOAD%22%5D%2C%22platform%22%3A%22PS5%22%7D&extensions=%7B%22persistedQuery%22%3A%7B%22version%22%3A1%2C%22sha256Hash%22%3A%2269d2bd08d18da3efe2902d9a97a760d436469e89551486f5985f746a026e0038%22%7D%7D"
URL_PROGRESS = "https://web.np.playstation.com/api/graphql/v1/op?operationName=downloadProgress&variables=%7B%22duid%22%3A%22U2FsdGVkX1%2BFAKEconsoleA%2F0000000000000000000000000000000000000000000000000000000000000000000000000%3D%22%2C%22entitlementId%22%3A%22EP0459-CUSA05228_00-JAZZPUNK00000001%22%7D&extensions=%7B%22persistedQuery%22%3A%7B%22version%22%3A1%2C%22sha256Hash%22%3A%22309442955add106c4539030e34b10be8618ecb93283b411918a11657288bee52%22%7D%7D"

BODY_INITIATE = (
    '{"operationName":"initiateDownload","variables":{"scheduleRequests":{"downloadSchedules":'
    '[{"duid":"' + CONSOLE_DUID + '","entitlementId":"HP7386-PPSA27158_00-0022337374597692",'
    '"platform":"PS5"}]}},"extensions":{"persistedQuery":{"version":1,"sha256Hash":'
    '"cddb5a81da705dadf59a9ea0d791fe81d7c9631ad6a34b1a8b592d61412abb39"}}}'
)
BODY_CANCEL = (
    '{"operationName":"remoteDownloadCancel","variables":{"updateRequests":{"downloadUpdates":'
    '[{"duid":"' + CONSOLE_DUID + '","entitlementId":"EP1210-CUSA03048_00-ETHANCARTERPS4EU",'
    '"platform":"PS5","reasonCode":"1","status":"USERCANCELLED"}]}},"extensions":'
    '{"persistedQuery":{"version":1,"sha256Hash":'
    '"b50f425df4b073ed2032b61d12c742f1b0ca5671591a66d897a1cc9aac821670"}}}'
)

AUTHORIZE_URL = (
    "https://ca.account.sony.com/api/authz/v3/oauth/authorize?access_type=offline"
    "&client_id=09515159-7237-4370-9b40-3806e67c0891"
    "&redirect_uri=com.scee.psxandroid.scecompcall%3A%2F%2Fredirect"
    "&response_type=code&scope=psn%3Amobile.v2.core+psn%3Aclientapp"
    "&duid=0000000700090100" + "a" * 64
)
TOKEN_BODY = (
    "code=v3.ABC%2B%2F%3D&redirect_uri=com.scee.psxandroid.scecompcall%3A%2F%2Fredirect"
    "&grant_type=authorization_code&token_format=jwt&duid=0000000700090100" + "a" * 64
)
REFRESH_BODY = (
    "refresh_token=rt%2B%2F%3D+x&grant_type=refresh_token&token_format=jwt"
    "&scope=psn%3Amobile.v2.core+psn%3Aclientapp&duid=0000000700090100" + "a" * 64
)

EXPECTED_HEADERS = {
    "Authorization": "Bearer AT",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "apollographql-client-name": "my-playstation",
    "apollographql-client-version": "0.62.0-20260821140605-1-g30007c97",
}


def _url(name: str, *args: object) -> str:
    op = OPERATIONS[name]
    return build_graphql_get_url(name, op.build_variables(*args), HASHES[name])


def _body(name: str, *args: object) -> str:
    op = OPERATIONS[name]
    return build_graphql_post_body(name, op.build_variables(*args), HASHES[name])


def test_read_urls() -> None:
    assert _url("getUserDevices") == URL_GET_USER_DEVICES
    assert _url("getPurchasedGameList", 24) == URL_PURCHASED_24
    assert _url("GetRemoteDownloadList", CONSOLE_DUID) == URL_QUEUE
    assert _url("downloadProgress", CONSOLE_DUID, EID_JAZZPUNK) == URL_PROGRESS


def test_write_bodies() -> None:
    assert _body("initiateDownload", CONSOLE_DUID, EID_DISPATCH) == BODY_INITIATE
    assert _body("remoteDownloadCancel", CONSOLE_DUID, EID_ETHAN) == BODY_CANCEL
    # The bodies are exactly the compact JSON of the documented objects.
    assert json.dumps(json.loads(BODY_INITIATE), separators=(",", ":")) == BODY_INITIATE


def test_auth_requests() -> None:
    assert build_authorize_url(CLIENT_DUID) == AUTHORIZE_URL
    assert build_token_body("v3.ABC+/=", CLIENT_DUID) == TOKEN_BODY
    assert build_refresh_body("rt+/= x", CLIENT_DUID) == REFRESH_BODY


class _Captured(Exception):
    """Raised by the capture middleware to stop before any connection."""


@pytest.mark.parametrize(
    ("name", "args", "write", "expected"),
    [
        ("getUserDevices", (), False, URL_GET_USER_DEVICES),
        ("getPurchasedGameList", (24,), False, URL_PURCHASED_24),
        ("GetRemoteDownloadList", (CONSOLE_DUID,), False, URL_QUEUE),
        ("downloadProgress", (CONSOLE_DUID, EID_JAZZPUNK), False, URL_PROGRESS),
        ("initiateDownload", (CONSOLE_DUID, EID_DISPATCH), True, BODY_INITIATE),
        ("remoteDownloadCancel", (CONSOLE_DUID, EID_ETHAN), True, BODY_CANCEL),
    ],
)
async def test_aiohttp_request_is_not_reencoded(
    name: str, args: tuple, write: bool, expected: str
) -> None:
    """The URL, headers and body aiohttp builds equal what we built."""
    captured = {}

    async def capture(request, handler):
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["path_qs"] = request.url.raw_path_qs
        captured["headers"] = dict(request.headers)
        raise _Captured

    async with ClientSession(cookie_jar=DummyCookieJar(), middlewares=(capture,)) as session:
        transport = GraphQLTransport(session, RateLimiter(min_spacing=0, write_spacing=0))
        op = OPERATIONS[name]
        with pytest.raises(_Captured):
            await transport.execute(
                name, op.sha256_hash, op.build_variables(*args), write=write, access_token="AT"
            )

    for key, value in EXPECTED_HEADERS.items():
        assert captured["headers"][key] == value
    sony_specific = {
        k for k in captured["headers"] if k.lower().startswith(("x-psn", "sec-", "apollo"))
    }
    assert sony_specific == {"apollographql-client-name", "apollographql-client-version"}
    assert not {"origin", "referer"} & {k.lower() for k in captured["headers"]}
    if write:
        assert captured["method"] == "POST"
        assert captured["url"] == "https://web.np.playstation.com/api/graphql/v1/op"
    else:
        assert captured["method"] == "GET"
        assert captured["url"] == expected
        assert captured["path_qs"] == expected.removeprefix("https://web.np.playstation.com")


async def test_wire_bytes_on_local_server(
    monkeypatch: pytest.MonkeyPatch, socket_enabled: None
) -> None:
    """A real aiohttp round trip to a loopback server: raw request line, headers, body."""
    received = []

    async def handler(request: web.Request) -> web.Response:
        received.append(
            {
                "method": request.method,
                "raw_path": request.raw_path,
                "headers": dict(request.headers),
                "body": await request.text(),
            }
        )
        if request.method == "POST":
            return web.json_response(
                {"data": {"remoteDownloadSchedule": [{"errorCode": None, "reasonCode": None}]}}
            )
        return web.json_response({"data": {"downloadProgressRetrieve": {"status": "playable"}}})

    app = web.Application()
    app.router.add_route("*", "/api/graphql/v1/op", handler)
    async with TestServer(app, host="127.0.0.1") as server:
        local = f"http://127.0.0.1:{server.port}/api/graphql/v1/op"
        monkeypatch.setattr(encoding, "GRAPHQL_URL", local)
        monkeypatch.setattr(transport_mod, "GRAPHQL_URL", local)
        async with ClientSession(cookie_jar=DummyCookieJar()) as session:
            transport = GraphQLTransport(session, RateLimiter(min_spacing=0, write_spacing=0))
            op = OPERATIONS["downloadProgress"]
            await transport.execute(
                op.name,
                op.sha256_hash,
                op.build_variables(CONSOLE_DUID, EID_JAZZPUNK),
                write=False,
                access_token="AT",
            )
            op = OPERATIONS["initiateDownload"]
            await transport.execute(
                op.name,
                op.sha256_hash,
                op.build_variables(CONSOLE_DUID, EID_DISPATCH),
                write=True,
                access_token="AT",
            )

    get, post = received
    assert get["method"] == "GET"
    assert get["raw_path"] == URL_PROGRESS.removeprefix("https://web.np.playstation.com")
    assert get["body"] == ""
    assert post["method"] == "POST"
    assert post["body"] == BODY_INITIATE
    for req in received:
        for key, value in EXPECTED_HEADERS.items():
            assert req["headers"][key] == value
        assert "Cookie" not in req["headers"]


async def test_auth_wire_requests() -> None:
    """The authorize request sends the exact URL and only the npsso cookie."""
    captured = []

    async def capture(request, handler):
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": request.body._value if request.body else None,
            }
        )
        raise _Captured

    async with ClientSession(cookie_jar=DummyCookieJar(), middlewares=(capture,)) as session:
        auth = AuthClient(session)
        with pytest.raises(_Captured):
            await auth.authorize("N" * 64, CLIENT_DUID)
        with pytest.raises(_Captured):
            await auth.refresh("rt+/= x", CLIENT_DUID)
        with pytest.raises(_Captured):
            await auth.exchange_code("v3.ABC+/=", CLIENT_DUID)

    authorize, refresh, token = captured
    assert authorize["method"] == "GET"
    assert authorize["url"] == AUTHORIZE_URL
    assert authorize["headers"]["Cookie"] == "npsso=" + "N" * 64
    for req, body in ((refresh, REFRESH_BODY), (token, TOKEN_BODY)):
        assert req["method"] == "POST"
        assert req["url"] == "https://ca.account.sony.com/api/authz/v3/oauth/token"
        assert req["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert req["headers"]["Authorization"].startswith("Basic MDk1MTUxNTkt")
        assert req["body"] == body.encode()
