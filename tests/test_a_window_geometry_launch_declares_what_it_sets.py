"""The pure parts of the window-geometry capture: what the launch declares, how a wait ends, where the mouse goes.

No browser starts and no Windows call is made. Held here:
  * the launch is `firefox.exe -no-remote -profile <empty directory> -marionette <url>` and its profile holds one file,
    user.js, with exactly the declared prefs: anything else (a second file, another pref, a policy in the install folder, a
    MOZ_ variable) is refused before the spawn;
  * a wait ends only when the value is ready and has not moved for the hold time, and says so when it never settles;
  * the mouse points are inside the browser rectangle, from the client origin, at the scale of the window;
  * the structures handed to Windows have the sizes Windows expects on every OS (a WCHAR is 16 bits there, not the 32 bits
    of ctypes.c_wchar on Linux), and the scale steps are relative to the recommended one;
  * the run plan: every launch at the first resolution, the first alone at the others;
  * the launch takes only the steps asked for;
  * the page reads, in itself, everything that can differ under responsive-design mode (page.html __probe), and the content
    record of every step carries it.
"""
from __future__ import annotations

import ctypes
import json
from pathlib import Path

import pytest

from _retail_scripts import load_window_geometry

pytestmark = pytest.mark.unit

measure = load_window_geometry("measure")
display = load_window_geometry("display")
native = load_window_geometry("native")
run_scale = load_window_geometry("run_scale")


def make_profile(tmp_path, prefs):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "user.js").write_text(measure.user_js(prefs), encoding="utf-8")
    return profile


def command(profile):
    return measure.launch_command(Path("firefox.exe"), profile, "http://127.0.0.1:1/")


def test_the_launch_is_the_declared_one_and_a_clean_profile_passes(tmp_path):
    prefs = {"marionette.port": 2828}
    profile = make_profile(tmp_path, prefs)
    cmd = command(profile)
    assert cmd == ["firefox.exe", "-no-remote", "-profile", str(profile), "-marionette", "http://127.0.0.1:1/"]
    measure.assert_launch(cmd, profile, tmp_path / "install", {"PATH": "x"}, prefs)


def test_the_derived_path_declares_its_one_more_pref(tmp_path):
    prefs = {"marionette.port": 2828, "layout.css.devPixelsPerPx": "1.25"}
    profile = make_profile(tmp_path, prefs)
    measure.assert_launch(command(profile), profile, tmp_path / "install", {}, prefs)
    assert 'user_pref("layout.css.devPixelsPerPx", "1.25");' in (profile / "user.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("what", ["second file", "extra pref", "policy", "moz env", "other command"])
def test_a_launch_that_sets_anything_else_is_refused(tmp_path, what):
    prefs = {"marionette.port": 2828}
    profile, install, env, cmd = make_profile(tmp_path, prefs), tmp_path / "install", {}, None
    if what == "second file":
        (profile / "prefs.js").write_text("", encoding="utf-8")
    elif what == "extra pref":
        (profile / "user.js").write_text(measure.user_js(dict(prefs, **{"browser.x": True})), encoding="utf-8")
    elif what == "policy":
        (install / "distribution").mkdir(parents=True)
        (install / "distribution" / "policies.json").write_text("{}", encoding="utf-8")
    elif what == "moz env":
        env = {"MOZ_FOO": "1"}
    else:
        cmd = command(profile) + ["-remote-debugging-port", "1"]
    with pytest.raises(measure.MeasureError, match="declared launch"):
        measure.assert_launch(cmd or command(profile), profile, install, env, prefs)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_a_wait_ends_when_the_value_has_not_moved_for_the_hold_time():
    clock = Clock()
    values = iter([1, 2, 3, 3, 3, 3, 3, 3, 3, 3])
    value, settled = measure.wait_stable(lambda: next(values), hold=1.0, timeout=20, poll=0.25, clock=clock, sleep=clock.sleep)
    assert (value, settled) == (3, True)
    assert clock.now == 1.5, "the value became 3 at 0.5 s and held for a full second"


def test_a_wait_does_not_end_on_a_value_that_is_not_ready():
    clock = Clock()
    value, settled = measure.wait_stable(lambda: 5, ready=lambda v: v == 9, hold=0.5, timeout=3, clock=clock, sleep=clock.sleep)
    assert (value, settled) == (5, False)


def test_a_wait_that_never_settles_says_so_instead_of_raising():
    clock = Clock()
    counter = iter(range(1000))
    _, settled = measure.wait_stable(lambda: next(counter), timeout=5, clock=clock, sleep=clock.sleep)
    assert settled is False and clock.now >= 5


def test_a_wait_with_no_hold_ends_at_the_first_ready_value():
    clock = Clock()
    seen = iter([False, False, True])
    assert measure.wait_stable(lambda: next(seen), ready=bool, hold=0, clock=clock, sleep=clock.sleep) == (True, True)


def test_the_mouse_points_are_inside_the_browser_rect_from_the_client_origin_at_the_window_scale():
    chrome = {"devicePixelRatio": 1.25, "browser": {"x": 0, "y": 80, "width": 800, "height": 400}}
    points = measure.mouse_points(chrome, {"client_origin": [-9, 0]})
    assert points == [(round(-9 + 200 * 1.25), round((80 + 0.3 * 400) * 1.25)), (round(-9 + 400 * 1.25), round(280 * 1.25)),
                      (round(-9 + 600 * 1.25), round((80 + 0.7 * 400) * 1.25))]
    for x, y in points:
        assert -9 < x < -9 + 800 * 1.25 and 80 * 1.25 < y < 480 * 1.25


def test_the_structures_handed_to_windows_have_the_sizes_it_expects():
    assert ctypes.sizeof(native.DEVMODEW) == 220
    assert ctypes.sizeof(native.WINDOWPLACEMENT) == 44
    assert ctypes.sizeof(native.MONITORINFO) == 40
    assert ctypes.sizeof(native.LOGFONTW) == 92
    assert ctypes.sizeof(native.NONCLIENTMETRICSW) == 504
    assert ctypes.sizeof(native.RTL_OSVERSIONINFOW) == 276
    assert ctypes.sizeof(native.RECT) == 16 and ctypes.sizeof(native.LUID) == 8
    assert ctypes.sizeof(display._ScaleGet) == 32 and ctypes.sizeof(display._ScaleSet) == 24
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(native.INPUT) == 40


def test_a_wchar_array_of_a_structure_is_16_bit_units_on_every_os_and_its_text_decodes_as_utf_16():
    assert ctypes.sizeof(native.WCHAR) == 2
    dm = native.DEVMODEW()
    assert ctypes.sizeof(dm.dmDeviceName) == 64 and ctypes.sizeof(dm.dmFormName) == 64
    name = "\\\\.\\DISPLAY1"
    units = [ord(c) for c in name] + [0, 0x41]
    for index, unit in enumerate(units):
        dm.dmDeviceName[index] = unit
    assert native.wide_text(dm.dmDeviceName) == name
    surrogate_pair = [0xD83D, 0xDE00, 0]
    assert native.wide_text(surrogate_pair) == "\U0001F600"


def test_the_scale_steps_are_relative_to_the_recommended_one():
    values = display.DPI_VALUES
    assert display.relative_step(values, 0, 125) == 1  # recommended 100%
    assert display.relative_step(values, -2, 100) == -2  # recommended 150%: two steps down
    assert display.relative_step(values, -2, 150) == 0
    assert display.relative_step(values, 0, 130) is None
    assert [display.percent(d) for d in (1, 1.25, 1.5, 2.0)] == [100, 125, 150, 200]


def test_every_launch_runs_at_the_first_resolution_and_the_first_alone_at_the_others():
    request = {"launches": ["a", "b"]}
    assert run_scale.launches_for(request, first=True) == ["a", "b"]
    assert run_scale.launches_for(request, first=False) == ["a"]
    assert run_scale.folder([2560, 1440]) == "2560x1440"


def test_the_marionette_exception_is_written_the_same_in_the_meta_and_in_the_workflow():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "retail-window-geometry.yml").read_text(encoding="utf-8")
    flat = " ".join(line.lstrip("# ").strip() for line in workflow.splitlines() if line.startswith("#"))
    assert " ".join(run_scale.MARIONETTE_EXCEPTION.split()) in " ".join(flat.split())


def test_the_one_variable_the_launch_sets_is_marionette_s_system_access():
    env = measure.launch_env({"PATH": "x", "MOZ_FOO": "1"})
    assert env == {"PATH": "x", "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}


def test_marionette_s_variable_with_another_value_is_refused(tmp_path):
    prefs = {"marionette.port": 2828}
    profile, install = make_profile(tmp_path, prefs), tmp_path / "install"
    measure.assert_launch(command(profile), profile, install, {"MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}, prefs)
    with pytest.raises(measure.MeasureError, match="declared launch"):
        measure.assert_launch(command(profile), profile, install, {"MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "0"}, prefs)


def test_the_launch_takes_the_maximized_step_always_and_the_others_only_when_asked():
    source = (Path(measure.__file__)).read_text(encoding="utf-8")
    for name in ("fullscreen_max_in", "iframes_max", "iframes_transformed"):
        assert f'if "{name}" in steps_wanted:' in source, name
    assert 'steps["maximized"] = session.maximized()' in source


PAGE_HTML = (Path(measure.__file__).parent / "page.js").read_text(encoding="utf-8")  # the script every page runs


def test_the_page_reads_in_itself_what_can_differ_under_rdm():
    probe = PAGE_HTML.split("window.__probe = ")[1]
    for needle in (
            # screen
            "s.width", "s.height", "s.availWidth", "s.availHeight", "s.availLeft", "s.availTop", "s.colorDepth", "s.pixelDepth",
            "s.orientation.type", "s.orientation.angle", "window.devicePixelRatio",
            # media queries: the integers by binary search over min- queries, the booleans as .matches
            "min-width: ${n}px", "min-height: ${n}px", "min-device-width: ${n}px", "min-device-height: ${n}px",
            "min-aspect-ratio: ${n}/10000", "min-device-aspect-ratio: ${n}/10000", "min-resolution: ${n / 100}dppx",
            "Math.ceil((lo + hi) / 2)", ".matches",
            "(pointer: fine)", "(pointer: coarse)", "(pointer: none)", "(any-pointer: fine)", "(any-pointer: coarse)",
            "(any-pointer: none)", "(hover: hover)", "(hover: none)", "(any-hover: hover)", "(any-hover: none)",
            "(orientation: portrait)", "(orientation: landscape)",
            # touch, visual viewport, the root's client size
            "navigator.maxTouchPoints", '"ontouchstart" in window', "typeof TouchEvent",
            "vv.width", "vv.height", "vv.scale", "vv.offsetLeft", "vv.offsetTop", "vv.pageLeft", "vv.pageTop",
            "document.documentElement.clientWidth", "document.documentElement.clientHeight"):
        assert needle in probe, needle
    main_page = (Path(measure.__file__).parent / "page.html").read_text(encoding="utf-8")
    assert "JSON.stringify" in probe and main_page.count('<script src="/page.js"></script>') == 1 and main_page.count("<script>") == 1, "the one inline script is the early one"


def test_the_content_record_of_every_step_calls_the_probe_in_the_page_itself_and_keeps_what_it_read_before():
    state = measure.CONTENT_STATE
    assert "probe: JSON.parse((window.wrappedJSObject || window).__probe())" in state
    for field in ("innerWidth", "outerWidth", "screenX", "mozInnerScreenX", "mozInnerScreenY", "events: {count: m.count, dx: m.dx, dy: m.dy}"):
        assert field in state, field


def test_raw_json_is_written_alone_in_its_folder(tmp_path):
    raw = {"steps": {}}
    path = measure.write_raw(raw, tmp_path / "a")
    assert json.loads(path.read_text(encoding="utf-8")) == {"steps": {}} and sorted(p.name for p in (tmp_path / "a").iterdir()) == ["raw.json"]
