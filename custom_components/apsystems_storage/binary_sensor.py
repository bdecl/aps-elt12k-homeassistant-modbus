"""Alarm binary sensors decoded from the event bitfields."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import StorageConfigEntry
from .entity import StorageEntity
from .registers import BATTERY_EVENT_BITS, PCS_EVENT_BITS, active_bits

ALARMS = (
    (BinarySensorEntityDescription(
        key="battery_alarm", name="Battery alarm", device_class=BinarySensorDeviceClass.PROBLEM,
    ), "battery_events", BATTERY_EVENT_BITS),
    (BinarySensorEntityDescription(
        key="pcs_alarm", name="PCS alarm", device_class=BinarySensorDeviceClass.PROBLEM,
    ), "pcs_events", PCS_EVENT_BITS),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: StorageConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        AlarmSensor(entry.runtime_data, desc, src, names) for desc, src, names in ALARMS
    )


class AlarmSensor(StorageEntity, BinarySensorEntity):
    def __init__(self, coordinator, description, source: str, names: dict[int, str]) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._source, self._names = source, names

    @property
    def is_on(self) -> bool | None:
        raw = self.coordinator.data.get(self._source)
        return None if raw is None else raw != 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"active": active_bits(self.coordinator.data.get(self._source), self._names)}
