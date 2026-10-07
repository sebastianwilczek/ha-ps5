"""Config flow for the PS5 integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import time
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api.auth import (
    AuthClient,
    Credentials,
    TokenManager,
    generate_client_duid,
    parse_npsso_input,
)
from .api.client import PsnClient
from .api.const import NPSSO_URL
from .api.errors import NpssoInvalid, PsnError, RateLimited, TransientError
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
)
from .entity import device_identifier
from .issues import async_delete_npsso_issue
from .util import async_create_psn_session

_LOGGER = logging.getLogger(__name__)

CONF_CONSOLE = "console"

NPSSO_SCHEMA = vol.Schema(
    {vol.Required(CONF_NPSSO): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


class FlowError(Exception):
    """An error shown in the form."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _console_schema(names: list[str], default: str | None = None) -> vol.Schema:
    key = (
        vol.Required(CONF_CONSOLE, default=default)
        if default in names
        else vol.Required(CONF_CONSOLE)
    )
    return vol.Schema(
        {key: SelectSelector(SelectSelectorConfig(options=names, mode=SelectSelectorMode.LIST))}
    )


def _has_duplicates(names: list[str]) -> bool:
    return len(set(names)) != len(names)


class PS5ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PS5."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._consoles: list[str] = []

    async def _async_login(
        self, raw_npsso: str, client_duid: str, *, fetch_consoles: bool
    ) -> tuple[dict[str, Any], list[str]]:
        """Authenticate with the NPSSO; optionally list the PS5 consoles."""
        try:
            npsso, npsso_expires_at = parse_npsso_input(raw_npsso, time.time())
        except NpssoInvalid as err:
            raise FlowError("invalid_npsso") from err
        session = async_create_psn_session(self.hass, auto_cleanup=False)
        limiter = RateLimiter()
        try:
            auth = AuthClient(session, limiter)
            result = await auth.login(npsso, client_duid)
            if result.rotated_npsso is not None:
                npsso, rotated_expiry = result.rotated_npsso
                npsso_expires_at = rotated_expiry or npsso_expires_at
            tokens = result.tokens
            names: list[str] = []
            if fetch_consoles:
                manager = TokenManager(
                    auth,
                    client_duid,
                    Credentials(
                        npsso, npsso_expires_at, tokens.refresh_token, tokens.refresh_expires_at
                    ),
                    tokens=tokens,
                )
                client = PsnClient(GraphQLTransport(session, limiter), manager)
                names = [console.name for console in await client.get_consoles()]
        except NpssoInvalid as err:
            raise FlowError("invalid_npsso") from err
        except (TransientError, RateLimited) as err:
            raise FlowError("cannot_connect") from err
        except PsnError as err:
            _LOGGER.warning("Unexpected answer from Sony: %s", err)
            raise FlowError("unknown") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error")
            raise FlowError("unknown") from err
        finally:
            session.detach()
        data = {
            CONF_CLIENT_DUID: client_duid,
            CONF_NPSSO: npsso,
            CONF_NPSSO_EXPIRES_AT: npsso_expires_at,
            CONF_REFRESH_TOKEN: tokens.refresh_token,
            CONF_REFRESH_TOKEN_EXPIRES_AT: tokens.refresh_expires_at,
            CONF_ACCOUNT_ID: tokens.account_id,
        }
        return data, names

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the NPSSO."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data, names = await self._async_login(
                    user_input[CONF_NPSSO], generate_client_duid(), fetch_consoles=True
                )
            except FlowError as err:
                errors["base"] = err.reason
            else:
                await self.async_set_unique_id(data[CONF_ACCOUNT_ID])
                self._abort_if_unique_id_configured()
                if not names:
                    errors["base"] = "no_ps5"
                elif _has_duplicates(names):
                    return self.async_abort(reason="duplicate_console_names")
                else:
                    self._data = data
                    self._consoles = names
                    if len(names) == 1:
                        return self._async_create(names[0])
                    return await self.async_step_select_console()
        return self.async_show_form(
            step_id="user",
            data_schema=NPSSO_SCHEMA,
            errors=errors,
            description_placeholders={"npsso_url": NPSSO_URL},
        )

    async def async_step_select_console(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the console when the account has several."""
        if user_input is not None:
            return self._async_create(user_input[CONF_CONSOLE])
        return self.async_show_form(
            step_id="select_console", data_schema=_console_schema(self._consoles)
        )

    @callback
    def _async_create(self, console_name: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title=console_name, data={**self._data, CONF_CONSOLE_NAME: console_name}
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new NPSSO (the client duid of the entry is kept)."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data, _ = await self._async_login(
                    user_input[CONF_NPSSO], entry.data[CONF_CLIENT_DUID], fetch_consoles=False
                )
            except FlowError as err:
                errors["base"] = err.reason
            else:
                await self.async_set_unique_id(data[CONF_ACCOUNT_ID])
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                async_delete_npsso_issue(self.hass, entry)
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=NPSSO_SCHEMA,
            errors=errors,
            description_placeholders={"npsso_url": NPSSO_URL, "console": entry.title},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the console again (e.g. after renaming it)."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            new_name = user_input[CONF_CONSOLE]
            old_name = entry.data[CONF_CONSOLE_NAME]
            if new_name != old_name:
                async_migrate_console_name(self.hass, entry, old_name, new_name)
            return self.async_update_reload_and_abort(
                entry, title=new_name, data_updates={CONF_CONSOLE_NAME: new_name}
            )
        try:
            names = await _async_entry_console_names(self.hass, entry)
        except NpssoInvalid:
            return self.async_abort(reason="reauth_required")
        except TransientError, RateLimited:
            return self.async_abort(reason="cannot_connect")
        except PsnError as err:
            _LOGGER.warning("Unexpected answer from Sony: %s", err)
            return self.async_abort(reason="unknown")
        if not names:
            return self.async_abort(reason="no_ps5")
        if _has_duplicates(names):
            return self.async_abort(reason="duplicate_console_names")
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_console_schema(names, entry.data[CONF_CONSOLE_NAME]),
            description_placeholders={"console": entry.data[CONF_CONSOLE_NAME]},
        )


async def _async_entry_console_names(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """List the consoles with the entry's current tokens."""
    if entry.state is ConfigEntryState.LOADED:
        consoles = await entry.runtime_data.client.get_consoles()
        return [console.name for console in consoles]
    # Not loaded (e.g. the console was not found at startup): use the stored tokens.
    from . import async_store_credentials, credentials_from_entry  # noqa: PLC0415

    session = async_create_psn_session(hass, auto_cleanup=False)
    try:
        limiter = RateLimiter()
        manager = TokenManager(
            AuthClient(session, limiter),
            entry.data[CONF_CLIENT_DUID],
            credentials_from_entry(entry),
            on_credentials_changed=lambda creds: async_store_credentials(hass, entry, creds),
        )
        manager.account_id = entry.data[CONF_ACCOUNT_ID]
        client = PsnClient(GraphQLTransport(session, limiter), manager)
        return [console.name for console in await client.get_consoles()]
    finally:
        session.detach()


@callback
def async_migrate_console_name(
    hass: HomeAssistant, entry: ConfigEntry, old_name: str, new_name: str
) -> None:
    """Move the device and entity unique IDs to the new console name."""
    account_id = entry.data[CONF_ACCOUNT_ID]
    old_id = device_identifier(account_id, old_name)
    new_id = device_identifier(account_id, new_name)
    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity.unique_id.startswith(f"{old_id}_"):
            ent_reg.async_update_entity(
                entity.entity_id,
                new_unique_id=new_id + entity.unique_id.removeprefix(old_id),
            )
    dev_reg = dr.async_get(hass)
    if device := dev_reg.async_get_device_by_identifier((DOMAIN, old_id), entry.entry_id):
        dev_reg.async_update_device(device.id, new_identifiers={(DOMAIN, new_id)})
