"""examples/dashboard.yaml only references entities that really exist.

The integration (3-phase, simulated device) and the self-consumption package
are loaded, then every entity id found in the dashboard (after replacing the
elt_12_xxxx placeholder) must have a state.
"""
import pathlib

from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import parse_yaml

from test_integration import _setup
from test_self_consumption import _load_package, device  # noqa: F401  (fixture)

DASHBOARD = pathlib.Path(__file__).parents[1] / "examples" / "dashboard.yaml"


def _entity_ids(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "entity" and isinstance(value, str):
                yield value
            else:
                yield from _entity_ids(value)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, str) and "." in item and " " not in item:
                yield item
            else:
                yield from _entity_ids(item)


async def test_dashboard_entities_exist(hass: HomeAssistant, device):  # noqa: F811
    port, _server = device
    await _setup(hass, port, "3")
    await _load_package(hass)
    config = parse_yaml(DASHBOARD.read_text().replace("elt_12_xxxx", "elt_12_0999"))
    ids = sorted(set(_entity_ids(config["views"])))
    assert len(ids) > 20
    missing = [e for e in ids if hass.states.get(e) is None]
    assert not missing, f"entities used by the dashboard but not created: {missing}"
