"""Load a module of scripts/retail/ by its own name, the way its siblings import it.

The retail scripts import each other as plain modules (`import capture_format as cf`), so they are
loaded with scripts/retail on sys.path; the same module object is then what a script and a test see.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
RETAIL = ROOT / "scripts" / "retail"


def load(name: str) -> ModuleType:
    sys.path.insert(0, str(RETAIL))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(RETAIL))
