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
from .registers import entry_title, is_placeholder_title

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
    # Entries made before 0.1.2 are named after the model register, which the
    # ELT-12 fills with the placeholder "Model". The serial prefix tells the
    # real model; the device takes the entry title as its name.
    serial = entry.unique_id or ""
    if serial and is_placeholder_title(entry.title, serial):
        hass.config_entries.async_update_entry(
            entry, title=entry_title(serial, (coordinator.data or {}).get("model"))
        )
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
