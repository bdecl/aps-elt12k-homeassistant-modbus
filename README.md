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
