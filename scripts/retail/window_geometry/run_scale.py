"""The job entry point: one display scale, every launch, written under OUT/dpr<x>/.

    python scripts/retail/window_geometry/run_scale.py --install DIR --build build.json --request FILE --dpr 1.25 --out DIR

For each resolution of the request that the display accepts (in the order asked): set the resolution, then the
scale, and measure. The first accepted resolution gets every launch of the request (a, b, ...; the judge compares
them); every other accepted resolution gets launch "a" only (what the window position depends on). Last, the
display goes back to 100% and one launch, "p", runs with the scale as the preference layout.css.devPixelsPerPx
(the derived path). Layout:

    OUT/dpr<x>/<width>x<height>/<launch>/raw.json      and      OUT/dpr<x>/meta.json

A scale Windows would not take is recorded (`dpi_reached: false`, with the reason) and is not an error here.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import display  # noqa: E402
import measure  # noqa: E402
import request as request_file  # noqa: E402
import capture_request  # noqa: E402

MARIONETTE_EXCEPTION = (
    "This capture launches retail Firefox with -marionette, unlike retail-capture.yml (plain launch), because "
    "reproducing Juggler's resize needs chrome-privileged script: the restored window is produced by exactly the "
    "line Juggler runs (TargetRegistry.js setViewportSize: window.resizeBy). Proof that Marionette does not change "
    "the frame: at every scale reached live, the maximized geometry measured here must equal invisible_core "
    "WINDOW_FRAME_BY_DPR (measured from plain launches) exactly, or the scale is not valid (judge.py).")
COMMAND_IN_WORDS = (
    "Per scale: set each requested resolution, then the Windows display scale (DisplayConfig -3/-4), read the "
    "effective DPI back; launch retail Firefox -no-remote -profile <fresh dir> -marionette <page>; maximize the "
    "window, move the mouse, record; run Juggler's resizeBy line; move the mouse, record. Last, 100% scale and "
    "layout.css.devPixelsPerPx = the scale (launch p).")
PROOF_RULE = "judge.py: launch a equals launch b; at a scale reached live, the maximized step equals WINDOW_FRAME_BY_DPR."


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def folder(resolution: List[int]) -> str:
    return f"{resolution[0]}x{resolution[1]}"


def launches_for(request: Dict[str, Any], first: bool) -> List[str]:
    """Every launch at the first accepted resolution, the first launch alone at the others."""
    return request["launches"] if first else request["launches"][:1]


def run(install_dir: Path, build: Dict[str, Any], request: Dict[str, Any], dpr: float, out: Path) -> int:
    display.enable_dpi_awareness()
    base = out / f"dpr{capture_request.label(dpr)}"
    problems, raws = 0, []

    def take(resolution: List[int], launch: str, shown: Dict[str, Any], mode: str = "display", prefs: Optional[dict] = None) -> None:
        nonlocal problems
        raw = measure.measure(install_dir, dpr, launch, resolution, shown, build, mode, prefs)
        raws.append(measure.write_raw(raw, base / folder(resolution) / launch))
        problems += raw["error"] is not None or raw["processes_left"] != 0
        print(f"dpr {dpr} {folder(resolution)} {launch}: error={raw['error']} left={raw['processes_left']} "
              f"dpi_reached={shown.get('dpi_reached')}", flush=True)

    shown_by_resolution: Dict[str, Dict[str, Any]] = {}
    accepted: List[List[int]] = []
    for resolution in request["resolutions"]:
        shown = display.apply(dpr, tuple(resolution))
        shown_by_resolution[folder(resolution)] = shown
        if shown["resolution"].get("ok"):
            accepted.append(resolution)
            for launch in launches_for(request, first=len(accepted) == 1):
                take(resolution, launch, shown)
    if not accepted:  # no requested resolution could be set: the display as it is, named by what it is
        now = display.current_resolution()
        take(now, request["launches"][0], display.apply(dpr, None))
    first = accepted[0] if accepted else display.current_resolution()
    derived = display.apply(1.0, tuple(first))  # back to 100% for the derived path
    derived["dpr"] = dpr
    take(first, "p", derived, "devPixelsPerPx", {"layout.css.devPixelsPerPx": "%g" % dpr})
    meta = {"format": "retail-window-geometry-meta/1", "dpr": dpr, "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "firefox_version": build.get("firefox_version"), "build_id": build.get("build_id"),
            "installer_sha256": build.get("installer_sha256"), "command_in_words": COMMAND_IN_WORDS,
            "marionette_exception": MARIONETTE_EXCEPTION, "proof_rule": PROOF_RULE, "display": shown_by_resolution,
            "raw_sha256": {p.relative_to(base).as_posix(): sha256(p) for p in sorted(raws)}}
    (base / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 1 if problems else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", type=Path, required=True)
    ap.add_argument("--build", type=Path, required=True)
    ap.add_argument("--request", type=Path, default=request_file.REQUEST)
    ap.add_argument("--dpr", type=float, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    request = request_file.load(args.request)
    if args.dpr not in [float(d) for d in request["dpr"]]:
        print(f"run_scale: dpr {args.dpr} is not in the request", file=sys.stderr)
        return 2
    return run(args.install.resolve(), json.loads(args.build.read_text(encoding="utf-8")), request, args.dpr, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
