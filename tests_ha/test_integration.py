"""End-to-end: config flow, setup, entities and writes against a simulated ELT."""
import asyncio
import socket

import pytest
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusTcpServer

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.apsystems_storage.const import DOMAIN

BASE = 40000


def _words():
    w = [0] * 260  # up to 40259: covers the experimental block 40184-40241
    serial = b"B05000000999".ljust(32, b"\x00")
    for i in range(16):
        w[52 + i] = int.from_bytes(serial[2 * i:2 * i + 2], "big")
    vals = {81: 630, 83: 98, 73: 34, 74: 12000, 75: 11160, 80: 200, 79: 1000, 86: 3,
            117: 2205, 114: 0x10000 - 464, 135: 700, 136: 750, 137: 760, 183: 2203,
            147: 1017,  # 40147: one phase of today's discharge
            150: 0, 151: 50000,  # 40150: total discharge 500.00 kWh
            194: 36656, 195: 16534,  # 40194: PV L1 4.705 kWh (float, low word first)
            }
    for a, v in vals.items():
        w[a] = v
    return w


@pytest.fixture
async def elt():
    block = ModbusSequentialDataBlock(BASE + 1, _words())
    ctx = ModbusServerContext(devices=ModbusDeviceContext(hr=block), single=True)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = ModbusTcpServer(ctx, address=("127.0.0.1", port))
    task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0.3)
    yield port, server
    await server.shutdown()
    task.cancel()


async def _setup(hass: HomeAssistant, port: int, phases: str, grid_ct: bool = False):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"host": "127.0.0.1", "port": port, "unit_id": 1})
    assert r["step_id"] == "phases"
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"phases": phases, "grid_ct": grid_ct})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return r["result"]


def _eid(hass, key):
    return er.async_get(hass).async_get_entity_id(
        "sensor" if key not in ("set_power",) else "number", DOMAIN, f"B05000000999_{key}")


async def test_three_phase(hass: HomeAssistant, elt):
    port, _server = elt
    entry = await _setup(hass, port, "3")
    assert entry.title == "ELT-12 (0999)"
    assert hass.states.get(_eid(hass, "soc")).state == "63.0"
    assert hass.states.get(_eid(hass, "charge_status")).state == "discharging"
    assert hass.states.get(_eid(hass, "dc_current")).state == "-46.4"
    assert hass.states.get(_eid(hass, "active_power")).state == "2210"
    assert _eid(hass, "active_power_c") is not None

    num = _eid(hass, "set_power")
    st = hass.states.get(num)
    assert float(st.state) == 2203
    assert st.attributes["min"] == -12000 and st.attributes["max"] == 11160
    await hass.services.async_call(
        "number", "set_value", {"entity_id": num, "value": -1500}, blocking=True)
    assert float(hass.states.get(num).state) == -1500

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_single_phase(hass: HomeAssistant, elt):
    port, _ = elt
    await _setup(hass, port, "1")
    assert _eid(hass, "active_power_b") is None
    assert hass.states.get(_eid(hass, "active_power")).state == "700"


async def test_cannot_connect(hass: HomeAssistant):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"host": "127.0.0.1", "port": 1, "unit_id": 1})
    assert r["errors"] == {"base": "cannot_connect"}


async def test_form_uses_input_boxes(hass: HomeAssistant):
    """Port and unit ID are typed in a box, not picked on a slider."""
    import probatio  # what the frontend view serialises flow forms with
    from homeassistant.helpers import config_validation as cv
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    fields = {f["name"]: f for f in probatio.to_field_list(
        r["data_schema"], custom_serializer=cv.custom_serializer)}
    for name in ("port", "unit_id"):
        assert fields[name]["selector"]["number"]["mode"] == "box"


async def test_options_flow(hass: HomeAssistant, elt):
    port, _ = elt
    entry = await _setup(hass, port, "3")
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {"scan_interval": 10.0, "phases": "3", "setpoint_keepalive": 0.0})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["scan_interval"] == 10 and isinstance(entry.options["scan_interval"], int)
    await hass.async_block_till_done()


async def test_uses_shared_connection(hass: HomeAssistant, elt):
    """The ELT serves a single client, so everything rides on the modbus
    integration's one link: the entry holds its unit there, a new identify
    while it is loaded reuses that link, and unloading closes it."""
    from homeassistant.components.modbus.connection import async_get_connection_info

    port, _ = elt
    entry = await _setup(hass, port, "3")
    info = async_get_connection_info(hass)
    assert len(info) == 1
    assert info[0].endpoint == ("tcp", "127.0.0.1", port)
    assert info[0].units == {entry.entry_id: [1]}

    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"host": "127.0.0.1", "port": port, "unit_id": 1})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured", r
    assert len(async_get_connection_info(hass)) == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert async_get_connection_info(hass) == []


async def test_options_reload_without_update_listener(hass: HomeAssistant, elt, caplog):
    """Options changes reload through OptionsFlowWithReload; an update
    listener would draw a deprecation warning from Home Assistant."""
    port, _ = elt
    entry = await _setup(hass, port, "3")
    assert not entry.update_listeners
    r = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        r["flow_id"], {"scan_interval": 9.0, "phases": "3", "setpoint_keepalive": 0.0})
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval.total_seconds() == 9

    # Same device added again: updates the host, no update listener warning.
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"host": "127.0.0.1", "port": port, "unit_id": 1})
    assert r["reason"] == "already_configured"
    await hass.async_block_till_done()
    assert "update listener" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_placeholder_title_is_renamed(hass: HomeAssistant, elt):
    """Entries made before 0.1.2 are called "Model (xxxx)": renamed at setup
    from the serial prefix, device included. A title the user chose stays."""
    from homeassistant.helpers import device_registry as dr

    port, _ = elt
    entry = await _setup(hass, port, "3")
    assert entry.title == "ELT-12 (0999)"

    hass.config_entries.async_update_entry(entry, title="Model (0999)")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.title == "ELT-12 (0999)"
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.unique_id), entry.entry_id)
    assert device.name == "ELT-12 (0999)" and device.model == "ELT-12"

    hass.config_entries.async_update_entry(entry, title="Batterie garage")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.title == "Batterie garage"
    assert await hass.config_entries.async_unload(entry.entry_id)


def _reg_eid(hass, key, domain="sensor"):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"B05000000999_{key}")


async def test_groups_of_a_new_entry(hass: HomeAssistant, elt):
    """New entries: grid CT entities only when the user says they are wired,
    experimental block off, the raw 40147 sensor disabled by default."""
    port, _ = elt
    entry = await _setup(hass, port, "3")
    assert _reg_eid(hass, "grid_power") is None
    assert _reg_eid(hass, "pv_energy_today") is None
    assert _reg_eid(hass, "soc") and _reg_eid(hass, "daily_discharge_energy")
    raw = er.async_get(hass).async_get(_reg_eid(hass, "daily_discharge_energy_raw"))
    assert raw.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_grid_ct_wired(hass: HomeAssistant, elt):
    port, _ = elt
    await _setup(hass, port, "3", grid_ct=True)
    assert _reg_eid(hass, "grid_power") is not None


async def test_entry_without_groups_keeps_everything(hass: HomeAssistant, elt):
    """Entries made before entity groups existed keep the grid entities."""
    port, _ = elt
    entry = await _setup(hass, port, "3")
    hass.config_entries.async_update_entry(entry, options={})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _reg_eid(hass, "grid_power") is not None
    assert _reg_eid(hass, "pv_energy_today") is None


async def test_deselected_group_is_removed(hass: HomeAssistant, elt):
    port, _ = elt
    entry = await _setup(hass, port, "3", grid_ct=True)
    assert _reg_eid(hass, "battery_voltage") is not None
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "scan_interval": 5, "phases": "3", "setpoint_keepalive": 0, "connection_max_age": 0,
        "entity_groups": ["elt_ac", "energy", "diagnostic"], "detail_interval": 60,
    })
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert _reg_eid(hass, "battery_voltage") is None  # battery DC group off
    assert _reg_eid(hass, "grid_power") is None
    assert hass.states.get(_reg_eid(hass, "soc")).state == "63.0"  # essential stays
    assert _reg_eid(hass, "set_power", "number") is not None


async def test_daily_discharge_from_total(hass: HomeAssistant, elt):
    """40147 counts one phase only: today's discharge comes from 40150."""
    port, server = elt
    entry = await _setup(hass, port, "3")
    eid = _reg_eid(hass, "daily_discharge_energy")
    assert hass.states.get(eid).state == "0.0"
    # +30.50 kWh discharged (low word of 40150-40151)
    await server.context.async_setValues(1, 6, BASE + 151, [50000 + 3050])
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(eid).state) == 30.5


async def test_experimental_block(hass: HomeAssistant, elt):
    port, _ = elt
    entry = await _setup(hass, port, "3")
    r = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(r["flow_id"], {
        "scan_interval": 5, "phases": "3", "setpoint_keepalive": 0, "connection_max_age": 0,
        "entity_groups": ["battery_dc", "elt_ac", "energy", "diagnostic", "detail_energy"],
        "detail_interval": 60,
    })
    await hass.async_block_till_done()
    assert entry.runtime_data.detail_ok is True
    assert float(hass.states.get(_reg_eid(hass, "pv_energy_today")).state) == 4.705
    assert hass.states.get(_reg_eid(hass, "net_energy_today")).state == "0.0"
