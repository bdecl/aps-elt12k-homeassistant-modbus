"""Constants for the APsystems Storage integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "apsystems_storage"
MANUFACTURER = "APsystems"

CONF_UNIT = "unit_id"
CONF_PHASES = "phases"
CONF_KEEPALIVE = "setpoint_keepalive"
CONF_MAX_AGE = "connection_max_age"
CONF_GROUPS = "entity_groups"
CONF_DETAIL_INTERVAL = "detail_interval"
CONF_GRID_CT = "grid_ct"

# Entity groups the user can switch off. The essential group (SoC, battery
# power, status, setpoint, reserves) always exists: control depends on it.
# Groups only decide which entities exist: the storage block is read in one
# request whatever is selected. Only GROUP_DETAIL adds a request.
GROUP_DC = "battery_dc"
GROUP_AC = "elt_ac"
GROUP_GRID = "grid_ct"
GROUP_ENERGY = "energy"
GROUP_DIAG = "diagnostic"
GROUP_DETAIL = "detail_energy"
GROUPS = [GROUP_DC, GROUP_AC, GROUP_GRID, GROUP_ENERGY, GROUP_DIAG, GROUP_DETAIL]
# New entries: grid CT only if the user says they are wired; experimental off.
DEFAULT_GROUPS = [GROUP_DC, GROUP_AC, GROUP_ENERGY, GROUP_DIAG]
# Entries made before 0.3.0 keep every entity they had.
LEGACY_GROUPS = [GROUP_DC, GROUP_AC, GROUP_GRID, GROUP_ENERGY, GROUP_DIAG]

DEFAULT_PORT = 502
DEFAULT_UNIT = 1
DEFAULT_SCAN_INTERVAL = 5  # s, one request per cycle when the block read works
MIN_SCAN_INTERVAL = 2
COMMON_REFRESH = 3600  # s, identity block (strings, address)
DEFAULT_KEEPALIVE = 0  # s, 0 = do not rewrite the power setpoint periodically
DEFAULT_MAX_AGE = 0  # s, 0 = keep the Modbus link open (it is renewed after a failed request anyway)
DEFAULT_DETAIL_INTERVAL = 60  # s, experimental block (daily energies, slow)
MIN_DETAIL_INTERVAL = 10
FALLBACK_MAX_POWER = 12000  # W, used until max charge/discharge rates are read

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SENSOR]
