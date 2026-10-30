"""Hub against a pymodbus server, through a modbus_connection unit as Home
Assistant hands it out: full-block read and per-run fallback."""
import asyncio
import socket

import pytest
from pymodbus.datastore import (
    ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext, ModbusSparseDataBlock,
)
from pymodbus.server import ModbusTcpServer

from modbus_connection import ModbusTcpParams
from modbus_connection.tmodbus import ModbusConnection

from apsys.hub import StorageHub
from apsys.registers import BLOCK_COMMON, BLOCK_STORAGE, REG_BY_KEY

SPARSE_OFFSET = 0  # pymodbus 3.10+ maps sparse blocks without the +1 shift
LIVE = {  # values seen on a live ELT-12K
    "soc": [630], "soh": [98], "capacity": [34], "charge_status": [3],
    "battery_power": [2205], "dc_current": [0x10000 - 464], "set_power": [2203],
    "total_charge_energy": [574877 >> 16, 574877 & 0xFFFF],
}

def _image(documented_only: bool) -> dict[int, int]:
    words = {}
    for reg in BLOCK_COMMON.regs + BLOCK_STORAGE.regs:
        for i in range(reg.size):
            words[reg.address + i] = 0
    serial = b"B05000000999".ljust(32, b"\x00")
    for i in range(16):
        words[40052 + i] = int.from_bytes(serial[2 * i:2 * i + 2], "big")
    for key, vals in LIVE.items():
        for i, v in enumerate(vals):
            words[REG_BY_KEY[key].address + i] = v
    if not documented_only:
        for a in range(40000, 40200):
            words.setdefault(a, 0)
    return words

def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

async def _run(documented_only: bool):
    words = _image(documented_only)
    if documented_only:
        block = ModbusSparseDataBlock({a + SPARSE_OFFSET: v for a, v in words.items()})
    else:
        block = ModbusSequentialDataBlock(40001, [words[a] for a in range(40000, 40200)])
    ctx = ModbusServerContext(devices=ModbusDeviceContext(hr=block), single=True)
    port = _free_port()
    server = ModbusTcpServer(ctx, address=("127.0.0.1", port))
    task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0.3)
    conn = ModbusConnection(ModbusTcpParams(host="127.0.0.1", port=port))
    hub = StorageHub(conn.for_unit(1), "127.0.0.1", port)
    try:
        common = await hub.read_block(BLOCK_COMMON)
        data = await hub.read_block(BLOCK_STORAGE)
        return hub, common, data
    finally:
        await conn.close()
        await server.shutdown()
        task.cancel()

@pytest.mark.asyncio
@pytest.mark.parametrize("documented_only", [False, True])
async def test_read_blocks(documented_only):
    hub, common, data = await _run(documented_only)
    assert common["serial"] == "B05000000999"
    assert data["soc"] == 63.0 and data["capacity"] == 34
    assert data["dc_current"] == -46.4 and data["set_power"] == 2203
    assert data["total_charge_energy"] == 5748.77
    assert data["charge_status"] == 3
    # gaps in the address space force the per-run fallback
    assert hub.fragmented.get("storage", False) is documented_only


class _FlakyUnit:
    """A unit whose first `lost` requests get no answer."""

    def __init__(self, lost: int):
        self.lost, self.calls, self.disconnects = lost, 0, 0

    async def disconnect(self):
        self.disconnects += 1

    def require_timeout(self, seconds):
        pass

    async def _answer(self, value):
        from modbus_connection import ModbusTimeoutError
        self.calls += 1
        if self.calls <= self.lost:
            raise ModbusTimeoutError("no answer")
        return value

    async def read_holding_registers(self, address, count):
        return await self._answer([7] * count)

    async def write_register(self, address, value):
        return await self._answer(None)


@pytest.mark.asyncio
async def test_one_lost_reply_is_retried():
    """A single lost frame must not fail the poll (battery power would go
    unavailable and the controller would drop the setpoint to 0)."""
    unit = _FlakyUnit(lost=1)
    hub = StorageHub(unit, "elt", 502)
    assert await hub.read(40117, 1) == [7]
    assert unit.calls == 2

    unit = _FlakyUnit(lost=1)
    await StorageHub(unit, "elt", 502).write(40183, 2000)
    assert unit.calls == 2


@pytest.mark.asyncio
async def test_persistent_silence_still_fails():
    from apsys.hub import HubError
    unit = _FlakyUnit(lost=99)
    with pytest.raises(HubError):
        await StorageHub(unit, "elt", 502).read(40117, 1)
    assert unit.calls == 2


@pytest.mark.asyncio
async def test_retry_goes_through_a_new_link():
    """A server that keeps the socket open but stops answering would ignore
    the retry too: the link is dropped before retrying."""
    unit = _FlakyUnit(lost=1)
    hub = StorageHub(unit, "elt", 502)
    assert await hub.read(40117, 1) == [7]
    assert unit.disconnects == 1 and hub.recycles == 1

    unit = _FlakyUnit(lost=0)
    await StorageHub(unit, "elt", 502).read(40117, 1)
    assert unit.disconnects == 0


@pytest.mark.asyncio
async def test_max_age_renews_the_link(monkeypatch):
    import apsys.hub as hubmod
    now = [1000.0]
    monkeypatch.setattr(hubmod.time, "monotonic", lambda: now[0])
    unit = _FlakyUnit(lost=0)
    hub = StorageHub(unit, "elt", 502, max_age=480)
    await hub.recycle_if_old()
    assert unit.disconnects == 0
    now[0] += 480
    await hub.recycle_if_old()
    assert unit.disconnects == 1
    await hub.recycle_if_old()  # timer restarted
    assert unit.disconnects == 1

    unit = _FlakyUnit(lost=0)
    hub = StorageHub(unit, "elt", 502)  # max_age 0: never
    now[0] += 10**6
    await hub.recycle_if_old()
    assert unit.disconnects == 0


@pytest.mark.asyncio
async def test_recycle_on_a_real_link():
    """disconnect() on a modbus_connection unit: the next read reconnects."""
    words = _image(False)
    block = ModbusSequentialDataBlock(40001, [words[a] for a in range(40000, 40200)])
    ctx = ModbusServerContext(devices=ModbusDeviceContext(hr=block), single=True)
    port = _free_port()
    server = ModbusTcpServer(ctx, address=("127.0.0.1", port))
    task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0.3)
    conn = ModbusConnection(ModbusTcpParams(host="127.0.0.1", port=port))
    unit = conn.for_unit(1)
    hub = StorageHub(unit, "127.0.0.1", port)
    try:
        assert (await hub.read_block(BLOCK_STORAGE))["soc"] == 63.0
        assert unit.connected
        await hub.recycle()
        assert not unit.connected
        assert (await hub.read_block(BLOCK_STORAGE))["soc"] == 63.0
        assert unit.connected
    finally:
        await conn.close()
        await server.shutdown()
        task.cancel()
