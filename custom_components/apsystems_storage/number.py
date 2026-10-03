"""Writable registers: power setpoint and SoC reserves.

The integration only exposes the device. Ramps, clamps and control loops
belong in automations.
"""
from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import UpdateFailed

from . import StorageConfigEntry
from .const import FALLBACK_MAX_POWER
from .entity import StorageEntity

SET_POWER = NumberEntityDescription(
    key="set_power", name="Power setpoint", device_class=NumberDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.WATT, native_step=1, mode=NumberMode.BOX,
)
RESERVES = (
    NumberEntityDescription(
        key="soc_reserve_min", name="SoC reserve min", native_unit_of_measurement=PERCENTAGE,
        native_min_value=0, native_max_value=100, native_step=1, mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key="soc_reserve_max", name="SoC reserve max", native_unit_of_measurement=PERCENTAGE,
        native_min_value=0, native_max_value=100, native_step=1, mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: StorageConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities([PowerSetpoint(c, SET_POWER), *(StorageNumber(c, d) for d in RESERVES)])


class StorageNumber(StorageEntity, NumberEntity):
    def __init__(self, coordinator, description: NumberEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get(self._key)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.async_write(self._key, value)
        except (UpdateFailed, ValueError) as err:
            raise HomeAssistantError(str(err)) from err


class PowerSetpoint(StorageNumber):
    """Signed setpoint: > 0 discharge, < 0 charge, 0 standby."""

    @property
    def native_min_value(self) -> float:
        return -(self.coordinator.data.get("max_charge_rate") or FALLBACK_MAX_POWER)

    @property
    def native_max_value(self) -> float:
        return self.coordinator.data.get("max_discharge_rate") or FALLBACK_MAX_POWER
