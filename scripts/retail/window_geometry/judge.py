"""The one verdict of the window-geometry capture: reads every downloaded artifact, prints a table, exits 1 when red.

    python scripts/retail/window_geometry/judge.py --in DIR [--request FILE]

Red when:
  * a scale of the request has no run, a run has an error or leaves a process of the install folder alive, a run lacks
    a step of its launch (a, b, p, q: maximized, restored, juggler, juggler_rdm; m: maximized);
  * launch a differs from launch b (launch p from q) at a resolution that has both, on any number of the four steps
    (the diff is printed); the grid, the minimum and the grid check are not part of that comparison;
  * at the scales of plan.GRID_DPRS, launch a lacks its grid and minimum, launch b lacks its grid check, a grid row is
    an error, or a point of the grid check does not show what launch a shows at that point (content top, size mode);
  * at the scales of plan.HOSTGATE_DPRS, launches a and b lack their `rdm_hostgate` step, or the collection page did not
    post its result WITHOUT inRDMPane (a missing POST WITH inRDMPane is printed as `RDM HANG` and is a finding, not red);
  * at the FIRST resolution of scale 1 (display mode, scale reached live), the MAXIMIZED step is not exactly the row of
    invisible_core WINDOW_FRAME_BY_DPR (read from the source of this checkout): the invisible border
    (GetWindowRect width minus work-area width, halved), screenX = round-half-up(-border / dpr) and
    round(mozInnerScreenY * dpr) = maximized_ui. This is the proof that Marionette does not change the frame.
    Everywhere else the comparison is printed and a difference is "DIFFERS (window-size dependent, see grid)", not red:
    decision of the pm. The table came from Firefox 151 on a different panel, and the chrome height above the content
    depends on the window size in Firefox 159 (85 CSS maximized at 1920x1032 CSS, 72.67 at 1280x688); only at the one
    point where the table and 159 agree in window size is it a proof. That dependency is what the grid measures.
A scale Windows would not take is "not reachable live: derived path" and is not red by itself.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import frame_table  # noqa: E402
import plan  # noqa: E402
import request as request_file  # noqa: E402

STEPS = plan.CORE_STEPS
#: (scale, resolution) where the maximized step must equal the core table (see the docstring).
STRICT_POINT = (1.0, (1920, 1080))


def half_up(value: float) -> int:
    """Rounding as Gecko does it for these values: halves go up."""
    return math.floor(value + 0.5)


def leaves(value: Any, path: str = "") -> Dict[str, Any]:
    if isinstance(value, dict):
        return {k: v for key, item in value.items() for k, v in leaves(item, f"{path}.{key}" if path else key).items()}
    if isinstance(value, list):
        return {k: v for i, item in enumerate(value) for k, v in leaves(item, f"{path}[{i}]").items()}
    return {path: value}


def differences(a: Dict[str, Any], b: Dict[str, Any]) -> List[Tuple[str, Any, Any]]:
    la, lb = leaves(a), leaves(b)
    return [(k, la.get(k), lb.get(k)) for k in sorted(set(la) | set(lb)) if la.get(k) != lb.get(k)]


def derived(step: Dict[str, Any], dpr: float) -> Dict[str, Any]:
    """The numbers of a step the verdict and the table are made of (device pixels unless it says CSS)."""
    native, chrome, content = step["native"], step["chrome"], step["content"]
    left, top, right, bottom = native["window_rect"]
    work = native["work_rect"]
    client = native["client_rect"]
    origin_x, origin_y = native["client_origin"]
    client_right, client_bottom = origin_x + client[2] - client[0], origin_y + client[3] - client[1]
    ui = (origin_y - top) + half_up(chrome["stack"]["y"] * chrome["devicePixelRatio"])
    screen = content.get("screen") or {}
    return {"border": ((right - left) - (work[2] - work[0])) / 2,
            "border_left": origin_x - left, "border_right": right - client_right, "border_bottom": bottom - client_bottom,
            "ui": ui, "position_dev": [left, top], "position_css": [left / dpr, top / dpr],
            "outer_minus_inner_css": [content["outerWidth"] - content["innerWidth"], content["outerHeight"] - content["innerHeight"]],
            "screen_xy": [content["screenX"], content["screenY"]],
            "moz_inner_screen": [content["mozInnerScreenX"], content["mozInnerScreenY"]],
            "events": [content["events"]["count"], content["events"]["dx"], content["events"]["dy"]],
            "page_inner": [content["innerWidth"], content["innerHeight"]], "page_outer": [content["outerWidth"], content["outerHeight"]],
            "screen_wh": [screen.get("width"), screen.get("height")], "event_dxdy": [content["events"]["dx"], content["events"]["dy"]],
            "client_rect": list(client), "window_rect": [left, top, right, bottom],
            "work_css": [(work[2] - work[0]) / dpr, (work[3] - work[1]) / dpr]}


def check_maximized(run: Dict[str, Any], table: Dict[float, Dict[str, int]]) -> List[Tuple[str, str, Any, Any, bool]]:
    """(name, what, got, expected, ok) for the three comparisons with the core table."""
    dpr = float(run["dpr_asked"])
    row = table[dpr]
    step = run["steps"]["maximized"]
    d = derived(step, dpr)
    screen_x = step["content"]["screenX"]
    ui = half_up(step["content"]["mozInnerScreenY"] * dpr)
    return [("border", "(GetWindowRect width - rcWork width) / 2", d["border"], row["border"], d["border"] == row["border"]),
            ("screenX", "round-half-up(-border / dpr)", screen_x, half_up(-row["border"] / dpr), screen_x == half_up(-row["border"] / dpr)),
            ("maximized_ui", "round(mozInnerScreenY * dpr)", ui, row["maximized_ui"], ui == row["maximized_ui"])]


def name_of(run: Dict[str, Any]) -> str:
    label = f" {run['dev_px_per_px']}" if run["mode"] == "devPixelsPerPx" and run.get("dev_px_per_px") else ""
    return f"dpr {run['dpr_asked']:g} {run['resolution'][0]}x{run['resolution'][1]} launch {run['launch']} ({run['mode']}{label})"


def required_steps(run: Dict[str, Any]) -> Tuple[str, ...]:
    """The steps a launch must have: launch m takes the maximized step alone."""
    return plan.MAXIMIZED_ONLY if run["launch"] == "m" else STEPS


def step_is_complete(step: Any) -> bool:
    """A step with its window record, or one the capture says it skipped (Juggler's size did not fit the work area)."""
    return isinstance(step, dict) and ("skipped" in step or "window_rect" in (step.get("native") or {}))


def probe_line(step_name: str, step: Dict[str, Any]) -> Optional[str]:
    """What the page reads that can differ under responsive-design mode (page.html __probe), on one line; None without it."""
    probe = (step.get("content") or {}).get("probe")
    if not probe:
        return None
    screen, found, touch = probe.get("screen") or {}, probe.get("found") or {}, probe.get("touch") or {}
    orientation = screen.get("orientation") or {}
    true_queries = sorted(q for q, v in (probe.get("matches") or {}).items() if v)
    viewport = probe.get("visualViewport")
    return (f"    {step_name} probe: screen {screen.get('width')}x{screen.get('height')} avail {screen.get('availWidth')}x{screen.get('availHeight')} "
            f"at {screen.get('availLeft')},{screen.get('availTop')} colorDepth {screen.get('colorDepth')} "
            f"orientation {orientation.get('type')}/{orientation.get('angle')} dpr {probe.get('devicePixelRatio')} ; "
            f"matchMedia width {found.get('width')} height {found.get('height')} device-width {found.get('device-width')} "
            f"device-height {found.get('device-height')} aspect-ratio x10000 {found.get('aspect-ratio_x10000')} "
            f"device-aspect-ratio x10000 {found.get('device-aspect-ratio_x10000')} resolution x100 {found.get('resolution_x100')} "
            f"true {true_queries} ; touch maxTouchPoints {touch.get('maxTouchPoints')} ontouchstart {touch.get('ontouchstart')} "
            f"TouchEvent {touch.get('touchEvent')} ; visualViewport "
            + ("none" if viewport is None else " ".join(f"{k} {viewport[k]}" for k in sorted(viewport)))
            + f" ; root client {(probe.get('client') or {}).get('width')}x{(probe.get('client') or {}).get('height')}")


def hostgate_lines(run: Dict[str, Any]) -> List[str]:
    """The two outcomes of the collection page in the tab (without, then with inRDMPane)."""
    step = run["steps"].get("rdm_hostgate")
    if not step:
        return []
    if "skipped" in step:
        return [f"    rdm_hostgate: skipped ({step['skipped']})"]
    lines = []
    for key, label in (("rdm_false", "false"), ("rdm_true", "true")):
        entry = step.get(key) or {}
        if entry.get("posted"):
            lines.append(f"    rdm_hostgate inRDMPane={label}: posted in {entry.get('seconds')} s, {entry.get('bytes')} bytes, "
                         f"sha256 {entry.get('sha256')}, inRDMPane read back {entry.get('inRDMPane')}")
        elif key == "rdm_true":
            lines.append(f"    rdm_hostgate inRDMPane={label}: RDM HANG (no POST in {entry.get('seconds')} s, "
                         f"inRDMPane read back {entry.get('inRDMPane')})")
        else:
            lines.append(f"    rdm_hostgate inRDMPane={label}: NO POST in {entry.get('seconds')} s")
    return lines


def hostgate_problems(runs: Iterable[Dict[str, Any]], dpr: float) -> List[str]:
    """Red: the step is missing, or the page did not post without inRDMPane. An RDM hang is not red."""
    problems = []
    for run in runs:
        step = run["steps"].get("rdm_hostgate")
        if step is None:
            problems.append(f"dpr {dpr:g}: launch {run['launch']} has no rdm_hostgate")
        elif "skipped" not in step and not (step.get("rdm_false") or {}).get("posted"):
            problems.append(f"dpr {dpr:g}: launch {run['launch']}: host-gate-2.html did not post without inRDMPane")
        elif "skipped" not in step and "rdm_true" not in step:
            problems.append(f"dpr {dpr:g}: launch {run['launch']}: rdm_hostgate has no run with inRDMPane")
    return problems


def grid_cell(row: Dict[str, Any]) -> str:
    """`y/device` of a grid row: the content top (the stack's y) in CSS and in device pixels; `-` skipped, ERR failed,
    `*` not settled, `!` the chrome inner size is not the one asked."""
    if "skipped" in row:
        return "-"
    if "error" in row:
        return "ERR"
    chrome, native = row["chrome"], row["native"]
    y = chrome["stack"]["y"]
    device = "?"
    if "window_rect" in native:
        device = (native["client_origin"][1] - native["window_rect"][1]) + half_up(y * chrome["devicePixelRatio"])
    got = (chrome["innerWidth"], chrome["innerHeight"])
    return f"{y:.2f}/{device}" + ("" if row.get("settled") else "*") + ("" if got == (row["w"], row["h"]) else "!")


def row_summary(row: Dict[str, Any]) -> str:
    chrome, page, native = row["chrome"], row["page"], row["native"]
    return (f"chrome inner {chrome['innerWidth']}x{chrome['innerHeight']} sizemode {chrome.get('sizemode')} "
            f"page inner {page['innerWidth']}x{page['innerHeight']} outer {page['outerWidth']}x{page['outerHeight']} "
            f"stack y/height css {chrome['stack']['y']:.2f}/{chrome['stack']['height']:.2f} "
            f"window rect {native.get('window_rect')} client rect {native.get('client_rect')}")


def grid_lines(run: Dict[str, Any]) -> List[str]:
    """The grid of a launch as a compact table (one line per height, `width:y/device` cells) and its minimum window."""
    steps = run["steps"]
    lines: List[str] = []
    rows = steps.get("grid")
    if rows:
        widths = sorted({r["w"] for r in rows})
        lines.append(f"    grid of restored windows, chrome inner W x H CSS -> content top y css/device "
                     f"(- does not fit, * not settled, ! size differs from asked, ERR failed); widths {widths}")
        by_point = {(r["w"], r["h"]): r for r in rows}
        for height in sorted({r["h"] for r in rows}):
            lines.append(f"      H {height}: " + " ".join(f"{w}:{grid_cell(by_point[(w, height)])}" for w in widths if (w, height) in by_point))
        moved = [f"{r['w']}x{r['h']} -> {r['chrome']['innerWidth']}x{r['chrome']['innerHeight']}" for r in rows
                 if "chrome" in r and (r["chrome"]["innerWidth"], r["chrome"]["innerHeight"]) != (r["w"], r["h"])]
        if moved:
            lines.append(f"      asked -> got (chrome inner): {', '.join(moved)}")
    minimum = steps.get("minimum")
    if minimum:
        lines.append(f"    minimum window (asked {plan.MINIMUM_ASKED[0]}x{plan.MINIMUM_ASKED[1]} chrome inner css): "
                     + (row_summary(minimum) if "chrome" in minimum else str(minimum)))
    return lines


def shown_at(row: Dict[str, Any]) -> Dict[str, Any]:
    """What a grid row says the window shows: the content top in CSS and the size mode."""
    chrome = row.get("chrome") or {}
    return {"stack_y": (chrome.get("stack") or {}).get("y"), "sizemode": chrome.get("sizemode")}


def grid_problems(first: Dict[str, Any], second: Dict[str, Any], dpr: float) -> Tuple[List[str], List[str]]:
    """(lines, problems) of the grid rules for launch a (`first`) against launch b (`second`) at a scale of GRID_DPRS."""
    lines: List[str] = []
    problems: List[str] = []
    steps_a, steps_b = first["steps"], second["steps"]
    for step in ("grid", "minimum"):
        if step not in steps_a:
            problems.append(f"dpr {dpr:g}: launch {first['launch']} has no {step}")
    if "grid_check" not in steps_b:
        problems.append(f"dpr {dpr:g}: launch {second['launch']} has no grid_check")
    for row in steps_a.get("grid", []):
        if "error" in row:
            problems.append(f"dpr {dpr:g}: grid point {row['w']}x{row['h']} failed: {row['error']}")
    rows_a = {(r["w"], r["h"]): r for r in steps_a.get("grid", [])}
    equal = 0
    for row in steps_b.get("grid_check", []):
        point = (row["w"], row["h"])
        if point not in rows_a:
            problems.append(f"dpr {dpr:g}: grid check point {point[0]}x{point[1]} is not in the grid of launch {first['launch']}")
            continue
        # Only what the window SHOWS at that point is compared: the content top and the size mode. The sizes
        # reached are not: at a fractional scale resizeBy rounds from the size it starts at, so the device size a
        # point lands on depends on the route there (run 45672953042, scale 1.25: 671.2 vs 672 CSS), not on Firefox.
        diff = differences(shown_at(rows_a[point]), shown_at(row))
        if not diff:
            equal += 1
            continue
        lines.append(f"    grid check {point[0]}x{point[1]}: {len(diff)} difference(s) from launch {first['launch']}")
        lines.extend(f"        {path}: {va!r} vs {vb!r}" for path, va, vb in diff)
        problems.append(f"dpr {dpr:g}: grid check point {point[0]}x{point[1]} differs from launch {first['launch']} on {len(diff)} number(s)")
    lines.append(f"dpr {dpr:g} grid check: {equal} of {len(steps_b.get('grid_check', []))} point(s) equal to launch {first['launch']}")
    return lines, problems


def core_steps_of(run: Dict[str, Any]) -> Dict[str, Any]:
    return {name: run["steps"][name] for name in STEPS if name in run["steps"]}


def evaluate(runs: List[Dict[str, Any]], expected_dprs: Iterable[float], table: Dict[float, Dict[str, int]],
             strict_point: Tuple[float, Tuple[int, int]] = STRICT_POINT) -> Dict[str, Any]:
    """{"red": bool, "problems": [...], "lines": [...]}"""
    problems: List[str] = []
    lines: List[str] = []
    expected = [float(d) for d in expected_dprs]
    for dpr in expected:
        if not any(float(r["dpr_asked"]) == dpr for r in runs):
            problems.append(f"dpr {dpr:g}: no result (the job is missing or wrote nothing)")
    usable = []
    for run in sorted(runs, key=lambda r: (float(r["dpr_asked"]), r["mode"], r["resolution"], r["launch"], str(r.get("dev_px_per_px")))):
        steps = run.get("steps", {})
        if (run.get("error") or run.get("processes_left") != 0
                or not all(s in steps and step_is_complete(steps[s]) for s in required_steps(run))):
            problems.append(f"{name_of(run)}: error={run.get('error')!r} processes_left={run.get('processes_left')!r} "
                            f"steps={sorted(run.get('steps', {}))}")
            continue
        usable.append(run)
    for run in usable:
        dpr, shown = float(run["dpr_asked"]), run["display"]
        lines.append(f"{name_of(run)}: dpi_reached={shown.get('dpi_reached')} effective_dpi={shown.get('effective_dpi')} "
                     f"resolution_now={shown.get('resolution_now')}")
        if run["mode"] == "display" and not shown.get("dpi_reached"):
            lines.append(f"    not reachable live: derived path ({shown.get('reason')})")
        elif run["mode"] == "display":
            # Red only at the one point where the core table and Firefox 159 agree in window size; see the docstring.
            strict = (dpr, tuple(run["resolution"])) == (float(strict_point[0]), tuple(strict_point[1]))
            for name, what, got, want, ok in check_maximized(run, table):
                verdict = "ok" if ok else ("DIFFERENT" if strict else "DIFFERS (window-size dependent, see grid)")
                lines.append(f"    maximized {name}: {what} = {got} ; core table {want} : {verdict}")
                if not ok and strict:
                    problems.append(f"{name_of(run)}: maximized {name} is {got}, the core table says {want}")
        for step_name in required_steps(run):
            step = run["steps"][step_name]
            if "skipped" in step:
                lines.append(f"    {step_name}: skipped ({step['skipped']})")
                continue
            d = derived(step, dpr)
            lines.append(f"    {step_name}: border L/R/B {d['border_left']}/{d['border_right']}/{d['border_bottom']}  ui {d['ui']}  "
                         f"position dev {d['position_dev']} css {d['position_css']}  outer-inner css {d['outer_minus_inner_css']}  "
                         f"screenX/Y {d['screen_xy']}  mozInnerScreenX/Y {d['moz_inner_screen']}  events(count,dx,dy) {d['events']}")
            lines.append(f"    {step_name} page: inner {d['page_inner']} outer {d['page_outer']} screen {d['screen_wh']} "
                         f"event dx/dy {d['event_dxdy']} work area css {d['work_css']} native client rect {d['client_rect']} "
                         f"window rect {d['window_rect']}")
        for step_name in required_steps(run):
            line = probe_line(step_name, run["steps"][step_name]) if "skipped" not in run["steps"][step_name] else None
            if line:
                lines.append(line)
        lines.extend(hostgate_lines(run))
        if "restored" in run["steps"]:
            lines.append(f"    restored size used {run.get('restored_size_used')}")
        if "juggler" in run["steps"]:
            lines.append(f"    juggler size used {run.get('juggler_size_used')}")
        lines.extend(grid_lines(run))
    display_groups: Dict[Tuple[float, tuple], List[Dict[str, Any]]] = {}
    derived_groups: Dict[Tuple[float, tuple], List[Dict[str, Any]]] = {}
    for run in usable:
        key = (float(run["dpr_asked"]), tuple(run["resolution"]))
        if run["mode"] == "display" and run["launch"] != "m":
            display_groups.setdefault(key, []).append(run)
        elif run["mode"] == "devPixelsPerPx" and run["launch"] in ("p", "q"):
            derived_groups.setdefault(key, []).append(run)
    for dpr in expected:
        groups = [g for groups_ in (display_groups, derived_groups) for (d, _), g in groups_.items() if d == dpr and len(g) >= 2]
        if not groups and any(float(r["dpr_asked"]) == dpr for r in usable):
            problems.append(f"dpr {dpr:g}: no resolution has two launches to compare")
        for group in groups:
            first = group[0]
            for other in group[1:]:
                diff = differences(core_steps_of(first), core_steps_of(other))
                lines.append(f"dpr {dpr:g} launch {first['launch']} vs {other['launch']}: {len(diff)} difference(s)")
                for path, va, vb in diff:
                    lines.append(f"    {path}: {va!r} vs {vb!r}")
                if diff:
                    problems.append(f"dpr {dpr:g}: launch {first['launch']} and {other['launch']} differ on {len(diff)} number(s)")
            if first["mode"] == "display" and dpr in plan.GRID_DPRS and first["display"].get("dpi_reached"):
                more_lines, more_problems = grid_problems(first, group[1], dpr)
                lines.extend(more_lines)
                problems.extend(more_problems)
                if dpr in plan.HOSTGATE_DPRS:
                    problems.extend(hostgate_problems(group[:2], dpr))
    return {"red": bool(problems), "problems": problems, "lines": lines}


def load_runs(root: Path) -> List[Dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(root).rglob("raw.json"))]


def report(result: Dict[str, Any]) -> str:
    verdict = "RED" if result["red"] else "GREEN"
    return "\n".join(result["lines"] + [""] + [f"PROBLEM: {p}" for p in result["problems"]] + [f"VERDICT: {verdict}"])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="root", type=Path, required=True)
    ap.add_argument("--request", type=Path, default=request_file.REQUEST)
    args = ap.parse_args(argv)
    request = request_file.load(args.request)
    result = evaluate(load_runs(args.root), request["dpr"], frame_table.load(), (1.0, tuple(request["resolutions"][0])))
    text = report(result)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("```\n" + text + "\n```\n")
    return 1 if result["red"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
