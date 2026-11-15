"""Base entity."""
from __future__ import annotations

import time

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_GROUPS,
    CONF_PHASES,
    CONF_UPDATE_EVERY,
    DEFAULT_UPDATE_EVERY,
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
         "soc_reserve_max", "battery_alarm", "pcs_alarm",
         # current charge/discharge limits: control clamps on them (40075
         # drops to 0 while discharge is not allowed)
         "max_charge_rate", "max_discharge_rate"), ESSENTIAL),
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


def update_every(entry, key: str) -> float:
    """Minimum seconds between two state writes for this entity (0 = each poll)."""
    group = group_of(key)
    if group not in DEFAULT_UPDATE_EVERY:
        return 0
    return entry.options.get(CONF_UPDATE_EVERY.format(group), DEFAULT_UPDATE_EVERY[group])

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
        self._update_every = update_every(entry, key)
        self._last_write: float | None = None
        self._last_available: bool | None = None
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

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The state written when the entity is added counts as the first write.
        self._last_write, self._last_available = time.monotonic(), self.available

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write the state at most once per update interval of the group; a
        change of availability is always written at once."""
        now = time.monotonic()
        available = self.available
        if (
            self._update_every
            and self._last_write is not None
            and available == self._last_available
            and now - self._last_write < self._update_every
        ):
            return
        self._last_write, self._last_available = now, available
        super()._handle_coordinator_update()
