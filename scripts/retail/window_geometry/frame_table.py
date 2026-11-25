"""WINDOW_FRAME_BY_DPR as written in the package source, read without importing the package.

The judge compares a measurement with the table of the checked-out ref, so the table is read from
src/invisible_core/constants.py itself (the literal, by ast): nothing is installed and no value is retyped.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict

REPO = Path(__file__).resolve().parents[3]
CONSTANTS = REPO / "src" / "invisible_core" / "constants.py"
NAME = "WINDOW_FRAME_BY_DPR"


def load(path: Path = CONSTANTS) -> Dict[float, Dict[str, int]]:
    """{dpr: {"border": .., "maximized_ui": .., "popup_ui": .., "popup_position": ..}}"""
    for node in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        target = node.target if isinstance(node, ast.AnnAssign) else (node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == NAME:
            table = {}
            for key, call in zip(node.value.keys, node.value.values):
                table[float(ast.literal_eval(key))] = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
            return table
    raise LookupError(f"{NAME} is not in {path}")
