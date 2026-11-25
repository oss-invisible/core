"""captures/WINDOW_GEOMETRY_REQUEST.json: what the next window-geometry capture is of.

    {"firefox_version": "159.0", "dpr": [1, 1.25, 1.5, 2], "launches": ["a", "b"], "resolutions": [[1920, 1080]]}

    python scripts/retail/window_geometry/request.py [file]    prints firefox_version=... and dpr=[labels] (the job matrix)
"""
from __future__ import annotations

import json
import string
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import capture_request  # noqa: E402
import frame_table  # noqa: E402

REQUEST = HERE.parents[2] / "captures" / "WINDOW_GEOMETRY_REQUEST.json"
KEYS = {"firefox_version", "dpr", "launches", "resolutions"}
#: Launch letter of the run made with the scale as a preference (run_scale.py), never named by the request.
DERIVED_LAUNCH = "p"


class RequestError(ValueError):
    """The request file is not one the workflow can run."""


def validate(request: Any, table: Dict[float, Any] = None) -> Dict[str, Any]:
    if not isinstance(request, dict):
        raise RequestError("the request must be one JSON object")
    missing, extra = KEYS - set(request), set(request) - KEYS
    if missing or extra:
        raise RequestError(f"keys: lacks {sorted(missing)}, unknown {sorted(extra)}")
    try:
        capture_request.validate({"firefox_version": request["firefox_version"], "dpr": [1]})
    except capture_request.RequestError as exc:
        raise RequestError(str(exc)) from None
    table = frame_table.load() if table is None else table
    dpr = request["dpr"]
    if not isinstance(dpr, list) or not dpr:
        raise RequestError("dpr must be a non-empty list")
    for value in dpr:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) not in table:
            raise RequestError(f"dpr {value!r} is not a scale of WINDOW_FRAME_BY_DPR {sorted(table)}")
    if len({float(v) for v in dpr}) != len(dpr):
        raise RequestError("dpr names a scale twice")
    launches = request["launches"]
    if (not isinstance(launches, list) or len(set(launches)) < 2 or len(set(launches)) != len(launches)
            or any(not (isinstance(x, str) and len(x) == 1 and x in string.ascii_lowercase) for x in launches)
            or DERIVED_LAUNCH in launches):
        raise RequestError(f"launches must be at least two distinct lowercase letters, not {DERIVED_LAUNCH!r}")
    resolutions = request["resolutions"]
    if not isinstance(resolutions, list) or not resolutions:
        raise RequestError("resolutions must be a non-empty list of [width, height]")
    for item in resolutions:
        if (not isinstance(item, list) or len(item) != 2
                or any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in item)):
            raise RequestError(f"resolution {item!r} is not [width, height] of positive integers")
    if len({tuple(r) for r in resolutions}) != len(resolutions):
        raise RequestError("resolutions names a size twice")
    return request


def load(path: Path = REQUEST) -> Dict[str, Any]:
    try:
        return validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise RequestError(f"not JSON: {exc}") from None


def labels(request: Dict[str, Any]) -> List[str]:
    return [capture_request.label(float(d)) for d in request["dpr"]]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        request = load(Path(argv[0]) if argv else REQUEST)
    except (RequestError, OSError) as exc:
        print(f"WINDOW_GEOMETRY_REQUEST.json: {exc}", file=sys.stderr)
        return 2
    print(f"firefox_version={request['firefox_version']}")
    print(f"dpr={json.dumps(labels(request), separators=(',', ':'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
