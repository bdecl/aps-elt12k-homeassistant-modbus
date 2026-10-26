"""Modbus access to the storage system, one request at a time.

The ELT accepts a second TCP connection but never answers it: only one client
is served. The unit is therefore taken from Home Assistant's `modbus`
integration, which keeps one connection per host and port for every
integration that asks for it and serialises their requests. This module only
needs a `modbus_connection` unit, so it can be tested without Home Assistant.
"""
from __future__ import annotations

import logging

from modbus_connection import ModbusError, ModbusExceptionError, ModbusUnit

from .registers import Block, decode

_LOGGER = logging.getLogger(__name__)

TIMEOUT = 3.0  # s per request


class HubError(Exception):
    """Transport-level failure (no answer, connection lost)."""


class IllegalAddress(Exception):
    """The device answered with a Modbus exception (e.g. illegal address)."""


class StorageHub:
    def __init__(self, unit: ModbusUnit, host: str, port: int, timeout: float = TIMEOUT) -> None:
        self.host, self.port = host, port
        self._unit = unit
        # The link's timeout is the largest any holder asks for.
        unit.require_timeout(timeout)
        self.fragmented: dict[str, bool] = {}

    async def read(self, address: int, count: int) -> list[int]:
        try:
            return list(await self._unit.read_holding_registers(address, count))
        except ModbusExceptionError as err:
            raise IllegalAddress(f"{address}+{count}: {err}") from err
        except ModbusError as err:
            # Silence: typical when another client already holds the ELT
            raise HubError(str(err)) from err

    async def write(self, address: int, word: int) -> None:
        try:
            await self._unit.write_register(address, word)
        except ModbusExceptionError as err:
            raise IllegalAddress(f"write {address}={word}: {err}") from err
        except ModbusError as err:
            raise HubError(str(err)) from err

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
