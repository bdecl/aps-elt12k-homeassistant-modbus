"""Sensors: the readable registers of the PCS protocol, by entity group."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.components.sensor import RestoreSensor
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import StorageConfigEntry
from .entity import StorageEntity, entry_phases, key_enabled, phase_allowed
from .registers import BATTERY_EVENT_BITS, CHARGE_STATUS, PCS_EVENT_BITS, active_bits

P = SensorDeviceClass.POWER
MEAS = SensorStateClass.MEASUREMENT
DIAG = EntityCategory.DIAGNOSTIC


@dataclass(frozen=True, kw_only=True)
class StorageSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any] | None = None
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    phased: bool = False  # derived sum over the configured phases
    daily_from: str | None = None  # today's increase of this total counter


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
    # 40147 counts one phase only on a three-phase ELT-12: derived from 40150.
    _kwh("daily_discharge_energy", "Daily discharge energy", daily_from="total_discharge_energy"),
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
    _kwh(
        "daily_discharge_energy_raw", "Daily discharge energy (register 40147)",
        entity_category=DIAG, entity_registry_enabled_default=False,
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
    # --- experimental vendor block (40184-40241, not in the PCS document) --
    _kwh("pv_energy_today", "PV energy today (ELT CT)", phased=True),
    *(
        _kwh(f"pv_energy_today_{p}", f"PV energy today L{n} (ELT CT)",
             entity_registry_enabled_default=False)
        for n, p in enumerate("abc", 1)
    ),
    # Signed, resets daily: no state class, so no long-term statistics.
    StorageSensorDescription(
        key="net_energy_today", name="PV + battery net energy today",
        device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2, phased=True,
    ),
    *(
        StorageSensorDescription(
            key=f"net_energy_today_{p}", name=f"PV + battery net energy today L{n}",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2, entity_registry_enabled_default=False,
        )
        for n, p in enumerate("abc", 1)
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: StorageConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    phases = entry_phases(entry)
    async_add_entities(
        (DailyFromTotalSensor if desc.daily_from else StorageSensor)(coordinator, desc, phases)
        for desc in DESCRIPTIONS
        if phase_allowed(desc.key, phases) and key_enabled(entry, desc.key)
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


@dataclass
class _DailyBase(SensorExtraStoredData):
    """Restored with the state: the total counter at the start of the day."""

    base: float | None = None
    day: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {**super().as_dict(), "base": self.base, "day": self.day}

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> _DailyBase | None:
        extra = SensorExtraStoredData.from_dict(restored)
        if extra is None:
            return None
        return cls(
            extra.native_value, extra.native_unit_of_measurement,
            restored.get("base"), restored.get("day"),
        )


class DailyFromTotalSensor(StorageSensor, RestoreSensor):
    """Today's increase of a lifetime counter, from local midnight.

    The start-of-day value survives restarts. If Home Assistant was not
    running at midnight, the day starts at the first value read."""

    def __init__(self, coordinator, description: StorageSensorDescription, phases: int) -> None:
        super().__init__(coordinator, description, phases)
        self._source = description.daily_from
        self._base: float | None = None
        self._day: date | None = None
        self._value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_extra_data()
        if last is not None:
            restored = _DailyBase.from_dict(last.as_dict())
            if restored and restored.base is not None and restored.day:
                self._base = float(restored.base)
                self._day = date.fromisoformat(restored.day)
        self._compute()

    @property
    def extra_restore_state_data(self) -> _DailyBase:
        return _DailyBase(
            self._value, self.native_unit_of_measurement,
            self._base, self._day.isoformat() if self._day else None,
        )

    def _compute(self) -> None:
        total = (self.coordinator.data or {}).get(self._source)
        if total is None:
            self._value = None
            return
        today = dt_util.now().date()
        # New day, first value ever, or the counter went back (device reset).
        if self._base is None or self._day != today or total < self._base:
            self._base, self._day = total, today
        self._value = round(total - self._base, 2)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._compute()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        return self._value
