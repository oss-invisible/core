"""One measurement: retail Firefox, one window, maximized.

    python scripts/retail/window_geometry/measure.py --install DIR --build build.json --dpr 1 --launch a
        --resolution 1920x1080 --out DIR [--mode devPixelsPerPx]

The launch is `firefox.exe -no-remote -profile <fresh empty directory> -marionette <page url>`: a profile whose
only file is a user.js with `marionette.port` (and, for the derived path, `layout.css.devPixelsPerPx`), nothing
in distribution/policies.json, and no MOZ_* variable but MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1, without which Marionette refuses
the chrome context. Marionette is the one difference from retail-capture.yml; why it is allowed is in the header of
.github/workflows/retail-window-geometry.yml.

Steps (plan.py says which launch takes which). `maximized` has the same fields as every full record (what the page reads, what the
chrome reads, what Windows says about the top-level window, and a real mouse move over the content). The steps of plan.ROUND5_STEPS, taken on launches a and b (p and q where no display launch could be made), in
this order: on the maximized window, element fullscreen entered by a REAL click (SendInput on #fs, whose handler calls
requestFullscreen; Marionette cannot give the user activation) and left by document.exitFullscreen() (`fullscreen_max_in`,
`fullscreen_max_out`), F11-style fullscreen by window.fullScreen in the chrome (`f11_max_in`, `f11_max_out`), the main page with a
same-origin and a cross-site iframe, each recording its own content record after real mouse moves over it (`iframes_max`), the same
two origins as four iframes under CSS transforms (a container with scale(1.5), one with rotate(10deg), each holding a 300x120 frame,
one at a time; every mouse event a frame saw at three points of its transformed area is kept: `iframes_transformed`), and two popups
opened by a real click on #open, the second with left and top (`popup`, `popup_pos`; the popup page records its content
record and Windows' rects of its window are kept; it is closed). A step this browser did not allow is
`{"not_possible": reason}`.
Writes OUT/raw.json. The result is valid only when no process of the install folder is left.
"""
from __future__ import annotations

import argparse
import ctypes
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from ctypes import wintypes as w
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import display  # noqa: E402
import environment  # noqa: E402
import marionette  # noqa: E402
import plan  # noqa: E402
import surfaces  # noqa: E402
import run_retail_capture as launcher  # noqa: E402
from native import INPUT, MONITORINFO, POINT, RECT, WINDOWPLACEMENT, api, guarded  # noqa: E402

FORMAT = "retail-window-geometry/1"
PAGE = HERE / "page.html"
#: What PageServer serves, by path: the main page, the script every page runs, and the page of an iframe and of a popup.
SERVED = {"/": ("page.html", "text/html; charset=utf-8"), "/page.js": ("page.js", "text/javascript; charset=utf-8"),
          "/surface.html": ("surface.html", "text/html; charset=utf-8")}
HOLD_SECONDS, SETTLE_TIMEOUT, POLL_SECONDS = 1.0, 20.0, 0.25
#: Where the mouse goes, as fractions of the content area.
MOUSE_FRACTIONS = ((0.25, 0.3), (0.5, 0.5), (0.75, 0.7))
#: The one variable the launch sets: Marionette refuses chrome-context script without it (Firefox 159).
MARIONETTE_ENV = {"MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}
CLASS_NAME = "MozillaWindowClass"
DWMWA_EXTENDED_FRAME_BOUNDS, GWL_STYLE, GWL_EXSTYLE, MONITOR_DEFAULTTONEAREST = 9, -16, -20, 2
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x1, 0x8000, 0x4000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x2, 0x4
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

CHROME_STATE = """
const rect = (r) => ({x: r.x, y: r.y, width: r.width, height: r.height});
return {windowState: window.windowState, maximized: window.windowState === window.STATE_MAXIMIZED,
  screenX: window.screenX, screenY: window.screenY, outerWidth: window.outerWidth, outerHeight: window.outerHeight,
  innerWidth: window.innerWidth, innerHeight: window.innerHeight, devicePixelRatio: window.devicePixelRatio,
  stack: rect(gBrowser.selectedBrowser.closest('.browserStack').getBoundingClientRect()),
  browser: rect(gBrowser.selectedBrowser.getBoundingClientRect()),
  sizemode: document.documentElement.getAttribute('sizemode')};"""
CONTENT_STATE = """
const m = (window.wrappedJSObject || window).__m;
return {innerWidth: window.innerWidth, innerHeight: window.innerHeight, outerWidth: window.outerWidth,
  outerHeight: window.outerHeight, screenX: window.screenX, screenY: window.screenY, screenLeft: window.screenLeft,
  screenTop: window.screenTop, mozInnerScreenX: window.mozInnerScreenX, mozInnerScreenY: window.mozInnerScreenY,
  devicePixelRatio: window.devicePixelRatio,
  screen: {width: screen.width, height: screen.height, availWidth: screen.availWidth, availHeight: screen.availHeight,
           availLeft: screen.availLeft, availTop: screen.availTop},
  events: {count: m.count, dx: m.dx, dy: m.dy},
  probe: JSON.parse((window.wrappedJSObject || window).__probe())};"""
CONTENT_SIZE = "return [window.outerWidth, window.outerHeight, window.innerWidth, window.innerHeight];"
CHROME_SIZE = "return [window.windowState === window.STATE_MAXIMIZED, window.innerWidth, window.innerHeight, window.outerWidth, window.outerHeight];"
PAGE_READY = "return !!(window.wrappedJSObject || window).__m;"
RESET_EVENTS = "(window.wrappedJSObject || window).__reset(); return true;"
TWO_FRAMES = ("const done = arguments[arguments.length - 1]; let once = false; "
              "const end = (v) => { if (!once) { once = true; done(v); } }; setTimeout(() => end(false), 2000); "
              "requestAnimationFrame(() => requestAnimationFrame(() => end(true)));")


class MeasureError(Exception):
    """The measurement could not be taken as asked."""


# --------------------------------------------------------------------------- the launch


def launch_command(firefox_exe: Path, profile: Path, url: str) -> List[str]:
    return [str(firefox_exe), "-no-remote", "-profile", str(profile), "-marionette", url]


def launch_env(base: Dict[str, str]) -> Dict[str, str]:
    """The runner's environment without any MOZ_* variable, plus MARIONETTE_ENV."""
    env = {k: v for k, v in base.items() if not k.upper().startswith("MOZ_")}
    env.update(MARIONETTE_ENV)
    return env


def user_js(prefs: Dict[str, Any]) -> str:
    return "".join(f"user_pref({json.dumps(name)}, {json.dumps(value)});\n" for name, value in sorted(prefs.items()))


def assert_launch(command: Sequence[str], profile: Path, install_dir: Path, env: Dict[str, str],
                  prefs: Dict[str, Any]) -> None:
    """Refuse anything but the declared launch: this command, a profile of one user.js with exactly `prefs`, no policy, no MOZ_* but MARIONETTE_ENV."""
    problems: List[str] = []
    if len(command) != 6 or list(command[1:5]) != ["-no-remote", "-profile", str(profile), "-marionette"]:
        problems.append(f"the command is {list(command)}")
    names = sorted(p.name for p in profile.iterdir())
    if names != ["user.js"]:
        problems.append(f"the profile holds {names}, not just user.js")
    elif (profile / "user.js").read_text(encoding="utf-8") != user_js(prefs):
        problems.append("user.js is not exactly the declared prefs")
    for relative in launcher.FORBIDDEN_IN_INSTALL:
        if (install_dir / relative).exists():
            problems.append(f"the install folder has {relative.as_posix()}")
    moz = sorted(k for k in env if k.upper().startswith("MOZ_") and (k, env[k]) not in MARIONETTE_ENV.items())
    if moz:
        problems.append(f"the environment sets {moz}")
    if problems:
        raise MeasureError("not the declared launch: " + "; ".join(problems))


class PageServer:
    """Serves the pages of SERVED on 127.0.0.1 and nothing else (and on ::1 at the same port when `also_ipv6`, for a page reached
    as `localhost`)."""

    def __init__(self, folder: Path = HERE, also_ipv6: bool = False) -> None:
        files = {path: ((folder / name).read_bytes(), kind) for path, (name, kind) in SERVED.items()}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                served = files.get(self.path.split("?")[0])
                body = served[0] if served else b""
                self.send_response(200 if served else 404)
                self.send_header("Content-Type", served[1] if served else "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        class Server6(http.server.ThreadingHTTPServer):
            address_family = socket.AF_INET6

        self.httpds = [http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)]
        self.port = self.httpds[0].server_address[1]
        if also_ipv6:
            try:
                self.httpds.append(Server6(("::1", self.port), Handler))
            except OSError:
                pass  # no IPv6 loopback on this machine: localhost falls back to 127.0.0.1
        for httpd in self.httpds:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        for httpd in self.httpds:
            httpd.shutdown()
            httpd.server_close()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------- waiting


def wait_stable(read: Callable[[], Any], ready: Callable[[Any], bool] = lambda v: True, hold: float = HOLD_SECONDS,
                timeout: float = SETTLE_TIMEOUT, poll: float = POLL_SECONDS, clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep) -> Tuple[Any, bool]:
    """Poll `read` until it is `ready` and has not changed for `hold` seconds. Returns (last value, settled)."""
    start = clock()
    last, since = None, start
    while clock() - start <= timeout:
        now = read()
        if now != last:
            last, since = now, clock()
        if ready(now) and clock() - since >= hold:
            return now, True
        sleep(poll)
    return last, False


# --------------------------------------------------------------------------- Windows


def find_windows(pids: Sequence[int]) -> List[int]:
    """Every visible top-level window of class MozillaWindowClass owned by one of `pids` (the popup is one besides the main window)."""
    a, found = api(), []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def visit(hwnd: int, _: int) -> bool:
        pid, name = w.DWORD(), ctypes.create_unicode_buffer(64)
        a.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        a.user32.GetClassNameW(hwnd, name, 64)
        if pid.value in pids and name.value == CLASS_NAME and a.user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    a.user32.EnumWindows(visit, 0)
    return found


def find_window(pids: Sequence[int]) -> Optional[int]:
    """The first visible top-level window of class MozillaWindowClass owned by one of `pids`."""
    found = find_windows(pids)
    return found[0] if found else None


def window_record(hwnd: int) -> Dict[str, Any]:
    """What Windows says about the top-level window, in device pixels."""
    a = api()

    def rect_of(fn: Callable[[Any, Any], Any]) -> List[int]:
        r = RECT()
        fn(hwnd, ctypes.byref(r))
        return r.list()

    frame = RECT()
    hresult = a.dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(frame), ctypes.sizeof(frame))
    origin, placement, info = POINT(0, 0), WINDOWPLACEMENT(), MONITORINFO()
    a.user32.ClientToScreen(hwnd, ctypes.byref(origin))
    placement.length = ctypes.sizeof(placement)
    a.user32.GetWindowPlacement(hwnd, ctypes.byref(placement))
    info.cbSize = ctypes.sizeof(info)
    a.user32.GetMonitorInfoW(a.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
    return {"window_rect": rect_of(a.user32.GetWindowRect), "extended_frame_bounds": frame.list(),
            "extended_frame_bounds_hresult": hresult, "client_rect": rect_of(a.user32.GetClientRect),
            "client_origin": [origin.x, origin.y],
            "placement": {"showCmd": placement.showCmd, "rcNormalPosition": placement.rcNormalPosition.list()},
            "dpi": a.user32.GetDpiForWindow(hwnd),
            "style": a.user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF,
            "exstyle": a.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF,
            "monitor_rect": info.rcMonitor.list(), "work_rect": info.rcWork.list()}


def mouse_points(chrome: Dict[str, Any], native: Dict[str, Any]) -> List[Tuple[int, int]]:
    """Screen points (device pixels) inside the content area: the browser's rect in the chrome, scaled, from the client origin."""
    ox, oy = native["client_origin"]
    rect, scale = chrome["browser"], chrome["devicePixelRatio"]
    return [(round(ox + (rect["x"] + fx * rect["width"]) * scale), round(oy + (rect["y"] + fy * rect["height"]) * scale))
            for fx, fy in MOUSE_FRACTIONS]


def move_mouse(points: Sequence[Tuple[int, int]], pause: float = 0.2) -> List[Dict[str, Any]]:
    """Real absolute mouse moves with SendInput; reports where the cursor was after each."""
    a, out = api(), []
    vx, vy = a.user32.GetSystemMetrics(SM_XVIRTUALSCREEN), a.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = a.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), a.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    for x, y in points:
        item = INPUT(type=0)
        item.mi.dx, item.mi.dy = round((x - vx) * 65535 / (vw - 1)), round((y - vy) * 65535 / (vh - 1))
        item.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        sent = a.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
        time.sleep(pause)
        at = POINT()
        a.user32.GetCursorPos(ctypes.byref(at))
        out.append({"asked": [x, y], "sent": sent, "cursor": [at.x, at.y]})
    return out


def click(point: Tuple[int, int], pause: float = 0.2) -> Dict[str, Any]:
    """A real click with SendInput: the mouse moves there, then the left button goes down and up (a page gets the user
    activation fullscreen and a popup need). Reports where the cursor was and what SendInput took."""
    moved = move_mouse([point], pause)[0]
    a, sent = api(), []
    for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        item = INPUT(type=0)
        item.mi.dwFlags = flag
        sent.append(a.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)))
        time.sleep(0.05)
    return dict(moved, buttons_sent=sent)


# --------------------------------------------------------------------------- the steps


class Session:
    """One running browser: its Marionette client and the install folder whose processes are its own."""

    def __init__(self, client: marionette.Client, install_dir: Path, table: launcher.ProcessTable,
                 urls: Optional[Dict[str, str]] = None) -> None:
        self.client, self.install_dir, self.table = client, install_dir, table
        #: The pages the surfaces load: `popup`, and the iframes `same` and `cross`.
        self.urls = urls or {}

    def chrome(self, script: str, args: Optional[list] = None) -> Any:
        return self.client.execute_script(script, args, context="chrome")

    def content(self, script: str, args: Optional[list] = None) -> Any:
        return self.client.execute_script(script, args, context="content")

    def hwnd(self) -> int:
        hwnd = find_window(self.table.under(self.install_dir))
        if hwnd is None:
            raise MeasureError("no visible MozillaWindowClass window of the launched firefox")
        return hwnd

    def windows(self) -> List[int]:
        return find_windows(self.table.under(self.install_dir))

    def native(self, hwnd: int) -> Dict[str, Any]:
        return guarded(lambda: window_record(hwnd))

    def foreground(self, hwnd: int) -> None:
        api().user32.SetForegroundWindow(hwnd)

    def click(self, point: Tuple[int, int]) -> Dict[str, Any]:
        return click(point)

    def move(self, points: Sequence[Tuple[int, int]]) -> List[Dict[str, Any]]:
        return move_mouse(points)

    def record(self) -> Dict[str, Any]:
        """The full record of the window as it is now, then a real mouse move over the content and what the page saw."""
        chrome = self.chrome(CHROME_STATE)
        hwnd = self.hwnd()
        native = self.native(hwnd)
        self.foreground(hwnd)
        self.content(RESET_EVENTS)
        moves = self.move(mouse_points(chrome, native))
        self.client.execute_async_script(TWO_FRAMES, context="content")
        return {"chrome": chrome, "native": native, "input": moves, "content": self.content(CONTENT_STATE)}

    def maximized(self) -> Dict[str, Any]:
        self.chrome("window.maximize(); return true;")

        def read() -> Any:
            return [self.chrome(CHROME_SIZE), self.content(CONTENT_SIZE)]

        _, settled = wait_stable(read, ready=lambda v: v[0][0] is True)
        return dict(self.record(), settled=settled)

    # ----- round 5: fullscreen, popups, iframes (the scripts and the points are in surfaces.py)

    def surface(self, names: Sequence[str], run: Callable[[], Sequence[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
        """{name: step} from `run()`, which returns the steps in the order of `names`. A step this browser did not allow
        (StepNotPossible, or a command Marionette answered with an error) is `{"not_possible": reason}` in each of `names`, after
        putting the window back; a closed connection is not that: it ends the launch."""
        try:
            return dict(zip(names, run()))
        except marionette.ConnectionClosed:
            raise
        except (surfaces.StepNotPossible, marionette.MarionetteError) as exc:
            self.leave_surfaces()
            reason = str(exc) if isinstance(exc, surfaces.StepNotPossible) else f"marionette: {exc}"[:300]
            return {name: {"not_possible": reason} for name in names}

    def leave_surfaces(self) -> None:
        """Best effort after a failed step: leave fullscreen, close a popup, remove the iframes."""
        for script, context in ((surfaces.EXIT_FULLSCREEN, "content"), (surfaces.WINDOW_FULLSCREEN_OFF, "chrome"),
                                (surfaces.POPUP_CLOSE, "content"), (surfaces.REMOVE_FRAMES, "content"),
                                (surfaces.REMOVE_TRANSFORMED, "content")):
            try:
                self.client.execute_script(script, context=context)
            except Exception:  # noqa: BLE001 - cleanup only; the next step says what is wrong
                pass

    def fullscreen_read(self) -> Any:
        return [self.chrome(surfaces.FULLSCREEN_CHROME), self.content(surfaces.FULLSCREEN_CONTENT)]

    def fullscreen_facts(self) -> Dict[str, Any]:
        chrome, content = self.fullscreen_read()
        return {"window_fullScreen": chrome[0], "windowState": chrome[1], "document_fullscreen": content[0]}

    def element_fullscreen(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """(entered, left): a REAL click (SendInput) on #fs, whose handler calls document.documentElement.requestFullscreen();
        the full record in fullscreen; document.exitFullscreen(); the full record once it has settled."""
        rect = self.content(surfaces.ELEMENT_RECT, ["fs"])
        chrome = self.chrome(CHROME_STATE)
        hwnd = self.hwnd()
        native = self.native(hwnd)
        self.foreground(hwnd)
        clicked = self.click(surfaces.region_points(chrome, native, rect, surfaces.CLICK_FRACTION)[0])
        last, settled = wait_stable(self.fullscreen_read, ready=lambda v: v[1][0] is True, timeout=surfaces.FULLSCREEN_TIMEOUT)
        if not (last and last[1][0] is True):
            error = self.content(surfaces.FULLSCREEN_ERROR)
            raise surfaces.StepNotPossible("document.fullscreenElement stayed null after a real click on the button"
                                           + (f" (requestFullscreen rejected: {error})" if error else ""))
        entered = dict(self.record(), settled=settled, click=clicked, fullscreen=self.fullscreen_facts())
        self.content(surfaces.EXIT_FULLSCREEN)
        forced = False
        _, settled = wait_stable(self.fullscreen_read, ready=lambda v: v[1][0] is False and v[0][0] is False,
                                 timeout=surfaces.FULLSCREEN_TIMEOUT)
        if not settled:  # still fullscreen: force the window out so the next step starts from the same state
            forced = True
            self.chrome(surfaces.WINDOW_FULLSCREEN_OFF)
            _, settled = wait_stable(self.fullscreen_read, ready=lambda v: v[1][0] is False and v[0][0] is False,
                                     timeout=surfaces.FULLSCREEN_TIMEOUT)
        left = dict(self.record(), settled=settled, forced_exit=forced, fullscreen=self.fullscreen_facts())
        return entered, left

    def window_fullscreen(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """(entered, left): F11-style fullscreen of the window, `window.fullScreen = true` and then false from the chrome, the
        full record after each."""
        self.chrome(surfaces.WINDOW_FULLSCREEN_ON)
        last, settled = wait_stable(self.fullscreen_read, ready=lambda v: v[0][0] is True, timeout=surfaces.FULLSCREEN_TIMEOUT)
        if not (last and last[0][0] is True):
            raise surfaces.StepNotPossible("window.fullScreen stayed false after setting it")
        entered = dict(self.record(), settled=settled, fullscreen=self.fullscreen_facts())
        self.chrome(surfaces.WINDOW_FULLSCREEN_OFF)
        _, settled = wait_stable(self.fullscreen_read, ready=lambda v: v[0][0] is False, timeout=surfaces.FULLSCREEN_TIMEOUT)
        return entered, dict(self.record(), settled=settled, fullscreen=self.fullscreen_facts())

    def fullscreen(self) -> Dict[str, Dict[str, Any]]:
        """The four fullscreen steps of the maximized window: fullscreen_max_in/out by a real click, f11_max_in/out by
        window.fullScreen."""
        steps = self.surface(("fullscreen_max_in", "fullscreen_max_out"), self.element_fullscreen)
        steps.update(self.surface(("f11_max_in", "f11_max_out"), self.window_fullscreen))
        return steps

    def new_windows(self, before: set) -> List[int]:
        return sorted(set(self.windows()) - before)

    def popup(self, features: str) -> Dict[str, Any]:
        """A popup from the main page: a REAL click on #open, whose handler calls window.open(url, '_blank', features). Its
        page records its full content record (inner, outer, screenX/Y, mozInnerScreen, probe) after real mouse moves over its
        content; Windows' rects of the popup window are recorded; the popup is closed."""
        if "popup" not in self.urls:
            raise surfaces.StepNotPossible("no popup page was served")
        before = set(self.windows())
        self.content(surfaces.SET_POPUP, [self.urls["popup"], features])
        rect = self.content(surfaces.ELEMENT_RECT, ["open"])
        chrome = self.chrome(CHROME_STATE)
        hwnd = self.hwnd()
        main_native = self.native(hwnd)
        self.foreground(hwnd)
        clicked = self.click(surfaces.region_points(chrome, main_native, rect, surfaces.CLICK_FRACTION)[0])
        opened, _ = wait_stable(lambda: self.new_windows(before), ready=bool, hold=0.5, timeout=surfaces.POPUP_TIMEOUT)
        if not opened:
            raise surfaces.StepNotPossible("no popup window opened after a real click on the button")
        loaded, _ = wait_stable(lambda: self.content(surfaces.POPUP_READY), ready=bool, hold=0, timeout=surfaces.POPUP_TIMEOUT)
        if not loaded:
            raise surfaces.StepNotPossible("the popup window opened but its page did not load")
        _, settled = wait_stable(lambda: self.content(surfaces.POPUP_SIZE))
        popup_hwnd = opened[0]
        native = self.native(popup_hwnd)
        self.foreground(popup_hwnd)
        self.content(surfaces.POPUP_RESET)
        moves = self.move(surfaces.client_points(native)) if "client_rect" in native else []
        time.sleep(0.3)
        step = {"features": features, "windows_opened": len(opened), "native": native, "input": moves, "settled": settled,
                "click": clicked, "content": json.loads(self.content(surfaces.POPUP_STATE))}
        self.content(surfaces.POPUP_CLOSE)
        _, closed = wait_stable(lambda: self.new_windows(before), ready=lambda v: not v, hold=0.3, timeout=surfaces.POPUP_TIMEOUT)
        step["closed"] = closed
        return step

    def iframes(self) -> Dict[str, Any]:
        """The main page with a same-origin and a cross-site iframe (both local): each frame records its full content record
        (inner, outer, screenX/Y, mozInnerScreen, devicePixelRatio, probe, the mouse events) after real mouse moves over it, and what the
        frame read in its first moments (`early`: parse, DOMContentLoaded, the first and second animation frame, load; surface.html's
        first inline script). The frames are added fresh at each step (new iframe elements, so each is a new document, the parse
        reading its first moment) and removed afterwards."""
        if not all(name in self.urls for name in surfaces.FRAME_NAMES):
            raise surfaces.StepNotPossible("no iframe page was served")
        names = sorted(surfaces.FRAME_NAMES)
        self.content(surfaces.ADD_FRAMES, [self.urls["same"], self.urls["cross"]])
        try:
            hello, _ = wait_stable(lambda: self.content(surfaces.FRAMES_HELLO), ready=lambda v: v == names, hold=0,
                                   timeout=surfaces.FRAME_TIMEOUT)
            if hello != names:
                raise surfaces.StepNotPossible(f"an iframe did not load (hello from {hello})")
            self.client.execute_async_script(TWO_FRAMES, context="content")
            chrome = self.chrome(CHROME_STATE)
            hwnd = self.hwnd()
            native = self.native(hwnd)
            self.foreground(hwnd)
            rects = self.content(surfaces.FRAME_RECTS)
            self.content(surfaces.ASK_FRAMES, ["reset"])
            time.sleep(0.3)
            moves = {name: self.move(surfaces.region_points(chrome, native, rects[name], surfaces.FRAME_FRACTIONS))
                     for name in surfaces.FRAME_NAMES}
            self.client.execute_async_script(TWO_FRAMES, context="content")
            self.content(surfaces.ASK_FRAMES, ["record"])

            def answered() -> Any:
                return sorted(json.loads(self.content(surfaces.FRAME_RECORDS)))

            got, _ = wait_stable(answered, ready=lambda v: v == names, hold=0, timeout=surfaces.FRAME_TIMEOUT)
            if got != names:
                raise surfaces.StepNotPossible(f"an iframe did not answer the record request (answers from {got})")
            records = json.loads(self.content(surfaces.FRAME_RECORDS))
            return {"chrome": chrome, "native": native, "content": self.content(CONTENT_STATE), "settled": True,
                    "frames": {name: {"kind": surfaces.FRAME_KINDS[name], "rect": rects[name], "input": moves[name],
                                      "state": records[name]} for name in surfaces.FRAME_NAMES}}
        finally:
            self.content(surfaces.REMOVE_FRAMES)

    def iframe_transformed(self, name: str, transform: str, chrome: Dict[str, Any], native: Dict[str, Any]) -> Dict[str, Any]:
        """One iframe (`name` = <origin>_<transform>) in a container with `transform` (origin 0 0), fixed at left 10, top 200 of the
        page: the frame answers after real mouse moves at three points of its transformed area, with its record and every
        mouse event it saw (client and screen point, and their difference). The container is removed afterwards."""
        self.content(surfaces.ADD_TRANSFORMED, [name, self.urls[name], transform])
        try:
            hello, _ = wait_stable(lambda: self.content(surfaces.FRAME_HELLO_OF, [name]), ready=bool, hold=0,
                                   timeout=surfaces.FRAME_TIMEOUT)
            if not hello:
                raise surfaces.StepNotPossible(f"the iframe {name} did not load")
            self.client.execute_async_script(TWO_FRAMES, context="content")
            geometry = self.content(surfaces.TRANSFORMED_GEOMETRY)
            points = surfaces.transformed_points(chrome, native, geometry)
            vw, vh = geometry["viewport"]
            if not all(0 <= p["css"][0] <= vw and 0 <= p["css"][1] <= vh for p in points):
                raise surfaces.StepNotPossible(f"a point of the transformed iframe {name} is outside the viewport {[vw, vh]}")
            self.content(surfaces.ASK_TRANSFORMED, [name, "reset"])
            time.sleep(0.3)
            moves = self.move([tuple(p["device"]) for p in points])
            self.client.execute_async_script(TWO_FRAMES, context="content")
            self.content(surfaces.ASK_TRANSFORMED, [name, "record"])
            state, _ = wait_stable(lambda: self.content(surfaces.FRAME_RECORD_OF, [name]), ready=lambda v: v != "null", hold=0,
                                   timeout=surfaces.FRAME_TIMEOUT)
            if state in (None, "null"):
                raise surfaces.StepNotPossible(f"the iframe {name} did not answer the record request")
            return {"kind": surfaces.FRAME_KINDS[name.split("_")[0]], "transform": transform, "geometry": geometry,
                    "points": points, "input": moves, "state": json.loads(state)}
        finally:
            self.content(surfaces.REMOVE_TRANSFORMED)

    def iframes_transformed(self) -> Dict[str, Any]:
        """The cross-site and the same-origin iframe under scale(1.5) and under rotate(10deg), one at a time (they would cover each
        other), each a 300 x 120 frame in a container fixed at left 10, top 200: what each frame reads about itself and, per point
        of three spread over its transformed area, the client and screen point of the real mouse move it received."""
        if not all(name in self.urls for name in surfaces.TRANSFORMED_NAMES):
            raise surfaces.StepNotPossible("no iframe page was served")
        chrome = self.chrome(CHROME_STATE)
        hwnd = self.hwnd()
        native = self.native(hwnd)
        self.foreground(hwnd)
        transforms = dict(surfaces.TRANSFORMS)
        frames = {name: self.iframe_transformed(name, transforms[name.split("_")[1]], chrome, native)
                  for name in surfaces.TRANSFORMED_NAMES}
        return {"chrome": chrome, "native": native, "content": self.content(CONTENT_STATE), "settled": True, "frames": frames}


def measure(install_dir: Path, dpr: float, launch: str, resolution: Sequence[int], display_result: Dict[str, Any],
            build: Dict[str, Any], mode: str = "display", prefs: Optional[Dict[str, Any]] = None,
            table: Optional[launcher.ProcessTable] = None,
            steps_wanted: Sequence[str] = plan.CORE_STEPS) -> Dict[str, Any]:
    """One launch, measured. Returns the raw record; it never raises for a failure of the browser (`error` says it).
    `maximized` is always taken first; the other steps are those of `steps_wanted`."""
    table = table or launcher.ProcessTable()
    exe = install_dir / "firefox.exe"
    marionette_port = free_port()
    all_prefs = dict(prefs or {}, **{"marionette.port": marionette_port})
    raw: Dict[str, Any] = {"format": FORMAT, "firefox_version": build.get("firefox_version"), "build_id": build.get("build_id"),
                           "installer_sha256": build.get("installer_sha256"), "dpr_asked": dpr, "mode": mode,
                           "launch": launch, "resolution": list(resolution), "display": display_result,
                           "environment": guarded(lambda: environment.collect(display_result.get("effective_dpi"))),
                           "prefs_declared": {k: v for k, v in all_prefs.items() if k != "marionette.port"},
                           "dev_px_per_px": (prefs or {}).get("layout.css.devPixelsPerPx"),
                           "steps_planned": list(steps_wanted), "steps": {}, "error": None}
    server, cross, process, client = PageServer(), PageServer(also_ipv6=True), None, marionette.Client(marionette_port)
    urls = dict(surfaces.frame_urls(server.port, cross.port), popup=surfaces.popup_url(server.port),
                **surfaces.transformed_urls(server.port, cross.port))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        profile = Path(tmp) / "profile"
        profile.mkdir()
        (profile / "user.js").write_text(user_js(all_prefs), encoding="utf-8")
        command = launch_command(exe, profile, f"http://127.0.0.1:{server.port}/")
        raw["command"] = command
        try:
            env = launch_env(dict(os.environ))
            raw["env_set"] = dict(MARIONETTE_ENV)
            assert_launch(command, profile, install_dir, env, all_prefs)
            process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            client.connect()
            client.new_session()
            client.navigate(command[-1])
            ready, _ = wait_stable(lambda: client.execute_script(PAGE_READY, context="content"), ready=bool, hold=0, timeout=20)
            if not ready:
                raise MeasureError("page.html did not load")
            session = Session(client, install_dir, table, urls)
            steps = raw["steps"]
            steps["maximized"] = session.maximized()
            # Round 5, on the maximized window: fullscreen (a real click; then F11-style), the iframes, the two popups.
            if "fullscreen_max_in" in steps_wanted:
                steps.update(session.fullscreen())
            if "iframes_max" in steps_wanted:
                steps.update(session.surface(("iframes_max",), lambda: (session.iframes(),)))
            if "iframes_transformed" in steps_wanted:
                steps.update(session.surface(("iframes_transformed",), lambda: (session.iframes_transformed(),)))
            for name, features in surfaces.POPUP_FEATURES:
                if name in steps_wanted:
                    steps.update(session.surface((name,), lambda features=features: (session.popup(features),)))
            client.delete_session()
        except Exception as exc:  # noqa: BLE001 - the record says what failed
            raw["error"] = f"{type(exc).__name__}: {exc}"
            raw["traceback"] = traceback.format_exc()[-2000:]
        finally:
            client.close()
            left = launcher.end_all(table, install_dir, getattr(process, "pid", None))
            server.close()
            cross.close()
    raw["processes_left"] = len(left)
    return raw


def write_raw(raw: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "raw.json"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", type=Path, required=True)
    ap.add_argument("--build", type=Path, required=True)
    ap.add_argument("--dpr", type=float, required=True)
    ap.add_argument("--launch", required=True)
    ap.add_argument("--resolution", required=True, help="WxH, as asked of the display (recorded, not set)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--mode", choices=["display", "devPixelsPerPx"], default="display")
    ap.add_argument("--steps", default=",".join(plan.CORE_STEPS), help="comma-separated steps after `maximized`")
    args = ap.parse_args(argv)
    display.enable_dpi_awareness()
    dpi = display.effective_dpi()
    shown = {"dpr": args.dpr, "effective_dpi": dpi, "resolution_now": display.current_resolution(),
             "dpi_reached": dpi == round(96 * args.dpr)}
    prefs = {"layout.css.devPixelsPerPx": "%g" % args.dpr} if args.mode == "devPixelsPerPx" else {}
    raw = measure(args.install.resolve(), args.dpr, args.launch, [int(n) for n in args.resolution.split("x")], shown,
                  json.loads(args.build.read_text(encoding="utf-8")), args.mode, prefs,
                  steps_wanted=[n for n in args.steps.split(",") if n])
    print(write_raw(raw, args.out))
    return 0 if raw["error"] is None and raw["processes_left"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
