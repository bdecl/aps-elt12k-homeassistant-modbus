"""Modbus access to the storage system, one request at a time.

The ELT accepts a second TCP connection but never answers it: only one client
is served. The unit is therefore taken from Home Assistant's `modbus`
integration, which keeps one connection per host and port for every
integration that asks for it and serialises their requests. This module only
needs a `modbus_connection` unit, so it can be tested without Home Assistant.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
import time
from typing import TypeVar

from modbus_connection import ModbusError, ModbusExceptionError, ModbusUnit

from .registers import Block, decode

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

TIMEOUT = 3.0  # s per request
RETRIES = 1
"""Extra attempts after a transport failure. One lost reply would otherwise
fail the whole poll and make every sensor unavailable for a cycle, which a
controller reads as a missing battery measurement. modbus_connection does not
retry by itself. Exception responses are answers and are never retried; a
repeated write sends the same value, so it is harmless.

Before the retry the link is recycled: the ELT's Modbus server sometimes keeps
the TCP socket open but stops answering, and a request on that socket would
time out again. A new connection answers at once."""


class HubError(Exception):
    """Transport-level failure (no answer, connection lost)."""


class IllegalAddress(Exception):
    """The device answered with a Modbus exception (e.g. illegal address)."""


class StorageHub:
    def __init__(
        self,
        unit: ModbusUnit,
        host: str,
        port: int,
        timeout: float = TIMEOUT,
        retries: int = RETRIES,
        max_age: float = 0,
    ) -> None:
        self.host, self.port = host, port
        self._unit = unit
        self._retries = retries
        self._max_age = max_age
        self._since = time.monotonic()
        self.recycles = 0
        # The link's timeout is the largest any holder asks for.
        unit.require_timeout(timeout)
        self.fragmented: dict[str, bool] = {}

    async def _call(self, what: str, request: Callable[[], Awaitable[T]]) -> T:
        for attempt in range(self._retries + 1):
            try:
                return await request()
            except ModbusExceptionError as err:
                raise IllegalAddress(f"{what}: {err}") from err
            except ModbusError as err:
                if attempt == self._retries:
                    # Silence: typical when another client already holds the ELT
                    raise HubError(f"{what}: {err}") from err
                _LOGGER.debug("%s failed (%s), reconnecting and retrying", what, err)
                await self.recycle()
        raise AssertionError("unreachable")

    async def recycle(self) -> None:
        """Drop the link; the next request opens a new one.

        The link is shared with every holder of the same host and port, which
        reconnect on their next request as well."""
        disconnect = getattr(self._unit, "disconnect", None)
        if disconnect is not None:
            try:
                await disconnect()
            except ModbusError as err:
                _LOGGER.debug("Dropping the link failed (%s); it is gone anyway", err)
        self._since = time.monotonic()
        self.recycles += 1

    async def recycle_if_old(self) -> None:
        """Recycle the link once it is older than max_age (0 = never)."""
        if self._max_age > 0 and time.monotonic() - self._since >= self._max_age:
            _LOGGER.debug("Link older than %s s, reconnecting", self._max_age)
            await self.recycle()

    async def read(self, address: int, count: int) -> list[int]:
        return list(await self._call(
            f"{address}+{count}",
            lambda: self._unit.read_holding_registers(address, count),
        ))

    async def write(self, address: int, word: int) -> None:
        await self._call(
            f"write {address}={word}",
            lambda: self._unit.write_register(address, word),
        )

    async def read_block(self, block: Block) -> dict[str, object]:
        """Read a block in one request, or run by run if the device rejects
        the full window. Undocumented addresses that fail are skipped."""
        words: dict[int, int] = {}
        if not self.fragmented.get(block.name):
            try:
                regs = await self.read(block.start, block.count)
                words = {block.start + i: w for i, w in enumerate(regs)}
            except IllegalAddress as err:
                _LOGGER.info("Block %s rejected (%s), switching to per-run reads", block.name, err)
                self.fragmented[block.name] = True
        if self.fragmented.get(block.name):
            for start, count in block.runs():
                try:
                    regs = await self.read(start, count)
                except IllegalAddress:
                    _LOGGER.debug("Run %s+%s not implemented", start, count)
                    continue
                words.update({start + i: w for i, w in enumerate(regs)})
        return {reg.key: decode(reg, words) for reg in block.regs}
