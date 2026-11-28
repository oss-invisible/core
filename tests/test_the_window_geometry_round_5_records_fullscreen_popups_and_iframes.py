"""Round 5 of the window-geometry capture: fullscreen, popups and iframes of the maximized window, with no browser and no Windows.

scripts/retail/window_geometry/surfaces.py (the scripts the browser runs and the points the real mouse goes to) and the Session
methods of measure.py that drive them, against a small model of the browser (`Browser`: what the chrome and the page answer,
what a real click on a button does). Held here:
  * element fullscreen is entered by a REAL click on the middle of the button #fs, recorded, left with exitFullscreen and recorded
    again; a click that does not enter fullscreen is `not_possible` with the page's own reason in both steps; a window that stays
    fullscreen after exitFullscreen is forced out and says so;
  * F11-style fullscreen is `window.fullScreen = true` then false in the chrome, each recorded; one that never turns on is
    `not_possible`;
  * a popup is opened by a REAL click on #open with the features asked, its window rects and its page's record are kept, the mouse
    moves inside its client area, and it is closed; none opening is `not_possible`; the two popups are the two features;
  * the iframes (same-origin and cross-site, both local) are added, each gets real mouse moves inside its rectangle, each answers
    with its own record, and they are removed even when one did not load;
  * a step Marionette refused is `not_possible` and puts the window back; a closed connection is not that and ends the launch;
  * the order of a launch, the pages the server serves, the page script's hooks, the real click's two button events.
"""
from __future__ import annotations

import json
import math
import socket
import urllib.request
from pathlib import Path

import pytest

from _retail_scripts import load_window_geometry

pytestmark = pytest.mark.unit

measure = load_window_geometry("measure")
surfaces = load_window_geometry("surfaces")
plan = load_window_geometry("plan")
marionette = load_window_geometry("marionette")
HERE = Path(measure.__file__).parent
URLS = {"popup": "http://127.0.0.1:5001/surface.html?name=popup", "same": "http://127.0.0.1:5001/surface.html?name=same",
        "cross": "http://localhost:5002/surface.html?name=cross",
        **{f"same_{k}": f"http://127.0.0.1:5001/surface.html?name=same_{k}" for k in ("scale", "rotate")},
        **{f"cross_{k}": f"http://localhost:5002/surface.html?name=cross_{k}" for k in ("scale", "rotate")}}
TRANSFORM_MATRICES = {"scale(1.5)": [1.5, 0.0, 0.0, 1.5, 0.0, 0.0],
                      "rotate(10deg)": [math.cos(math.radians(10)), math.sin(math.radians(10)),
                                        -math.sin(math.radians(10)), math.cos(math.radians(10)), 0.0, 0.0]}
BUTTONS = {"fs": {"x": 8, "y": 8, "width": 120, "height": 40}, "open": {"x": 140, "y": 8, "width": 120, "height": 40}}
FRAME_RECTS = {"same": {"x": 10, "y": 60, "width": 300, "height": 120}, "cross": {"x": 10, "y": 200, "width": 300, "height": 120}}
CHROME = {"devicePixelRatio": 1.0, "browser": {"x": 0, "y": 85, "width": 1900, "height": 900}, "windowState": 1}
NATIVE = {"client_origin": [0, 0], "client_rect": [0, 0, 1900, 985], "window_rect": [-8, -8, 1928, 1040]}
POPUP_NATIVE = {"client_origin": [108, 152], "client_rect": [0, 0, 500, 400], "window_rect": [100, 80, 616, 620]}
EARLY_MOMENTS = ("parse", "dcl", "raf1", "raf2", "load")
PAGE_EARLY = {m: {"mozInnerScreenX": 8.0, "mozInnerScreenY": 1.0 + i, "now": 5.0 + i} for i, m in enumerate(EARLY_MOMENTS)}
POPUP_EARLY = {m: {"mozInnerScreenX": 108.0, "mozInnerScreenY": 152.0, "now": 9.0 + i} for i, m in enumerate(EARLY_MOMENTS)}


def state(events=3):
    return {"innerWidth": 300, "innerHeight": 120, "events": {"count": events, "dx": 7, "dy": 91}, "probe": {"screen": {"pixelDepth": 24}}}


class FakeClient:
    """The Marionette client of a Browser: scripts go to its chrome and content."""

    def __init__(self, browser):
        self.browser = browser

    def execute_script(self, script, args=None, context=None):
        return (self.browser.chrome if context == "chrome" else self.browser.content)(script, args)

    def execute_async_script(self, script, args=None, context=None):
        return True


class Browser(measure.Session):
    """A Session with no browser and no Windows: the chrome and the page answer from a model of what fullscreen, a popup and
    an iframe do; every call that changes something is logged in `log`, the real clicks in `clicks`, the real moves in `moves`."""

    def __init__(self, enters=True, window_fullscreen=True, exit_leaves=True, opens=True, frames_load=True, fail_on=None):
        super().__init__(FakeClient(self), Path("."), None, dict(URLS))
        self.enters, self.window_ok, self.exit_leaves, self.opens, self.frames_load = enters, window_fullscreen, exit_leaves, opens, frames_load
        self.fail_on = fail_on  # (script, exception) raised when that script runs
        self.fs = self.window_fs = self.popup_open = self.frames_on = False
        self.fs_error, self.log, self.clicks, self.moves, self.popup_args, self.frames_args = None, [], [], [], None, None
        self.transformed = None  # (name, url, transform) of the container that is in the page now

    # ---- what the harness reads from Windows and the mouse
    def hwnd(self):
        return 1

    def native(self, hwnd):
        return dict(POPUP_NATIVE if hwnd == 2 else NATIVE)

    def foreground(self, hwnd):
        self.log.append(("foreground", hwnd))

    def windows(self):
        return [1, 2] if self.popup_open else [1]

    def move(self, points):
        self.moves.append(list(points))
        return [{"asked": list(p), "cursor": list(p)} for p in points]

    def click(self, point):
        self.clicks.append(tuple(point))
        for name, rect in BUTTONS.items():
            if tuple(point) == tuple(surfaces.region_points(CHROME, NATIVE, rect, surfaces.CLICK_FRACTION)[0]):
                if name == "fs":
                    if self.enters:
                        self.fs = True
                    else:
                        self.fs_error = "TypeError: Fullscreen request denied"
                if name == "open" and self.opens:
                    self.popup_open = True
        return {"asked": list(point), "cursor": list(point), "buttons_sent": [1, 1]}

    def record(self):
        self.log.append(("record", self.fs, self.window_fs, self.popup_open))
        return {"chrome": dict(CHROME), "native": dict(NATIVE), "input": [], "content": {}}

    # ---- the chrome and the page
    def chrome(self, script, args=None):
        if self.fail_on and script == self.fail_on[0]:
            raise self.fail_on[1]
        if script == measure.CHROME_STATE:
            return dict(CHROME)
        if script == surfaces.FULLSCREEN_CHROME:
            on = self.fs or self.window_fs
            return [on, 4 if on else 1, 1920, 1080, 1936, 1049]
        if script == surfaces.WINDOW_FULLSCREEN_ON:
            self.log.append("window_fullscreen_on")
            self.window_fs = self.window_ok
            return True
        if script == surfaces.WINDOW_FULLSCREEN_OFF:
            self.log.append("window_fullscreen_off")
            self.window_fs = self.fs = False
            return True
        raise AssertionError(f"unexpected chrome script {script[:50]!r}")

    def content(self, script, args=None):
        if self.fail_on and script == self.fail_on[0]:
            raise self.fail_on[1]
        table = {surfaces.FULLSCREEN_CONTENT: lambda: [self.fs, 1920, 1080], surfaces.FULLSCREEN_ERROR: lambda: self.fs_error,
                 surfaces.POPUP_READY: lambda: self.popup_open, surfaces.POPUP_SIZE: lambda: [500, 400, 516, 540, 100, 80],
                 surfaces.POPUP_STATE: lambda: json.dumps(dict(state(), innerWidth=500)),
                 surfaces.POPUP_EARLY: lambda: json.dumps(POPUP_EARLY), surfaces.EARLY: lambda: json.dumps(PAGE_EARLY),
                 surfaces.FRAMES_HELLO: lambda: ["cross", "same"] if self.frames_load else ["same"],
                 surfaces.FRAME_RECTS: lambda: FRAME_RECTS,
                 surfaces.FRAME_RECORDS: lambda: json.dumps({"same": state(), "cross": state()}),
                 measure.CONTENT_STATE: lambda: {"events": {"count": 0, "dx": None, "dy": None}, "probe": {}}}
        if script in table:
            return table[script]()
        if script == surfaces.ELEMENT_RECT:
            return BUTTONS[args[0]]
        if script == surfaces.EXIT_FULLSCREEN:
            self.log.append("exit_fullscreen")
            if self.exit_leaves:
                self.fs = False
            return True
        if script == surfaces.SET_POPUP:
            self.popup_args = args
            return True
        if script == surfaces.POPUP_RESET:
            self.log.append("popup_reset")
            return True
        if script == surfaces.POPUP_CLOSE:
            self.log.append("popup_close")
            self.popup_open = False
            return True
        if script == surfaces.ADD_FRAMES:
            self.frames_args, self.frames_on = args, True
            return True
        if script == surfaces.ASK_FRAMES:
            self.log.append(("ask_frames", args[0]))
            return True
        if script == surfaces.REMOVE_FRAMES:
            self.log.append("remove_frames")
            self.frames_on = False
            return True
        if script == surfaces.ADD_TRANSFORMED:
            self.transformed = tuple(args)
            self.log.append(("add_transformed", args[0], args[2]))
            return True
        if script == surfaces.FRAME_HELLO_OF:
            return self.frames_load and self.transformed is not None and self.transformed[0] == args[0]
        if script == surfaces.TRANSFORMED_GEOMETRY:
            return self.transformed_geometry()
        if script == surfaces.ASK_TRANSFORMED:
            self.log.append(("ask_transformed", args[0], args[1]))
            return True
        if script == surfaces.FRAME_RECORD_OF:
            return json.dumps(self.transformed_record())
        if script == surfaces.REMOVE_TRANSFORMED:
            self.log.append("remove_transformed")
            self.transformed = None
            return True
        raise AssertionError(f"unexpected content script {script[:50]!r}")

    def transformed_geometry(self):
        a, b, c, d, e, f = TRANSFORM_MATRICES[self.transformed[2]]
        corners = [(10 + a * u + c * v, 200 + b * u + d * v) for u, v in ((0, 0), (300, 0), (0, 120), (300, 120))]
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        return {"rect": {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}, "left": 10, "top": 200,
                "width": 300, "height": 120, "matrix": [a, b, c, d, e, f], "viewport": [1900, 900]}

    def transformed_record(self):
        """What the frame saw: the last real moves, each through the inverse of the container's matrix (the frame's own client point)."""
        a, b, c, d, _, _ = TRANSFORM_MATRICES[self.transformed[2]]
        det = a * d - b * c
        moves = []
        for x, y in self.moves[-1]:
            px, py = x - 10, y - 85 - 200
            u, v = (d * px - c * py) / det, (-b * px + a * py) / det
            moves.append({"clientX": u, "clientY": v, "screenX": x, "screenY": y, "dx": x - u, "dy": y - v})
        return dict(state(3), moves=moves, mozInnerScreenX=10.0, mozInnerScreenY=285.0)


@pytest.fixture(autouse=True)
def faithful_and_instant(monkeypatch):
    """A wait that returns the value and whether it was ready (no hold), and no sleeping."""
    def stub(read, ready=lambda v: True, **kwargs):
        value = read()
        return value, bool(ready(value))

    monkeypatch.setattr(measure, "wait_stable", stub)
    monkeypatch.setattr(measure.time, "sleep", lambda seconds: None)


def button_centre(name):
    return surfaces.region_points(CHROME, NATIVE, BUTTONS[name], surfaces.CLICK_FRACTION)[0]


# ---------------------------------------------------------------------------- the points and the pages (pure)


def test_a_region_s_points_are_its_fractions_from_the_client_origin_and_the_browser_at_the_window_scale():
    chrome = {"devicePixelRatio": 1.25, "browser": {"x": 0, "y": 80, "width": 800, "height": 400}}
    native = {"client_origin": [-9, 0]}
    region = {"x": 10, "y": 60, "width": 300, "height": 120}
    assert surfaces.region_points(chrome, native, region, ((0.5, 0.5), (0.25, 0.3))) == [
        (round(-9 + (0 + 10 + 150) * 1.25), round((80 + 60 + 60) * 1.25)), (round(-9 + (10 + 75) * 1.25), round((80 + 60 + 36) * 1.25))]
    assert button_centre("fs") == (68, 113) and button_centre("open") == (200, 113)


def test_the_popup_points_are_inside_the_client_area_below_the_popup_s_own_bar():
    native = {"client_origin": [108, 152], "client_rect": [0, 0, 500, 400]}
    points = surfaces.client_points(native)
    assert points == [(108 + 125, 152 + 220), (108 + 250, 152 + 280), (108 + 375, 152 + 340)]
    for x, y in points:
        assert 108 < x < 608 and 152 + 0.5 * 400 <= y < 552
    assert surfaces.POPUP_FRACTIONS == ((0.25, 0.55), (0.5, 0.7), (0.75, 0.85))


def test_the_two_iframes_are_two_origins_and_two_sites_and_the_popup_is_the_page_s_own_origin():
    urls = surfaces.frame_urls(5001, 5002)
    assert urls == {"same": "http://127.0.0.1:5001/surface.html?name=same", "cross": "http://localhost:5002/surface.html?name=cross"}
    assert surfaces.popup_url(5001) == "http://127.0.0.1:5001/surface.html?name=popup"
    assert surfaces.POPUP_FEATURES == (("popup", "width=500,height=400"), ("popup_pos", "left=100,top=80,width=500,height=400"))
    assert surfaces.FRAME_NAMES == ("same", "cross") and set(surfaces.FRAME_KINDS) == {"same", "cross"}


def test_the_server_serves_the_main_page_its_script_and_the_surface_page_and_nothing_else():
    server = measure.PageServer(also_ipv6=True)
    try:
        for path, needle in (("/", b'id="fs"'), ("/page.js", b"window.__probe"), ("/surface.html", b"/page.js")):
            with urllib.request.urlopen(f"http://127.0.0.1:{server.port}{path}?name=x", timeout=10) as response:
                assert response.status == 200 and needle in response.read()
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"http://127.0.0.1:{server.port}/secret", timeout=10)
        assert err.value.code == 404
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/page.js", timeout=10) as response:
            assert response.headers["Content-Type"].startswith("text/javascript")
        assert len(server.httpds) in (1, 2)
        if len(server.httpds) == 2:  # `localhost` can be reached on ::1 too
            with socket.create_connection(("::1", server.port), timeout=5):
                pass
    finally:
        server.close()


def test_two_servers_are_two_ports_so_the_cross_site_frame_is_another_origin():
    first, second = measure.PageServer(), measure.PageServer()
    try:
        assert first.port != second.port
    finally:
        first.close()
        second.close()


PAGE_JS = (HERE / "page.js").read_text(encoding="utf-8")
PAGE_HTML = (HERE / "page.html").read_text(encoding="utf-8")
SURFACE_HTML = (HERE / "surface.html").read_text(encoding="utf-8")


def test_the_pages_load_the_one_script_and_the_main_page_has_the_two_buttons_the_real_clicks_land_on():
    assert PAGE_HTML.count('<script src="/page.js"></script>') == 1 and PAGE_HTML.count("<script>") == 1, "the one inline script is the early one"
    assert SURFACE_HTML.count('<script src="/page.js"></script>') == 1
    assert 'id="fs"' in PAGE_HTML and 'id="open"' in PAGE_HTML and "position:fixed" in PAGE_HTML
    assert "requestFullscreen()" in PAGE_JS and "window.open(window.__popupUrl, window.__popupName || \"_blank\", window.__popupFeatures)" in PAGE_JS
    assert PAGE_JS.count("pixelDepth: s.pixelDepth") == 1 and "pixelDepth: screen.pixelDepth" in PAGE_JS


def test_the_page_script_keeps_the_frame_protocol_the_capture_relies_on():
    for needle in ("window.__addFrames = ", "window.__removeFrames = ", "window.__askFrames = ", "window.__frameHello", "window.__frameRecords",
                   '"geometry-parent"', '"geometry-frame"', 'data.cmd === "reset"', 'data.cmd === "record"', "window.parent !== window",
                   "window.__state = ", "window.__fsError"):
        assert needle in PAGE_JS, needle
    for script, name in ((surfaces.ADD_FRAMES, "__addFrames"), (surfaces.ASK_FRAMES, "__askFrames"), (surfaces.REMOVE_FRAMES, "__removeFrames"),
                         (surfaces.POPUP_STATE, "__state"), (surfaces.POPUP_RESET, "__reset")):
        assert name in script, name
    assert "frame-same" in surfaces.FRAME_RECTS and "frame-cross" in surfaces.FRAME_RECTS
    assert "frame.id = \"frame-\" + name" in PAGE_JS


def test_the_state_a_popup_and_a_frame_report_has_the_fields_of_the_main_page_record_and_the_probe():
    state_js = PAGE_JS.split("window.__state = ")[1].split("});")[0]
    for field in ("innerWidth", "innerHeight", "outerWidth", "outerHeight", "screenX", "screenY", "screenLeft", "screenTop",
                  "mozInnerScreenX", "mozInnerScreenY", "devicePixelRatio", "events: {count", "probe: JSON.parse(window.__probe())"):
        assert field in state_js, field
    main = measure.CONTENT_STATE
    for field in ("innerWidth", "mozInnerScreenY", "devicePixelRatio", "events: {count: m.count, dx: m.dx, dy: m.dy}"):
        assert field in main, field


# ---------------------------------------------------------------------------- the real click


def test_a_real_click_is_a_move_then_the_left_button_going_down_and_up(monkeypatch):
    sent = []

    class User32:
        def SendInput(self, count, pointer, size):  # noqa: N802
            sent.append(pointer._obj.mi.dwFlags if hasattr(pointer, "_obj") else None)
            return count

    class Api:
        user32 = User32()

    monkeypatch.setattr(measure, "api", lambda: Api())
    monkeypatch.setattr(measure, "move_mouse", lambda points, pause=0.2: [{"asked": list(points[0]), "sent": 1, "cursor": list(points[0])}])
    result = measure.click((10, 20))
    assert sent == [measure.MOUSEEVENTF_LEFTDOWN, measure.MOUSEEVENTF_LEFTUP] == [0x2, 0x4]
    assert result == {"asked": [10, 20], "sent": 1, "cursor": [10, 20], "buttons_sent": [1, 1]}


# ---------------------------------------------------------------------------- element fullscreen


def test_element_fullscreen_is_a_real_click_on_the_button_a_record_exitfullscreen_and_a_record():
    browser = Browser()
    steps = browser.fullscreen()
    assert list(steps) == ["fullscreen_max_in", "fullscreen_max_out", "f11_max_in", "f11_max_out"]
    assert browser.clicks == [button_centre("fs")] and browser.fs is False
    entered, left = steps["fullscreen_max_in"], steps["fullscreen_max_out"]
    assert entered["fullscreen"] == {"window_fullScreen": True, "windowState": 4, "document_fullscreen": True} and entered["settled"] is True
    assert left["fullscreen"] == {"window_fullScreen": False, "windowState": 1, "document_fullscreen": False} and left["forced_exit"] is False
    assert entered["click"]["asked"] == list(button_centre("fs")) and entered["chrome"]["browser"]["y"] == 85
    element_log = [item for item in browser.log if item != ("foreground", 1)][:3]
    assert element_log == [("record", True, False, False), "exit_fullscreen", ("record", False, False, False)]


def test_a_click_that_does_not_enter_fullscreen_is_not_possible_in_both_steps_with_the_page_s_own_reason():
    browser = Browser(enters=False)
    steps = browser.fullscreen()
    reason = ("document.fullscreenElement stayed null after a real click on the button "
              "(requestFullscreen rejected: TypeError: Fullscreen request denied)")
    assert steps["fullscreen_max_in"] == {"not_possible": reason} and steps["fullscreen_max_out"] == {"not_possible": reason}
    assert "exit_fullscreen" in browser.log and "remove_frames" in browser.log, "the window is put back"
    assert set(steps["f11_max_in"]) >= {"chrome", "native", "fullscreen"}, "the F11 steps are still taken"


def test_a_window_still_fullscreen_after_exitfullscreen_is_forced_out_and_says_so():
    browser = Browser(exit_leaves=False)
    steps = browser.fullscreen()
    assert steps["fullscreen_max_out"]["forced_exit"] is True and steps["fullscreen_max_out"]["settled"] is True
    assert browser.log.count("window_fullscreen_off") == 2, "once to force the element out, once at the end of F11"


def test_f11_style_fullscreen_is_the_chrome_s_window_fullscreen_true_then_false_each_recorded():
    browser = Browser()
    entered, left = browser.window_fullscreen()
    assert [item for item in browser.log if item != ("foreground", 1)] == [
        "window_fullscreen_on", ("record", False, True, False), "window_fullscreen_off", ("record", False, False, False)]
    assert entered["fullscreen"]["window_fullScreen"] is True and left["fullscreen"]["window_fullScreen"] is False


def test_a_window_that_never_turns_fullscreen_on_is_not_possible_in_both_f11_steps():
    steps = Browser(window_fullscreen=False).fullscreen()
    assert steps["f11_max_in"] == {"not_possible": "window.fullScreen stayed false after setting it"} == steps["f11_max_out"]
    assert "chrome" in steps["fullscreen_max_in"]


# ---------------------------------------------------------------------------- popups


@pytest.mark.parametrize("features", ["width=500,height=400", "left=100,top=80,width=500,height=400"])
def test_a_popup_is_opened_by_a_real_click_with_the_features_asked_recorded_with_the_mouse_over_it_and_closed(features):
    browser = Browser()
    step = browser.popup(features)
    assert browser.clicks == [button_centre("open")] and browser.popup_args == [URLS["popup"], features]
    assert step["features"] == features and step["windows_opened"] == 1 and step["closed"] is True and step["settled"] is True
    assert step["native"] == POPUP_NATIVE and step["content"]["innerWidth"] == 500 and step["content"]["probe"]["screen"]["pixelDepth"] == 24
    assert step["content"]["early"] == POPUP_EARLY, "what the popup's page read in its first moments is in its record"
    assert [m["asked"] for m in step["input"]] == [list(p) for p in surfaces.client_points(POPUP_NATIVE)]
    assert browser.moves == [surfaces.client_points(POPUP_NATIVE)] and browser.popup_open is False
    assert ("foreground", 2) in browser.log and browser.log.index("popup_reset") < browser.log.index("popup_close")


def test_the_two_popups_of_a_launch_are_the_two_features_in_their_own_steps():
    browser = Browser()
    steps = {}
    for name, features in surfaces.POPUP_FEATURES:
        steps.update(browser.surface((name,), lambda features=features: (browser.popup(features),)))
    assert list(steps) == ["popup", "popup_pos"]
    assert steps["popup"]["features"] == "width=500,height=400" and "left=100,top=80" in steps["popup_pos"]["features"]


def test_a_click_that_opens_no_popup_is_not_possible_with_that_reason_and_the_window_is_put_back():
    browser = Browser(opens=False)
    steps = browser.surface(("popup",), lambda: (browser.popup("width=500,height=400"),))
    assert steps == {"popup": {"not_possible": "no popup window opened after a real click on the button"}}
    assert "popup_close" in browser.log


def test_a_popup_without_its_page_is_not_possible():
    browser = Browser()
    browser.urls.pop("popup")
    assert browser.surface(("popup",), lambda: (browser.popup("width=500,height=400"),)) == {"popup": {"not_possible": "no popup page was served"}}


class TwoPopups(Browser):
    """popup_second's model: each REAL click on #open opens a window named by the last SET_POPUP (the next handle; the second one
    sits 26 device px right and down of the first, as Windows staggers it); KEEP_POPUP keeps the current one; each window closes
    on its own. `live_at_click` is what was open at each click, `set_calls` what each SET_POPUP was given."""

    def __init__(self, second_opens=True, **kw):
        super().__init__(**kw)
        self.second_opens, self.live, self.current, self.kept, self.next_handle = second_opens, [], None, None, 2
        self.live_at_click, self.set_calls = [], []

    def offset(self, handle):
        return 26 * [h for h, _ in self.live].index(handle)

    def windows(self):
        return [1] + [h for h, _ in self.live]

    def native(self, hwnd):
        if hwnd == 1:
            return dict(NATIVE)
        k = self.offset(hwnd)
        return {"client_origin": [108 + k, 152 + k], "client_rect": [0, 0, 500, 400], "window_rect": [100 + k, 80 + k, 616 + k, 620 + k]}

    def click(self, point):
        self.clicks.append(tuple(point))
        if tuple(point) == button_centre("open") and self.opens and (self.second_opens or not self.live):
            self.live_at_click.append([name for _, name in self.live])
            name = self.set_calls[-1][2] if self.set_calls else "_blank"
            self.live.append((self.next_handle, name))
            self.current, self.next_handle = self.next_handle, self.next_handle + 1
        return {"asked": list(point), "cursor": list(point), "buttons_sent": [1, 1]}

    def close(self, handle):
        self.live = [(h, n) for h, n in self.live if h != handle]
        self.log.append(("close", handle))

    def content(self, script, args=None):
        if script == surfaces.SET_POPUP:
            self.set_calls.append(list(args))
        if script == surfaces.POPUP_READY:
            return any(h == self.current for h, _ in self.live)
        if script == surfaces.POPUP_SIZE:
            k = self.offset(self.current)
            return [500, 400, 516, 540, 100 + k, 80 + k]
        if script == surfaces.KEEP_POPUP:
            self.kept = self.current
            return True
        if script == surfaces.POPUP_CLOSE:
            self.close(self.current)
            return True
        if script == surfaces.KEPT_CLOSE:
            self.close(self.kept)
            return True
        return super().content(script, args)


def test_popup_second_opens_p1_then_p2_by_two_real_clicks_with_no_left_or_top_keeps_p1_open_and_closes_both():
    browser = TwoPopups()
    step = browser.surface(("popup_second",), lambda: (browser.popup_second(),))["popup_second"]
    assert browser.clicks == [button_centre("open"), button_centre("open")], "two REAL clicks on #open"
    assert browser.set_calls == [[URLS["popup"], "width=500,height=400", "p1"], [URLS["popup"], "width=500,height=400", "p2"]]
    assert browser.live_at_click == [[], ["p1"]], "p1 is open when p2 is asked for"
    assert browser.live == [] and ("close", 3) in browser.log and ("close", 2) in browser.log
    assert browser.log.index(("close", 3)) < browser.log.index(("close", 2)), "p2 (the current popup) first, then the kept p1"
    assert step["features"] == "width=500,height=400" and "left" not in step["features"] and "top" not in step["features"]
    assert step["name"] == "p2" and step["windows_opened"] == 1 and step["closed"] is True and step["settled"] is True


def test_the_second_popup_records_what_popup_records_and_the_first_its_screen_position_and_window_rect():
    browser = TwoPopups()
    step = browser.popup_second()
    assert step["first"]["name"] == "p1" and step["first"]["windows_opened"] == 1
    assert (step["first"]["screenX"], step["first"]["screenY"]) == (100, 80)
    assert step["first"]["native"]["window_rect"] == [100, 80, 616, 620], "p1's own window rect, read before p2 opened"
    assert step["native"]["window_rect"] == [126, 106, 642, 646], "p2 at the offset the stagger gives (26, 26) in this model"
    assert step["native"]["client_origin"] == [134, 178]
    assert step["content"]["innerWidth"] == 500 and step["content"]["probe"]["screen"]["pixelDepth"] == 24
    assert step["content"]["early"] == POPUP_EARLY
    assert [m["asked"] for m in step["input"]] == [list(p) for p in surfaces.client_points(step["native"])]
    assert ("foreground", 3) in browser.log and browser.log.index("popup_reset") < browser.log.index(("close", 3))


def test_a_first_popup_that_does_not_open_is_not_possible_with_that_reason_and_nothing_is_left_open():
    browser = TwoPopups(opens=False)
    steps = browser.surface(("popup_second",), lambda: (browser.popup_second(),))
    assert steps == {"popup_second": {"not_possible": "no first popup window opened after a real click on the button"}}
    assert browser.live == [] and browser.clicks == [button_centre("open")]


def test_a_second_popup_that_does_not_open_is_not_possible_and_the_first_is_closed_too():
    browser = TwoPopups(second_opens=False)
    steps = browser.surface(("popup_second",), lambda: (browser.popup_second(),))
    assert steps == {"popup_second": {"not_possible": "no second popup window opened after a real click on the button"}}
    assert browser.live == [] and ("close", 2) in browser.log, "the cleanup closes the first popup too"


# ---------------------------------------------------------------------------- iframes


def test_the_iframes_get_real_moves_inside_their_rectangles_and_each_answers_with_its_own_record_and_they_are_removed():
    browser = Browser()
    step = browser.iframes()
    assert browser.frames_args == [URLS["same"], URLS["cross"]] and browser.frames_on is False
    assert list(step["frames"]) == ["same", "cross"] and step["settled"] is True
    for name in ("same", "cross"):
        frame = step["frames"][name]
        want = surfaces.region_points(CHROME, NATIVE, FRAME_RECTS[name], surfaces.FRAME_FRACTIONS)
        assert [m["asked"] for m in frame["input"]] == [list(p) for p in want]
        assert frame["rect"] == FRAME_RECTS[name] and frame["state"]["events"] == {"count": 3, "dx": 7, "dy": 91}
        assert frame["kind"] == surfaces.FRAME_KINDS[name] and frame["state"]["probe"]["screen"]["pixelDepth"] == 24
        for x, y in want:  # inside the frame, on the screen
            rect = FRAME_RECTS[name]
            assert rect["x"] < x < rect["x"] + rect["width"] and 85 + rect["y"] < y < 85 + rect["y"] + rect["height"]
    ask = [item for item in browser.log if isinstance(item, tuple) and item[0] == "ask_frames"]
    assert ask == [("ask_frames", "reset"), ("ask_frames", "record")] and browser.log[-1] == "remove_frames"
    assert "chrome" in step and "native" in step and step["content"]["events"]["count"] == 0
    assert "url" not in json.dumps(step), "no port in the step: launch a and launch b must be equal"


def test_the_transformed_points_are_the_matrix_applied_to_fractions_of_the_frame_and_inside_the_container_s_rectangle():
    for transform, matrix in TRANSFORM_MATRICES.items():
        browser = Browser()
        browser.transformed = ("cross_x", "u", transform)
        geometry = browser.transformed_geometry()
        points = surfaces.transformed_points(CHROME, NATIVE, geometry)
        a, b, c, d, _, _ = matrix
        for (fx, fy), point in zip(surfaces.FRAME_FRACTIONS, points):
            x, y = 10 + a * 300 * fx + c * 120 * fy, 200 + b * 300 * fx + d * 120 * fy
            assert point["css"] == pytest.approx([x, y]) and point["device"] == [round(x), round(85 + y)] and point["inside_rect"] is True
    scaled = surfaces.transformed_points({"devicePixelRatio": 1.25, "browser": {"x": 0, "y": 80}}, {"client_origin": [-9, 0]},
                                         {"rect": {"x": 10, "y": 200, "width": 450, "height": 180}, "left": 10, "top": 200, "width": 300,
                                          "height": 120, "matrix": [1.5, 0, 0, 1.5, 0, 0]})
    assert scaled[1]["device"] == [round(-9 + (10 + 225) * 1.25), round((80 + 200 + 90) * 1.25)]


def test_a_rotated_frame_s_point_that_is_in_the_bounding_box_only_would_be_outside_the_frame_so_the_points_are_not_taken_from_the_box():
    browser = Browser()
    browser.transformed = ("cross_rotate", "u", "rotate(10deg)")
    geometry = browser.transformed_geometry()
    box = geometry["rect"]
    a, b, c, d, _, _ = geometry["matrix"]
    det = a * d - b * c
    dx, dy = box["x"] - 10, box["y"] - 200  # the box's top left: not a point of the rotated frame
    u, v = (d * dx - c * dy) / det, (-b * dx + a * dy) / det
    assert not (0 <= u <= 300 and 0 <= v <= 120)
    for point in surfaces.transformed_points(CHROME, NATIVE, geometry):
        dx, dy = point["css"][0] - 10, point["css"][1] - 200
        u, v = (d * dx - c * dy) / det, (-b * dx + a * dy) / det
        assert 0 <= u <= 300 and 0 <= v <= 120


def test_the_four_transformed_iframes_run_one_at_a_time_each_with_three_real_moves_in_its_transformed_area_and_are_removed():
    browser = Browser()
    step = browser.iframes_transformed()
    assert list(step["frames"]) == ["cross_scale", "cross_rotate", "same_scale", "same_rotate"] == list(surfaces.TRANSFORMED_NAMES)
    assert [item[1:] for item in browser.log if isinstance(item, tuple) and item[0] == "add_transformed"] == [
        ("cross_scale", "scale(1.5)"), ("cross_rotate", "rotate(10deg)"), ("same_scale", "scale(1.5)"), ("same_rotate", "rotate(10deg)")]
    assert browser.log.count("remove_transformed") == 4 and browser.transformed is None and len(browser.moves) == 4
    for name, frame in step["frames"].items():
        points = frame["points"]
        assert len(points) == 3 and [m["asked"] for m in frame["input"]] == [p["device"] for p in points]
        assert frame["transform"] == dict(surfaces.TRANSFORMS)[name.split("_")[1]] and frame["kind"] == surfaces.FRAME_KINDS[name.split("_")[0]]
        assert all(p["inside_rect"] for p in points) and len(frame["state"]["moves"]) == 3
        for move, fraction in zip(frame["state"]["moves"], surfaces.FRAME_FRACTIONS):  # the frame's own client point is the fraction
            assert move["clientX"] == pytest.approx(fraction[0] * 300, abs=1.0) and move["clientY"] == pytest.approx(fraction[1] * 120, abs=1.0)
        assert frame["geometry"]["matrix"] == TRANSFORM_MATRICES[frame["transform"]]
    asked = [item[2] for item in browser.log if isinstance(item, tuple) and item[0] == "ask_transformed"]
    assert asked == ["reset", "record"] * 4
    assert "chrome" in step and "native" in step and "url" not in json.dumps(step)


def test_a_transformed_iframe_that_did_not_load_makes_the_step_not_possible_and_the_container_is_removed():
    browser = Browser(frames_load=False)
    steps = browser.surface(("iframes_transformed",), lambda: (browser.iframes_transformed(),))
    assert steps == {"iframes_transformed": {"not_possible": "the iframe cross_scale did not load"}}
    assert browser.transformed is None and browser.moves == []
    missing = Browser()
    missing.urls.pop("same_rotate")
    assert missing.surface(("iframes_transformed",), lambda: (missing.iframes_transformed(),)) == {
        "iframes_transformed": {"not_possible": "no iframe page was served"}}


def test_the_transformed_urls_are_the_two_origins_of_the_plain_frames_each_named_for_its_transform():
    urls = surfaces.transformed_urls(5001, 5002)
    assert urls == {"cross_scale": "http://localhost:5002/surface.html?name=cross_scale",
                    "cross_rotate": "http://localhost:5002/surface.html?name=cross_rotate",
                    "same_scale": "http://127.0.0.1:5001/surface.html?name=same_scale",
                    "same_rotate": "http://127.0.0.1:5001/surface.html?name=same_rotate"}
    assert {k: v for k, v in URLS.items() if "_" in k} == urls, "the fake browser serves the same names"


def test_the_page_script_puts_the_frame_in_a_container_at_left_10_top_200_with_the_transform_on_the_container_and_keeps_every_move():
    for needle in ("position:fixed;left:10px;top:200px;width:300px;height:120px", "transform:${transform};transform-origin:0 0",
                   "window.__moves.push(", "new DOMMatrix(getComputedStyle(box).transform)", "if (data.moves) state.moves = window.__moves"):
        assert needle in PAGE_JS, needle


def test_an_iframe_that_did_not_load_is_not_possible_and_the_frames_are_still_removed():
    browser = Browser(frames_load=False)
    steps = browser.surface(("iframes_max",), lambda: (browser.iframes(),))
    assert steps == {"iframes_max": {"not_possible": "an iframe did not load (hello from ['same'])"}}
    assert browser.frames_on is False and browser.moves == []


# ---------------------------------------------------------------------------- what a refused step does


def test_a_command_marionette_refuses_makes_the_step_not_possible_and_a_closed_connection_ends_the_launch():
    refused = Browser(fail_on=(surfaces.FULLSCREEN_ERROR, marionette.MarionetteError("WebDriver:ExecuteScript: boom")), enters=False)
    steps = refused.fullscreen()
    assert steps["fullscreen_max_in"] == {"not_possible": "marionette: WebDriver:ExecuteScript: boom"} == steps["fullscreen_max_out"]
    closed = Browser(fail_on=(surfaces.ELEMENT_RECT, marionette.ConnectionClosed("the connection closed")))
    with pytest.raises(marionette.ConnectionClosed):
        closed.fullscreen()
    assert issubclass(marionette.ConnectionClosed, marionette.MarionetteError)


def test_the_marionette_client_raises_the_closed_connection_error_when_the_browser_hangs_up():
    class Hung:
        def settimeout(self, seconds):
            pass

        def sendall(self, data):
            pass

        def recv(self, size):
            return b""

    client = marionette.Client(1)
    client.sock = Hung()
    with pytest.raises(marionette.ConnectionClosed):
        client.call("WebDriver:GetTitle")


# ---------------------------------------------------------------------------- the order of a launch


def test_a_launch_takes_the_round_5_steps_in_the_order_of_the_plan_on_the_maximized_window_and_nothing_of_a_restored_one():
    source = Path(measure.__file__).read_text(encoding="utf-8")
    order = [source.index(text) for text in (
        'steps["maximized"] = session.maximized()', "session.fullscreen()", 'steps.update(session.surface(("iframes_max",)',
        'steps.update(session.surface(("iframes_transformed",)', "for name, features in surfaces.POPUP_FEATURES:")]
    assert order == sorted(order) and len(set(order)) == len(order)
    for name in ("fullscreen_max_in", "iframes_max", "iframes_transformed"):
        assert f'if "{name}" in steps_wanted:' in source, name
    assert 'if name in steps_wanted:' in source, "the two popups"
    for gone in ("juggler", "restored", "inRDMPane", "resizeBy", "hostgate", "grid"):
        assert gone not in source, gone


def test_the_pages_the_launch_serves_are_named_for_the_steps_that_load_them():
    assert set(measure.SERVED) == {"/", "/page.js", "/surface.html"}
    for name, _ in measure.SERVED.values():
        assert (HERE / name).is_file(), name
    assert surfaces.FULLSCREEN_TIMEOUT == 15.0 and surfaces.POPUP_TIMEOUT == 20.0 and surfaces.FRAME_TIMEOUT == 20.0


# ---------------------------------------------------------------------------- round 7: what a frame reads in its first moments

EARLY_FIELDS = ("mozInnerScreenX", "mozInnerScreenY", "screenX", "screenY", "outerWidth", "outerHeight", "innerWidth", "innerHeight",
                "devicePixelRatio")


def early_script(html=None):
    head = (SURFACE_HTML if html is None else html).split("<head>", 1)[1]
    return head.split("<script>", 1)[1].split("</script>", 1)[0]


@pytest.mark.parametrize("html", [SURFACE_HTML, PAGE_HTML], ids=["the frame page", "the top page"])
def test_the_page_has_the_inline_early_script_first_in_head_before_any_other_script_or_stylesheet(html):
    assert html.startswith("<!doctype html>\n<head>\n<script>\n")
    head = html.split("<head>", 1)[1]
    inline = head.index("<script>")
    for other in ('<script src=', "<style", "<link", "<meta", "<title", "src=", "<button"):
        assert head.find(other) == -1 or head.find(other) > inline, other  # a tag that is in the page comes after the early script
    assert head.count("<script>") == 1 and head.index("</script>") < head.index('<script src="/page.js"></script>')
    assert html.count('<script src="/page.js"></script>') == 1


def test_the_top_page_and_the_frame_page_run_the_same_early_script_byte_for_byte():
    assert early_script(PAGE_HTML) == early_script(SURFACE_HTML)
    assert "window.__early = {}" in early_script(PAGE_HTML)


def test_the_early_script_stores_window_early_with_the_five_moments_the_nine_reads_and_the_time_each_read_in_its_own_try():
    script = early_script()
    assert "window.__early = {}" in script
    for field in EARLY_FIELDS + ("performance.now()",):
        assert field in script, field
    for moment, hook in (("parse", 'at("parse");'), ("dcl", 'document.addEventListener("DOMContentLoaded"'),
                         ("raf1", "requestAnimationFrame(function () {\n      at(\"raf1\");"),
                         ("raf2", 'requestAnimationFrame(function () { at("raf2"); })'), ("load", 'window.addEventListener("load"')):
        assert hook in script, moment
    assert script.count("try {") >= 6 and script.count('"error: " + e') >= 6, "a try/catch around each read, an error string on failure"
    assert "window[names[i]]" in script and "out.now = performance.now()" in script
    for forbidden in ("preventDefault", "stopPropagation", "setTimeout", "setInterval", "innerHTML", "style", "fetch(", "postMessage"):
        assert forbidden not in script, forbidden


def test_the_frame_s_record_answer_carries_early_and_the_parent_keeps_it_in_the_record():
    assert 'say({kind: "record", state: state, early: window.__early});' in PAGE_JS
    assert 'window.__frameRecords[data.name] = Object.assign({}, data.state, {early: data.early})' in PAGE_JS
    assert 'say({kind: "record", state: JSON.parse(window.__state())' not in PAGE_JS, "the old reply is gone"
    assert 'if (data.moves) state.moves = window.__moves;' in PAGE_JS, "the moves of the transformed frames are kept"


def test_the_early_of_a_frame_reaches_the_step_with_the_frame_s_record_in_iframes_and_the_fields_stay():
    early = {m: {"mozInnerScreenX": 8.0 + i, "mozInnerScreenY": 1.0, "now": 5.0 + i} for i, m in enumerate(("parse", "dcl", "raf1", "raf2", "load"))}

    class WithEarly(Browser):
        def content(self, script, args=None):
            if script == surfaces.FRAME_RECORDS:
                return json.dumps({n: dict(state(), early=early) for n in ("same", "cross")})
            return super().content(script, args)

    step = WithEarly().iframes()
    for name in ("same", "cross"):
        assert step["frames"][name]["state"]["early"] == early
        assert step["frames"][name]["state"]["events"] == {"count": 3, "dx": 7, "dy": 91}
        assert set(step["frames"][name]) == {"kind", "rect", "input", "state"}


def test_each_iframe_step_adds_new_iframe_elements_so_a_frame_s_parse_is_the_first_moment_of_a_new_document():
    adds = PAGE_JS.split("window.__addFrames = ")[1].split("window.__removeFrames = ")[0]
    assert adds.index("window.__removeFrames();") < adds.index('document.createElement("iframe")') and "frame.src = url;" in adds
    assert "window.__frameHello = {};" in adds and "window.__frameRecords = {};" in adds
    transformed = PAGE_JS.split("window.__addTransformed = ")[1].split("window.__removeTransformed = ")[0]
    assert transformed.index("window.__removeTransformed();") < transformed.index('document.createElement("iframe")')
    browser = Browser()
    browser.iframes()
    browser.iframes()
    assert browser.log.count("remove_frames") == 2 and browser.frames_on is False


# ---------------------------------------------------------------------------- round 8: what the top document and the popups read in their first moments


def test_the_two_scripts_that_read_the_early_moments_are_the_pages_own_and_the_popup_s():
    assert "w.__early" in surfaces.EARLY and "JSON.stringify" in surfaces.EARLY and surfaces.EARLY.startswith(surfaces._PAGE)
    assert "p.__early" in surfaces.POPUP_EARLY and "JSON.stringify" in surfaces.POPUP_EARLY and surfaces.POPUP_EARLY.startswith(surfaces._POPUP)
    assert surfaces.popup_url(5001).split("/")[3].startswith("surface.html"), "the popup page is the frame page, with the early script"


class Maximizing(Browser):
    """A Browser that also maximizes: the chrome says it is maximized and the page answers the sizes."""

    def chrome(self, script, args=None):
        if script == "window.maximize(); return true;":
            self.log.append("maximize")
            return True
        if script == measure.CHROME_SIZE:
            return [True, 1900, 900, 1936, 1049]
        return super().chrome(script, args)

    def content(self, script, args=None):
        if script == measure.CONTENT_SIZE:
            return [1936, 1049, 1900, 900]
        return super().content(script, args)

    def record(self):
        return dict(super().record(), content={"innerWidth": 1900})


def test_the_maximized_step_records_the_top_page_s_early_moments_in_its_content_and_keeps_the_other_fields():
    step = Maximizing().maximized()
    assert step["content"]["early"] == PAGE_EARLY and step["settled"] is True
    assert set(step) == {"chrome", "native", "input", "content", "settled"}
    assert step["content"] == {"innerWidth": 1900, "early": PAGE_EARLY}, "the settled reading is kept, the early one added"


def test_a_page_that_stored_no_early_is_recorded_as_null_and_not_an_error():
    class Bare(Maximizing):
        def content(self, script, args=None):
            return json.dumps(None) if script == surfaces.EARLY else super().content(script, args)

    assert Bare().maximized()["content"]["early"] is None
