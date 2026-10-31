"""Load the plugin as a package, like the Host (not as top-level modules)."""

from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = "_virtual_travel_test_pkg"
if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = package
    spec.loader.exec_module(package)
