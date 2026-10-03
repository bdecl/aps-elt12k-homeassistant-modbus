from apsys.registers import (
    BLOCK_COMMON, BLOCK_STORAGE, REG_BY_KEY, active_bits, decode, detect_model, encode,
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
    # "1.1.8.12\x00637" as seen on a live ELT
    raw = b"1.1.8.12\x00637\x00\x00\x00\x00"
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
