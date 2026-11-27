"""The judge of the window-geometry capture is red for what the rule names and green for what it does not.

scripts/retail/window_geometry/judge.py reads the raw files of every scale. Held here, on small synthetic runs built from the
what the core declares (frame_table.py reads the checked-out constants.py with the core's own functions):
  * launch a equal to launch b and a maximized step equal to WINDOW_FRAME_BY_DPR and declared_maximized_ui (the density rule
    at the measured availHeight) at every scale: green;
  * the border one pixel off, screenX off, the content top off, at any scale and size reached live: red, naming what differs;
    the bar of a maximized window is judged at the measured availHeight (1.5 at 672 CSS: 109 green, 127 red);
  * launch a different from launch b on one number of any of the six steps (maximized, restored, juggler, juggler_rdm, juggler2, juggler_back): red,
    with the number printed; the grid, the minimum and the grid check are not in that comparison;
  * round 5: the two launches that are compared (a and b; p and q) also need the fifteen steps of plan.ROUND5_STEPS (fullscreen,
    F11-style fullscreen, iframes, popups, the repeated Juggler call), compare equal on each, and print each; a step recorded
    as not possible is complete only with a reason, which is printed; a missing step is red;
  * at the scales of the grid, launch a must have its grid and minimum, launch b its grid check, and every point of the check
    must be exactly launch a's row at that point;
  * launch p must equal launch q (the derived path at a scale Windows did not reach);
  * launch m (every resolution Windows offers) needs the maximized step alone;
  * a scale whose display scale was not reached: "not reachable live: derived path", not red, whatever it measured;
  * the derived-path launch (devPixelsPerPx) is reported and never compared with the table;
  * a missing scale, an error, a process left, a scale with no pair of launches: red.
"""
from __future__ import annotations

import copy
import json

import pytest

from _retail_scripts import load_window_geometry

pytestmark = pytest.mark.unit

judge = load_window_geometry("judge")
plan = load_window_geometry("plan")
frame_table = load_window_geometry("frame_table")
TABLE = frame_table.load()
WORK = [0, 0, 1920, 1032]


def grid_row(w, h, dpr, skipped=False):
    if skipped:
        return {"w": w, "h": h, "skipped": "does_not_fit", "frame_css": [16, 16]}
    return {"w": w, "h": h, "settled": True,
            "chrome": {"windowState": 3, "sizemode": "normal", "innerWidth": w, "innerHeight": h, "outerWidth": w + 16,
                       "outerHeight": h + 16, "devicePixelRatio": dpr, "stack": {"x": 0, "y": 73.0, "width": w, "height": h - 73}},
            "page": {"innerWidth": w, "innerHeight": h - 73, "outerWidth": w + 16, "outerHeight": h + 16},
            "native": {"window_rect": [4, 4, 4 + round((w + 16) * dpr), 4 + round((h + 16) * dpr)], "client_rect": [0, 0, 10, 10],
                       "client_origin": [12, 8]}}


def probe(dpr):
    """What page.html's __probe returns, as the content script parses it."""
    return {"screen": {"width": 1536, "height": 864, "availWidth": 1536, "availHeight": 825, "availLeft": 0, "availTop": 0,
                       "colorDepth": 24, "pixelDepth": 24, "orientation": {"type": "landscape-primary", "angle": 0}},
            "devicePixelRatio": dpr,
            "found": {"width": 1900, "height": 900, "device-width": 1536, "device-height": 864, "aspect-ratio_x10000": 21111,
                      "device-aspect-ratio_x10000": 17777, "resolution_x100": round(dpr * 100)},
            "matches": {"(width: 1900px)": True, "(pointer: fine)": True, "(pointer: coarse)": False, "(hover: hover)": True,
                        "(orientation: landscape)": True},
            "touch": {"maxTouchPoints": 0, "ontouchstart": False, "touchEvent": "undefined"},
            "visualViewport": {"width": 1900, "height": 900, "scale": 1, "offsetLeft": 0, "offsetTop": 0, "pageLeft": 0, "pageTop": 0},
            "client": {"width": 1900, "height": 900}}


def popup_step(features="width=500,height=400", dpr=1.0):
    """What a popup step holds: the popup window's own rects, the mouse it was given, its page's record (no chrome record)."""
    return {"features": features, "windows_opened": 1, "settled": True, "closed": True,
            "native": {"window_rect": [100, 80, 100 + round(516 * dpr), 80 + round(540 * dpr)], "client_rect": [0, 0, round(500 * dpr), round(400 * dpr)],
                       "client_origin": [108, 152], "work_rect": WORK, "dpi": round(96 * dpr)},
            "input": [{"asked": [300, 400], "cursor": [300, 400]}],
            "content": {"innerWidth": 500, "innerHeight": 400, "outerWidth": 516, "outerHeight": 540, "screenX": 100, "screenY": 80,
                        "screenLeft": 100, "screenTop": 80, "mozInnerScreenX": 108.0, "mozInnerScreenY": 152.0, "devicePixelRatio": dpr,
                        "screen": {"width": 1536, "height": 864, "pixelDepth": 24}, "events": {"count": 3, "dx": 108, "dy": 72},
                        "probe": probe(dpr)}}


def frame_state(dpr):
    return {"innerWidth": 300, "innerHeight": 120, "outerWidth": 1936, "outerHeight": 1049, "screenX": -7, "screenY": -7,
            "mozInnerScreenX": 10.0, "mozInnerScreenY": 200.0, "devicePixelRatio": dpr,
            "events": {"count": 3, "dx": 7, "dy": 91}, "probe": probe(dpr)}


def round5_steps(step, rect, ui, dpr):
    """The fifteen steps of round 5 of a launch whose window is `rect` with `ui` device pixels of chrome."""
    steps = {}
    for name in plan.ROUND5_STEPS:
        if name.startswith("popup"):
            steps[name] = popup_step("left=100,top=80,width=500,height=400" if name == "popup_pos" else "width=500,height=400", dpr)
        elif name.startswith("iframes"):
            steps[name] = dict(step(rect, ui), frames={n: {"kind": n, "rect": {"x": 10, "y": 60, "width": 300, "height": 120},
                                                           "input": [{"asked": [5, 6], "cursor": [5, 6]}], "state": frame_state(dpr)}
                                                       for n in ("same", "cross")})
        elif name.startswith(("fullscreen", "f11")):
            steps[name] = dict(step(rect, ui), fullscreen={"window_fullScreen": name.endswith("_in"), "windowState": 4,
                                                           "document_fullscreen": name.startswith("fullscreen") and name.endswith("_in")})
        else:
            steps[name] = step(rect, ui)
    return steps


def hostgate_step(false_posted=True, true_posted=True):
    def entry(posted):
        return {"posted": posted, "seconds": 14.2 if posted else 120.0, "bytes": 31234 if posted else 0,
                "sha256": "ab" * 32 if posted else None, "inRDMPane": False}

    return {"juggler_height": 844, "timeout_seconds": 120.0, "rdm_false": entry(false_posted), "rdm_true": entry(true_posted)}


GRID = [(w, h) for w in (500, 800, 1280, 1366, 1536, 1900) for h in (450, 600, 672, 768, 816, 1000)]


def make_run(dpr, launch="a", resolution=(1920, 1080), mode="display", reached=True, table=TABLE, grid=None, dppx=None,
             avail=None, ui=None):
    """A run whose maximized step is what the core declares (the bar from the density rule at availHeight `avail` CSS, default the work area). `grid` None: what the launch has in the plan at the scales of the grid."""
    frame = table[dpr]
    border = frame.border
    avail = (WORK[3] - WORK[1]) / dpr if avail is None else avail
    ui = frame_table.declared_maximized_ui(frame, avail) if ui is None else ui
    window = [-border, -border, WORK[2] + border, WORK[3] + border]

    def step(rect, top_ui):
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        return {
            "chrome": {"windowState": 1, "maximized": True, "devicePixelRatio": dpr, "stack": {"x": 0, "y": top_ui / dpr - 0.4, "width": 1, "height": 1},
                       "browser": {"x": 0, "y": top_ui / dpr, "width": 100, "height": 100}, "sizemode": "maximized"},
            "native": {"window_rect": rect, "client_rect": [0, 0, width - 2 * border, height - border],
                       "client_origin": [rect[0] + border, rect[1]], "work_rect": WORK, "dpi": round(96 * dpr)},
            "content": {"innerWidth": 1900, "innerHeight": 900, "outerWidth": 1936, "outerHeight": 1049,
                        "screenX": judge.half_up(-border / dpr), "screenY": judge.half_up(-border / dpr),
                        "mozInnerScreenX": 0.0, "mozInnerScreenY": ui / dpr,
                        "screen": {"width": 1536, "height": 864, "availHeight": avail},
                        "events": {"count": 3, "dx": 7, "dy": 91}, "probe": probe(dpr)},
            "input": [{"asked": [1, 2], "cursor": [1, 2]}], "settled": True}

    restored = [100, 120, 100 + 390 + 2 * border, 120 + 844 + 100]
    steps = {"maximized": step(window, ui)}
    if launch != "m":
        steps.update({"restored": step(restored, frame.popup_ui), "juggler": step(restored, frame.popup_ui),
                      "juggler_rdm": step(restored, frame.popup_ui), "juggler2": step(restored, frame.popup_ui),
                      "juggler_back": dict(step(restored, frame.popup_ui), window_state={"windowState": 3, "sizemode": "normal", "maximized": False})})
        steps.update(round5_steps(step, restored, frame.popup_ui, dpr))
    if grid is None:
        grid = mode == "display" and reached and float(dpr) in plan.GRID_DPRS and launch in ("a", "b")
    if grid and launch == "a":
        steps["grid"] = [grid_row(w, h, dpr) for w, h in GRID]
        steps["minimum"] = grid_row(100, 100, dpr)
        steps["minimum"]["chrome"].update(innerWidth=500, innerHeight=493)
    if grid and launch == "b":
        steps["grid_check"] = [grid_row(w, h, dpr) for w, h in plan.GRID_CHECK_POINTS]
    if grid and float(dpr) in plan.HOSTGATE_DPRS and launch in ("a", "b"):
        steps["rdm_hostgate"] = hostgate_step()
    out = {"format": "retail-window-geometry/1", "dpr_asked": dpr, "mode": mode, "launch": launch, "resolution": list(resolution),
           "display": {"dpi_reached": reached, "effective_dpi": round(96 * dpr), "resolution_now": list(resolution),
                       "reason": None if reached else "the scale is outside the steps this monitor offers"},
           "steps": steps, "restored_size_used": {"width": 390, "height": 844},
           "juggler_size_used": {"width": 390, "height": 844, "asked_height": 844, "does_not_fit": False},
           "juggler2_size_used": {"width": 414, "height": 896, "max_height": 896},
           "juggler_back_size_used": {"width": 1900, "height": 900, "from_step": "maximized"},
           "dev_px_per_px": dppx, "processes_left": 0, "error": None}
    return out


def two_launches(dpr, **kw):
    return [make_run(dpr, "a", **kw), make_run(dpr, "b", **kw)]


def evaluate(runs, dprs=None, **kw):
    return judge.evaluate(runs, dprs or sorted({r["dpr_asked"] for r in runs}), TABLE, **kw)


def all_scales():
    return [run for dpr in sorted(TABLE) for run in two_launches(dpr)]


def test_the_core_s_own_table_is_green_at_every_scale():
    result = evaluate(all_scales(), sorted(TABLE))
    assert result["problems"] == [] and not result["red"]
    assert "maximized border" in "\n".join(result["lines"])


@pytest.mark.parametrize("dpr", sorted(TABLE))
def test_the_comparisons_are_the_three_of_the_rule(dpr):
    names = [c[0] for c in judge.check_maximized(make_run(dpr), TABLE)]
    assert names == ["border", "screenX", "maximized_ui"]
    assert all(c[4] for c in judge.check_maximized(make_run(dpr), TABLE))


def test_gecko_rounds_a_half_up():
    assert [judge.half_up(v) for v in (-6.5, -7.2 + 0.0, 0.5, 1.5, -0.5)] == [-6, -7, 1, 2, 0]


def widen_border(runs):
    for run in runs:
        rect = run["steps"]["maximized"]["native"]["window_rect"]
        rect[0] -= 1
        rect[2] += 1


def test_a_border_one_pixel_off_at_the_first_resolution_of_scale_1_is_red():
    runs = two_launches(1.0)
    widen_border(runs)
    result = evaluate(runs)
    assert result["red"]
    assert any("maximized border" in p for p in result["problems"])
    assert any(line.endswith(": DIFFERENT") for line in result["lines"])


@pytest.mark.parametrize("dpr", [d for d in sorted(TABLE) if d != 1.0])
def test_a_border_one_pixel_off_at_any_other_scale_is_red(dpr):
    runs = two_launches(dpr)
    widen_border(runs)
    result = evaluate(runs)
    assert result["red"] and any("maximized border" in p for p in result["problems"])
    assert not any("window-size dependent" in line for line in result["lines"])


def test_scale_1_at_another_size_is_red_too():
    runs = [make_run(1.0, launch, resolution=(1280, 720)) for launch in "ab"]
    assert not evaluate(runs)["red"]
    widen_border(runs)
    result = evaluate(runs)
    assert result["red"] and any("maximized border" in p for p in result["problems"])


def test_a_screen_x_one_off_is_red():
    runs = two_launches(1.0)
    for run in runs:
        run["steps"]["maximized"]["content"]["screenX"] += 1
    assert any("maximized screenX" in p for p in evaluate(runs)["problems"])


def test_a_content_top_one_device_pixel_off_is_red():
    runs = two_launches(1.0)
    for run in runs:
        run["steps"]["maximized"]["content"]["mozInnerScreenY"] += 1
    assert any("maximized maximized_ui" in p for p in evaluate(runs)["problems"])


def maximized_at(avail, ui, dpr=1.5):
    runs = [make_run(dpr, launch, avail=avail, ui=ui) for launch in "ab"]
    return evaluate(runs)


def test_the_bar_of_a_maximized_window_is_judged_at_the_measured_avail_height_by_the_density_rule():
    """1.5, availHeight 672 CSS: below 720, compact, 109 device px; 127 is the normal density's bar."""
    green = maximized_at(672, 109)
    assert green["problems"] == [] and not green["red"]
    red = maximized_at(672, 127)
    assert red["red"]
    assert any("maximized maximized_ui is 127, the core declares 109" in p for p in red["problems"])


@pytest.mark.parametrize("dpr", sorted(TABLE))
def test_the_bar_follows_the_density_rule_at_both_densities(dpr):
    frame = TABLE[dpr]
    for avail, bar in ((600, frame.ui_compact), (1000, frame.ui_normal)):
        assert not maximized_at(avail, bar, dpr)["red"]
        assert maximized_at(avail, frame.ui_normal if bar == frame.ui_compact else frame.ui_compact, dpr)["red"]


def test_launch_a_and_launch_b_that_differ_by_one_number_are_red_and_the_number_is_printed():
    a, b = two_launches(1.0)
    b["steps"]["restored"]["content"]["screenY"] += 1
    result = evaluate([a, b])
    assert result["red"] and any("differ" in p for p in result["problems"])
    assert any("restored.content.screenY" in line for line in result["lines"])


def test_equal_launches_report_no_difference():
    result = evaluate(two_launches(1.0))
    assert any("0 difference(s)" in line for line in result["lines"])


def test_a_scale_whose_display_scale_was_not_reached_is_not_red_even_when_it_measured_something_else():
    runs = two_launches(2.0, reached=False)
    for run in runs:
        run["steps"]["maximized"]["native"]["window_rect"][0] -= 5
        run["steps"]["maximized"]["content"]["screenX"] = 99
    result = evaluate(runs)
    assert not result["red"], result["problems"]
    assert any("not reachable live: derived path" in line for line in result["lines"])


def test_a_scale_not_reached_still_needs_its_launches_to_agree():
    a, b = two_launches(2.0, reached=False)
    b["steps"]["restored"]["content"]["innerWidth"] = 1
    assert evaluate([a, b])["red"]


def test_the_derived_path_is_reported_and_not_compared_with_the_table():
    runs = two_launches(1.25) + [make_run(1.25, "p", mode="devPixelsPerPx", reached=False)]
    runs[-1]["steps"]["maximized"]["content"]["screenX"] = 1234
    result = evaluate(runs)
    assert not result["red"], result["problems"]
    assert any("(devPixelsPerPx)" in line for line in result["lines"])


def test_the_restored_step_is_printed_with_its_borders_ui_position_and_css_values():
    result = evaluate(two_launches(1.25))
    text = "\n".join(result["lines"])
    for needle in ("restored: border L/R/B 9/9/9", "ui ", "position dev [100, 120] css [80.0, 96.0]", "outer-inner css [36, 149]",
                   "mozInnerScreenX/Y", "events(count,dx,dy) [3, 7, 91]", "restored size used {'width': 390, 'height': 844}"):
        assert needle in text, needle


def test_a_scale_of_the_request_with_no_result_is_red():
    result = evaluate(two_launches(1.0), [1.0, 1.25])
    assert result["red"] and any("dpr 1.25: no result" in p for p in result["problems"])


@pytest.mark.parametrize("change", [{"processes_left": 1}, {"error": "MarionetteError: boom"}])
def test_a_run_with_a_process_left_or_an_error_is_red(change):
    a, b = two_launches(1.0)
    a.update(change)
    assert evaluate([a, b])["red"]


def test_a_run_without_a_step_or_without_its_window_record_is_red():
    a, b = two_launches(1.0)
    del a["steps"]["restored"]
    assert evaluate([a, b])["red"]
    c, d = two_launches(1.0)
    c["steps"]["maximized"]["native"] = {"error": "no window"}
    assert evaluate([c, d])["red"]


def test_one_launch_alone_is_red_because_nothing_is_compared():
    result = evaluate([make_run(1.0)])
    assert result["red"] and any("two launches" in p for p in result["problems"])


def test_a_second_resolution_with_one_launch_is_not_compared_and_not_red():
    runs = two_launches(1.0) + [make_run(1.0, "a", resolution=(2560, 1440))]
    assert not evaluate(runs)["red"]


def test_the_runs_are_read_from_the_artifact_folders(tmp_path):
    for run in two_launches(1.0):
        folder = tmp_path / "retail-window-geometry-159.0-dpr1" / "dpr1" / "1920x1080" / run["launch"]
        folder.mkdir(parents=True)
        (folder / "raw.json").write_text(json.dumps(run), encoding="utf-8")
    runs = judge.load_runs(tmp_path)
    assert len(runs) == 2 and not evaluate(runs)["red"]


def test_the_command_line_exits_one_when_red_and_prints_the_verdict(tmp_path, capsys):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"firefox_version": "159.0", "dpr": [1, 1.25], "launches": ["a", "b"], "resolutions": [[1920, 1080]],
                                   "round": 2}), encoding="utf-8")
    folder = tmp_path / "in" / "x"
    folder.mkdir(parents=True)
    (folder / "raw.json").write_text(json.dumps(make_run(1.0)), encoding="utf-8")
    assert judge.main(["--in", str(tmp_path / "in"), "--request", str(request)]) == 1
    assert "VERDICT: RED" in capsys.readouterr().out


def test_the_judge_does_not_change_the_runs_it_is_given():
    runs = all_scales()
    before = copy.deepcopy(runs)
    evaluate(runs, sorted(TABLE))
    assert runs == before


# ---------------------------------------------------------------------------- round 2: Juggler's sequence, grid, p/q, m


@pytest.mark.parametrize("step", ["juggler", "juggler_rdm", "juggler2", "juggler_back", "maximized", "restored"])
def test_launch_a_and_launch_b_differing_on_any_of_the_six_steps_is_red(step):
    a, b = two_launches(1.0)
    b["steps"][step]["content"]["innerHeight"] += 1
    result = evaluate([a, b])
    assert result["red"] and any(f"{step}.content.innerHeight" in line for line in result["lines"])


def test_the_grid_and_the_minimum_are_not_part_of_the_a_b_comparison():
    a, b = two_launches(1.25)
    a["steps"]["minimum"]["chrome"]["innerWidth"] += 7
    a["steps"]["grid"][GRID.index((500, 600))]["chrome"]["stack"]["y"] += 3  # not a point of the grid check
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    assert any("launch a vs b: 0 difference(s)" in line for line in result["lines"])


def test_a_juggler_step_skipped_in_both_launches_is_not_red_and_in_one_only_is_red():
    a, b = two_launches(1.5)
    for run in (a, b):
        run["steps"]["juggler"] = {"skipped": "does_not_fit"}
        run["steps"]["juggler_rdm"] = {"skipped": "does_not_fit"}
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    assert any("juggler: skipped (does_not_fit)" in line for line in result["lines"])
    c, d = two_launches(1.5)
    c["steps"]["juggler"] = {"skipped": "does_not_fit"}
    assert evaluate([c, d])["red"]


@pytest.mark.parametrize("step", ["juggler_rdm", "juggler2", "juggler_back"])
def test_a_launch_without_one_of_the_juggler_steps_is_red_and_launch_m_does_not_need_them(step):
    a, b = two_launches(1.0)
    del b["steps"][step]
    assert evaluate([a, b])["red"]
    runs = two_launches(1.0) + [make_run(1.0, "m", resolution=(1280, 720))]
    assert set(runs[-1]["steps"]) == {"maximized"}
    assert not evaluate(runs)["red"]


def test_the_juggler_steps_are_printed_with_page_screen_event_and_native_rects():
    text = "\n".join(evaluate(two_launches(1.25))["lines"])
    for step in ("juggler", "juggler_rdm", "juggler2", "juggler_back"):
        for needle in (f"{step}: border L/R/B", f"{step} page: inner [1900, 900] outer [1936, 1049] screen [1536, 864]",
                       "event dx/dy [7, 91]", "native client rect", "window rect [", "mozInnerScreenX/Y", "screenX/Y",
                       "outer-inner css [36, 149]"):
            assert needle in text, (step, needle)
    assert "juggler size used {'width': 390, 'height': 844" in text
    assert "juggler2 size used {'width': 414, 'height': 896" in text
    assert "juggler_back size used {'width': 1900, 'height': 900, 'from_step': 'maximized'}" in text


def test_the_juggler_steps_print_the_chrome_inner_size_stack_y_size_mode_and_window_state():
    text = "\n".join(evaluate(two_launches(1.25))["lines"])
    for step in ("juggler", "juggler2", "juggler_back"):
        line = next(line for line in text.splitlines() if line.startswith(f"    {step} chrome:"))
        for needle in ("inner ", "stack y ", "sizemode ", "windowState ", "native window rect [", "client rect ["):
            assert needle in line, (step, needle)


def test_a_juggler_back_that_does_not_end_maximized_in_one_launch_only_is_red():
    a, b = two_launches(1.0)
    assert a["steps"]["juggler_back"]["chrome"]["windowState"] == 1, "launch a: maximized again"
    b["steps"]["juggler_back"]["chrome"]["windowState"] = 3
    b["steps"]["juggler_back"]["chrome"]["sizemode"] = "normal"
    result = evaluate([a, b])
    assert result["red"] and any("juggler_back.chrome.windowState" in line for line in result["lines"])


@pytest.mark.parametrize("what", ["a has no grid", "a has no minimum", "b has no grid_check", "a grid row failed",
                                  "check point not in the grid"])
def test_at_the_scales_of_the_grid_a_missing_or_failed_part_is_red(what):
    a, b = two_launches(1.25)
    if what == "a has no grid":
        del a["steps"]["grid"]
    elif what == "a has no minimum":
        del a["steps"]["minimum"]
    elif what == "b has no grid_check":
        del b["steps"]["grid_check"]
    elif what == "a grid row failed":
        a["steps"]["grid"][3] = {"w": 500, "h": 600, "error": "MarionetteError: boom"}
    else:
        b["steps"]["grid_check"][0] = grid_row(501, 450, 1.25)
    assert evaluate([a, b])["red"], what


def test_a_grid_check_point_that_shows_another_content_top_is_red_and_the_number_is_printed():
    a, b = two_launches(1.0)
    b["steps"]["grid_check"][2]["chrome"]["stack"]["y"] += 12
    result = evaluate([a, b])
    assert result["red"] and any("grid check point 1366x768 differs" in p for p in result["problems"])
    assert any("stack_y" in line for line in result["lines"])


def test_a_grid_check_point_reached_at_another_size_but_showing_the_same_is_not_red():
    """At a fractional scale resizeBy lands on a size that depends on where it starts (run 45672953042, 1.25)."""
    a, b = two_launches(1.25)
    b["steps"]["grid_check"][2]["native"]["window_rect"][3] += 1
    b["steps"]["grid_check"][2]["chrome"]["innerHeight"] -= 0.8
    assert not evaluate([a, b])["red"]
    ok = evaluate(two_launches(1.0))
    n = len(plan.GRID_CHECK_POINTS)
    assert any(f"grid check: {n} of {n} point(s) equal" in line for line in ok["lines"])


def test_a_point_that_does_not_fit_is_a_skipped_row_equal_on_both_sides():
    a, b = two_launches(1.25)
    a["steps"]["grid"][-1] = grid_row(1900, 1000, 1.25, skipped=True)
    b["steps"]["grid_check"][4] = grid_row(1900, 1000, 1.25, skipped=True)
    assert not evaluate([a, b])["red"]
    b["steps"]["grid_check"][4] = grid_row(1900, 1000, 1.25)
    assert evaluate([a, b])["red"]


def test_the_grid_is_not_required_at_other_scales_or_when_the_scale_was_not_reached():
    assert not evaluate(two_launches(1.5))["red"]
    a, b = two_launches(1.25, reached=False)
    assert "grid" not in a["steps"] and not evaluate([a, b])["red"]


def test_the_grid_is_printed_as_a_table_per_height_with_the_content_top_and_the_minimum_window():
    a, b = two_launches(1.25)
    a["steps"]["grid"][0]["chrome"]["innerHeight"] = 453
    a["steps"]["grid"][1]["settled"] = False
    a["steps"]["grid"][2] = grid_row(500, 672, 1.25, skipped=True)
    text = "\n".join(evaluate([a, b])["lines"])
    first_line = text.split("H 450:")[1].splitlines()[0]
    assert first_line.startswith(" 500:73.00/") and "!" in first_line
    assert "500:-" in text and "*" in text.split("H 600:")[1].splitlines()[0]
    assert "asked -> got (chrome inner): 500x450 -> 500x453" in text
    assert "minimum window (asked 100x100 chrome inner css): chrome inner 500x493" in text
    assert judge.grid_cell(grid_row(800, 600, 1.0)) == "73.00/" + str(4 + 73)


def test_launch_p_and_launch_q_are_compared_like_a_and_b_when_the_display_scale_was_not_reached():
    p, q = (make_run(2.0, launch, mode="devPixelsPerPx", reached=False) for launch in "pq")
    result = evaluate([p, q])
    assert not result["red"], result["problems"]
    assert any("launch p vs q: 0 difference(s)" in line for line in result["lines"])
    q["steps"]["juggler"]["content"]["innerWidth"] += 1
    assert evaluate([p, q])["red"]
    assert evaluate([p])["red"], "p alone compares with nothing"


def test_a_scale_reached_live_needs_no_q_and_its_p_is_never_compared():
    runs = two_launches(1.25) + [make_run(1.25, "p", mode="devPixelsPerPx", reached=False)]
    assert not evaluate(runs)["red"]


def test_launch_m_is_checked_against_the_core_at_every_size_and_is_printed():
    wide = make_run(1.25, "m", resolution=(1280, 720))
    result = evaluate(two_launches(1.25) + [wide])
    assert not result["red"], result["problems"]
    assert any("launch m (display)" in line for line in result["lines"])
    wide["steps"]["maximized"]["native"]["window_rect"][0] -= 1
    result = evaluate(two_launches(1.25) + [wide])
    assert result["red"] and any("launch m (display)" in p and "maximized border" in p for p in result["problems"])


def test_the_devpixelsperpx_launches_m_are_labelled_with_the_value_and_need_no_pair_and_no_table():
    runs = two_launches(1.0)
    for value in plan.DEVPX_VALUES:
        run = make_run(1.0, "m", mode="devPixelsPerPx", reached=False, dppx="%g" % value)
        run["steps"]["maximized"]["content"]["screenX"] = 1234
        runs.append(run)
    result = evaluate(runs)
    assert not result["red"], result["problems"]
    text = "\n".join(result["lines"])
    assert "launch m (devPixelsPerPx 0.75)" in text and "launch m (devPixelsPerPx 0.5)" in text


# ---------------------------------------------------------------------------- what an RDM page can read, and the host-gate page


def test_the_probe_of_the_page_is_printed_for_the_four_steps_on_one_line_each():
    text = "\n".join(evaluate(two_launches(1.25))["lines"])
    for step in ("maximized", "restored", "juggler", "juggler_rdm"):
        line = next(line for line in text.splitlines() if line.startswith(f"    {step} probe:"))
        for needle in ("screen 1536x864", "avail 1536x825 at 0,0", "colorDepth 24 pixelDepth 24", "orientation landscape-primary/0", "dpr 1.25",
                       "matchMedia width 1900 height 900 device-width 1536 device-height 864", "aspect-ratio x10000 21111",
                       "device-aspect-ratio x10000 17777", "resolution x100 125", "'(pointer: fine)'", "maxTouchPoints 0",
                       "ontouchstart False", "TouchEvent undefined", "visualViewport height 900 offsetLeft 0", "root client 1900x900"):
            assert needle in line, (step, needle)
        assert "'(pointer: coarse)'" not in line, "only the queries that match are listed"


def test_a_difference_in_what_the_page_reads_between_launch_a_and_launch_b_is_red_on_the_juggler_rdm_step():
    a, b = two_launches(1.0)
    b["steps"]["juggler_rdm"]["content"]["probe"]["matches"]["(pointer: coarse)"] = True
    result = evaluate([a, b])
    assert result["red"] and any("juggler_rdm.content.probe.matches" in line for line in result["lines"])


def test_a_probe_missing_from_a_step_is_not_an_error_of_the_judge():
    a, b = two_launches(1.5)
    for run in (a, b):
        del run["steps"]["maximized"]["content"]["probe"]
    assert not evaluate([a, b])["red"]


def test_the_host_gate_outcomes_are_printed_and_a_green_run_has_both_posted():
    result = evaluate(two_launches(1.0))
    assert not result["red"], result["problems"]
    text = "\n".join(result["lines"])
    assert "rdm_hostgate inRDMPane=false: posted in 14.2 s, 31234 bytes, sha256 " + "ab" * 32 in text
    assert "rdm_hostgate inRDMPane=true: posted in 14.2 s" in text


def test_no_post_under_rdm_is_an_rdm_hang_a_finding_and_not_red():
    a, b = two_launches(1.0)
    for run in (a, b):
        run["steps"]["rdm_hostgate"] = hostgate_step(true_posted=False)
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    assert any("rdm_hostgate inRDMPane=true: RDM HANG (no POST in 120.0 s" in line for line in result["lines"])


def test_no_post_without_rdm_is_red():
    a, b = two_launches(1.0)
    b["steps"]["rdm_hostgate"] = hostgate_step(false_posted=False)
    result = evaluate([a, b])
    assert result["red"] and any("launch b: host-gate-2.html did not post without inRDMPane" in p for p in result["problems"])
    assert any("NO POST in" in line for line in result["lines"])


def test_a_missing_rdm_hostgate_step_is_red_at_scale_1_and_not_asked_elsewhere():
    a, b = two_launches(1.0)
    del a["steps"]["rdm_hostgate"]
    assert any("launch a has no rdm_hostgate" in p for p in evaluate([a, b])["problems"])
    assert "rdm_hostgate" not in two_launches(1.25)[0]["steps"] and not evaluate(two_launches(1.25))["red"]


def test_a_skipped_rdm_hostgate_is_not_red_and_is_the_same_in_the_determinism_check():
    a, b = two_launches(1.0)
    for run in (a, b):
        run["steps"]["rdm_hostgate"] = {"skipped": "does_not_fit"}
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    assert any("rdm_hostgate: skipped (does_not_fit)" in line for line in result["lines"])


def test_the_host_gate_step_is_not_part_of_the_a_b_comparison():
    a, b = two_launches(1.0)
    b["steps"]["rdm_hostgate"]["rdm_false"]["seconds"] = 99.0
    b["steps"]["rdm_hostgate"]["rdm_false"]["sha256"] = "cd" * 32
    assert not evaluate([a, b])["red"]


# ---------------------------------------------------------------------------- round 5: fullscreen, popups, iframes, the repeated call


def test_the_fifteen_steps_of_round_5_are_named_in_the_order_they_run_and_compared_with_the_six_core_steps():
    assert plan.ROUND5_STEPS == ("fullscreen_max_in", "fullscreen_max_out", "f11_max_in", "f11_max_out", "iframes_max",
                                 "iframes_transformed", "popup", "popup_pos", "juggler_repeat", "juggler2_repeat", "fullscreen_res_in", "fullscreen_res_out",
                                 "f11_res_in", "f11_res_out", "iframes_juggler")
    assert plan.COMPARED_STEPS == plan.CORE_STEPS + plan.ROUND5_STEPS and len(set(plan.COMPARED_STEPS)) == 21
    a, b = two_launches(1.0)
    assert set(a["steps"]) >= set(plan.COMPARED_STEPS)


@pytest.mark.parametrize("step", plan.ROUND5_STEPS)
def test_a_launch_compared_with_another_that_lacks_a_round_5_step_is_red_and_names_it(step):
    a, b = two_launches(1.25)
    del b["steps"][step]
    result = evaluate([a, b])
    assert result["red"] and any(step in p and "launch b" in p for p in result["problems"]), result["problems"]


def test_the_round_5_steps_are_not_asked_of_a_launch_that_is_compared_with_nothing_or_of_launch_m():
    alone = make_run(1.25, "a", resolution=(1280, 720))
    other = two_launches(1.25)
    for name in plan.ROUND5_STEPS:
        del alone["steps"][name]
    result = evaluate(other + [alone])
    assert not result["red"], result["problems"]
    p_alone = make_run(1.25, "p", mode="devPixelsPerPx", reached=False)
    for name in plan.ROUND5_STEPS:
        del p_alone["steps"][name]
    assert not evaluate(other + [p_alone])["red"], "p beside a display pair is not compared"
    assert judge.required_steps({"launch": "m"}, True) == plan.MAXIMIZED_ONLY


def test_p_and_q_at_a_scale_windows_did_not_reach_are_the_pair_and_need_the_round_5_steps():
    p = make_run(2.0, "p", mode="devPixelsPerPx", reached=False)
    q = make_run(2.0, "q", mode="devPixelsPerPx", reached=False)
    assert not evaluate([p, q])["red"]
    del q["steps"]["popup_pos"]
    assert evaluate([p, q])["red"]


def test_which_runs_are_compared_is_the_first_two_launches_of_a_resolution_and_of_the_derived_path():
    a, b = two_launches(1.0)
    c = make_run(1.0, "c")
    p, q = make_run(1.0, "p", mode="devPixelsPerPx", reached=False), make_run(1.0, "q", mode="devPixelsPerPx", reached=False)
    m = make_run(1.0, "m", resolution=(1280, 720))
    other_size = make_run(1.0, "a", resolution=(1280, 720))
    ids = judge.compared_runs([a, b, c, p, q, m, other_size])
    assert ids == {id(a), id(b), id(p), id(q)}


def bump(run, step, path):
    """Add 1 to (or flip) the number at `path` of a step, e.g. frames.cross.state.screenX or native.window_rect.0."""
    *parents, last = path.split(".")
    node = run["steps"][step]
    for key in parents:
        node = node[int(key)] if key.isdigit() else node[key]
    index = int(last) if last.isdigit() else last
    node[index] = (not node[index]) if isinstance(node[index], bool) else node[index] + 1


@pytest.mark.parametrize("step,path,printed", [
    ("popup", "content.innerWidth", "popup.content.innerWidth"),
    ("popup_pos", "native.window_rect.0", "popup_pos.native.window_rect[0]"),
    ("iframes_max", "frames.cross.state.screenX", "iframes_max.frames.cross.state.screenX"),
    ("iframes_juggler", "frames.same.input.0.cursor.0", "iframes_juggler.frames.same.input[0].cursor[0]"),
    ("iframes_transformed", "frames.cross.state.screenX", "iframes_transformed.frames.cross.state.screenX"),
    ("fullscreen_max_in", "chrome.windowState", "fullscreen_max_in.chrome.windowState"),
    ("f11_res_out", "fullscreen.window_fullScreen", "f11_res_out.fullscreen.window_fullScreen"),
    ("juggler_repeat", "content.outerHeight", "juggler_repeat.content.outerHeight"),
    ("juggler2_repeat", "content.outerHeight", "juggler2_repeat.content.outerHeight"),
    ("fullscreen_res_in", "native.window_rect.1", "fullscreen_res_in.native.window_rect[1]")])
def test_launch_a_and_launch_b_differing_on_a_round_5_step_is_red_and_the_number_is_printed(step, path, printed):
    a, b = two_launches(1.0)
    bump(b, step, path)
    result = evaluate([a, b])
    assert result["red"] and any(line.strip().startswith(printed + ":") for line in result["lines"]), result["lines"][-6:]


def test_a_step_not_possible_with_a_reason_is_green_when_both_launches_say_it_and_the_reason_is_printed():
    a, b = two_launches(1.5)
    for run in (a, b):
        run["steps"]["popup"] = {"not_possible": "no popup window opened after a real click on the button"}
        run["steps"]["fullscreen_max_in"] = {"not_possible": "document.fullscreenElement stayed null after a real click on the button"}
        run["steps"]["fullscreen_max_out"] = {"not_possible": "element fullscreen was not entered"}
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    text = "\n".join(result["lines"])
    assert "popup: NOT POSSIBLE (no popup window opened after a real click on the button)" in text
    assert "fullscreen_max_in: NOT POSSIBLE (document.fullscreenElement stayed null after a real click on the button)" in text
    assert "popup_pos page:" in text, "the other popup is still printed"


def test_a_step_not_possible_in_one_launch_only_is_red_and_one_without_a_reason_is_red():
    a, b = two_launches(1.5)
    b["steps"]["popup_pos"] = {"not_possible": "no popup window opened after a real click on the button"}
    result = evaluate([a, b])
    assert result["red"] and any("launch a and b differ" in p for p in result["problems"]), result["problems"]
    for reason in ("", "   ", None, 3):
        c, d = two_launches(1.5)
        for run in (c, d):
            run["steps"]["iframes_max"] = {"not_possible": reason}
        result = evaluate([c, d])
        assert result["red"] and any("iframes_max" in p for p in result["problems"]), reason


def test_a_round_5_step_skipped_in_both_launches_is_green_and_in_one_only_is_red():
    a, b = two_launches(1.5)
    for run in (a, b):
        for name in ("juggler_repeat", "juggler2_repeat", "fullscreen_res_in", "fullscreen_res_out", "f11_res_in", "f11_res_out", "iframes_juggler"):
            run["steps"][name] = {"skipped": "does_not_fit"}
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    assert any("iframes_juggler: skipped (does_not_fit)" in line for line in result["lines"])
    c, d = two_launches(1.5)
    c["steps"]["f11_res_in"] = {"skipped": "does_not_fit"}
    assert evaluate([c, d])["red"]


def test_every_round_5_step_is_printed_with_what_it_recorded():
    text = "\n".join(evaluate(two_launches(1.25))["lines"])
    lines = text.splitlines()
    for name in plan.ROUND5_STEPS:
        assert any(line.startswith(f"    {name}") for line in lines), name
    for name in ("popup", "popup_pos"):
        line = next(line for line in lines if line.startswith(f"    {name} (window.open features"))
        for needle in ("native window rect [", "client rect [", "client origin [108, 152]", "window top to client top 72",
                       "core MODEL popup_ui", "not judged: Firefox 151 on a workbench", "border L/R/B"):
            assert needle in line, (name, needle)
        page = next(line for line in lines if line.startswith(f"    {name} page:"))
        for needle in ("inner [500, 400]", "outer [516, 540]", "screenX/Y [100, 80]", "mozInnerScreenX/Y [108.0, 152.0]",
                       "events(count,dx,dy) [3, 108, 72]"):
            assert needle in page, (name, needle)
    assert "features 'left=100,top=80,width=500,height=400'" in text and "features 'width=500,height=400'" in text
    for step in ("iframes_max", "iframes_juggler"):
        for frame in ("same", "cross"):
            line = next(line for line in lines if line.startswith(f"    {step} frame {frame} ("))
            for needle in ("rect css {", "inner [300, 120]", "outer [1936, 1049]", "screenX/Y [-7, -7]", "mozInnerScreenX/Y [10.0, 200.0]",
                           "dpr 1.25", "events(count,dx,dy) [3, 7, 91]", "mouse asked/cursor [([5, 6], [5, 6])]"):
                assert needle in line, (step, frame, needle)
            assert any(other.startswith(f"    {step} frame {frame} probe:") for other in lines)
    assert "fullscreen_max_in fullscreen: {'window_fullScreen': True" in text and "'document_fullscreen': True" in text
    assert "f11_res_out fullscreen: {'window_fullScreen': False" in text


def test_the_transformed_iframes_are_printed_with_each_point_s_client_and_screen_and_are_not_judged():
    a, b = two_launches(1.0)
    for run in (a, b):
        run["steps"]["iframes_transformed"]["frames"] = {
            name: {"kind": "cross-site", "transform": transform, "rect": {"x": 10, "y": 200, "width": 450, "height": 180},
                   "geometry": {"rect": {"x": 10, "y": 200, "width": 450, "height": 180}, "matrix": [1.5, 0, 0, 1.5, 0, 0]},
                   "points": [{"device": [100 + i, 300 + i], "css": [100.5 + i, 300.25 + i]} for i in range(3)],
                   "input": [{"asked": [100 + i, 300 + i], "cursor": [100 + i, 300 + i]} for i in range(3)],
                   "state": dict(frame_state(1.0), mozInnerScreenX=10.5, mozInnerScreenY=285.0,
                                 moves=[{"clientX": 40.5 + i, "clientY": 20.0, "screenX": 100 + i, "screenY": 300 + i, "dx": 59.5, "dy": 280.0}
                                        for i in range(3)])}
            for name, transform in (("cross_scale", "scale(1.5)"), ("cross_rotate", "rotate(10deg)"))}
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    lines = [line for line in result["lines"] if line.startswith("    iframes_transformed frame cross_scale")]
    head = next(line for line in lines if " scale(1.5): mozInnerScreenX/Y [10.5, 285.0]" in line)
    assert "moves seen 3 for 3 points" in head and "matrix [1.5, 0, 0, 1.5, 0, 0]" in head
    point = next(line for line in lines if " point 1: " in line)
    assert "asked [101, 301]" in point and "client [41.5, 20.0] screen [101, 301] delta [59.5, 280.0]" in point
    assert any(line.startswith("    iframes_transformed frame cross_rotate point 2:") for line in result["lines"])
    for run in (a, b):
        run["steps"]["iframes_transformed"] = {"not_possible": "the iframe cross_scale did not load"}
    assert not evaluate([a, b])["red"]
    del b["steps"]["iframes_transformed"]
    assert evaluate([a, b])["red"]


def test_the_repeat_of_the_first_juggler_call_is_printed_against_the_first_call_and_is_not_judged():
    a, b = two_launches(1.0)
    for run in (a, b):
        run["steps"]["juggler_repeat"]["content"]["outerHeight"] -= 6
        rect = run["steps"]["juggler_repeat"]["native"]["window_rect"]
        run["steps"]["juggler_repeat"]["native"]["window_rect"] = rect[:3] + [rect[3] - 6]
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    line = next(line for line in result["lines"] if line.startswith("    juggler_repeat minus juggler:"))
    assert "page outer css [0, -6]" in line and "window rect size dev [0, -6]" in line and "position dev [0, 0]" in line
    assert not any(other.startswith("    juggler2_repeat minus juggler2:") and "[0, -6]" in other for other in result["lines"])
    a["juggler_repeat_size_used"] = {"width": 390, "height": 844, "same_as": "juggler"}
    assert any("juggler_repeat size used {'width': 390, 'height': 844, 'same_as': 'juggler'}" in other
               for other in evaluate([a, b])["lines"])
    skipped = [make_run(1.0, "a"), make_run(1.0, "b")]
    for run in skipped:
        run["steps"]["juggler"] = {"skipped": "does_not_fit"}
        run["steps"]["juggler_repeat"] = {"skipped": "does_not_fit"}
    assert not any(other.startswith("    juggler_repeat minus") for other in evaluate(skipped)["lines"])


def test_the_repeated_juggler_call_is_printed_against_the_second_call_and_is_not_judged():
    a, b = two_launches(1.0)
    for run in (a, b):
        run["steps"]["juggler2_repeat"]["content"]["outerHeight"] -= 8
        rect = run["steps"]["juggler2_repeat"]["native"]["window_rect"]
        run["steps"]["juggler2_repeat"]["native"]["window_rect"] = rect[:3] + [rect[3] - 8]
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    line = next(line for line in result["lines"] if line.startswith("    juggler2_repeat minus juggler2:"))
    assert "page outer css [0, -8]" in line and "window rect size dev [0, -8]" in line and "position dev [0, 0]" in line
    a["juggler2_repeat_size_used"] = {"width": 414, "height": 896, "same_as": "juggler2"}
    assert any("juggler2_repeat size used {'width': 414, 'height': 896, 'same_as': 'juggler2'}" in other
               for other in evaluate([a, b])["lines"])
    skipped = [make_run(1.0, "a"), make_run(1.0, "b")]
    for run in skipped:
        run["steps"]["juggler2"] = {"skipped": "does_not_fit"}
        run["steps"]["juggler2_repeat"] = {"skipped": "does_not_fit"}
    assert not any(other.startswith("    juggler2_repeat minus") for other in evaluate(skipped)["lines"])


def test_pixel_depth_is_in_the_probe_line_of_every_step_that_has_a_page_record():
    text = "\n".join(evaluate(two_launches(1.0))["lines"])
    for name in plan.COMPARED_STEPS:
        line = next(line for line in text.splitlines() if line.startswith(f"    {name} probe:"))
        assert "pixelDepth 24" in line, name


# ---------------------------------------------------------------------------- round 7: what a frame reads in its first moments


def early_of(parse_x, now=1.0):
    return {moment: {"mozInnerScreenX": parse_x if moment == "parse" else 8.0, "mozInnerScreenY": 85.0 if moment == "parse" else 93.0,
                     "screenX": -8, "screenY": -8, "outerWidth": 1936, "outerHeight": 1049, "innerWidth": 300, "innerHeight": 120,
                     "devicePixelRatio": 1, "now": now + i} for i, moment in enumerate(("parse", "dcl", "raf1", "raf2", "load"))}


def with_early(run, parse_x=0.0, now=1.0):
    for step_name in ("iframes_max", "iframes_juggler"):
        for frame in run["steps"][step_name]["frames"].values():
            frame["state"]["early"] = early_of(parse_x, now)


def test_the_early_reads_of_each_frame_are_printed_per_moment_next_to_the_settled_reading_and_the_parent_and_are_not_judged():
    a, b = two_launches(1.0)
    with_early(a, 0.0, now=1.0)
    with_early(b, 0.0, now=7.5)  # only the time differs
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    for step_name in ("iframes_max", "iframes_juggler"):
        for name in ("same", "cross"):
            lines = [line for line in result["lines"] if line.startswith(f"    {step_name} frame {name} early ")]
            assert len(lines) == 10, "five moments, in the two compared launches"
            first = [line for line in lines if " early parse: " in line][0]
            assert "mozInnerScreenX/Y [0.0, 85.0] screenX/Y [-8, -8]" in first and "now 1.0 " in first
            assert "settled mozInnerScreenX/Y [10.0, 200.0] screenX/Y [-7, -7]" in first and "parent mozInnerScreenX/Y [" in first
            assert "window client origin css [" in first
            for moment in ("dcl", "raf1", "raf2", "load"):
                assert any(f" early {moment}: " in line for line in lines), moment
    assert any("0 difference(s) in what the frames read in their first moments" in line for line in result["lines"])


def test_a_difference_between_launch_a_and_launch_b_in_the_early_reads_is_printed_without_the_time_and_is_not_red():
    a, b = two_launches(1.0)
    with_early(a, 0.0)
    with_early(b, 0.0, now=9.0)
    b["steps"]["iframes_max"]["frames"]["cross"]["state"]["early"]["raf1"]["mozInnerScreenY"] = 85.0
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    lines = result["lines"]
    assert any("1 difference(s) in what the frames read in their first moments (printed, not judged)" in line for line in lines)
    assert any(line.startswith("    iframes_max.frames.cross.state.early.raf1.mozInnerScreenY: 93.0 vs 85.0") for line in lines)
    assert not any(".now" in line and "vs" in line for line in lines)
    assert any("dpr 1 launch a vs b: 0 difference(s)" in line for line in lines), "the judged comparison is the same as before"


def test_a_frame_without_early_or_with_a_moment_not_reached_is_printed_so_and_is_not_red():
    a, b = two_launches(1.0)
    for run in (a, b):
        with_early(run)
        del run["steps"]["iframes_max"]["frames"]["same"]["state"]["early"]
        del run["steps"]["iframes_juggler"]["frames"]["cross"]["state"]["early"]["raf2"]
        run["steps"]["iframes_juggler"]["frames"]["same"]["state"]["early"]["load"] = {"error": "error: boom"}
    result = evaluate([a, b])
    assert not result["red"], result["problems"]
    text = "\n".join(result["lines"])
    assert "iframes_max frame same early: none recorded" in text
    assert "iframes_juggler frame cross early raf2: not reached" in text
    assert "iframes_juggler frame same early load: mozInnerScreenX/Y [None, None]" in text and "error error: boom" in text


def test_the_early_reads_are_in_no_other_step_and_a_difference_in_a_judged_number_of_a_frame_is_still_red():
    a, b = two_launches(1.0)
    for run in (a, b):
        with_early(run)
        run["steps"]["iframes_transformed"]["frames"] = {}
    b["steps"]["iframes_max"]["frames"]["cross"]["state"]["screenX"] += 1
    result = evaluate([a, b])
    assert result["red"] and any("iframes_max.frames.cross.state.screenX" in line for line in result["lines"])
    assert not any(" early " in line for line in result["lines"] if line.startswith("    juggler"))
