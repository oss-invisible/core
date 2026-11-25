"""The judge of the window-geometry capture is red for what the rule names and green for what it does not.

scripts/retail/window_geometry/judge.py reads the raw files of every scale. Held here, on small synthetic runs built from the
table of the package itself:
  * launch a equal to launch b and a maximized step equal to WINDOW_FRAME_BY_DPR at every scale: green;
  * the border one pixel off, screenX off, the content top off: red, each naming what differs;
  * launch a different from launch b on one number of the restored step: red, with the number printed;
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
frame_table = load_window_geometry("frame_table")
TABLE = frame_table.load()
WORK = [0, 0, 1920, 1032]


def make_run(dpr, launch="a", resolution=(1920, 1080), mode="display", reached=True, table=TABLE):
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
                        "events": {"count": 3, "dx": 7, "dy": 91}},
            "input": [{"asked": [1, 2], "cursor": [1, 2]}], "settled": True}

    restored = [100, 120, 100 + 390 + 2 * border, 120 + 844 + 100]
    return {"format": "retail-window-geometry/1", "dpr_asked": dpr, "mode": mode, "launch": launch, "resolution": list(resolution),
            "display": {"dpi_reached": reached, "effective_dpi": round(96 * dpr), "resolution_now": list(resolution),
                        "reason": None if reached else "the scale is outside the steps this monitor offers"},
            "steps": {"maximized": step(window, ui), "restored": step(restored, row["popup_ui"])},
            "restored_size_used": {"width": 390, "height": 844}, "processes_left": 0, "error": None}


def two_launches(dpr, **kw):
    return [make_run(dpr, "a", **kw), make_run(dpr, "b", **kw)]


def evaluate(runs, dprs=None):
    return judge.evaluate(runs, dprs or sorted({r["dpr_asked"] for r in runs}), TABLE)


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


@pytest.mark.parametrize("dpr", sorted(TABLE))
def test_a_border_one_pixel_off_is_red(dpr):
    runs = two_launches(dpr)
    for run in runs:
        rect = run["steps"]["maximized"]["native"]["window_rect"]
        rect[0] -= 1
        rect[2] += 1
    result = evaluate(runs)
    assert result["red"]
    assert any("maximized border" in p for p in result["problems"])


def test_a_screen_x_one_off_is_red():
    runs = two_launches(1.25)
    for run in runs:
        run["steps"]["maximized"]["content"]["screenX"] += 1
    assert any("maximized screenX" in p for p in evaluate(runs)["problems"])


def test_a_content_top_one_device_pixel_off_is_red():
    runs = two_launches(1.5)
    for run in runs:
        run["steps"]["maximized"]["content"]["mozInnerScreenY"] += 1 / 1.5
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
    request.write_text(json.dumps({"firefox_version": "159.0", "dpr": [1, 1.25], "launches": ["a", "b"], "resolutions": [[1920, 1080]]}), encoding="utf-8")
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
