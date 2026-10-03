"""Sensors: every readable register of the PCS protocol."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import StorageConfigEntry
from .entity import StorageEntity, entry_phases, phase_allowed
from .registers import BATTERY_EVENT_BITS, CHARGE_STATUS, PCS_EVENT_BITS, active_bits

P = SensorDeviceClass.POWER
MEAS = SensorStateClass.MEASUREMENT
DIAG = EntityCategory.DIAGNOSTIC


@dataclass(frozen=True, kw_only=True)
class StorageSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any] | None = None
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    phased: bool = False  # derived sum over the configured phases


def _w(key: str, name: str, **kw) -> StorageSensorDescription:
    return StorageSensorDescription(
        key=key, name=name, device_class=P, state_class=MEAS,
        native_unit_of_measurement=UnitOfPower.WATT, **kw,
    )


def _var(key: str, name: str) -> StorageSensorDescription:
    return StorageSensorDescription(
        key=key, name=name, device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=MEAS, native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
    )


def _kwh(key: str, name: str, **kw) -> StorageSensorDescription:
    return StorageSensorDescription(
        key=key, name=name, device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, suggested_display_precision=2, **kw,
    )


def _pct(key: str, name: str, **kw) -> StorageSensorDescription:
    kw.setdefault("suggested_display_precision", 1)
    return StorageSensorDescription(
        key=key, name=name, native_unit_of_measurement=PERCENTAGE, **kw
    )


def _sum(prefix: str, phases: int) -> Callable[[dict[str, Any]], Any]:
    keys = [f"{prefix}_{p}" for p in "abc"[:phases]]

    def fn(d: dict[str, Any]) -> Any:
        vals = [d.get(k) for k in keys]
        return None if any(v is None for v in vals) else sum(vals)

    return fn


def _events(key: str, names: dict[int, str]):
    return lambda d: {"active": active_bits(d.get(key), names)}


DESCRIPTIONS: tuple[StorageSensorDescription, ...] = (
    _pct("soc", "State of charge", device_class=SensorDeviceClass.BATTERY, state_class=MEAS),
    _pct("soh", "State of health", state_class=MEAS, suggested_display_precision=0),
    StorageSensorDescription(
        key="charge_status", name="Charge status", device_class=SensorDeviceClass.ENUM,
        options=list(CHARGE_STATUS.values()),
        value_fn=lambda d: CHARGE_STATUS.get(d.get("charge_status")),
    ),
    _w("battery_power", "Battery power"),  # + discharge, - charge
    StorageSensorDescription(
        key="battery_voltage", name="Battery voltage", device_class=SensorDeviceClass.VOLTAGE,
        state_class=MEAS, native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
    ),
    StorageSensorDescription(
        key="dc_bus_voltage", name="DC bus voltage", device_class=SensorDeviceClass.VOLTAGE,
        state_class=MEAS, native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
    ),
    StorageSensorDescription(
        key="dc_current", name="DC current", device_class=SensorDeviceClass.CURRENT,
        state_class=MEAS, native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
    ),
    _w("active_power_a", "Active power L1"),
    _w("active_power_b", "Active power L2"),
    _w("active_power_c", "Active power L3"),
    _w("active_power", "Active power", phased=True),
    _var("reactive_power_a", "Reactive power L1"),
    _var("reactive_power_b", "Reactive power L2"),
    _var("reactive_power_c", "Reactive power L3"),
    _w("grid_power_a", "Grid power L1"),
    _w("grid_power_b", "Grid power L2"),
    _w("grid_power_c", "Grid power L3"),
    _w("grid_power", "Grid power", phased=True),
    _kwh("daily_charge_energy", "Daily charge energy"),
    _kwh("daily_discharge_energy", "Daily discharge energy"),
    _kwh("total_charge_energy", "Total charge energy"),
    _kwh("total_discharge_energy", "Total discharge energy"),
    StorageSensorDescription(
        key="battery_temperature", name="Battery temperature",
        device_class=SensorDeviceClass.TEMPERATURE, state_class=MEAS,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, suggested_display_precision=1,
    ),
    StorageSensorDescription(
        key="pcs_temperature", name="PCS temperature",
        device_class=SensorDeviceClass.TEMPERATURE, state_class=MEAS,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, suggested_display_precision=1,
    ),
    # --- ratings and limits (diagnostic) --------------------------------
    StorageSensorDescription(
        key="capacity", name="Energy capacity", device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, entity_category=DIAG,
    ),
    _w("max_charge_rate", "Max charge rate", entity_category=DIAG),
    _w("max_discharge_rate", "Max discharge rate", entity_category=DIAG),
    _pct("soc_max", "SoC max", entity_category=DIAG),
    _pct("soc_min", "SoC min", entity_category=DIAG),
    StorageSensorDescription(
        key="battery_events", name="Battery events", entity_category=DIAG,
        attrs_fn=_events("battery_events", BATTERY_EVENT_BITS),
    ),
    StorageSensorDescription(
        key="pcs_events", name="PCS events", entity_category=DIAG,
        attrs_fn=_events("pcs_events", PCS_EVENT_BITS),
    ),
    StorageSensorDescription(
        key="heartbeat", name="Controller heartbeat", entity_category=DIAG,
        entity_registry_enabled_default=False,
    ),
    StorageSensorDescription(key="chip1_version", name="Chip 1 version", entity_category=DIAG),
    StorageSensorDescription(key="chip2_version", name="Chip 2 version", entity_category=DIAG),
    StorageSensorDescription(key="chip3_version", name="Chip 3 version", entity_category=DIAG),
    StorageSensorDescription(
        key="version", name="Version", entity_category=DIAG, entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: StorageConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    phases = entry_phases(entry)
    async_add_entities(
        StorageSensor(coordinator, desc, phases)
        for desc in DESCRIPTIONS
        if phase_allowed(desc.key, phases)
    )


class StorageSensor(StorageEntity, SensorEntity):
    entity_description: StorageSensorDescription

    def __init__(self, coordinator, description: StorageSensorDescription, phases: int) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.phased:
            prefix = description.key
            self._value_fn = _sum(prefix, phases)
        else:
            self._value_fn = description.value_fn or (lambda d, k=description.key: d.get(k))

    @property
    def native_value(self) -> Any:
        return self._value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
