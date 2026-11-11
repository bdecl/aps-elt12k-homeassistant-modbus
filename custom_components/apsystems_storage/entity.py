"""Base entity."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_GROUPS,
    CONF_PHASES,
    DOMAIN,
    GROUP_AC,
    GROUP_DC,
    GROUP_DETAIL,
    GROUP_DIAG,
    GROUP_ENERGY,
    GROUP_GRID,
    LEGACY_GROUPS,
    MANUFACTURER,
)
from .coordinator import StorageCoordinator
from .registers import detect_model

ESSENTIAL = "essential"

# Entity key -> group. Phase entities share the group of their prefix.
_GROUP_OF: dict[str, str] = {
    **dict.fromkeys(
        ("soc", "battery_power", "charge_status", "set_power", "soc_reserve_min",
         "soc_reserve_max", "battery_alarm", "pcs_alarm"), ESSENTIAL),
    **dict.fromkeys(
        ("battery_voltage", "dc_bus_voltage", "dc_current", "battery_temperature",
         "pcs_temperature", "soh"), GROUP_DC),
    **dict.fromkeys(("active_power", "reactive_power"), GROUP_AC),
    "grid_power": GROUP_GRID,
    **dict.fromkeys(
        ("daily_charge_energy", "daily_discharge_energy", "total_charge_energy",
         "total_discharge_energy"), GROUP_ENERGY),
    **dict.fromkeys(("pv_energy_today", "net_energy_today"), GROUP_DETAIL),
}


def group_of(key: str) -> str:
    """Group of an entity key; anything not listed is diagnostic."""
    base = key[:-2] if key[-2:] in ("_a", "_b", "_c") else key
    return _GROUP_OF.get(base, GROUP_DIAG)


def entry_groups(entry) -> set[str]:
    """Selected groups plus the essential one. Entries made before the option
    existed keep every entity they had."""
    return {ESSENTIAL, *entry.options.get(CONF_GROUPS, LEGACY_GROUPS)}


def key_enabled(entry, key: str) -> bool:
    return group_of(key) in entry_groups(entry)

def entry_phases(entry) -> int:
    """Phase count: options override the value detected at setup."""
    return int(entry.options.get(CONF_PHASES, entry.data[CONF_PHASES]))


def phase_allowed(key: str, phases: int) -> bool:
    """Phase B/C entities only exist on three-phase units."""
    if key.endswith("_b") or key.endswith("_c"):
        return phases == 3
    return True


class StorageEntity(CoordinatorEntity[StorageCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: StorageCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        serial = entry.unique_id
        data = coordinator.data or {}
        model, _ = detect_model(data.get("serial"), data.get("model"))
        self._key = key
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=entry.title,
            manufacturer=data.get("manufacturer") or MANUFACTURER,
            model=model,
            serial_number=serial,
            sw_version=data.get("chip1_version") or data.get("version"),
            hw_version=data.get("version"),
        )

    @property
    def phases(self) -> int:
        return entry_phases(self.coordinator.config_entry)
