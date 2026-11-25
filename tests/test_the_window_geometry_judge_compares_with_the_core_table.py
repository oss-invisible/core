"""The judge of the window-geometry capture is red for what the rule names and green for what it does not.

scripts/retail/window_geometry/judge.py reads the raw files of every scale. Held here, on small synthetic runs built from the
table of the package itself:
  * launch a equal to launch b and a maximized step equal to WINDOW_FRAME_BY_DPR at every scale: green;
  * the border one pixel off, screenX off, the content top off: red at the first resolution of scale 1 only, naming what
    differs; at any other scale or size the difference is printed as window-size dependent and is not red;
  * launch a different from launch b on one number of any of the six steps (maximized, restored, juggler, juggler_rdm, juggler2, juggler_back): red,
    with the number printed; the grid, the minimum and the grid check are not in that comparison;
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
                       "colorDepth": 24, "orientation": {"type": "landscape-primary", "angle": 0}},
            "devicePixelRatio": dpr,
            "found": {"width": 1900, "height": 900, "device-width": 1536, "device-height": 864, "aspect-ratio_x10000": 21111,
                      "device-aspect-ratio_x10000": 17777, "resolution_x100": round(dpr * 100)},
            "matches": {"(width: 1900px)": True, "(pointer: fine)": True, "(pointer: coarse)": False, "(hover: hover)": True,
                        "(orientation: landscape)": True},
            "touch": {"maxTouchPoints": 0, "ontouchstart": False, "touchEvent": "undefined"},
            "visualViewport": {"width": 1900, "height": 900, "scale": 1, "offsetLeft": 0, "offsetTop": 0, "pageLeft": 0, "pageTop": 0},
            "client": {"width": 1900, "height": 900}}


def hostgate_step(false_posted=True, true_posted=True):
    def entry(posted):
        return {"posted": posted, "seconds": 14.2 if posted else 120.0, "bytes": 31234 if posted else 0,
                "sha256": "ab" * 32 if posted else None, "inRDMPane": False}

    return {"juggler_height": 844, "timeout_seconds": 120.0, "rdm_false": entry(false_posted), "rdm_true": entry(true_posted)}


GRID = [(w, h) for w in (500, 800, 1280, 1366, 1536, 1900) for h in (450, 600, 672, 768, 816, 1000)]


def make_run(dpr, launch="a", resolution=(1920, 1080), mode="display", reached=True, table=TABLE, grid=None, dppx=None):
    """A run whose maximized step is the core table's row. `grid` None: what the launch has in the plan at the scales of the grid."""
    row = table[dpr]
    border, ui = row["border"], row["maximized_ui"]
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
                        "screen": {"width": 1536, "height": 864},
                        "events": {"count": 3, "dx": 7, "dy": 91}, "probe": probe(dpr)},
            "input": [{"asked": [1, 2], "cursor": [1, 2]}], "settled": True}

    restored = [100, 120, 100 + 390 + 2 * border, 120 + 844 + 100]
    steps = {"maximized": step(window, ui)}
    if launch != "m":
        steps.update({"restored": step(restored, row["popup_ui"]), "juggler": step(restored, row["popup_ui"]),
                      "juggler_rdm": step(restored, row["popup_ui"]), "juggler2": step(restored, row["popup_ui"]),
                      "juggler_back": dict(step(restored, row["popup_ui"]), window_state={"windowState": 3, "sizemode": "normal", "maximized": False})})
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
def test_a_border_one_pixel_off_at_any_other_scale_is_printed_as_window_size_dependent_and_is_not_red(dpr):
    runs = two_launches(dpr)
    widen_border(runs)
    result = evaluate(runs)
    assert not result["red"], result["problems"]
    assert any("DIFFERS (window-size dependent, see grid)" in line for line in result["lines"])


def test_scale_1_at_another_size_is_not_the_strict_point():
    runs = [make_run(1.0, launch, resolution=(1280, 720)) for launch in "ab"]
    widen_border(runs)
    result = evaluate(runs)
    assert not result["red"], result["problems"]
    assert any("DIFFERS (window-size dependent" in line for line in result["lines"])
    assert evaluate(runs, strict_point=(1.0, (1280, 720)))["red"], "the strict point is an argument, the request's first resolution"


def test_a_screen_x_one_off_is_red_at_the_strict_point():
    runs = two_launches(1.0)
    for run in runs:
        run["steps"]["maximized"]["content"]["screenX"] += 1
    assert any("maximized screenX" in p for p in evaluate(runs)["problems"])


def test_a_content_top_one_device_pixel_off_is_red_at_the_strict_point():
    runs = two_launches(1.0)
    for run in runs:
        run["steps"]["maximized"]["content"]["mozInnerScreenY"] += 1
    assert any("maximized maximized_ui" in p for p in evaluate(runs)["problems"])


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


def test_launch_m_is_checked_against_the_table_only_at_the_strict_point_and_is_printed_everywhere():
    wide = make_run(1.25, "m", resolution=(1280, 720))
    wide["steps"]["maximized"]["native"]["window_rect"][0] -= 1
    result = evaluate(two_launches(1.25) + [wide])
    assert not result["red"], result["problems"]
    assert any("launch m (display)" in line for line in result["lines"])
    assert any("DIFFERS (window-size dependent, see grid)" in line for line in result["lines"])
    at_strict = make_run(1.0, "m")
    at_strict["steps"]["maximized"]["native"]["window_rect"][0] -= 1
    assert evaluate(two_launches(1.0) + [at_strict])["red"]


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
        for needle in ("screen 1536x864", "avail 1536x825 at 0,0", "colorDepth 24", "orientation landscape-primary/0", "dpr 1.25",
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
