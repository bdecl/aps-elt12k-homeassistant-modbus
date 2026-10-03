"""Load registers.py and hub.py without importing Home Assistant."""
import importlib.util
import pathlib
import sys
import types

PKG = "apsys"
ROOT = pathlib.Path(__file__).parents[1] / "custom_components" / "apsystems_storage"

pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
sys.modules[PKG] = pkg
for name in ("registers", "hub"):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
