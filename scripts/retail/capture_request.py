"""captures/REQUEST.json: what the next runner capture is of.

    {"firefox_version": "159.0", "dpr": [1]}

The retail-capture workflow runs when a pull request or a push changes this file, and
captures exactly what it says. A runner has a display scale of 1 and no way to ask for
another one without a preference, which the capture never sets: `dpr` is [1].

    python scripts/retail/capture_request.py [captures/REQUEST.json]    prints the version and the dpr labels
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
REQUEST = REPO / "captures" / "REQUEST.json"
KEYS = {"firefox_version", "dpr"}
#: Release versions only: 159.0, 159.0.1. A beta, an ESR or a path would not be a retail release of the archive.
VERSION = re.compile(r"[1-9][0-9]{1,3}\.[0-9]{1,2}(\.[0-9]{1,2})?")
#: The display scales a runner can have without a preference.
RUNNER_DPRS = (1,)


class RequestError(ValueError):
    """The request file is not one the workflow can run."""


def validate(request: Any) -> Dict[str, Any]:
    """The request when it is well formed; else a RequestError naming the first thing wrong."""
    if not isinstance(request, dict):
        raise RequestError("REQUEST.json must be one JSON object")
    missing, extra = KEYS - set(request), set(request) - KEYS
    if missing:
        raise RequestError(f"REQUEST.json lacks {sorted(missing)}")
    if extra:
        raise RequestError(f"REQUEST.json has keys the workflow does not read: {sorted(extra)}")
    version = request["firefox_version"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise RequestError(f"firefox_version {version!r} is not a release version like 159.0")
    validate_dpr(request["dpr"])
    return request


def validate_dpr(dpr: Any) -> List[float]:
    if not isinstance(dpr, list) or not dpr:
        raise RequestError("dpr must be a non-empty list")
    for value in dpr:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value not in RUNNER_DPRS:
            raise RequestError(f"dpr {value!r}: a runner captures at {list(RUNNER_DPRS)} only")
    if len(set(dpr)) != len(dpr):
        raise RequestError("dpr names a scale twice")
    return dpr


def load(path: Path = REQUEST) -> Dict[str, Any]:
    try:
        return validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise RequestError(f"REQUEST.json is not JSON: {exc}") from None


def label(dpr: float) -> str:
    """The directory-name form of a display scale: 1, 1.25 (the same spelling as `%g`)."""
    return "%g" % dpr


def labels(request: Dict[str, Any]) -> List[str]:
    return [label(d) for d in request["dpr"]]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        request = load(Path(argv[0]) if argv else REQUEST)
    except (RequestError, OSError) as exc:
        print(f"REQUEST.json: {exc}", file=sys.stderr)
        return 2
    print(f"firefox_version={request['firefox_version']}")
    print(f"dpr={' '.join(labels(request))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
