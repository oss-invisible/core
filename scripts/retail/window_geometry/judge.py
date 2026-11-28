"""The one verdict of the window-geometry capture: reads every downloaded artifact, prints a table, exits 1 when red.

    python scripts/retail/window_geometry/judge.py --in DIR [--request FILE]

Red when:
  * a scale of the request has no run, a run has an error or leaves a process of the install folder alive, a run lacks
    a step of its launch (a, b, p, q: maximized; m: maximized; and, in the two launches that are compared, the steps of
    plan.ROUND5_STEPS: fullscreen, F11-style fullscreen, iframes, popups; popup_second is printed, not judged). A step of round 5 that is
    `{"not_possible": <reason>}` is complete when it has a non-empty reason, which is printed;
  * launch a differs from launch b (launch p from q) at a resolution that has both, on any number of those steps (the diff is
    printed; the steps of plan.PRINTED_STEPS, `popup_second`, are compared and printed but not judged on their values). What a page or a frame read in its first moments (`early`: the frames of round 7, the maximized window's page and
    the popups' pages of round 8) is not part of it: it is printed per moment next to the settled reading and the window's client
    origin, and the differences between launch a and launch b (the timing `now` left out) are printed, never red;
  * at every scale reached live (display mode), at every resolution, the MAXIMIZED step is not exactly what the core declares
    (invisible_core, read from the source of this checkout by frame_table.py): the invisible border (GetWindowRect width
    minus work-area width, halved), screenX = round-half-up(-border / dpr) and round(mozInnerScreenY * dpr) =
    declared_maximized_ui(frame, the measured screen.availHeight in CSS px), i.e. the density rule, which depends on the
    window size. This is also the proof that Marionette does not change the frame.
A scale Windows would not take is "not reachable live: derived path" and is not red by itself.
"""
from __future__ import annotations

import argparse
import copy
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
EXTRA = plan.ROUND5_STEPS


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


def check_maximized(run: Dict[str, Any], table: Dict[float, Any]) -> List[Tuple[str, str, Any, Any, bool]]:
    """(name, what, got, expected, ok) for the three comparisons with what the core declares."""
    dpr = float(run["dpr_asked"])
    frame = table[dpr]
    content = run["steps"]["maximized"]["content"]
    d = derived(run["steps"]["maximized"], dpr)
    screen_x = content["screenX"]
    ui = half_up(content["mozInnerScreenY"] * dpr)
    avail = content["screen"]["availHeight"]
    want_x, want_ui = half_up(-frame.border / dpr), frame_table.declared_maximized_ui(frame, avail)
    return [("border", "(GetWindowRect width - rcWork width) / 2", d["border"], frame.border, d["border"] == frame.border),
            ("screenX", "round-half-up(-border / dpr)", screen_x, want_x, screen_x == want_x),
            ("maximized_ui", f"round(mozInnerScreenY * dpr) at availHeight {avail}", ui, want_ui, ui == want_ui)]


def name_of(run: Dict[str, Any]) -> str:
    label = f" {run['dev_px_per_px']}" if run["mode"] == "devPixelsPerPx" and run.get("dev_px_per_px") else ""
    return f"dpr {run['dpr_asked']:g} {run['resolution'][0]}x{run['resolution'][1]} launch {run['launch']} ({run['mode']}{label})"


def required_steps(run: Dict[str, Any], compared: bool = False) -> Tuple[str, ...]:
    """The steps a launch must have: launch m takes the maximized step alone; a launch that is compared with another (a with
    b, p with q) takes the round 5 steps too."""
    if run["launch"] == "m":
        return plan.MAXIMIZED_ONLY
    return STEPS + plan.round5_of(run["mode"]) if compared else STEPS


def compared_runs(runs: Iterable[Dict[str, Any]]) -> set:
    """The ids of the runs that are compared: the first two launches of a resolution (display launches other than m) and
    the first two derived launches (p, q) of a scale; a launch alone is compared with nothing."""
    groups: Dict[Tuple[float, tuple, str], List[Dict[str, Any]]] = {}
    for run in runs:
        if run.get("mode") == "display" and run.get("launch") != "m":
            kind = "display"
        elif run.get("mode") == "devPixelsPerPx" and run.get("launch") in ("p", "q"):
            kind = "derived"
        else:
            continue
        groups.setdefault((float(run["dpr_asked"]), tuple(run["resolution"]), kind), []).append(run)
    return {id(r) for group in groups.values() if len(group) >= 2 for r in sorted(group, key=lambda r: r["launch"])[:2]}


def step_is_complete(step: Any) -> bool:
    """A step with its window record, or one the capture says was not possible with a reason."""
    if not isinstance(step, dict):
        return False
    if "not_possible" in step:
        return isinstance(step["not_possible"], str) and bool(step["not_possible"].strip())
    return "window_rect" in (step.get("native") or {})


def chrome_line(step_name: str, step: Dict[str, Any]) -> str:
    """What the chrome says about the window after the step: its inner size, the content top (the stack's y, CSS), the size
    mode and the window state, and Windows' own rects."""
    chrome, native = step["chrome"], step["native"]
    stack = chrome.get("stack") or {}
    return (f"    {step_name} chrome: inner {chrome.get('innerWidth')}x{chrome.get('innerHeight')} stack y {stack.get('y')} "
            f"sizemode {chrome.get('sizemode')} windowState {chrome.get('windowState')} maximized {chrome.get('maximized')} "
            f"native window rect {native.get('window_rect')} client rect {native.get('client_rect')}")


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
            f"pixelDepth {screen.get('pixelDepth')} "
            f"orientation {orientation.get('type')}/{orientation.get('angle')} dpr {probe.get('devicePixelRatio')} ; "
            f"matchMedia width {found.get('width')} height {found.get('height')} device-width {found.get('device-width')} "
            f"device-height {found.get('device-height')} aspect-ratio x10000 {found.get('aspect-ratio_x10000')} "
            f"device-aspect-ratio x10000 {found.get('device-aspect-ratio_x10000')} resolution x100 {found.get('resolution_x100')} "
            f"true {true_queries} ; touch maxTouchPoints {touch.get('maxTouchPoints')} ontouchstart {touch.get('ontouchstart')} "
            f"TouchEvent {touch.get('touchEvent')} ; visualViewport "
            + ("none" if viewport is None else " ".join(f"{k} {viewport[k]}" for k in sorted(viewport)))
            + f" ; root client {(probe.get('client') or {}).get('width')}x{(probe.get('client') or {}).get('height')}")


def core_steps_of(run: Dict[str, Any], with_early: bool = False) -> Dict[str, Any]:
    """The steps the a/b comparison is made of. What a page or a frame read in its first moments (`early`: at parse, DOMContentLoaded,
    the two animation frames and load, in a step's `content` and in a frame's `state`) is measured, not judged: it is left out unless
    `with_early`."""
    steps = {name: run["steps"][name] for name in plan.COMPARED_STEPS if name in run["steps"]}
    if with_early:
        return steps
    steps = copy.deepcopy(steps)
    for step in steps.values():
        if isinstance(step, dict) and isinstance(step.get("content"), dict):
            step["content"].pop("early", None)
        for frame in ((step.get("frames") or {}).values() if isinstance(step, dict) else ()):
            if isinstance(frame.get("state"), dict):
                frame["state"].pop("early", None)
    return steps


def early_differences(first: Dict[str, Any], other: Dict[str, Any]) -> List[Tuple[str, Any, Any]]:
    """The differences between two launches in what the pages and the frames read in their first moments, only: `now` (the time) is
    left out."""
    def without_now(early: Dict[str, Any]) -> Dict[str, Any]:
        return {m: {k: v for k, v in (r.items() if isinstance(r, dict) else []) if k != "now"} for m, r in early.items()}

    def only_early(run: Dict[str, Any]) -> Dict[str, Any]:
        found = {}
        for name, step in core_steps_of(run, with_early=True).items():
            if not isinstance(step, dict):
                continue
            page_early = (step.get("content") or {}).get("early")
            if isinstance(page_early, dict):
                found[f"{name}.content.early"] = without_now(page_early)
            for frame_name, frame in (step.get("frames") or {}).items():
                early = (frame.get("state") or {}).get("early")
                if early is not None:
                    found[f"{name}.frames.{frame_name}.state.early"] = without_now(early)
        return found
    return differences(only_early(first), only_early(other))


def standard_lines(step_name: str, step: Dict[str, Any], dpr: float) -> List[str]:
    """The lines of a step that has the full record of the main window."""
    d = derived(step, dpr)
    return [f"    {step_name}: border L/R/B {d['border_left']}/{d['border_right']}/{d['border_bottom']}  ui {d['ui']}  "
            f"position dev {d['position_dev']} css {d['position_css']}  outer-inner css {d['outer_minus_inner_css']}  "
            f"screenX/Y {d['screen_xy']}  mozInnerScreenX/Y {d['moz_inner_screen']}  events(count,dx,dy) {d['events']}",
            f"    {step_name} page: inner {d['page_inner']} outer {d['page_outer']} screen {d['screen_wh']} "
            f"event dx/dy {d['event_dxdy']} work area css {d['work_css']} native client rect {d['client_rect']} "
            f"window rect {d['window_rect']}",
            chrome_line(step_name, step)]


def popup_lines(step_name: str, step: Dict[str, Any], dpr: float, frame: Any = None) -> List[str]:
    """What a popup says about itself and what Windows says about its window. The core's popup values are a MODEL (Firefox
    151 on a workbench): they are printed beside the measure and are not judged."""
    native, content = step["native"], step["content"]
    left, top, right, bottom = native["window_rect"]
    origin = native["client_origin"]
    client = native["client_rect"]
    client_right, client_bottom = origin[0] + client[2] - client[0], origin[1] + client[3] - client[1]
    model = "" if frame is None else (f" ; core MODEL popup_ui {frame.popup_ui} popup_position {frame.popup_position} "
                                      "(not judged: Firefox 151 on a workbench)")
    events = content["events"]
    return [f"    {step_name} (window.open features {step.get('features')!r}): native window rect {[left, top, right, bottom]} "
            f"size dev {right - left}x{bottom - top} client rect {client} client origin {origin} "
            f"window top to client top {origin[1] - top} content top dev {half_up(content['mozInnerScreenY'] * dpr)} "
            f"position dev {[left, top]} css {[left / dpr, top / dpr]} "
            f"border L/R/B {origin[0] - left}/{right - client_right}/{bottom - client_bottom}{model}",
            f"    {step_name} page: inner {[content['innerWidth'], content['innerHeight']]} outer "
            f"{[content['outerWidth'], content['outerHeight']]} screenX/Y {[content['screenX'], content['screenY']]} "
            f"screenLeft/Top {[content.get('screenLeft'), content.get('screenTop')]} mozInnerScreenX/Y "
            f"{[content['mozInnerScreenX'], content['mozInnerScreenY']]} dpr {content['devicePixelRatio']} "
            f"events(count,dx,dy) {[events['count'], events['dx'], events['dy']]} screen {content.get('screen') or {}} ; "
            f"windows opened {step.get('windows_opened')} closed {step.get('closed')} settled {step.get('settled')}"]


def second_popup_lines(step_name: str, step: Dict[str, Any], dpr: float) -> List[str]:
    """popup_second: the first popup (p1, kept open) beside the second (p2): screenX/Y and window rect of each, and where Windows put
    p2 against p1 in device px (the stagger Firefox applies is round(22 * scale) when they are within round(4 * scale)). Printed only."""
    first = step.get("first")
    if not isinstance(first, dict):
        return [f"    {step_name} first popup: none recorded"]
    p1_rect, p2_rect = (first.get("native") or {}).get("window_rect"), (step.get("native") or {}).get("window_rect")
    offset = [p2_rect[0] - p1_rect[0], p2_rect[1] - p1_rect[1]] if p1_rect and p2_rect else None
    return [f"    {step_name} first popup {first.get('name')} (window.open features {first.get('features')!r}): screenX/Y "
            f"{[first.get('screenX'), first.get('screenY')]} native window rect {p1_rect} ; second window rect {p2_rect} "
            f"offset of the second from the first, device px {offset} (scale {dpr:g}: stagger {round(22 * dpr)} within {round(4 * dpr)})"]


def printed_differences(first: Dict[str, Any], other: Dict[str, Any]) -> List[Tuple[str, Any, Any]]:
    """The differences between two launches in the steps of plan.PRINTED_STEPS (popup_second), what the page read in its first moments
    left out: printed, never red."""
    def only_printed(run: Dict[str, Any]) -> Dict[str, Any]:
        steps = copy.deepcopy({n: run["steps"][n] for n in plan.PRINTED_STEPS if n in run["steps"]})
        for step in steps.values():
            if isinstance(step, dict) and isinstance(step.get("content"), dict):
                step["content"].pop("early", None)
        return steps
    return differences(only_printed(first), only_printed(other))


def frames_lines(step_name: str, step: Dict[str, Any]) -> List[str]:
    """The records of the iframes of an `iframes_*` step: each frame's own view, and the probe it read."""
    lines = []
    for name, frame in sorted((step.get("frames") or {}).items()):
        state = frame["state"]
        events = state["events"]
        lines.append(f"    {step_name} frame {name} ({frame.get('kind')}): rect css {frame.get('rect')} inner "
                     f"{[state['innerWidth'], state['innerHeight']]} outer {[state['outerWidth'], state['outerHeight']]} "
                     f"screenX/Y {[state['screenX'], state['screenY']]} mozInnerScreenX/Y "
                     f"{[state['mozInnerScreenX'], state['mozInnerScreenY']]} dpr {state['devicePixelRatio']} "
                     f"events(count,dx,dy) {[events['count'], events['dx'], events['dy']]} "
                     f"mouse asked/cursor {[(m.get('asked'), m.get('cursor')) for m in frame.get('input') or []]}")
        line = probe_line(f"{step_name} frame {name}", {"content": {"probe": state.get("probe")}})
        if line:
            lines.append(line)
    return lines


EARLY_MOMENTS = ("parse", "dcl", "raf1", "raf2", "load")
EARLY_STEPS = ("iframes_max",)
#: The steps whose own page records `content.early`: the maximized window's page, and the page of each popup.
PAGE_EARLY_STEPS = ("maximized", "popup", "popup_second", "popup_pos")


def moment_lines(label: str, early: Any, settled: str) -> List[str]:
    """One line per moment (parse, DOMContentLoaded, the first and the second animation frame, load) of a document's first life:
    mozInnerScreenX/Y, screenX/Y, the sizes and the pixel ratio it read then, next to its settled reading (`settled`)."""
    if not isinstance(early, dict):
        return [f"    {label} early: none recorded ; {settled}"]
    lines = []
    for moment in EARLY_MOMENTS:
        read = early.get(moment)
        if not isinstance(read, dict):
            lines.append(f"    {label} early {moment}: not reached ; {settled}")
            continue
        lines.append(f"    {label} early {moment}: mozInnerScreenX/Y {[read.get('mozInnerScreenX'), read.get('mozInnerScreenY')]} "
                     f"screenX/Y {[read.get('screenX'), read.get('screenY')]} outer {[read.get('outerWidth'), read.get('outerHeight')]} "
                     f"inner {[read.get('innerWidth'), read.get('innerHeight')]} dpr {read.get('devicePixelRatio')} now {read.get('now')} ; {settled}"
                     + (f" ; error {read['error']}" if "error" in read else ""))
    return lines


def client_origin_css(step: Dict[str, Any], dpr: float) -> Any:
    """The window's client origin (Windows' ClientToScreen of its top left) in CSS px, or None without it."""
    origin = (step.get("native") or {}).get("client_origin")
    return [origin[0] / dpr, origin[1] / dpr] if origin else None


def early_lines(step_name: str, step: Dict[str, Any], dpr: float) -> List[str]:
    """Per frame of iframes_max (same-origin and cross-site), per moment of its first life: mozInnerScreenX/Y and screenX/Y the frame
    read then, next to its settled reading, the parent's mozInnerScreen and the window's client origin in CSS px. Measured, not
    judged."""
    if step_name not in EARLY_STEPS:
        return []
    parent = [step["content"]["mozInnerScreenX"], step["content"]["mozInnerScreenY"]]
    client = client_origin_css(step, dpr)
    lines = []
    for name, frame in sorted((step.get("frames") or {}).items()):
        state = frame["state"]
        settled = (f"settled mozInnerScreenX/Y {[state['mozInnerScreenX'], state['mozInnerScreenY']]} "
                   f"screenX/Y {[state['screenX'], state['screenY']]} ; parent mozInnerScreenX/Y {parent} ; window client origin css {client}")
        lines.extend(moment_lines(f"{step_name} frame {name}", state.get("early"), settled))
    return lines


def page_early_lines(step_name: str, step: Dict[str, Any], dpr: float) -> List[str]:
    """For the maximized window's own page and for each popup's page, per moment of its first life: what the page read then next to
    its settled reading (the step's `content`) and the client origin of its window in CSS px. Measured, not judged."""
    if step_name not in PAGE_EARLY_STEPS:
        return []
    content = step["content"]
    settled = (f"settled mozInnerScreenX/Y {[content.get('mozInnerScreenX'), content.get('mozInnerScreenY')]} "
               f"screenX/Y {[content.get('screenX'), content.get('screenY')]} ; window client origin css {client_origin_css(step, dpr)}")
    return moment_lines(step_name, content.get("early"), settled)


def transformed_lines(step_name: str, step: Dict[str, Any]) -> List[str]:
    """The frames of an `iframes_transformed` step: each frame's transform, and per point of the real mouse the screen point asked
    (device px), the point the frame received as client and as screen CSS px, and their difference. Measured, not judged."""
    lines = []
    for name, frame in sorted((step.get("frames") or {}).items()):
        moves = (frame.get("state") or {}).get("moves")
        if moves is None:
            continue
        state = frame["state"]
        points = [f"asked {p.get('device')} css {[round(c, 3) for c in p.get('css') or []]} -> client {[m['clientX'], m['clientY']]} "
                  f"screen {[m['screenX'], m['screenY']]} delta {[m['dx'], m['dy']]}"
                  for p, m in zip(frame.get("points") or [], moves)]
        lines.append(f"    {step_name} frame {name} {frame.get('transform')}: mozInnerScreenX/Y "
                     f"{[state['mozInnerScreenX'], state['mozInnerScreenY']]} container rect {(frame.get('geometry') or {}).get('rect')} "
                     f"matrix {(frame.get('geometry') or {}).get('matrix')} moves seen {len(moves)} for {len(frame.get('points') or [])} points")
        lines.extend(f"    {step_name} frame {name} point {i}: {text}" for i, text in enumerate(points))
    return lines


def fullscreen_line(step_name: str, step: Dict[str, Any]) -> List[str]:
    facts = step.get("fullscreen")
    if facts is None:
        return []
    return [f"    {step_name} fullscreen: {facts} settled {step.get('settled')} forced_exit {step.get('forced_exit')} "
            f"click {step.get('click')}"]


def evaluate(runs: List[Dict[str, Any]], expected_dprs: Iterable[float], table: Dict[float, Any]) -> Dict[str, Any]:
    """{"red": bool, "problems": [...], "lines": [...]}"""
    problems: List[str] = []
    lines: List[str] = []
    expected = [float(d) for d in expected_dprs]
    for dpr in expected:
        if not any(float(r["dpr_asked"]) == dpr for r in runs):
            problems.append(f"dpr {dpr:g}: no result (the job is missing or wrote nothing)")
    usable = []
    pairs = compared_runs(runs)
    for run in sorted(runs, key=lambda r: (float(r["dpr_asked"]), r["mode"], r["resolution"], r["launch"], str(r.get("dev_px_per_px")))):
        steps = run.get("steps", {})
        incomplete = [s for s in required_steps(run, id(run) in pairs) if not (s in steps and step_is_complete(steps[s]))]
        if run.get("error") or run.get("processes_left") != 0 or incomplete:
            problems.append(f"{name_of(run)}: error={run.get('error')!r} processes_left={run.get('processes_left')!r} "
                            f"missing or incomplete steps={incomplete} steps={sorted(run.get('steps', {}))}")
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
                lines.append(f"    maximized {name}: {what} = {got} ; core declares {want} : {'ok' if ok else 'DIFFERENT'}")
                if not ok:
                    problems.append(f"{name_of(run)}: maximized {name} is {got}, the core declares {want}")
        wanted = required_steps(run, id(run) in pairs)
        for step_name in wanted:
            step = run["steps"][step_name]
            if "not_possible" in step:
                lines.append(f"    {step_name}: NOT POSSIBLE ({step['not_possible']})")
                continue
            if "chrome" not in step:  # a popup: its own page and its own window, no chrome record of the main window
                lines.extend(popup_lines(step_name, step, dpr, table.get(dpr)))
                if step_name == "popup_second":
                    lines.extend(second_popup_lines(step_name, step, dpr))
                lines.extend(page_early_lines(step_name, step, dpr))
                continue
            lines.extend(standard_lines(step_name, step, dpr))
            lines.extend(page_early_lines(step_name, step, dpr))
            lines.extend(fullscreen_line(step_name, step))
            lines.extend(frames_lines(step_name, step))
            lines.extend(early_lines(step_name, step, dpr))
            lines.extend(transformed_lines(step_name, step))
        for step_name in wanted:
            step = run["steps"][step_name]
            line = probe_line(step_name, step) if "not_possible" not in step else None
            if line:
                lines.append(line)
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
                printed = printed_differences(first, other)
                lines.append(f"dpr {dpr:g} launch {first['launch']} vs {other['launch']}: {len(printed)} difference(s) in the steps "
                             f"{list(plan.PRINTED_STEPS)} (printed, not judged)")
                lines.extend(f"    {path}: {va!r} vs {vb!r}" for path, va, vb in printed)
                early = early_differences(first, other)
                lines.append(f"dpr {dpr:g} launch {first['launch']} vs {other['launch']}: {len(early)} difference(s) in what the pages and "
                             "the frames read in their first moments (printed, not judged)")
                lines.extend(f"    {path}: {va!r} vs {vb!r}" for path, va, vb in early)
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
