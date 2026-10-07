"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Generator
from functools import partial
import time
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ps5.api.limiter import RateLimiter
from custom_components.ps5.const import (
    CONF_ACCOUNT_ID,
    CONF_CLIENT_DUID,
    CONF_CONSOLE_NAME,
    CONF_NPSSO,
    CONF_NPSSO_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN_EXPIRES_AT,
    DOMAIN,
)
from tests.common import ACCOUNT_ID, CLIENT_DUID, NPSSO, REFRESH_TOKEN, FakeSony

FastLimiter = partial(RateLimiter, min_spacing=0, write_spacing=0)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in all tests."""


@pytest.fixture
def fake_sony() -> FakeSony:
    return FakeSony()


@pytest.fixture
def mock_sony(fake_sony: FakeSony) -> Generator[FakeSony]:
    """Route every session the integration creates to the fake (no network)."""
    with (
        patch(
            "custom_components.ps5.util.async_create_clientsession",
            return_value=fake_sony,
        ) as create,
        patch("custom_components.ps5.RateLimiter", FastLimiter),
        patch("custom_components.ps5.config_flow.RateLimiter", FastLimiter),
    ):
        fake_sony.create_session = create  # type: ignore[attr-defined]
        yield fake_sony


def entry_data(**overrides: Any) -> dict[str, Any]:
    data = {
        CONF_CLIENT_DUID: CLIENT_DUID,
        CONF_NPSSO: NPSSO,
        CONF_NPSSO_EXPIRES_AT: time.time() + 50 * 24 * 3600,
        CONF_REFRESH_TOKEN: REFRESH_TOKEN,
        CONF_REFRESH_TOKEN_EXPIRES_AT: time.time() + 8 * 24 * 3600,
        CONF_ACCOUNT_ID: ACCOUNT_ID,
        CONF_CONSOLE_NAME: "PS5",
    }
    data.update(overrides)
    return data


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="PS5",
        unique_id=ACCOUNT_ID,
        data=entry_data(),
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_sony: FakeSony
) -> MockConfigEntry:
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
