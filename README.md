# APS ELT-12K – Home Assistant Modbus configuration

This repository provides a tested Modbus TCP configuration for integrating
the **APS ELT-12K** energy storage system into **Home Assistant**.

It exposes battery, PCS, grid and energy metrics using the native
Home Assistant Modbus integration.

---

## Features

- Battery SoC / SoH
- Charge & discharge power
- DC bus voltage & current
- Grid power per phase
- Daily and total energy counters
- Temperatures (battery & PCS)
- Firmware / version information

Optimized scan intervals are used to reduce Modbus load while keeping
fast-changing values responsive.

---

## Requirements

- Home Assistant
- Modbus TCP enabled on the ELT-12K
- Network access to the device (default port: `502`)

!! When modbus enabled, the internal modes will not work any more. Charge, discharge has to be controlled with the battery set power modbus parameter.

---

## Installation

1. Copy `modbus/elt12k.yaml` into your Home Assistant configuration.
2. Merge it into your existing `modbus:` section if needed.
3. Restart Home Assistant.

Example:
```yaml
modbus: !include modbus/elt12k.yaml
```

---

## Controlling charge / discharge: `apsstorage_power_set_script`

With Modbus enabled, the ELT-12K no longer runs its internal modes. The
battery only does what the **power setpoint register (40183)** tells it to.
`modbus/apsstorage_power_set_script` is an example Home Assistant script
(`script.elt_write_power_signed`) that writes this register safely.

Your own logic (zero-export controller, tariff schedule, manual slider…)
decides *how much* power you want. It then calls this script, which takes
care of *how* that value gets written to the inverter.

### Sign convention

| Value | Meaning |
|------:|---------|
| `< 0` | Charge the battery (e.g. `-1500` = charge at 1.5 kW) |
| `0`   | Idle |
| `> 0` | Discharge the battery (e.g. `2000` = discharge at 2 kW) |

Register 40183 is a signed 16-bit value (`int16`). `modbus.write_register`
only accepts unsigned values, so the script converts negative values to
two's complement (`65536 + v`), so `-1500` is written as `64036`.
The `ELT Set Power Raw` sensor in `elt12k.yaml` reads the register back
as `int16`. You can use it to check that the value you wrote actually landed.

### What the script does

Each time it is called, the script runs these steps in order:

1. **Ramp.** It moves from the last value sent towards the requested
   `power`, by at most `elt_ramp_step_w × dt` watts per call. This avoids
   sudden steps of several kW on the PCS and smooths out control loops.
   If the step is `0` or less, the target is applied directly.
2. **Safety gates.**
   - Discharge requested but `binary_sensor.elt_soc_allows_discharge` is off → the setpoint is forced to **0**.
   - Charge requested but `binary_sensor.elt_soc_allows_charge` is off → charge is limited to **1.5 kW** (`max(v, -1500)`), not blocked completely.
3. **Hard clamp** to ±12 000 W (the ELT-12K rating).
4. **Write only when needed.** The register is written only when the new
   value differs from the last one sent, **or** when the last write is older
   than `elt_write_heartbeat_s` (heartbeat). This keeps Modbus traffic low but
   still refreshes the setpoint on a regular basis, and a lost write gets
   corrected automatically.
5. **Write** to register 40183 (hub `ELT12K`, slave `1`). The value is passed
   as a scalar, so a single-register write (FC6) is used.
6. **Remember** the value sent in `input_number.elt_last_sent_power_w`.

The script runs in `mode: restart`. A new call cancels a run that has not
finished yet, so the most recent setpoint always wins.

### Script fields

| Field   | Type | Description |
|---------|------|-------------|
| `power` | int (W) | Requested signed power. Charge < 0, discharge > 0. |
| `dt`    | float (s) | Time since the previous call, used to scale the ramp. Default `2`. |

### Required helpers

The script depends on a few helpers that are **not** included in this
repository. You need to create them yourself, in the UI or in YAML:

```yaml
input_number:
  elt_last_sent_power_w:
    name: ELT last sent power
    min: -12000
    max: 12000
    step: 1
    unit_of_measurement: W
    mode: box
  elt_ramp_step_w:
    name: ELT ramp rate
    min: 0          # 0 = no ramp, setpoint applied immediately
    max: 5000
    step: 50
    unit_of_measurement: W/s   # multiplied by dt in the script
    initial: 500
  elt_write_heartbeat_s:
    name: ELT write heartbeat
    min: 5
    max: 300
    step: 5
    unit_of_measurement: s
    initial: 30
```

You also need two binary sensors that decide whether charging or
discharging is allowed. Here is a minimal example based on the sensors in
`elt12k.yaml`. Adapt the conditions to your own battery. For example, you
can add a battery-voltage hysteresis, or block discharge while a PV export
limit is active.

```yaml
template:
  - binary_sensor:
      - name: elt_soc_allows_discharge
        unique_id: elt_soc_allows_discharge
        state: >
          {{ states('sensor.elt_battery_soc') | float(0)
               > states('sensor.elt_soc_reserve_min') | float(0)
             and states('sensor.elt_max_discharge_rate') | float(0) > 0 }}
      - name: elt_soc_allows_charge
        unique_id: elt_soc_allows_charge
        state: >
          {{ states('sensor.elt_battery_soc') | float(0)
               < states('sensor.elt_soc_max') | float(100)
             and states('sensor.elt_max_charge_rate') | float(0) > 0 }}
```

### Installation

The file starts with a top-level `script:` key, so the easiest way to load
it is as a [package](https://www.home-assistant.io/docs/configuration/packages/):

```yaml
homeassistant:
  packages:
    elt_power_set: !include modbus/apsstorage_power_set_script
```

Another option is to copy the content under `elt_write_power_signed:` into
your `scripts.yaml`, without the `script:` line.

Check that `hub: ELT12K` matches the `name:` of your Modbus hub.

### Usage examples

Call the script from an automation or another script:

```yaml
- action: script.elt_write_power_signed
  data:
    power: -2000   # charge at 2 kW
    dt: 2          # called every ~2 s
```

Here is a manual control example, where an `input_number` slider drives
the battery:

```yaml
automation:
  - alias: ELT - manual setpoint
    triggers:
      - trigger: state
        entity_id: input_number.elt_set_power_w
      - trigger: time_pattern
        seconds: "/10"
    actions:
      - action: script.elt_write_power_signed
        data:
          power: "{{ states('input_number.elt_set_power_w') | int(0) }}"
          dt: 10
```

Calling the script periodically is useful: it lets the ramp reach the
target step by step, and it lets the heartbeat run.

### Notes and pitfalls

- **`dt` should match your real call interval.** If you call the script
  every 2 s but pass `dt: 10`, the ramp will be 5× faster than you expect.
- **The ramp starts from the last value *sent*, not from the measured
  battery power.** If you change `elt_last_sent_power_w` by hand, the next
  ramp starts from that value.
- **Avoid constant fallback values in upstream controllers.** If your
  controller keeps sending the same clamped value, only the heartbeat
  triggers a rewrite. Make sure the heartbeat is enabled (`> 0`).
- **Default `slave`.** In recent Home Assistant versions, the default
  slave for `modbus.write_register` changed. The script sets `slave: 1`
  explicitly. Keep it that way.
- Remember: once Modbus control is enabled, **nothing** charges or
  discharges the battery unless you write to 40183.
