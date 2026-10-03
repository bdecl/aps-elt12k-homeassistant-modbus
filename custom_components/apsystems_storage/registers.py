"""Register map of the APsystems PCS Modbus protocol (ELT-12 / ELS-5K / ELS-11.4).

Source: "Modbus protocol for PCS" REV1.0 (SunSpec common model 1 + storage
model 802 layout). Scales are the ones validated on a live ELT-12K, which
differ from the PDF for WHRtg (the device returns kWh directly).

This module has no Home Assistant dependency so it can be unit-tested alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Data types
U16 = "uint16"
I16 = "int16"
U32 = "uint32"
STR = "string"
ENUM16 = "enum16"
BIT32 = "bitfield32"
SUNSSF = "sunssf"

_COUNT = {U16: 1, I16: 1, ENUM16: 1, SUNSSF: 1, U32: 2, BIT32: 2}


@dataclass(frozen=True)
class Reg:
    """One logical value made of one or more holding registers."""

    key: str
    address: int
    dtype: str
    count: int = 0  # 0 = derived from dtype (strings must set it)
    scale: float = 1.0
    writable: bool = False

    @property
    def size(self) -> int:
        return self.count or _COUNT[self.dtype]

    @property
    def end(self) -> int:
        """Last register address used (inclusive)."""
        return self.address + self.size - 1


# --- SunSpec common model (read rarely) -----------------------------------
COMMON: tuple[Reg, ...] = (
    Reg("manufacturer", 40004, STR, 16),
    Reg("model", 40020, STR, 16),
    Reg("options", 40036, STR, 8),
    Reg("version", 40044, STR, 8),
    Reg("serial", 40052, STR, 16),
    Reg("device_address", 40068, U16),
)

# --- Storage model 802 + vendor extension (read every cycle) --------------
STORAGE: tuple[Reg, ...] = (
    Reg("capacity", 40073, U16),  # kWh (PDF says SF -2, device says kWh)
    Reg("max_charge_rate", 40074, U16),
    Reg("max_discharge_rate", 40075, U16),
    Reg("soc_max", 40077, U16, scale=0.1),
    Reg("soc_min", 40078, U16, scale=0.1),
    Reg("soc_reserve_max", 40079, U16, scale=0.1, writable=True),
    Reg("soc_reserve_min", 40080, U16, scale=0.1, writable=True),
    Reg("soc", 40081, U16, scale=0.1),
    Reg("soh", 40083, U16),
    Reg("charge_status", 40086, ENUM16),
    Reg("heartbeat", 40089, U16),
    Reg("battery_events", 40096, BIT32),
    Reg("pcs_events", 40100, BIT32),
    Reg("dc_bus_voltage", 40104, U16, scale=0.1),
    Reg("dc_current", 40114, I16, scale=0.1),
    Reg("battery_power", 40117, I16),
    Reg("sf_whrtg", 40123, SUNSSF),
    Reg("sf_wchadischamax", 40124, SUNSSF),
    Reg("sf_dischrte", 40125, SUNSSF),
    Reg("sf_soc", 40126, SUNSSF),
    Reg("sf_soh", 40128, SUNSSF),
    Reg("sf_v", 40129, SUNSSF),
    Reg("sf_a", 40131, SUNSSF),
    Reg("sf_amax", 40132, SUNSSF),
    Reg("sf_w", 40133, SUNSSF),
    Reg("battery_voltage", 40134, U16, scale=0.1),
    Reg("active_power_a", 40135, I16),
    Reg("active_power_b", 40136, I16),
    Reg("active_power_c", 40137, I16),
    Reg("reactive_power_a", 40138, I16),
    Reg("reactive_power_b", 40139, I16),
    Reg("reactive_power_c", 40140, I16),
    Reg("daily_charge_energy", 40146, U16, scale=0.01),
    Reg("daily_discharge_energy", 40147, U16, scale=0.01),
    Reg("total_charge_energy", 40148, U32, scale=0.01),
    Reg("total_discharge_energy", 40150, U32, scale=0.01),
    Reg("sf_energy", 40152, SUNSSF),
    Reg("grid_power_a", 40153, I16),
    Reg("grid_power_b", 40154, I16),
    Reg("grid_power_c", 40155, I16),
    Reg("battery_temperature", 40156, I16, scale=0.1),
    Reg("pcs_temperature", 40157, I16, scale=0.1),
    Reg("sf_temp", 40158, SUNSSF),
    Reg("chip1_version", 40159, STR, 8),
    Reg("chip2_version", 40167, STR, 8),
    Reg("chip3_version", 40175, STR, 8),
    Reg("set_power", 40183, I16, writable=True),
)

REG_BY_KEY: dict[str, Reg] = {r.key: r for r in COMMON + STORAGE}

CHARGE_STATUS = {
    1: "off",
    2: "empty",
    3: "discharging",
    4: "charging",
    5: "full",
    6: "holding",
    7: "testing",
}

BATTERY_EVENT_BITS = {
    0: "communication_error",
    1: "over_temp_alarm",
    3: "under_temp_alarm",
    5: "over_charge_current_alarm",
    7: "over_discharge_current_alarm",
    9: "over_volt_alarm",
    11: "under_volt_alarm",
    22: "ground_fault",
}

PCS_EVENT_BITS: dict[int, str] = {0: "pcs_communication_error"}
_bit = 1
for _stage in range(1, 5):
    for _phase in "abc":
        PCS_EVENT_BITS[_bit] = f"ac_{_phase}_voltage_stage{_stage}_over_range"
        PCS_EVENT_BITS[_bit + 1] = f"ac_{_phase}_voltage_stage{_stage}_under_range"
        _bit += 2


@dataclass(frozen=True)
class Block:
    """A contiguous register window read with a single request when possible."""

    name: str
    regs: tuple[Reg, ...]

    @property
    def start(self) -> int:
        return min(r.address for r in self.regs)

    @property
    def count(self) -> int:
        return max(r.end for r in self.regs) - self.start + 1

    def runs(self) -> list[tuple[int, int]]:
        """Contiguous runs of documented registers, used as a fallback when
        the device rejects a read spanning undocumented addresses."""
        out: list[list[int]] = []
        for reg in sorted(self.regs, key=lambda r: r.address):
            if out and reg.address == out[-1][1] + 1:
                out[-1][1] = reg.end
            else:
                out.append([reg.address, reg.end])
        return [(a, b - a + 1) for a, b in out]


BLOCK_COMMON = Block("common", COMMON)  # 40004-40068, 65 registers
BLOCK_STORAGE = Block("storage", STORAGE)  # 40073-40183, 111 registers (< 125)


def _s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def decode(reg: Reg, words: dict[int, int]) -> Any:
    """Decode one register from an address->word map. Returns None when the
    value is missing or flagged "not implemented" by SunSpec conventions."""
    try:
        raw = [words[reg.address + i] for i in range(reg.size)]
    except KeyError:
        return None

    if reg.dtype == STR:
        data = b"".join(w.to_bytes(2, "big") for w in raw)
        text = data.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        return text or None
    if reg.dtype in (U16, ENUM16):
        val = raw[0]
        if val == 0xFFFF:
            return None
    elif reg.dtype in (I16, SUNSSF):
        if raw[0] == 0x8000:
            return None
        val = _s16(raw[0])
    elif reg.dtype in (U32, BIT32):
        val = (raw[0] << 16) | raw[1]
        if val == 0xFFFFFFFF:
            return None
    else:  # pragma: no cover
        raise ValueError(reg.dtype)

    if reg.dtype in (ENUM16, BIT32, SUNSSF) or reg.scale == 1:
        return val
    return round(val * reg.scale, 3)


def encode(reg: Reg, value: float) -> int:
    """Encode a user value into one uint16 word for writing."""
    raw = int(round(value / reg.scale))
    if reg.dtype == I16:
        if not -32767 <= raw <= 32767:
            raise ValueError(f"{reg.key}: {value} out of int16 range")
        return raw & 0xFFFF
    if reg.dtype == U16:
        if not 0 <= raw <= 65534:
            raise ValueError(f"{reg.key}: {value} out of uint16 range")
        return raw
    raise ValueError(f"{reg.key} is not writable as a single register")


def active_bits(value: int | None, names: dict[int, str]) -> list[str]:
    """Names of the set bits (unknown bits reported as bit_N)."""
    if not value:
        return []
    return [names.get(b, f"bit_{b}") for b in range(32) if value >> b & 1]


def detect_model(serial: str | None, model: str | None) -> tuple[str, int]:
    """Return (model name, default phase count) from the serial prefix.

    The model string (40020) is preferred when the device implements it.
    """
    if model:
        name = model
    elif serial and serial.startswith("B050"):
        name = "ELT-12"
    elif serial and serial.startswith("B040"):
        name = "ELS-11.4"
    elif serial and serial.startswith("215"):
        name = "ELS-5K"
    else:
        name = "APsystems storage"
    phases = 3 if "ELT" in name.upper() else 1
    return name, phases
