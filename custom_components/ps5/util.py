"""Helpers shared by the integration modules."""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession


def async_create_psn_session(
    hass: HomeAssistant, *, auto_cleanup: bool = True
) -> aiohttp.ClientSession:
    """Create a session that never stores cookies (no Sony cookie persists)."""
    return async_create_clientsession(
        hass, auto_cleanup=auto_cleanup, cookie_jar=aiohttp.DummyCookieJar()
    )
