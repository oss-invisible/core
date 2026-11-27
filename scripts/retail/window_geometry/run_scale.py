"""The job entry point: one display scale, every launch, written under OUT/dpr<x>/.

    python scripts/retail/window_geometry/run_scale.py --install DIR --build build.json --request FILE --dpr 1.25 --out DIR

For each resolution of the request that the display accepts at this scale (resolution set, then the scale, read
back; a scale Windows does not reach gets no display launch at all): the first accepted resolution gets every launch
of the request (a, b, ...; the judge compares them; plan.py says which steps each takes, the grid among them at the
scales of plan.GRID_DPRS), every other accepted resolution gets launch "a" only. Then the display goes back to 100%
and the derived launches run with layout.css.devPixelsPerPx = the scale: "p", and "q" too when no display launch could
be made (so the derived path is compared with itself); in the job of scale 1 also two launches "m" with
devPixelsPerPx 0.75 and 0.5. Then, in the job of scale 2 (plan.NORMAL_DENSITY_DPRS), launch "n": the maximized step and iframes_max
alone, on the first of plan.NORMAL_DENSITY_PANELS (3840x2160, then 2560x1600; a panel whose work area is too short for the
normal UI density is never asked) that the display takes with the scale in force; when none does, or the time budget is spent, a
raw.json of launch "n" says `not_possible` with the reason. Last, for every resolution Windows offers: set it, then the scale, and when the scale is
in force launch "m" and record the maximized step alone (skipped for lack of time past TIME_BUDGET_SECONDS). Layout:

    OUT/dpr<x>/<width>x<height>/<launch>/raw.json      and      OUT/dpr<x>/meta.json

(the devPixelsPerPx launches "m" live in folders m_dppx<value>; launch "n" in <panel>/n). A scale Windows would not take is recorded
(`dpi_reached: false`, with the reason) and is not an error here.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import display  # noqa: E402
import measure  # noqa: E402
import plan  # noqa: E402
import request as request_file  # noqa: E402
import capture_request  # noqa: E402

#: The job has 60 minutes (the workflow; round 5 added about two minutes of fullscreen, popups and iframes to each of the two
#: launches that compare, and to p and q at a scale Windows does not reach); the launches that matter run first, the
#: every-resolution launches stop here, leaving the minutes the setup, the last launch and the upload need.
TIME_BUDGET_SECONDS = 49 * 60

MARIONETTE_EXCEPTION = (
    "This capture launches retail Firefox with -marionette, unlike retail-capture.yml (plain launch), because "
    "reproducing Juggler's resize needs chrome-privileged script (and so MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1, "
    "which Marionette requires for it; no other MOZ_* variable): the restored window is produced by exactly the "
    "line Juggler runs (TargetRegistry.js setViewportSize: window.resizeBy). Proof that Marionette does not change "
    "the frame: at every scale reached live, the maximized geometry measured here must equal invisible_core "
    "WINDOW_FRAME_BY_DPR and declared_maximized_ui (measured from plain launches) exactly, or the scale is not valid "
    "(judge.py).")
COMMAND_IN_WORDS = (
    "Per scale: set each requested resolution, then the Windows display scale (DisplayConfig -3/-4), read the "
    "effective DPI back; launch retail Firefox -no-remote -profile <fresh dir> -marionette <page>; maximize the "
    "window, move the mouse, record; run Juggler's resizeBy line; run Juggler's whole sequence at 390x844 CSS (and "
    "again with inRDMPane), a second and a third call at 414 CSS wide (the same size twice) and a call back to the maximized content size; "
    "from the maximized window, before any Juggler call, element fullscreen by a real click and F11-style fullscreen, "
    "a same-origin and a cross-site iframe, and two popups opened by a real click; after the second call, the same "
    "call again, the fullscreen steps and the iframes on the window Juggler left; at scales 1 and 1.25 a grid of "
    "restored windows and the minimum window; at scale 1, last, "
    "the collection page host-gate-2.html without and with inRDMPane. Every "
    "resolution Windows offers: launch m, maximized only. Last, 100% scale and layout.css.devPixelsPerPx = the "
    "scale (launches p and q), and in the job of scale 1 devPixelsPerPx 0.75 and 0.5 (launch m); in the job of scale 2 the "
    "maximized window and its iframes on the first panel (3840x2160, then 2560x1600) whose work area holds the normal UI density (launch n).")
PROOF_RULE = ("judge.py: launch a equals launch b (p equals q) on every step, the fullscreen, popup and iframe steps among them; "
              "a step of round 5 is a record, skipped, or not possible with its reason; at every scale reached live the "
              "maximized step equals WINDOW_FRAME_BY_DPR and declared_maximized_ui; the grid check of launch b equals the rows "
              "of launch a; launch n (normal density at scale 2) is printed against the core's declared chrome height and not judged on it.")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def folder(resolution: Sequence[int]) -> str:
    return f"{resolution[0]}x{resolution[1]}"


def launches_for(request: Dict[str, Any], first: bool) -> List[str]:
    """Every launch at the first accepted resolution, the first launch alone at the others."""
    return request["launches"] if first else request["launches"][:1]


def reached(shown: Dict[str, Any]) -> bool:
    """The resolution was set (or none asked) and the scale is the one in force."""
    resolution = shown.get("resolution")
    return bool(shown.get("dpi_reached")) and (resolution is None or bool(resolution.get("ok")))


def why_not(shown: Dict[str, Any]) -> str:
    """Why a resolution got no display launch: the resolution was not set, or the scale was not reached."""
    resolution = shown.get("resolution") or {}
    if not resolution.get("ok"):
        return f"resolution not set (code {resolution.get('code')}, now {resolution.get('current')})"
    return str(shown.get("reason"))


def derived_launches(display_launches_made: bool) -> List[str]:
    """p alone when a display launch was compared with another; p and q when the derived path has only itself."""
    return ["p"] if display_launches_made else ["p", "q"]


def normal_density_not_possible(dpr: float, tried: List[Dict[str, Any]], reason: str) -> Dict[str, Any]:
    """The raw record of launch n when no panel could be measured: both steps `not_possible` with the reason, and what was tried."""
    panels = plan.normal_density_panels(dpr)
    first = list(panels[0]) if panels else [0, 0]
    return {"format": measure.FORMAT, "dpr_asked": dpr, "mode": "display", "launch": "n", "resolution": first,
            "display": {"role": plan.NORMAL_DENSITY_ROLE, "dpi_reached": False, "reason": reason, "tried": tried,
                        "panels_asked": [list(p) for p in panels]},
            "steps": {name: {"not_possible": reason} for name in plan.NORMAL_DENSITY_STEPS}, "steps_planned": list(plan.NORMAL_DENSITY_STEPS),
            "error": None, "processes_left": 0}


def run(install_dir: Path, build: Dict[str, Any], request: Dict[str, Any], dpr: float, out: Path) -> int:
    started = time.monotonic()
    display.enable_dpi_awareness()
    base = out / f"dpr{capture_request.label(dpr)}"
    problems, raws = 0, []

    def take(resolution: Sequence[int], launch: str, shown: Dict[str, Any], mode: str = "display", prefs: Optional[dict] = None,
             steps: Sequence[str] = plan.CORE_STEPS, subfolder: Optional[str] = None) -> None:
        nonlocal problems
        raw = measure.measure(install_dir, dpr, launch, resolution, shown, build, mode, prefs, steps_wanted=steps)
        written = measure.write_raw(raw, base / folder(resolution) / (subfolder or launch))
        raws.append(written)
        raws.extend(sorted(written.parent.glob("hostgate-*.json")))  # the host-gate results kept beside raw.json
        problems += raw["error"] is not None or raw["processes_left"] != 0
        print(f"dpr {dpr} {folder(resolution)} {subfolder or launch}: error={raw['error']} left={raw['processes_left']} "
              f"dpi_reached={shown.get('dpi_reached')} steps={sorted(raw['steps'])} t={time.monotonic() - started:.0f}s", flush=True)

    shown_by_resolution: Dict[str, Dict[str, Any]] = {}
    skipped: Dict[str, str] = {}
    accepted: List[List[int]] = []  # the resolution was set and the scale reached
    settable: List[List[int]] = []  # the resolution was set, whatever the scale
    offered: Any = None
    for resolution in request["resolutions"]:
        shown = display.apply(dpr, tuple(resolution))
        shown_by_resolution[folder(resolution)] = shown
        offered = offered if isinstance(offered, list) else shown.get("offered_resolutions")
        if shown["resolution"].get("ok"):
            settable.append(resolution)
        if reached(shown):
            accepted.append(resolution)
            first = len(accepted) == 1
            for launch in launches_for(request, first):
                take(resolution, launch, shown, steps=plan.steps_for(dpr, launch, request["launches"], first))
        else:
            skipped[folder(resolution)] = why_not(shown)
    # The derived path: 100% display scale, the scale as a preference.
    first_resolution = (settable or [display.current_resolution()])[0]
    derived = display.apply(1.0, tuple(first_resolution))
    derived["dpr"] = dpr
    shown_by_resolution["derived"] = derived
    for launch in derived_launches(bool(accepted)):
        take(first_resolution, launch, derived, "devPixelsPerPx", {"layout.css.devPixelsPerPx": "%g" % dpr},
             steps=plan.derived_steps(bool(accepted)))
    if float(dpr) == 1.0:
        for value in plan.DEVPX_VALUES:
            label = plan.devpx_label(value)
            take(first_resolution, "m", dict(derived, devPixelsPerPx=value), "devPixelsPerPx", {"layout.css.devPixelsPerPx": label},
                 steps=plan.MAXIMIZED_ONLY, subfolder=f"m_dppx{label}")
    # Round 7: the panel where the work area is tall enough for the normal UI density (scale 2 only).
    normal_density: Dict[str, Any] = {}
    if float(dpr) in plan.NORMAL_DENSITY_DPRS:
        normal_density = {"role": plan.NORMAL_DENSITY_ROLE, "panels_asked": [list(p) for p in plan.normal_density_panels(dpr)],
                          "panels_never_asked": [list(p) for p in plan.NORMAL_DENSITY_PANELS if not plan.qualifies_for_normal_density(p, dpr)],
                          "tried": [], "set": None}
        for panel in plan.normal_density_panels(dpr):
            if time.monotonic() - started > TIME_BUDGET_SECONDS:
                normal_density["tried"].append({"panel": list(panel), "skipped": "time budget"})
                break
            shown = display.apply(dpr, panel)
            shown_by_resolution["n_" + folder(panel)] = shown
            normal_density["tried"].append({"panel": list(panel), "resolution": shown.get("resolution"),
                                            "dpi_reached": shown.get("dpi_reached"), "effective_dpi": shown.get("effective_dpi"),
                                            "reason": None if reached(shown) else why_not(shown)})
            if reached(shown):
                shown["role"], shown["panels_tried"] = plan.NORMAL_DENSITY_ROLE, normal_density["tried"]
                normal_density["set"] = list(panel)
                take(panel, "n", shown, steps=plan.NORMAL_DENSITY_STEPS)
                break
        if normal_density["set"] is None:
            reason = "no panel of the plan could be set with the scale in force: " + "; ".join(
                f"{folder(t['panel'])}: {t.get('skipped') or t.get('reason')}" for t in normal_density["tried"])
            raw = normal_density_not_possible(dpr, normal_density["tried"], reason)
            raws.append(measure.write_raw(raw, base / folder(raw["resolution"]) / "n"))
            print(f"dpr {dpr} launch n: not_possible ({reason})", flush=True)
    # Every resolution Windows offers, at this scale: the maximized step alone.
    for resolution in (offered if isinstance(offered, list) else []):
        name = folder(resolution)
        if time.monotonic() - started > TIME_BUDGET_SECONDS:
            skipped[name] = "time budget"
            continue
        shown = display.apply(dpr, tuple(resolution))
        shown_by_resolution["m_" + name] = shown
        if reached(shown):
            take(resolution, "m", shown, steps=plan.MAXIMIZED_ONLY)
        else:
            skipped[name] = why_not(shown)
    meta = {"format": "retail-window-geometry-meta/2", "dpr": dpr, "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "firefox_version": build.get("firefox_version"), "build_id": build.get("build_id"),
            "installer_sha256": build.get("installer_sha256"), "command_in_words": COMMAND_IN_WORDS,
            "marionette_exception": MARIONETTE_EXCEPTION, "proof_rule": PROOF_RULE, "display": shown_by_resolution,
            "skipped_resolutions": skipped, "normal_density": normal_density, "seconds": round(time.monotonic() - started),
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
