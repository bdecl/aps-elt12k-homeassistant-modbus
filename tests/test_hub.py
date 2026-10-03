"""Hub against a pymodbus server: full-block read and per-run fallback."""
import asyncio
import socket

import pytest
from pymodbus.datastore import (
    ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext, ModbusSparseDataBlock,
)
from pymodbus.server import ModbusTcpServer

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
    hub = StorageHub("127.0.0.1", port, 1)
    try:
        common = await hub.read_block(BLOCK_COMMON)
        data = await hub.read_block(BLOCK_STORAGE)
        return hub, common, data
    finally:
        await hub.close()
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
