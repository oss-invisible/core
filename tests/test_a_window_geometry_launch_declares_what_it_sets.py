"""The pure parts of the window-geometry capture: what the launch declares, how a wait ends, where the mouse goes.

No browser starts and no Windows call is made. Held here:
  * the launch is `firefox.exe -no-remote -profile <empty directory> -marionette <url>` and its profile holds one file,
    user.js, with exactly the declared prefs: anything else (a second file, another pref, a policy in the install folder, a
    MOZ_ variable) is refused before the spawn;
  * the Juggler line is the one of TargetRegistry.js, word for word, and so is Juggler's whole sequence (styles on the browser
    and its stack, then the resizeBy), without inRDMPane (the step juggler_rdm sets it); what clears it removes every style set;
  * a wait ends only when the value is ready and has not moved for the hold time, and says so when it never settles;
  * the mouse points are inside the browser rectangle, from the client origin, at the scale of the window;
  * the structures handed to Windows have the sizes Windows expects, and the scale steps are relative to the recommended one;
  * the run plan: every launch at the first resolution, the first alone at the others;
  * the launch takes only the steps asked for;
  * the page reads, in itself, everything that can differ under responsive-design mode (page.html __probe), and the content
    record of every step carries it;
  * the host-gate step: Juggler's sequence again, then the collection page loaded without and with inRDMPane, each wait for
    the POST bounded; a result is kept in its own file (not in raw.json) with its size and sha256; a missing POST is recorded.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import re
import threading
import urllib.request
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


def test_the_resize_is_juggler_s_line_word_for_word():
    line = ("const stackRect = gBrowser.selectedBrowser.closest('.browserStack').getBoundingClientRect(); "
            "const toolbarTop = stackRect.y; "
            "window.resizeBy(width - window.innerWidth, height + toolbarTop - window.innerHeight);")
    assert line in measure.JUGGLER_RESIZE
    assert measure.JUGGLER_RESIZE.startswith("const width = arguments[0], height = arguments[1];")
    assert (measure.RESIZE_WIDTH, measure.RESIZE_HEIGHT, measure.FALLBACK_HEIGHT) == (390, 844, 400)


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
    assert ctypes.sizeof(native.RECT) == 16 and ctypes.sizeof(native.LUID) == 8
    assert ctypes.sizeof(display._ScaleGet) == 32 and ctypes.sizeof(display._ScaleSet) == 24
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(native.INPUT) == 40


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


#: juggler/TargetRegistry.js (setViewportSize), lines 999-1018 of the series, minus the stealth guard, `inRDMPane = true` and
#: the wait for the content size. Kept here as text: the test never reads the engine's source.
JUGGLER_LINES = [
    "this._linkedBrowser.style.setProperty('width', width + 'px');",
    "this._linkedBrowser.style.setProperty('height', height + 'px');",
    "this._linkedBrowser.style.setProperty('box-sizing', 'content-box');",
    "this._linkedBrowser.closest('.browserStack').style.setProperty('overflow', 'auto');",
    "this._linkedBrowser.closest('.browserStack').style.setProperty('contain', 'size');",
    "this._linkedBrowser.closest('.browserStack').style.setProperty('scrollbar-width', 'none');",
    "const stackRect = this._linkedBrowser.closest('.browserStack').getBoundingClientRect();",
    "const toolbarTop = stackRect.y;",
    "this._window.resizeBy(width - this._window.innerWidth, height + toolbarTop - this._window.innerHeight);",
]


def test_juggler_s_whole_sequence_is_its_lines_in_order_on_the_selected_browser_as_the_tab():
    lines = measure.JUGGLER_SEQUENCE.split("\n")
    assert lines[0] == "const width = arguments[0], height = arguments[1];"
    start = lines.index("(function () {")
    assert lines[start + 1:start + 1 + len(JUGGLER_LINES)] == JUGGLER_LINES
    assert lines[start + 1 + len(JUGGLER_LINES):] == ["}).call({_linkedBrowser: gBrowser.selectedBrowser, _window: window});", "return true;"]


def test_juggler_s_sequence_does_not_set_inrdmpane_and_the_rdm_step_sets_it_only_on_the_selected_browser():
    assert "inRDMPane" not in measure.JUGGLER_SEQUENCE
    assert measure.RDM_ON == "gBrowser.selectedBrowser.browsingContext.inRDMPane = true; return true;"
    assert "inRDMPane = false" in measure.JUGGLER_CLEAR


def test_what_clears_juggler_s_sequence_removes_every_property_it_set_and_no_other():
    set_on_browser = re.findall(r"this\._linkedBrowser\.style\.setProperty\('([\w-]+)'", measure.JUGGLER_SEQUENCE)
    set_on_stack = re.findall(r"closest\('\.browserStack'\)\.style\.setProperty\('([\w-]+)'", measure.JUGGLER_SEQUENCE)
    assert set_on_browser == ["width", "height", "box-sizing"] and set_on_stack == ["overflow", "contain", "scrollbar-width"]
    removed_on_browser = re.search(r"for \(const name of \[([^\]]*)\]\) browser\.style\.removeProperty", measure.JUGGLER_CLEAR).group(1)
    removed_on_stack = re.search(r"for \(const name of \[([^\]]*)\]\) stack\.style\.removeProperty", measure.JUGGLER_CLEAR).group(1)
    assert re.findall(r"'([\w-]+)'", removed_on_browser) == set_on_browser
    assert re.findall(r"'([\w-]+)'", removed_on_stack) == set_on_stack


def test_the_old_restored_step_keeps_its_plain_resize_and_its_fallback():
    assert (measure.RESIZE_WIDTH, measure.RESIZE_HEIGHT, measure.FALLBACK_HEIGHT, measure.FRAME_ALLOWANCE_CSS) == (390, 844, 400, 200)
    assert "setProperty" not in measure.JUGGLER_RESIZE


def test_the_grid_resize_asks_for_the_chrome_inner_size_by_a_difference_and_the_row_reads_the_stack():
    assert "window.resizeBy(w - window.innerWidth, h - window.innerHeight)" in measure.RESIZE_CHROME_INNER
    assert "getBoundingClientRect()" in measure.CHROME_ROW and "stack: {x: s.x, y: s.y, width: s.width, height: s.height}" in measure.CHROME_ROW
    assert (measure.GRID_TIMEOUT, measure.HOLD_SECONDS) == (10.0, 1.0)


def test_the_launch_takes_the_maximized_step_always_and_the_others_only_when_asked():
    source = (Path(measure.__file__)).read_text(encoding="utf-8")
    for name in ("restored", "juggler", "grid", "minimum", "grid_check"):
        assert f'if "{name}" in steps_wanted:' in source, name
    assert 'steps["maximized"] = session.maximized()' in source


PAGE_HTML = (Path(measure.__file__).parent / "page.html").read_text(encoding="utf-8")


def test_the_page_reads_in_itself_what_can_differ_under_rdm():
    probe = PAGE_HTML.split("window.__probe = ")[1]
    for needle in (
            # screen
            "s.width", "s.height", "s.availWidth", "s.availHeight", "s.availLeft", "s.availTop", "s.colorDepth",
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
    assert "JSON.stringify" in probe and PAGE_HTML.count("<script>") == 1 == PAGE_HTML.count("</script>")


def test_the_content_record_of_every_step_calls_the_probe_in_the_page_itself_and_keeps_what_it_read_before():
    state = measure.CONTENT_STATE
    assert "probe: JSON.parse((window.wrappedJSObject || window).__probe())" in state
    for field in ("innerWidth", "outerWidth", "screenX", "mozInnerScreenX", "mozInnerScreenY", "events: {count: m.count, dx: m.dx, dy: m.dy}"):
        assert field in state, field


def test_the_collection_page_is_the_cores_own_and_posts_to_the_result_path_of_the_capture_server():
    assert measure.HOSTGATE_PAGE.is_file() and measure.HOSTGATE_PAGE.parts[-3:] == ("captures", "pages", "host-gate-2.html")
    assert "fetch('/result' + location.search" in measure.HOSTGATE_PAGE.read_text(encoding="utf-8")
    assert measure.HOSTGATE_TIMEOUT == 120.0


class PostingSession(measure.Session):
    """A session with no browser: the chrome and content calls are recorded, and `load` plays the page POSTing its result."""

    def __init__(self, posts):
        super().__init__(None, Path("."), None)
        self.posts, self.calls = posts, []

    def chrome(self, script, args=None):
        self.calls.append(("chrome", script, args))
        return False if script == measure.RDM_STATE else True

    def content(self, script, args=None):
        self.calls.append(("content", script, args))
        return [390, 844]

    def load(self, url):
        self.calls.append(("load", url, None))
        base, label = url.split("/?")
        if self.posts.get(label):
            request = urllib.request.Request(f"{base}/result?{label}", data=self.posts[label], method="POST")
            threading.Thread(target=lambda: urllib.request.urlopen(request, timeout=10).read(), daemon=True).start()


@pytest.fixture
def instant(monkeypatch):
    monkeypatch.setattr(measure, "wait_stable", lambda read, *a, **k: (read(), True))


def test_the_host_gate_step_reapplies_juggler_s_sequence_then_loads_the_page_without_and_with_rdm(instant):
    posts = {"rdmfalse": b'{"result": "plain"}', "rdmtrue": b'{"result": "under rdm"}'}
    session = PostingSession(posts)
    step, files = session.rdm_hostgate(844, timeout=10)
    order = [(kind, script if kind != "load" else script.split("/?")[1]) for kind, script, _ in session.calls
             if script not in (measure.RDM_STATE, "return [window.innerWidth, window.innerHeight];")]
    assert order == [("chrome", measure.JUGGLER_SEQUENCE), ("chrome", measure.RDM_OFF), ("load", "rdmfalse"),
                     ("chrome", measure.RDM_ON), ("load", "rdmtrue")]
    assert session.calls[0][2] == [390, 844]
    assert files == {"hostgate-rdm-false.json": posts["rdmfalse"], "hostgate-rdm-true.json": posts["rdmtrue"]}
    for key, label in (("rdm_false", "rdmfalse"), ("rdm_true", "rdmtrue")):
        entry = step[key]
        assert entry["posted"] is True and entry["bytes"] == len(posts[label]) and entry["sha256"] == hashlib.sha256(posts[label]).hexdigest()
        assert 0 <= entry["seconds"] < 10 and entry["inRDMPane"] is False
    assert step["juggler_height"] == 844 and step["timeout_seconds"] == 10
    assert b"result" not in json.dumps(step).encode("utf-8"), "the results are in files, not in the step"


def test_a_page_that_never_posts_under_rdm_is_recorded_as_not_posted_after_the_timeout_and_keeps_no_file(instant):
    session = PostingSession({"rdmfalse": b"{}", "rdmtrue": None})
    step, files = session.rdm_hostgate(560, timeout=0.3)
    assert step["rdm_false"]["posted"] is True and step["rdm_true"] == dict(step["rdm_true"], posted=False, bytes=0, sha256=None)
    assert 0.3 <= step["rdm_true"]["seconds"] < 5
    assert list(files) == ["hostgate-rdm-false.json"]
    assert session.calls[0][2] == [390, 560], "the juggler height used"


def test_the_host_gate_step_is_skipped_when_juggler_s_size_did_not_fit():
    session = PostingSession({})
    assert session.rdm_hostgate(None) == ({"skipped": "does_not_fit"}, {})
    assert session.calls == []


def test_the_page_is_loaded_from_the_chrome_without_waiting_and_from_the_page_when_that_fails():
    calls = []

    class Loader(measure.Session):
        def __init__(self, fail):
            super().__init__(None, Path("."), None)
            self.fail = fail

        def chrome(self, script, args=None):
            calls.append(("chrome", script))
            if self.fail:
                raise RuntimeError("no Services")

        def content(self, script, args=None):
            calls.append(("content", script))

    Loader(False).load("http://127.0.0.1:1/?x")
    Loader(True).load("http://127.0.0.1:1/?x")
    assert calls == [("chrome", measure.LOAD_IN_TAB), ("chrome", measure.LOAD_IN_TAB), ("content", measure.LOAD_IN_CONTENT)]
    assert "fixupAndLoadURIString" in measure.LOAD_IN_TAB and "getSystemPrincipal" in measure.LOAD_IN_TAB
    assert "location.assign" in measure.LOAD_IN_CONTENT


def test_the_files_next_to_raw_json_are_written_and_not_part_of_raw_json(tmp_path):
    raw = {"steps": {}, "_attachments": {"hostgate-rdm-false.json": b"A", "hostgate-rdm-true.json": b"B"}}
    path = measure.write_raw(raw, tmp_path / "a")
    assert (tmp_path / "a" / "hostgate-rdm-false.json").read_bytes() == b"A" and (tmp_path / "a" / "hostgate-rdm-true.json").read_bytes() == b"B"
    assert "_attachments" not in json.loads(path.read_text(encoding="utf-8")) and "_attachments" in raw, "the caller's record is not changed"
