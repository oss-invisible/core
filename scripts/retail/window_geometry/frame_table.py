"""What invisible_core declares for the window frame, read from the checkout without installing the package.

The judge compares a measurement with what the checked-out core declares today: per display scale the row of
WINDOW_FRAME_BY_DPR (border, popup frame, ...) and the chrome height a maximized window declares for a given
availHeight (declared_maximized_ui, the density rule). Both come from src/invisible_core/constants.py itself, through
the core's own functions: nothing is retyped here.

constants.py imports its siblings by relative import (seal, _retail, _version), and those import only the standard
library, so the judge job, which has a checkout and Python and nothing installed, loads the package folder under a
private name (`_window_geometry_core`) with importlib.util.spec_from_file_location. It is not `invisible_core`, so a
process that has the real package imported (the tests) keeps it untouched.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[3]
PACKAGE = REPO / "src" / "invisible_core"
CONSTANTS = PACKAGE / "constants.py"
PRIVATE_NAME = "_window_geometry_core"
NAME = "WINDOW_FRAME_BY_DPR"


def core_constants(path: Path = CONSTANTS) -> types.ModuleType:
    """constants.py of the checkout, executed by file path inside a private package named after the folder it is in."""
    path = Path(path).resolve()
    package = f"{PRIVATE_NAME}_{abs(hash(str(path)))}"
    if package + ".constants" in sys.modules:
        return sys.modules[package + ".constants"]
    parent = types.ModuleType(package)
    parent.__path__ = [str(path.parent)]
    sys.modules[package] = parent
    spec = importlib.util.spec_from_file_location(package + ".constants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        for name in [n for n in sys.modules if n == package or n.startswith(package + ".")]:
            del sys.modules[name]
        raise
    return module


def load(path: Path = CONSTANTS) -> Dict[float, Any]:
    """{dpr: WindowFrame} exactly as the core has it (border, popup_ui, popup_position, ui_normal, ui_compact, ...)."""
    table = getattr(core_constants(path), NAME, None)
    if not table:
        raise LookupError(f"{NAME} is not in {path}")
    return {float(dpr): frame for dpr, frame in table.items()}


def declared_maximized_ui(frame: Any, avail_height_css: float, path: Path = CONSTANTS) -> int:
    """The chrome height (device px) a maximized window declares at this availHeight (CSS): the core's density rule."""
    return core_constants(path).declared_maximized_ui(frame, avail_height_css)
