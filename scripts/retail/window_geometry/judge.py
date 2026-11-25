"""The one verdict of the window-geometry capture: reads every downloaded artifact, prints a table, exits 1 when red.

    python scripts/retail/window_geometry/judge.py --in DIR [--request FILE]

Red when:
  * a scale of the request has no run, a run has an error or leaves a process of the install folder alive;
  * launch a differs from launch b at a resolution that has both, on any number of either step (the diff is printed);
  * at a scale whose display scale was reached live (display mode), the MAXIMIZED step is not exactly the row of
    invisible_core WINDOW_FRAME_BY_DPR (read from the source of this checkout): the invisible border
    (GetWindowRect width minus work-area width, halved), screenX = round-half-up(-border / dpr) and
    round(mozInnerScreenY * dpr) = maximized_ui. This is the proof that Marionette does not change the frame.
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
import request as request_file  # noqa: E402

STEPS = ("maximized", "restored")


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
    return {"border": ((right - left) - (work[2] - work[0])) / 2,
            "border_left": origin_x - left, "border_right": right - client_right, "border_bottom": bottom - client_bottom,
            "ui": ui, "position_dev": [left, top], "position_css": [left / dpr, top / dpr],
            "outer_minus_inner_css": [content["outerWidth"] - content["innerWidth"], content["outerHeight"] - content["innerHeight"]],
            "screen_xy": [content["screenX"], content["screenY"]],
            "moz_inner_screen": [content["mozInnerScreenX"], content["mozInnerScreenY"]],
            "events": [content["events"]["count"], content["events"]["dx"], content["events"]["dy"]]}


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
    return f"dpr {run['dpr_asked']:g} {run['resolution'][0]}x{run['resolution'][1]} launch {run['launch']} ({run['mode']})"


def evaluate(runs: List[Dict[str, Any]], expected_dprs: Iterable[float], table: Dict[float, Dict[str, int]]) -> Dict[str, Any]:
    """{"red": bool, "problems": [...], "lines": [...]}"""
    problems: List[str] = []
    lines: List[str] = []
    expected = [float(d) for d in expected_dprs]
    for dpr in expected:
        if not any(float(r["dpr_asked"]) == dpr for r in runs):
            problems.append(f"dpr {dpr:g}: no result (the job is missing or wrote nothing)")
    usable = []
    for run in sorted(runs, key=lambda r: (float(r["dpr_asked"]), r["mode"], r["resolution"], r["launch"])):
        steps = run.get("steps", {})
        if (run.get("error") or run.get("processes_left") != 0 or not all(s in steps for s in STEPS)
                or not all("window_rect" in steps[s]["native"] for s in STEPS)):
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
            for name, what, got, want, ok in check_maximized(run, table):
                lines.append(f"    maximized {name}: {what} = {got} ; core table {want} : {'ok' if ok else 'DIFFERENT'}")
                if not ok:
                    problems.append(f"{name_of(run)}: maximized {name} is {got}, the core table says {want}")
        for step_name in STEPS:
            d = derived(run["steps"][step_name], dpr)
            lines.append(f"    {step_name}: border L/R/B {d['border_left']}/{d['border_right']}/{d['border_bottom']}  ui {d['ui']}  "
                         f"position dev {d['position_dev']} css {d['position_css']}  outer-inner css {d['outer_minus_inner_css']}  "
                         f"screenX/Y {d['screen_xy']}  mozInnerScreenX/Y {d['moz_inner_screen']}  events(count,dx,dy) {d['events']}")
        lines.append(f"    restored size used {run.get('restored_size_used')}")
    groups: Dict[Tuple[float, tuple], List[Dict[str, Any]]] = {}
    for run in usable:
        if run["mode"] == "display":
            groups.setdefault((float(run["dpr_asked"]), tuple(run["resolution"])), []).append(run)
    for dpr in expected:
        pairs = [g for (d, _), g in groups.items() if d == dpr and len(g) >= 2]
        if not pairs and any(float(r["dpr_asked"]) == dpr for r in usable):
            problems.append(f"dpr {dpr:g}: no resolution has two launches to compare")
        for group in pairs:
            first = group[0]
            for other in group[1:]:
                diff = differences(first["steps"], other["steps"])
                lines.append(f"dpr {dpr:g} launch {first['launch']} vs {other['launch']}: {len(diff)} difference(s)")
                for path, va, vb in diff:
                    lines.append(f"    {path}: {va!r} vs {vb!r}")
                if diff:
                    problems.append(f"dpr {dpr:g}: launch {first['launch']} and {other['launch']} differ on {len(diff)} number(s)")
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
    result = evaluate(load_runs(args.root), request["dpr"], frame_table.load())
    text = report(result)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("```\n" + text + "\n```\n")
    return 1 if result["red"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
