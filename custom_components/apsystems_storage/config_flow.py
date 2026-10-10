"""Config flow: host/port/unit, identity check, phase count."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_KEEPALIVE,
    CONF_PHASES,
    CONF_UNIT,
    DEFAULT_KEEPALIVE,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNIT,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .hub import HubError, IllegalAddress, StorageHub
from .registers import REG_BY_KEY, decode, detect_model

def _box(minimum: int, maximum: int, unit: str | None = None) -> selector.NumberSelector:
    """Plain numeric input box (an int range would render as a slider)."""
    config = selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=1, mode=selector.NumberSelectorMode.BOX
    )
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


PHASE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=["1", "3"], translation_key="phases", mode=selector.SelectSelectorMode.LIST
    )
)


async def _identify(host: str, port: int, unit: int) -> dict[str, Any]:
    hub = StorageHub(host, port, unit)
    try:
        out: dict[str, Any] = {}
        for key in ("serial", "model"):
            reg = REG_BY_KEY[key]
            try:
                words = await hub.read(reg.address, reg.size)
            except IllegalAddress:
                continue  # model string is not implemented on every firmware
            out[key] = decode(reg, {reg.address + i: w for i, w in enumerate(words)})
        return out
    finally:
        await hub.close()


class StorageConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._title = ""
        self._default_phases = 3

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_PORT] = int(user_input[CONF_PORT])
            user_input[CONF_UNIT] = int(user_input[CONF_UNIT])
            try:
                ident = await _identify(
                    user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_UNIT]
                )
            except HubError:
                errors["base"] = "cannot_connect"
            else:
                serial = ident.get("serial")
                if not serial:
                    errors["base"] = "no_serial"
                else:
                    await self.async_set_unique_id(serial)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                    model, self._default_phases = detect_model(serial, ident.get("model"))
                    self._title = f"{model} ({serial[-4:]})"
                    self._data = dict(user_input)
                    return await self.async_step_phases()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): _box(1, 65535),
                vol.Required(CONF_UNIT, default=DEFAULT_UNIT): _box(0, 247),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_phases(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_PHASES] = int(user_input[CONF_PHASES])
            return self.async_create_entry(title=self._title, data=self._data)
        schema = vol.Schema(
            {vol.Required(CONF_PHASES, default=str(self._default_phases)): PHASE_SELECTOR}
        )
        return self.async_show_form(
            step_id="phases", data_schema=schema, description_placeholders={"model": self._title}
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return StorageOptionsFlow()


class StorageOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_PHASES] = int(user_input[CONF_PHASES])
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            user_input[CONF_KEEPALIVE] = int(user_input[CONF_KEEPALIVE])
            return self.async_create_entry(data=user_input)
        opts, data = self.config_entry.options, self.config_entry.data
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): _box(MIN_SCAN_INTERVAL, 300, "s"),
                vol.Required(
                    CONF_PHASES, default=str(opts.get(CONF_PHASES, data[CONF_PHASES]))
                ): PHASE_SELECTOR,
                vol.Required(
                    CONF_KEEPALIVE, default=opts.get(CONF_KEEPALIVE, DEFAULT_KEEPALIVE)
                ): _box(0, 3600, "s"),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
