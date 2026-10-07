"""The PS5 integration: library and remote downloads via PlayStation's web API."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api.auth import AuthClient, Credentials, TokenManager
from .api.client import PsnClient
from .api.limiter import RateLimiter
from .api.transport import GraphQLTransport
from .const import (
    CONF_ACCOUNT_ID,
    CONF_CLIENT_DUID,
    CONF_CONSOLE_NAME,
    CONF_NPSSO,
    CONF_NPSSO_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN_EXPIRES_AT,
    DOMAIN,
    LIBRARY_STORAGE_VERSION,
)
from .coordinator import (
    LibraryCoordinator,
    PS5ConfigEntry,
    PS5RuntimeData,
    StatusCoordinator,
    library_storage_key,
)
from .issues import async_check_npsso_expiry
from .services import async_setup_services
from .util import async_create_psn_session

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""
    async_setup_services(hass)
    return True


def credentials_from_entry(entry: ConfigEntry) -> Credentials:
    return Credentials(
        npsso=entry.data[CONF_NPSSO],
        npsso_expires_at=entry.data.get(CONF_NPSSO_EXPIRES_AT),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        refresh_token_expires_at=entry.data.get(CONF_REFRESH_TOKEN_EXPIRES_AT),
    )


@callback
def async_store_credentials(
    hass: HomeAssistant, entry: ConfigEntry, credentials: Credentials
) -> None:
    """Persist credentials that changed (silent re-auth, NPSSO rotation)."""
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_NPSSO: credentials.npsso,
            CONF_NPSSO_EXPIRES_AT: credentials.npsso_expires_at,
            CONF_REFRESH_TOKEN: credentials.refresh_token,
            CONF_REFRESH_TOKEN_EXPIRES_AT: credentials.refresh_token_expires_at,
        },
    )


async def async_setup_entry(hass: HomeAssistant, entry: PS5ConfigEntry) -> bool:
    """Set up PS5 from a config entry."""
    session = async_create_psn_session(hass)
    limiter = RateLimiter()
    tokens = TokenManager(
        AuthClient(session, limiter),
        entry.data[CONF_CLIENT_DUID],
        credentials_from_entry(entry),
        on_credentials_changed=lambda creds: async_store_credentials(hass, entry, creds),
    )
    tokens.account_id = entry.data[CONF_ACCOUNT_ID]
    client = PsnClient(GraphQLTransport(session, limiter), tokens, entry.data[CONF_CONSOLE_NAME])
    library = LibraryCoordinator(hass, entry, client)
    status = StatusCoordinator(hass, entry, client, library)
    entry.runtime_data = PS5RuntimeData(
        client=client, limiter=limiter, status=status, library=library
    )

    await status.async_config_entry_first_refresh()
    await library.async_initialize()
    async_check_npsso_expiry(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PS5ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the library cache with the entry."""
    await Store(hass, LIBRARY_STORAGE_VERSION, library_storage_key(entry.entry_id)).async_remove()
