"""Constants for the APsystems Storage integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "apsystems_storage"
MANUFACTURER = "APsystems"

CONF_UNIT = "unit_id"
CONF_PHASES = "phases"
CONF_KEEPALIVE = "setpoint_keepalive"

DEFAULT_PORT = 502
DEFAULT_UNIT = 1
DEFAULT_SCAN_INTERVAL = 5  # s, one request per cycle when the block read works
MIN_SCAN_INTERVAL = 2
COMMON_REFRESH = 3600  # s, identity block (strings, address)
DEFAULT_KEEPALIVE = 0  # s, 0 = do not rewrite the power setpoint periodically
FALLBACK_MAX_POWER = 12000  # W, used until max charge/discharge rates are read

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SENSOR]
