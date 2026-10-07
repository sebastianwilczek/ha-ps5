"""Tests for the rate limiter."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.ps5.api.errors import RateLimited
from custom_components.ps5.api.limiter import RateLimiter


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(**kwargs) -> tuple[RateLimiter, FakeTime]:
    t = FakeTime()
    return RateLimiter(clock=t.clock, sleep=t.sleep, **kwargs), t


async def test_min_spacing() -> None:
    limiter, t = make()
    stamps = []
    for _ in range(3):
        await limiter.acquire()
        stamps.append(t.now)
    assert stamps == [0.0, 2.0, 4.0]


async def test_write_spacing() -> None:
    limiter, t = make()
    await limiter.acquire(write=True)
    await limiter.acquire()  # reads only need 2 s
    assert t.now == 2.0
    await limiter.acquire(write=True)  # writes 10 s apart
    assert t.now == 10.0


async def test_hourly_cap() -> None:
    limiter, t = make(min_spacing=0)
    for _ in range(150):
        await limiter.acquire()
    assert t.now == 0.0
    await limiter.acquire()
    assert t.now == 3600.0
    assert limiter.snapshot()["requests_last_hour"] == 1


async def test_queues_in_order() -> None:
    limiter, t = make()
    order: list[int] = []

    async def worker(i: int) -> None:
        await limiter.acquire()
        order.append(i)

    await asyncio.gather(*(worker(i) for i in range(4)))
    assert order == [0, 1, 2, 3]
    assert t.now == 6.0


async def test_pause() -> None:
    limiter, t = make()
    limiter.pause(900)
    with pytest.raises(RateLimited) as err:
        await limiter.acquire()
    assert err.value.retry_after == 900
    t.now += 900
    await limiter.acquire()
    assert limiter.snapshot()["paused_for_s"] == 0
