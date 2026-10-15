"""The examples/self_consumption.yaml package, run against the simulated ELT.

Each scenario sets the device registers, triggers the controller once and
checks the setpoint the device actually received (register 40183).
"""
import asyncio
import pathlib
import socket
import time

import pytest
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusTcpServer

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import parse_yaml

from test_integration import BASE, _setup, _words

PACKAGE = pathlib.Path(__file__).parents[1] / "examples" / "self_consumption.yaml"
AUTOMATION = "automation.elt_self_consumption_controller"

# registers written by the test (offset from 40000)
SETPOINT, BATT, SOC, VBAT, STATUS, MAX_CHG, MAX_DIS = 183, 117, 81, 134, 86, 74, 75


async def _set(server, reg: int, value: int) -> None:
    """Change a register of the running simulated device."""
    await server.context.async_setValues(1, 6, BASE + reg, [value & 0xFFFF])


@pytest.fixture
async def device():
    block = ModbusSequentialDataBlock(BASE + 1, _words())
    ctx = ModbusServerContext(devices=ModbusDeviceContext(hr=block), single=True)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = ModbusTcpServer(ctx, address=("127.0.0.1", port))
    task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0.3)
    yield port, server
    await server.shutdown()
    task.cancel()


async def _load_package(hass: HomeAssistant) -> None:
    text = PACKAGE.read_text().replace("elt_12_xxxx", "elt_12_0999").replace(
        "sensor.elt_12_0999_grid_power", "input_number.test_grid"
    )
    pkg = parse_yaml(text)
    pkg["input_number"]["test_grid"] = {"min": -20000, "max": 20000, "step": 1, "mode": "box"}
    for domain in ("input_select", "input_number", "input_boolean", "automation"):
        assert await async_setup_component(hass, domain, {domain: pkg[domain]})
    await hass.async_block_till_done()


@pytest.fixture
async def ctrl(hass: HomeAssistant, device):
    port, server = device
    entry = await _setup(hass, port, "3")
    await _load_package(hass)
    coordinator = entry.runtime_data

    async def run(mode="Self-consumption", grid=0, *, setpoint=0, batt=0, soc=600,
                  vbat=520, status=3, max_chg=12000, max_dis=11160, write_age=1000):
        # Changing the mode triggers the controller by itself: let that run
        # finish before preparing the scenario.
        await hass.services.async_call(
            "input_select", "select_option",
            {"entity_id": "input_select.elt_sc_mode", "option": mode}, blocking=True)
        await hass.async_block_till_done()
        for reg, val in ((SETPOINT, setpoint), (BATT, batt), (SOC, soc), (VBAT, vbat),
                         (STATUS, status), (MAX_CHG, max_chg), (MAX_DIS, max_dis)):
            await _set(server, reg, val)
        await coordinator.async_refresh()
        await hass.services.async_call(
            "input_number", "set_value", {"entity_id": "input_number.test_grid", "value": grid},
            blocking=True)
        await hass.services.async_call(
            "input_number", "set_value",
            {"entity_id": "input_number.elt_sc_last_write_ts", "value": int(time.time()) - write_age},
            blocking=True)
        await hass.services.async_call(
            "automation", "trigger", {"entity_id": AUTOMATION, "skip_condition": True},
            blocking=True)
        await hass.async_block_till_done()
        await coordinator.async_refresh()
        return float(hass.states.get("number.elt_12_0999_power_setpoint").state)

    return run, hass


async def test_off_writes_zero(ctrl):
    run, _ = ctrl
    assert await run("Off", setpoint=2203, batt=2203) == 0


async def test_import_is_covered_by_discharge(ctrl):
    run, _ = ctrl
    # battery idle, house imports 1000 W -> discharge kp * 1000
    assert await run(grid=1000, setpoint=0, batt=0) == 500


async def test_export_is_absorbed_by_charge(ctrl):
    run, _ = ctrl
    assert await run(grid=-2000, setpoint=-1000, batt=-1000) == -2000


async def test_deadband_keeps_setpoint(ctrl):
    run, _ = ctrl
    assert await run(grid=30, setpoint=-1500, batt=-1500) == -1500


async def test_waits_for_battery_to_follow(ctrl):
    run, _ = ctrl
    # just written, battery not there yet -> hold
    assert await run(grid=1000, setpoint=3000, batt=0, write_age=2) == 3000


async def test_restarts_from_measured_after_timeout(ctrl):
    run, _ = ctrl
    # BMS limits charge to 2000 W while -6000 was asked: no wind-up
    assert await run(grid=-1000, setpoint=-6000, batt=-2000, write_age=60) == -2500


async def test_min_soc_with_hysteresis(ctrl):
    run, hass = ctrl
    assert await run(grid=2000, setpoint=0, batt=0, soc=140) == 0
    assert hass.states.get("input_boolean.elt_sc_discharge_ok").state == "off"
    # 16 % is above the minimum but inside the hysteresis band: still blocked
    assert await run(grid=2000, setpoint=0, batt=0, soc=160) == 0
    # 18 % = 15 + 3: discharge resumes
    assert await run(grid=2000, setpoint=0, batt=0, soc=180) == 1000
    assert hass.states.get("input_boolean.elt_sc_discharge_ok").state == "on"


async def test_low_soc_still_allows_charging(ctrl):
    run, _ = ctrl
    assert await run(grid=-1000, setpoint=0, batt=0, soc=100) == -500


async def test_no_soc_taper_near_full(ctrl):
    run, _ = ctrl
    # 95 %, not full yet: only the caps apply, end of charge is the BMS's job
    assert await run(grid=-8000, setpoint=-6000, batt=-6000, soc=950) == -6000


async def test_full_stops_charging(ctrl):
    run, _ = ctrl
    assert await run(grid=-3000, setpoint=-1000, batt=-1000, soc=990, status=5) == 0


async def test_device_limit_applies(ctrl):
    run, _ = ctrl
    assert await run(grid=-9000, setpoint=-1000, batt=-1000, max_chg=1800) == -1800


async def test_manual_is_clamped_by_safety(ctrl):
    run, hass = ctrl
    await hass.services.async_call(
        "input_number", "set_value",
        {"entity_id": "input_number.elt_sc_manual_power_w", "value": 9000}, blocking=True)
    await hass.async_block_till_done()
    assert await run("Manual", setpoint=0, batt=0) == 6000
    assert await run("Manual", setpoint=0, batt=0, soc=100) == 0


async def test_missing_grid_meter_stops_battery(ctrl):
    run, hass = ctrl
    assert await run(grid=1000, setpoint=0, batt=0) == 500
    hass.states.async_set("input_number.test_grid", "unavailable")
    await hass.services.async_call(
        "automation", "trigger", {"entity_id": AUTOMATION, "skip_condition": True},
        blocking=True)
    await hass.async_block_till_done()
    await hass.config_entries.async_entries("apsystems_storage")[0].runtime_data.async_refresh()
    assert float(hass.states.get("number.elt_12_0999_power_setpoint").state) == 0
