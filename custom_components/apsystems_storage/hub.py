"""Single Modbus TCP connection to the storage system.

The ELT accepts a second TCP connection but never answers it: only one client
is served. Everything therefore goes through this object, one request at a
time, on one persistent connection.
"""
from __future__ import annotations

import asyncio
import inspect
import logging

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .registers import Block, decode

_LOGGER = logging.getLogger(__name__)


class HubError(Exception):
    """Transport-level failure (no answer, connection lost)."""


class IllegalAddress(Exception):
    """The device answered with a Modbus exception (e.g. illegal address)."""


class StorageHub:
    def __init__(self, host: str, port: int, unit: int, timeout: float = 3.0) -> None:
        self.host, self.port, self.unit = host, port, unit
        self._client = AsyncModbusTcpClient(host, port=port, timeout=timeout, retries=1)
        self._lock = asyncio.Lock()
        # pymodbus renamed slave= to device_id= in 3.10
        params = inspect.signature(self._client.read_holding_registers).parameters
        self._unit_kw = "device_id" if "device_id" in params else "slave"
        self.fragmented: dict[str, bool] = {}

    async def _ensure_connected(self) -> None:
        if not self._client.connected:
            if not await self._client.connect():
                raise HubError(f"cannot connect to {self.host}:{self.port}")

    async def close(self) -> None:
        self._client.close()

    async def read(self, address: int, count: int) -> list[int]:
        async with self._lock:
            await self._ensure_connected()
            try:
                rr = await self._client.read_holding_registers(
                    address, count=count, **{self._unit_kw: self.unit}
                )
            except ModbusException as err:
                # Silence: typical when another client already holds the ELT
                self._client.close()
                raise HubError(str(err)) from err
        if rr.isError():
            raise IllegalAddress(f"{address}+{count}: {rr}")
        return list(rr.registers)

    async def write(self, address: int, word: int) -> None:
        async with self._lock:
            await self._ensure_connected()
            try:
                rr = await self._client.write_register(
                    address, word, **{self._unit_kw: self.unit}
                )
            except ModbusException as err:
                self._client.close()
                raise HubError(str(err)) from err
        if rr.isError():
            raise IllegalAddress(f"write {address}={word}: {rr}")

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
