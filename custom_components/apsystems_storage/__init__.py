"""APsystems Storage (ELT-12 / ELS) over Modbus TCP."""
from __future__ import annotations

from modbus_connection import ModbusTcpParams

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant

from .const import CONF_UNIT, DEFAULT_SCAN_INTERVAL, PLATFORMS
from .coordinator import StorageCoordinator
from .hub import StorageHub

type StorageConfigEntry = ConfigEntry[StorageCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: StorageConfigEntry) -> bool:
    host, port = entry.data[CONF_HOST], entry.data[CONF_PORT]
    # Shared with any other integration on the same host and port; released by
    # the modbus integration when this entry unloads.
    unit = async_get_unit(hass, entry, ModbusTcpParams(host=host, port=port), entry.data[CONF_UNIT])
    hub = StorageHub(unit, host, port)
    scan = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = StorageCoordinator(hass, entry, hub, scan)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.start_keepalive()
    # Options changes reload through OptionsFlowWithReload: no update listener.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StorageConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        # The modbus integration closes the link behind its last holder, which
        # frees the ELT's single Modbus slot for other tools.
        entry.runtime_data.stop_keepalive()
    return ok
