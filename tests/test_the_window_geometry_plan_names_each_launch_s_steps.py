"""The plan of the window-geometry capture: which launch takes which steps, which grid points fit, how a job is ordered.

scripts/retail/window_geometry/plan.py and run_scale.py, with no browser and no Windows call (the display and the
measurement are replaced by fakes that record what was asked). Held here:
  * the grid is 17 widths by 16 heights, every point of the grid check is a point of the grid, the minimum asked is 100x100;
  * a point fits when its outer size (inner plus the frame of the window, in CSS, at the scale) is inside the work area;
  * Juggler's height is 844 when the work area holds it plus the frame, else the largest multiple of 10 that fits, and None
    (the step is skipped and says so) when even 200 does not;
  * launch a takes the four steps plus the grid and the minimum, launch b the four plus the grid check, only at the scales of
    the grid and only at the first reached resolution; every other launch takes the four steps;
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


def test_the_grid_is_the_widths_and_heights_of_the_order_and_contains_the_grid_check():
    assert len(plan.GRID_WIDTHS) == 17 and len(plan.GRID_HEIGHTS) == 16 and len(plan.grid_points()) == 272
    assert plan.GRID_WIDTHS == tuple(sorted(plan.GRID_WIDTHS)) and plan.GRID_HEIGHTS == tuple(sorted(plan.GRID_HEIGHTS))
    assert plan.GRID_WIDTHS[0] == 500 and plan.GRID_WIDTHS[-1] == 1900 and plan.GRID_HEIGHTS[0] == 450 and plan.GRID_HEIGHTS[-1] == 1000
    assert plan.GRID_CHECK_POINTS == ((500, 450), (1280, 672), (1366, 768), (1536, 816), (1900, 1000), (800, 600))
    assert set(plan.GRID_CHECK_POINTS) <= set(plan.grid_points())
    assert plan.MINIMUM_ASKED == (100, 100) and plan.GRID_DPRS == (1.0, 1.25) and plan.DEVPX_VALUES == (0.75, 0.5)


def test_a_point_fits_when_its_outer_size_is_inside_the_work_area():
    frame = [16, 16]
    assert plan.fits(1900, 1000, frame, 1.0, WORK)  # 1916 x 1016 in 1920 x 1032
    assert not plan.fits(1900, 1000, frame, 1.25, WORK)
    assert not plan.fits(1905, 450, frame, 1.0, WORK) and plan.fits(1904, 450, frame, 1.0, WORK)
    assert plan.fits(1500, 800, frame, 1.25, WORK) and not plan.fits(1536, 800, frame, 1.25, WORK)
    assert not plan.fits(500, 1017, frame, 1.0, WORK) and plan.fits(500, 1016, frame, 1.0, WORK)


def test_the_points_that_fit_at_each_scale_of_the_grid_are_a_subset_that_shrinks_with_the_scale():
    def count(scale):
        return sum(plan.fits(w, h, [16, 16], scale, WORK) for w, h in plan.grid_points())

    assert count(1.0) == 272 and 0 < count(1.25) < 272 and count(1.5) < count(1.25)


def test_juggler_s_height_is_844_when_it_fits_else_the_largest_multiple_of_ten_that_does():
    assert plan.juggler_height(WORK, 1.0) == 844, "1032 css minus the frame holds 844"
    assert plan.juggler_height(WORK, 1.25) == 700 and plan.juggler_height(WORK, 1.25) % 10 == 0
    assert plan.juggler_height(WORK, 1.5) == 560
    assert plan.juggler_height(WORK, 2.0) == 390
    assert plan.juggler_height([0, 0, 800, 120 + 844], 1.0) == 844
    assert plan.juggler_height([0, 0, 800, 120 + 843], 1.0) == 840
    assert plan.juggler_height([0, 0, 800, 300], 1.0) is None, "180 css do not hold the 200 minimum: the step is skipped"
    assert (plan.JUGGLER_WIDTH, plan.JUGGLER_HEIGHT) == (390, 844)


@pytest.mark.parametrize("dpr,launch,first,expected", [
    (1.0, "a", True, [*plan.CORE_STEPS, "grid", "minimum"]),
    (1.25, "a", True, [*plan.CORE_STEPS, "grid", "minimum"]),
    (1.0, "b", True, [*plan.CORE_STEPS, "grid_check"]),
    (1.25, "b", True, [*plan.CORE_STEPS, "grid_check"]),
    (1.0, "a", False, list(plan.CORE_STEPS)),
    (1.5, "a", True, list(plan.CORE_STEPS)),
    (1.5, "b", True, list(plan.CORE_STEPS)),
    (2.0, "a", True, list(plan.CORE_STEPS)),
    (1.0, "c", True, list(plan.CORE_STEPS)),
])
def test_each_launch_takes_the_steps_of_the_plan(dpr, launch, first, expected):
    assert plan.steps_for(dpr, launch, ["a", "b", "c"], first) == expected


def test_the_four_steps_are_the_ones_the_judge_compares_and_the_grid_steps_are_not_among_them():
    assert plan.CORE_STEPS == ("maximized", "restored", "juggler", "juggler_rdm")
    assert not set(plan.GRID_STEPS) & set(plan.CORE_STEPS)
    assert plan.RESERVED_LAUNCHES == ("p", "q", "m")


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
           "resolutions": [[1920, 1080], [2560, 1440]], "round": 2}


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
    assert by_launch[("a", "display")] == [*plan.CORE_STEPS, "grid", "minimum"]
    assert by_launch[("b", "display")] == [*plan.CORE_STEPS, "grid_check"]
    assert by_launch[("p", "devPixelsPerPx")] == list(plan.CORE_STEPS)
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
    assert {tuple(c["steps"]) for c in fakes.calls if c["launch"] in ("a", "b", "p")} == {plan.CORE_STEPS}, "no grid at 1.5"


def test_a_scale_windows_does_not_reach_runs_p_and_q_only(monkeypatch, tmp_path):
    fakes = Fakes(monkeypatch, tmp_path)
    assert run_scale.run(tmp_path, {}, REQUEST, 2.0, tmp_path) == 0
    assert fakes.summary() == [("p", (1920, 1080), "devPixelsPerPx", "2"), ("q", (1920, 1080), "devPixelsPerPx", "2")]
    assert all(c["steps"] == list(plan.CORE_STEPS) for c in fakes.calls)
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
