"""Polling coordinator: one storage-block read per cycle, identity hourly,
and the optional experimental block at its own slower pace."""
from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    COMMON_REFRESH,
    CONF_DETAIL_INTERVAL,
    CONF_GROUPS,
    CONF_KEEPALIVE,
    DEFAULT_DETAIL_INTERVAL,
    DEFAULT_KEEPALIVE,
    DOMAIN,
    GROUP_DETAIL,
)
from .hub import HubError, IllegalAddress, StorageHub
from .registers import BLOCK_COMMON, BLOCK_DETAIL, BLOCK_STORAGE, REG_BY_KEY, encode

_LOGGER = logging.getLogger(__name__)


class StorageCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, hub: StorageHub, scan: int) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN,
            update_interval=timedelta(seconds=scan),
        )
        self.hub = hub
        self._common: dict[str, Any] = {}
        self._common_ts = 0.0
        self.last_setpoint: float | None = None
        self._unsub_keepalive = None
        opts = entry.options
        self._detail_on = GROUP_DETAIL in opts.get(CONF_GROUPS, [])
        self._detail_every = opts.get(CONF_DETAIL_INTERVAL, DEFAULT_DETAIL_INTERVAL)
        self._detail: dict[str, Any] = {}
        self._detail_ts = 0.0
        self.detail_ok: bool | None = None  # None until the first attempt

    async def _async_update_data(self) -> dict[str, Any]:
        await self.hub.recycle_if_old()
        try:
            if not self._common or time.monotonic() - self._common_ts > COMMON_REFRESH:
                try:
                    self._common = await self.hub.read_block(BLOCK_COMMON)
                    self._common_ts = time.monotonic()
                except HubError:
                    if not self._common:
                        raise
            data = await self.hub.read_block(BLOCK_STORAGE)
        except HubError as err:
            raise UpdateFailed(
                f"No answer from {self.hub.host}:{self.hub.port} ({err}). "
                "The device serves a single Modbus client: is another one connected?"
            ) from err
        await self._update_detail()
        return {**self._common, **data, **self._detail}

    async def _update_detail(self) -> None:
        """Experimental block: its failure never fails the poll, so control and
        the documented sensors keep working on firmwares without it."""
        # After a failure, retry rarely: a silent device costs a timeout and a
        # reconnection of the link shared with the main block.
        every = self._detail_every if self.detail_ok is not False else max(600, self._detail_every)
        if not self._detail_on or time.monotonic() - self._detail_ts < every:
            return
        self._detail_ts = time.monotonic()
        try:
            self._detail = await self.hub.read_block(BLOCK_DETAIL)
        except (HubError, IllegalAddress) as err:
            if self.detail_ok is not False:
                _LOGGER.warning("Experimental energy block not readable: %s", err)
            self._detail = {}
            self.detail_ok = False
        else:
            self.detail_ok = True

    async def async_write(self, key: str, value: float) -> None:
        reg = REG_BY_KEY[key]
        try:
            await self.hub.write(reg.address, encode(reg, value))
        except (HubError, IllegalAddress) as err:
            raise UpdateFailed(f"Write {key}={value} failed: {err}") from err
        if key == "set_power":
            self.last_setpoint = value
        await self.async_request_refresh()

    # --- optional periodic rewrite of the last power setpoint -------------
    def start_keepalive(self) -> None:
        period = self.config_entry.options.get(CONF_KEEPALIVE, DEFAULT_KEEPALIVE)
        if period <= 0:
            return

        async def _tick(_now) -> None:
            if self.last_setpoint is None:
                return
            reg = REG_BY_KEY["set_power"]
            try:
                await self.hub.write(reg.address, encode(reg, self.last_setpoint))
            except (HubError, IllegalAddress) as err:
                _LOGGER.warning("Setpoint keepalive failed: %s", err)

        self._unsub_keepalive = async_track_time_interval(
            self.hass, _tick, timedelta(seconds=period)
        )

    def stop_keepalive(self) -> None:
        if self._unsub_keepalive:
            self._unsub_keepalive()
            self._unsub_keepalive = None
