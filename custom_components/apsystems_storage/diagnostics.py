"""Diagnostics: raw decoded values, scale factors and read strategy."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import StorageConfigEntry

REDACT = {CONF_HOST, "serial", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: StorageConfigEntry
) -> dict[str, Any]:
    c = entry.runtime_data
    data = dict(c.data or {})
    return {
        "entry": async_redact_data({**entry.as_dict()}, REDACT),
        "fragmented_blocks": c.hub.fragmented,
        "scale_factors_reported": {k: v for k, v in data.items() if k.startswith("sf_")},
        "values": async_redact_data(data, REDACT),
    }
