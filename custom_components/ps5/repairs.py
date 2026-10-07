"""Repair flows for the PS5 integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
import voluptuous as vol


class NpssoExpiringRepairFlow(RepairsFlow):
    """Start re-authentication to enter a new NPSSO before the old one expires."""

    def __init__(self, entry_id: str | None) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            if self._entry_id and (
                entry := self.hass.config_entries.async_get_entry(self._entry_id)
            ):
                entry.async_start_reauth(self.hass)
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """Create the fix flow of a fixable issue."""
    entry_id = data.get("entry_id") if data else None
    return NpssoExpiringRepairFlow(str(entry_id) if entry_id else None)
