"""Base entity."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PHASES, DOMAIN, MANUFACTURER
from .coordinator import StorageCoordinator
from .registers import detect_model

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
