"""The plan of the window-geometry capture: which launch takes which steps, how a job is ordered.

scripts/retail/window_geometry/plan.py and run_scale.py, with no browser and no Windows call (the display and the
measurement are replaced by fakes that record what was asked). Held here:
  * launch a and launch b take the maximized step and the eight steps of round 5 (plan.ROUND5_STEPS), at the first reached
    resolution; every other launch takes the maximized step alone; p and q take the round 5 steps only when no display launch
    could be made (they are then the pair that is compared);
  * no restored-window step is in the plan or in a launch (Juggler's resize in any call, the plain resize, the grid, the minimum
    window, the host-gate page under inRDMPane, launch n), and the code that took them is gone;
  * a job at a scale Windows reaches: a and b at the first resolution, p, m at every offered resolution (and, at scale 1,
    m at devPixelsPerPx 0.75 and 0.5); at a scale it does not reach: p and q only, no display launch, no m;
  * the every-resolution launches stop when the time budget is spent, and say so.
"""
from __future__ import annotations

import json

import pytest

from _retail_scripts import load_window_geometry

pytestmark = pytest.mark.unit

plan = load_window_geometry("plan")
run_scale = load_window_geometry("run_scale")
measure = load_window_geometry("measure")
WORK = [0, 0, 1920, 1032]


@pytest.mark.parametrize("launch,first,expected", [
    ("a", True, [*plan.CORE_STEPS, *plan.ROUND5_STEPS]),
    ("b", True, [*plan.CORE_STEPS, *plan.ROUND5_STEPS]),
    ("a", False, list(plan.CORE_STEPS)),
    ("c", True, list(plan.CORE_STEPS)),
])
def test_each_launch_takes_the_steps_of_the_plan(launch, first, expected):
    assert plan.steps_for(launch, ["a", "b", "c"], first) == expected


def test_the_steps_of_the_plan_are_the_maximized_one_and_the_nine_of_round_5_and_no_restored_window_step():
    assert plan.CORE_STEPS == ("maximized",) and plan.MAXIMIZED_ONLY == ("maximized",)
    assert plan.ROUND5_STEPS == ("fullscreen_max_in", "fullscreen_max_out", "f11_max_in", "f11_max_out", "iframes_max",
                                 "iframes_transformed", "popup", "popup_second", "popup_pos")
    assert plan.COMPARED_STEPS == plan.CORE_STEPS + tuple(s for s in plan.ROUND5_STEPS if s != "popup_second")
    assert len(set(plan.COMPARED_STEPS)) == 9
    assert plan.RESERVED_LAUNCHES == ("p", "q", "m") and plan.DEVPX_VALUES == (0.75, 0.5)
    assert plan.derived_steps(True) == list(plan.CORE_STEPS), "p beside a display pair is not compared"
    assert plan.derived_steps(False) == list(plan.COMPARED_STEPS), "p and q are the pair when no display launch could be made"
    for gone in ("restored", "juggler", "juggler_rdm", "juggler2", "juggler_back", "juggler_repeat", "juggler2_repeat", "fullscreen_res_in",
                 "fullscreen_res_out", "f11_res_in", "f11_res_out", "iframes_juggler", "grid", "minimum", "grid_check", "rdm_hostgate"):
        assert gone not in plan.COMPARED_STEPS, gone


def test_what_took_the_restored_window_steps_is_gone_from_the_plan_the_measurement_and_the_judge():
    judge, frame_table = load_window_geometry("judge"), load_window_geometry("frame_table")
    for module, names in ((plan, ("GRID_DPRS", "HOSTGATE_DPRS", "GRID_WIDTHS", "GRID_HEIGHTS", "GRID_CHECK_POINTS", "MINIMUM_ASKED", "GRID_STEPS",
                                  "JUGGLER_WIDTH", "JUGGLER_HEIGHT", "JUGGLER2_WIDTH", "juggler_height", "juggler2_height", "fits",
                                  "grid_points", "NORMAL_DENSITY_DPRS", "NORMAL_DENSITY_PANELS", "NORMAL_DENSITY_STEPS",
                                  "normal_density_panels", "qualifies_for_normal_density", "work_height_css", "TASKBAR_CSS")),
                          (measure, ("JUGGLER_RESIZE", "JUGGLER_SEQUENCE", "JUGGLER_CLEAR", "RDM_ON", "RDM_OFF", "RDM_STATE", "LOAD_IN_TAB",
                                     "RESIZE_CHROME_INNER", "CHROME_ROW", "HOSTGATE_PAGE", "capture_server")),
                          (measure.Session, ("restored", "juggler", "juggler_call", "juggler_again", "juggler_repeat", "juggler2_repeat",
                                             "juggler_back", "clear_juggler", "inner_row", "grid", "rdm_hostgate", "load")),
                          (run_scale, ("normal_density_not_possible",)),
                          (judge, ("grid_lines", "grid_problems", "hostgate_lines", "hostgate_problems", "repeat_lines", "normal_density_lines")),
                          (frame_table, ("declared_chrome_height", "density_at"))):
        for name in names:
            assert not hasattr(module, name), (module.__name__, name)


# ---------------------------------------------------------------------------- the order of a job


class Fakes:
    """The display of a runner and the measurement, replaced: records every measurement asked for."""

    def __init__(self, monkeypatch, tmp_path, live=(1.0, 1.25, 1.5), settable=((1920, 1080), (1024, 768)),
                 offered=((1024, 768), (1920, 1080), (2560, 1440))):
        self.calls, self.applied = [], []
        self.live, self.settable, self.offered = live, set(settable), [list(o) for o in offered]
        monkeypatch.setattr(run_scale.display, "enable_dpi_awareness", lambda: True)
        monkeypatch.setattr(run_scale.display, "current_resolution", lambda: [1024, 768])
        monkeypatch.setattr(run_scale.display, "apply", self.apply)
        monkeypatch.setattr(run_scale.measure, "measure", self.measure)

    def apply(self, dpr, resolution):
        self.applied.append((dpr, resolution))
        ok = resolution is None or tuple(resolution) in self.settable
        reached = float(dpr) in self.live
        shown = {"dpr": dpr, "offered_resolutions": self.offered, "effective_dpi": round(96 * dpr) if reached else 96,
                 "resolution_now": list(resolution or [1024, 768]), "dpi_reached": reached,
                 "dpi": {"reason": "outside the steps"}}
        if resolution is not None:
            shown["resolution"] = {"requested": list(resolution), "code": 0 if ok else -2, "ok": ok, "current": [1024, 768]}
        if not reached:
            shown["reason"] = "outside the steps"
        return shown

    def measure(self, install_dir, dpr, launch, resolution, shown, build, mode="display", prefs=None, table=None, steps_wanted=()):
        self.calls.append({"launch": launch, "resolution": list(resolution), "mode": mode, "prefs": prefs or {},
                           "steps": list(steps_wanted), "dpr": dpr})
        return {"launch": launch, "steps": {name: {} for name in steps_wanted}, "error": None, "processes_left": 0}

    def summary(self):
        return [(c["launch"], tuple(c["resolution"]), c["mode"], c["prefs"].get("layout.css.devPixelsPerPx")) for c in self.calls]


REQUEST = {"firefox_version": "159.0", "dpr": [1, 1.25, 1.5, 2], "launches": ["a", "b"],
           "resolutions": [[1920, 1080], [2560, 1440]], "round": 8}


def meta_of(tmp_path, dpr_label):
    return json.loads((tmp_path / f"dpr{dpr_label}" / "meta.json").read_text(encoding="utf-8"))


def test_a_scale_reached_live_runs_a_and_b_then_the_derived_launch_then_m_at_every_offered_resolution(monkeypatch, tmp_path):
    fakes = Fakes(monkeypatch, tmp_path)
    assert run_scale.run(tmp_path, {}, REQUEST, 1.25, tmp_path) == 0
    assert fakes.summary() == [
        ("a", (1920, 1080), "display", None), ("b", (1920, 1080), "display", None),
        ("p", (1920, 1080), "devPixelsPerPx", "1.25"),
        ("m", (1024, 768), "display", None), ("m", (1920, 1080), "display", None)]
    by_launch = {(c["launch"], c["mode"]): c["steps"] for c in fakes.calls}
    assert by_launch[("a", "display")] == by_launch[("b", "display")] == [*plan.CORE_STEPS, *plan.ROUND5_STEPS]
    assert by_launch[("p", "devPixelsPerPx")] == list(plan.CORE_STEPS), "p beside a display pair takes no round 5 step"
    assert all(c["steps"] == ["maximized"] for c in fakes.calls if c["launch"] == "m")
    meta = meta_of(tmp_path, "1.25")
    assert "2560x1440" in meta["skipped_resolutions"] and "resolution not set" in meta["skipped_resolutions"]["2560x1440"]


def test_scale_1_also_runs_m_at_devpixelsperpx_075_and_05_in_their_own_folders(monkeypatch, tmp_path):
    fakes = Fakes(monkeypatch, tmp_path)
    assert run_scale.run(tmp_path, {}, REQUEST, 1.0, tmp_path) == 0
    dppx = [c for c in fakes.calls if c["launch"] == "m" and c["mode"] == "devPixelsPerPx"]
    assert [c["prefs"] for c in dppx] == [{"layout.css.devPixelsPerPx": "0.75"}, {"layout.css.devPixelsPerPx": "0.5"}]
    assert all(c["steps"] == ["maximized"] and c["resolution"] == [1920, 1080] for c in dppx)
    for folder in ("m_dppx0.75", "m_dppx0.5", "m", "a", "b", "p"):
        assert (tmp_path / "dpr1" / "1920x1080" / folder / "raw.json").is_file(), folder
    assert [c["launch"] for c in fakes.calls].index("p") < [c["launch"] for c in fakes.calls].index("m")


def test_the_other_scales_do_not_run_the_devpixelsperpx_m_launches(monkeypatch, tmp_path):
    fakes = Fakes(monkeypatch, tmp_path)
    run_scale.run(tmp_path, {}, REQUEST, 1.5, tmp_path)
    assert not [c for c in fakes.calls if c["mode"] == "devPixelsPerPx" and c["launch"] == "m"]
    assert {tuple(c["steps"]) for c in fakes.calls if c["launch"] in ("a", "b")} == {plan.CORE_STEPS + plan.ROUND5_STEPS}, "the same pair at every scale"
    assert [c["steps"] for c in fakes.calls if c["launch"] == "p"] == [list(plan.CORE_STEPS)]


def test_a_scale_windows_does_not_reach_runs_p_and_q_only(monkeypatch, tmp_path):
    fakes = Fakes(monkeypatch, tmp_path)
    assert run_scale.run(tmp_path, {}, REQUEST, 2.0, tmp_path) == 0
    assert fakes.summary() == [("p", (1920, 1080), "devPixelsPerPx", "2"), ("q", (1920, 1080), "devPixelsPerPx", "2")]
    assert all(c["steps"] == list(plan.COMPARED_STEPS) for c in fakes.calls), "p and q are the pair: the round 5 steps too"
    meta = meta_of(tmp_path, "2")
    assert set(meta["skipped_resolutions"]) >= {"1920x1080", "1024x768", "2560x1440"} and meta["seconds"] >= 0


def test_the_every_resolution_launches_stop_when_the_time_budget_is_spent_and_say_so(monkeypatch, tmp_path):
    fakes = Fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(run_scale, "TIME_BUDGET_SECONDS", -1)
    run_scale.run(tmp_path, {}, REQUEST, 1.25, tmp_path)
    assert [c["launch"] for c in fakes.calls] == ["a", "b", "p"]
    assert meta_of(tmp_path, "1.25")["skipped_resolutions"]["1024x768"] == "time budget"


def test_a_launch_with_an_error_makes_the_job_fail_but_the_others_still_run(monkeypatch, tmp_path):
    fakes = Fakes(monkeypatch, tmp_path)
    original = fakes.measure

    def failing(*args, **kw):
        raw = original(*args, **kw)
        if args[2] == "b":
            raw["error"] = "boom"
        return raw

    monkeypatch.setattr(run_scale.measure, "measure", failing)
    assert run_scale.run(tmp_path, {}, REQUEST, 1.0, tmp_path) == 1
    assert "p" in [c["launch"] for c in fakes.calls]


def test_the_helpers_of_the_order():
    assert run_scale.derived_launches(True) == ["p"] and run_scale.derived_launches(False) == ["p", "q"]
    assert run_scale.reached({"dpi_reached": True}) and run_scale.reached({"dpi_reached": True, "resolution": {"ok": True}})
    assert not run_scale.reached({"dpi_reached": True, "resolution": {"ok": False}})
    assert not run_scale.reached({"dpi_reached": False, "resolution": {"ok": True}})
    assert run_scale.why_not({"resolution": {"ok": False, "code": -2, "current": [1, 2]}}).startswith("resolution not set (code -2")
    assert run_scale.why_not({"resolution": {"ok": True}, "reason": "no"}) == "no"


def test_no_launch_of_any_scale_takes_a_restored_window_step(monkeypatch, tmp_path):
    fakes = Fakes(monkeypatch, tmp_path, live=(1.0, 1.25, 1.5, 2.0))
    for dpr in (1.0, 1.25, 1.5, 2.0):
        run_scale.run(tmp_path, {}, REQUEST, dpr, tmp_path)
    taken = {name for call in fakes.calls for name in call["steps"]}
    assert taken == set(plan.CORE_STEPS + plan.ROUND5_STEPS), sorted(taken ^ set(plan.CORE_STEPS + plan.ROUND5_STEPS))
    assert "n" not in {call["launch"] for call in fakes.calls}
    assert not list(tmp_path.glob("dpr*/*/*/hostgate-*"))


def test_popup_second_runs_right_after_popup_in_launches_a_and_b_and_is_printed_and_not_judged():
    assert plan.ROUND5_STEPS[plan.ROUND5_STEPS.index("popup") + 1] == "popup_second"
    assert plan.PRINTED_STEPS == ("popup_second",) and "popup_second" not in plan.COMPARED_STEPS
    assert plan.round5_of("display") == plan.ROUND5_STEPS
    assert plan.round5_of("devPixelsPerPx") == tuple(s for s in plan.ROUND5_STEPS if s != "popup_second")
    assert plan.steps_for("a", ["a", "b"], True)[-2:] == ["popup_second", "popup_pos"]

