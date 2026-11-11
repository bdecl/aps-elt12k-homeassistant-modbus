import struct

from apsys.registers import (
    BLOCK_COMMON, BLOCK_DETAIL, BLOCK_STORAGE, REG_BY_KEY, active_bits, decode, detect_model, encode, entry_title,
    is_placeholder_title,
    BATTERY_EVENT_BITS,
)


def words_for(key, values):
    reg = REG_BY_KEY[key]
    return {reg.address + i: v for i, v in enumerate(values)}


def test_block_sizes_fit_one_request():
    assert BLOCK_COMMON.count <= 125
    assert BLOCK_STORAGE.start == 40073
    assert BLOCK_STORAGE.count <= 125


def test_string_cut_at_nul():
    # "1.1.8.12\x00234" as seen on a live ELT
    raw = b"1.1.8.12\x00234\x00\x00\x00\x00"
    words = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, 16, 2)]
    assert decode(REG_BY_KEY["chip1_version"], words_for("chip1_version", words)) == "1.1.8.12"


def test_scaling_and_signs():
    assert decode(REG_BY_KEY["soc"], words_for("soc", [630])) == 63.0
    assert decode(REG_BY_KEY["dc_current"], words_for("dc_current", [0x10000 - 464])) == -46.4
    assert decode(REG_BY_KEY["capacity"], words_for("capacity", [34])) == 34
    total = 574877
    assert decode(REG_BY_KEY["total_charge_energy"],
                  words_for("total_charge_energy", [total >> 16, total & 0xFFFF])) == 5748.77


def test_not_implemented():
    assert decode(REG_BY_KEY["soc"], words_for("soc", [0xFFFF])) is None
    assert decode(REG_BY_KEY["battery_power"], words_for("battery_power", [0x8000])) is None
    assert decode(REG_BY_KEY["soc"], {}) is None


def test_encode():
    assert encode(REG_BY_KEY["set_power"], -1500) == 0x10000 - 1500
    assert encode(REG_BY_KEY["soc_reserve_min"], 30) == 300


def test_bits_and_model():
    assert active_bits(0b10, BATTERY_EVENT_BITS) == ["over_temp_alarm"]
    assert active_bits(1 << 30, BATTERY_EVENT_BITS) == ["bit_30"]
    assert detect_model("B05000000001", None) == ("ELT-12", 3)
    assert detect_model("215000000001", None) == ("ELS-5K", 1)
    assert detect_model("B04000000001", None) == ("ELS-11.4", 1)
    # the ELT-12 reports the placeholder "Model" in 40020: serial prefix wins
    assert detect_model("B05000001234", "Model") == ("ELT-12", 3)
    assert detect_model("B05000001234", "Something else") == ("ELT-12", 3)
    # unknown serial: a real model string is used, a placeholder is not
    assert detect_model("X1", "ELT-9") == ("ELT-9", 3)
    assert detect_model("X1", "Model") == ("APsystems storage", 1)


def test_entry_title_from_serial_prefix():
    # The ELT-12 answers "Model" in 40020: the serial prefix decides.
    assert entry_title("B05000001234", "Model") == "ELT-12 (1234)"
    assert entry_title("B04000000123", None) == "ELS-11.4 (0123)"
    assert entry_title("215000004567", "") == "ELS-5K (4567)"
    assert entry_title("X99000001234", "Model") == "APsystems storage (1234)"


def test_placeholder_titles_only():
    assert is_placeholder_title("Model (1234)", "B05000001234")
    assert is_placeholder_title("unknown (1234)", "B05000001234")
    assert not is_placeholder_title("ELT-12 (1234)", "B05000001234")
    assert not is_placeholder_title("Batterie garage", "B05000001234")
    assert not is_placeholder_title("Model (9999)", "B05000001234")  # another serial


def _f32ws(value):
    hi, lo = struct.unpack(">HH", struct.pack(">f", value))
    return [lo, hi]  # low word first, as the ELT sends it


def test_detail_block_window():
    # The ELT only answers this block when the read starts at 40184.
    assert BLOCK_DETAIL.start == 40184
    assert BLOCK_DETAIL.count == 58  # 40184-40241, the window validated live
    assert not BLOCK_DETAIL.splittable
    assert BLOCK_STORAGE.splittable


def test_float_low_word_first():
    # Words read on a live ELT-12 at 23:59: 4.705 kWh PV on L1
    assert decode(REG_BY_KEY["pv_energy_today_a"],
                  words_for("pv_energy_today_a", [36656, 16534])) == 4.705
    assert decode(REG_BY_KEY["net_energy_today_a"],
                  words_for("net_energy_today_a", _f32ws(-25.37))) == -25.37
    nan = _f32ws(float("nan"))
    assert decode(REG_BY_KEY["pv_energy_today_b"], words_for("pv_energy_today_b", nan)) is None


def test_raw_daily_discharge_renamed():
    assert REG_BY_KEY["daily_discharge_energy_raw"].address == 40147
    assert "daily_discharge_energy" not in REG_BY_KEY  # derived sensor, not a register
