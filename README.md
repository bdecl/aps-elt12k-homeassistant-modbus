# APsystems ELT-12K / ELS – Home Assistant Modbus

This repository connects the **APsystems ELT-12K** energy storage system
(and, experimentally, the **ELS-5K / ELS-11.4**, which share the same Modbus
protocol) to **Home Assistant** over Modbus TCP.

Two ways to use it:

- **Option A – custom integration** (`custom_components/apsystems_storage`),
  installable with HACS, configured from the UI. Recommended.
- **Option B – YAML configuration** for the native Home Assistant Modbus
  integration (`modbus/`), as before.

> **Use one or the other, never both.** The ELT serves a **single Modbus
> client**: a second connection is accepted at TCP level but never answered.

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
- A wired Ethernet link on the interface dedicated to Modbus TCP (see below)

### Network interface

**Use the dedicated wired network interface.** The Modbus TCP server is only
served on the wired Ethernet port dedicated to it. It is not available over
Wi-Fi: if Home Assistant points at the unit's Wi-Fi address, port 502 simply
does not answer.

Give that interface a fixed address (DHCP reservation or static IP) so the
`host:` in the hub definition keeps working after a reboot.

!! When modbus enabled, the internal modes will not work any more. Charge, discharge has to be controlled with the battery set power modbus parameter.

---

## Option A – Home Assistant integration (HACS)

### Install

1. In HACS → *Custom repositories*, add this repository as type **Integration**.
2. Install **APsystems Storage (ELT / ELS) Modbus** and restart Home Assistant.
3. **Remove any `modbus:` hub pointing at the device** (Option B) and restart,
   so the single Modbus slot is free.
4. *Settings → Devices & services → Add integration → APsystems Storage*.
   Enter the IP of the **wired** Modbus interface, port `502`, unit ID `1`.
5. Confirm the number of phases (three-phase is preselected for the ELT-12).

### What you get

| Platform | Entities |
|---|---|
| Sensor | SoC, SoH, charge status, battery power/voltage, DC bus voltage, DC current, active & reactive power per phase, grid power per phase, phase totals, daily/total charge & discharge energy, battery & PCS temperatures |
| Sensor (diagnostic) | capacity, max charge/discharge rate, SoC max/min, battery & PCS event bitfields (decoded in the `active` attribute), chip versions, controller heartbeat |
| Binary sensor | Battery alarm, PCS alarm (`problem`), with the active flags as attribute |
| Number | **Power setpoint** (register 40183, W, `< 0` charge, `> 0` discharge, `0` standby), **SoC reserve min / max** |

L2/L3 entities are only created for three-phase units. Single-phase units
(ELS) get L1 and the totals.

### How it reads the device

- One persistent connection, all requests serialized.
- The whole storage model (40073–40183) is read in **one request per cycle**
  (default every 5 s). If a firmware rejects reads spanning undocumented
  addresses, the integration switches automatically to one request per
  contiguous run of documented registers.
- The identity block (serial, versions) is read once per hour.
- Values flagged *not implemented* by SunSpec conventions (`0xFFFF`,
  `0x8000`) show as *unknown* instead of absurd numbers.

### Options

| Option | Default | Meaning |
|---|---|---|
| Polling interval | 5 s | Interval between two reads of the storage block |
| Phases | detected | 1 or 3 |
| Setpoint keepalive | 0 (off) | Rewrite the last power setpoint every *n* seconds |

The integration only **exposes** the device. Ramps, SoC gates and control
loops stay in your automations: set `number.<device>_power_setpoint` from
them with `number.set_value`. If your automation does not refresh the setpoint
itself, the keepalive option rewrites the last value periodically.

### Scale factors

Scales match the values measured on a live ELT-12K. The PDF protocol states
a scale factor of `-2` for the energy capacity (40073), but the device
returns kWh directly. The scale-factor registers reported by the device are
listed in the integration **diagnostics** (*Download diagnostics*): please
attach them to any issue, especially for ELS units.

### Status

- ELT-12K: tested.
- ELS-5K / ELS-11.4: **experimental**, untested. Reports welcome.
- Modbus TCP only for now (no RS485/RS232 serial transport).

---

## Option B – YAML configuration

### Installation

1. Copy `modbus/elt12k.yaml` into your Home Assistant configuration.
2. Merge it into your existing `modbus:` section if needed.
3. Restart Home Assistant.

Example:
```yaml
modbus: !include modbus/elt12k.yaml
```

---

### Controlling charge / discharge: `apsstorage_power_set_script`

With Modbus enabled, the ELT-12K no longer runs its internal modes. The
battery only does what the **power setpoint register (40183)** tells it to.
`modbus/apsstorage_power_set_script` is an example Home Assistant script
(`script.elt_write_power_signed`) that writes this register safely.

Your own logic (zero-export controller, tariff schedule, manual slider…)
decides *how much* power you want. It then calls this script, which takes
care of *how* that value gets written to the inverter.

#### Sign convention

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

#### What the script does

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

#### Script fields

| Field   | Type | Description |
|---------|------|-------------|
| `power` | int (W) | Requested signed power. Charge < 0, discharge > 0. |
| `dt`    | float (s) | Time since the previous call, used to scale the ramp. Default `2`. |

#### Required helpers

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

#### Installation

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

#### Usage examples

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

#### Notes and pitfalls

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
