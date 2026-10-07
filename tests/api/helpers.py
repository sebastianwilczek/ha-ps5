"""Helpers to build API objects on top of FakeSony."""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from custom_components.ps5.api.auth import AuthClient, Credentials, TokenManager
from custom_components.ps5.api.client import PsnClient
from custom_components.ps5.api.limiter import RateLimiter
from custom_components.ps5.api.transport import GraphQLTransport
from tests.common import CLIENT_DUID, NPSSO, REFRESH_TOKEN, FakeSony


@dataclass
class Clock:
    now: float = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Stack:
    fake: FakeSony
    limiter: RateLimiter
    auth: AuthClient
    tokens: TokenManager
    client: PsnClient
    mono: Clock
    stored: list[Credentials] = field(default_factory=list)


def build_stack(
    fake: FakeSony,
    *,
    refresh_expires_in: float = 8 * 24 * 3600,
    npsso_expires_at: float | None = None,
    console_name: str | None = "PS5",
) -> Stack:
    mono = Clock()
    limiter = RateLimiter(min_spacing=0, write_spacing=0)
    auth = AuthClient(fake, limiter)  # type: ignore[arg-type]
    stored: list[Credentials] = []
    tokens = TokenManager(
        auth,
        CLIENT_DUID,
        Credentials(
            npsso=NPSSO,
            npsso_expires_at=npsso_expires_at,
            refresh_token=REFRESH_TOKEN,
            refresh_token_expires_at=time.time() + refresh_expires_in,
        ),
        on_credentials_changed=stored.append,
    )
    transport = GraphQLTransport(fake, limiter)  # type: ignore[arg-type]
    client = PsnClient(transport, tokens, console_name, clock=mono)
    return Stack(fake, limiter, auth, tokens, client, mono, stored)
