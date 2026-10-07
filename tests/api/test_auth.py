"""Tests for authentication helpers and the token manager."""

from __future__ import annotations

import asyncio
import re
import time

from freezegun.api import FrozenDateTimeFactory
import pytest

from custom_components.ps5.api.auth import (
    AuthClient,
    decode_jwt_payload,
    extract_code,
    generate_client_duid,
    parse_npsso_input,
    parse_rotated_npsso,
)
from custom_components.ps5.api.errors import (
    ApiError,
    AuthRejected,
    NpssoInvalid,
    RateLimited,
    TransientError,
)
from tests.api.helpers import build_stack
from tests.common import (
    ACCOUNT_ID,
    AUTH_CODE,
    CLIENT_DUID,
    NPSSO,
    REFRESH_TOKEN,
    FakeResponse,
    FakeSony,
    authorize_redirect,
    make_jwt,
    token_response,
)


def test_client_duid_format() -> None:
    duid = generate_client_duid()
    assert re.fullmatch(r"0000000700090100[0-9a-f]{64}", duid)
    assert generate_client_duid() != duid


def test_decode_jwt_payload() -> None:
    token = make_jwt({"account_id": ACCOUNT_ID, "exp": 123, "device_type": "WEB APPS"})
    assert decode_jwt_payload(token) == {
        "account_id": ACCOUNT_ID,
        "exp": 123,
        "device_type": "WEB APPS",
    }
    with pytest.raises(ApiError):
        decode_jwt_payload("not-a-jwt")


@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("com.scee.psxandroid.scecompcall://redirect/?code=v3.AbC&cid=x", "v3.AbC"),
        ("com.scee.psxandroid.scecompcall://redirect?cid=x&code=v3.X%2BY", "v3.X+Y"),
        ("https://example.invalid/cb#code=v3.frag", "v3.frag"),
        ("com.scee.psxandroid.scecompcall://redirect/?error=login_required", None),
        ("com.scee.psxandroid.scecompcall://redirect/?code=", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_code(location: str | None, code: str | None) -> None:
    assert extract_code(location) == code


def test_parse_npsso_input() -> None:
    assert parse_npsso_input(f"  {NPSSO}\n", 100.0) == (NPSSO, None)
    raw = '{"npsso":"' + NPSSO + '","expires_in":5183985}'
    assert parse_npsso_input(raw, 100.0) == (NPSSO, 100.0 + 5183985)
    for bad in ("short", NPSSO + "x", "{not json", '{"foo":1}', "N" * 32 + " " + "N" * 31):
        with pytest.raises(NpssoInvalid):
            parse_npsso_input(bad, 0)


def test_parse_rotated_npsso() -> None:
    new = "R" * 64
    assert parse_rotated_npsso([f"npsso={new}; Max-Age=100; Path=/"], 10.0) == (new, 110.0)
    assert parse_rotated_npsso(
        [f"npsso={new}; Expires=Thu, 01 Jan 1970 00:01:40 GMT; Secure"], 0
    ) == (new, 100.0)
    assert parse_rotated_npsso([f"npsso={new}; Path=/"], 0) == (new, None)
    assert parse_rotated_npsso(["other=1", "npsso=; Max-Age=0"], 0) is None
    assert parse_rotated_npsso([], 0) is None


async def test_authorize_and_token(fake_sony: FakeSony) -> None:
    auth = AuthClient(fake_sony)  # type: ignore[arg-type]
    result = await auth.login(NPSSO, CLIENT_DUID)
    assert result.tokens.account_id == ACCOUNT_ID
    assert result.tokens.refresh_token == REFRESH_TOKEN
    assert result.rotated_npsso is None
    authorize, token = fake_sony.requests
    assert authorize.headers == {"Cookie": f"npsso={NPSSO}"}
    assert authorize.kwargs["allow_redirects"] is False
    assert f"code={AUTH_CODE}" in token.data


async def test_authorize_without_code_is_npsso_invalid(fake_sony: FakeSony) -> None:
    fake_sony.queue("authorize", authorize_redirect(code=None))
    with pytest.raises(NpssoInvalid):
        await AuthClient(fake_sony).login(NPSSO, CLIENT_DUID)  # type: ignore[arg-type]
    fake_sony.queue("authorize", FakeResponse(400, {"error": "invalid_grant"}))
    with pytest.raises(NpssoInvalid):
        await AuthClient(fake_sony).login(NPSSO, CLIENT_DUID)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(503), TransientError),
        (TimeoutError(), TransientError),
        (FakeResponse(429, headers={"Retry-After": "60"}), RateLimited),
    ],
)
async def test_authorize_transient(fake_sony: FakeSony, response, error) -> None:
    fake_sony.queue("authorize", response)
    with pytest.raises(error):
        await AuthClient(fake_sony).authorize(NPSSO, CLIENT_DUID)  # type: ignore[arg-type]


async def test_rotated_npsso_is_persisted(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony, refresh_expires_in=3600)  # < 2 days: silent re-auth
    new = "R" * 64
    fake_sony.queue("authorize", authorize_redirect(set_cookie=f"npsso={new}; Max-Age=500"))
    await stack.tokens.async_get_access_token()
    assert stack.stored[-1].npsso == new
    assert stack.stored[-1].npsso_expires_at == pytest.approx(time.time() + 500, abs=5)


async def test_refresh_when_less_than_5_min(
    fake_sony: FakeSony, freezer: FrozenDateTimeFactory
) -> None:
    stack = build_stack(fake_sony)
    token = await stack.tokens.async_get_access_token()
    assert fake_sony.ops() == ["refresh"]
    assert "duid=" + CLIENT_DUID in fake_sony.requests[0].data
    assert await stack.tokens.async_get_access_token() == token
    freezer.tick(3600 - 301)
    assert fake_sony.ops() == ["refresh"]
    await stack.tokens.async_get_access_token()
    assert fake_sony.ops() == ["refresh"]
    freezer.tick(2)  # now < 5 min left
    await stack.tokens.async_get_access_token()
    assert fake_sony.ops() == ["refresh", "refresh"]
    # Same refresh token returned: nothing persisted.
    assert stack.stored == []


async def test_single_flight_refresh(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    tokens = await asyncio.gather(*(stack.tokens.async_get_access_token() for _ in range(5)))
    assert len(set(tokens)) == 1
    assert fake_sony.ops() == ["refresh"]


async def test_silent_reauth_when_refresh_token_expires_soon(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony, refresh_expires_in=2 * 24 * 3600 - 60)
    fake_sony.set("token", lambda req: token_response(refresh_token="new-rt"))
    await stack.tokens.async_get_access_token()
    assert fake_sony.ops() == ["authorize", "token"]
    assert stack.stored[-1].refresh_token == "new-rt"
    assert stack.stored[-1].refresh_token_expires_at == pytest.approx(time.time() + 863999, abs=5)
    assert stack.stored[-1].npsso == NPSSO


async def test_no_reauth_with_more_than_2_days(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony, refresh_expires_in=2 * 24 * 3600 + 60)
    await stack.tokens.async_get_access_token()
    assert fake_sony.ops() == ["refresh"]


@pytest.mark.parametrize("status", [400, 401])
async def test_rejected_refresh_triggers_silent_reauth(fake_sony: FakeSony, status: int) -> None:
    stack = build_stack(fake_sony)
    fake_sony.queue("refresh", FakeResponse(status, {"error": "invalid_grant"}))
    await stack.tokens.async_get_access_token()
    assert fake_sony.ops() == ["refresh", "authorize", "token"]
    assert len(stack.stored) == 1


async def test_reauth_with_invalid_npsso(fake_sony: FakeSony) -> None:
    stack = build_stack(fake_sony)
    fake_sony.queue("refresh", FakeResponse(400, {"error": "invalid_grant"}))
    fake_sony.queue("authorize", authorize_redirect(code=None))
    with pytest.raises(NpssoInvalid):
        await stack.tokens.async_get_access_token()


@pytest.mark.parametrize("response", [FakeResponse(502), TimeoutError()])
async def test_transient_refresh_errors(fake_sony: FakeSony, response) -> None:
    stack = build_stack(fake_sony)
    fake_sony.queue("refresh", response)
    with pytest.raises(TransientError):
        await stack.tokens.async_get_access_token()
    assert fake_sony.ops() == ["refresh"]


async def test_token_exchange_rejected_is_not_npsso_invalid(fake_sony: FakeSony) -> None:
    fake_sony.queue("token", FakeResponse(400, {"error": "invalid_grant"}))
    with pytest.raises(ApiError):
        await AuthClient(fake_sony).login(NPSSO, CLIENT_DUID)  # type: ignore[arg-type]
    fake_sony.queue("refresh", FakeResponse(401))
    with pytest.raises(AuthRejected):
        await AuthClient(fake_sony).refresh(REFRESH_TOKEN, CLIENT_DUID)  # type: ignore[arg-type]
