"""One measurement: retail Firefox, one window, maximized and then resized by exactly Juggler's call.

    python scripts/retail/window_geometry/measure.py --install DIR --build build.json --dpr 1 --launch a \
        --resolution 1920x1080 --out DIR [--mode devPixelsPerPx]

The launch is `firefox.exe -no-remote -profile <fresh empty directory> -marionette <page url>`: a profile whose
only file is a user.js with `marionette.port` (and, for the derived path, `layout.css.devPixelsPerPx`), nothing
in distribution/policies.json, and no MOZ_* variable but MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1, without which Marionette refuses
the chrome context. Marionette is the one difference from retail-capture.yml; why it is allowed is in the header of
.github/workflows/retail-window-geometry.yml.

Steps (plan.py says which launch takes which), the six core steps each with the same fields (what the page reads, what the
chrome reads, what Windows says about the top-level window, and a real mouse move over the content): `maximized`;
`restored`, produced by the line of juggler/TargetRegistry.js (setViewportSize) that calls window.resizeBy; `juggler`,
Juggler's whole sequence (styles on the browser and its stack, then that resizeBy) at 390x844 CSS; `juggler_rdm`, the
same window with inRDMPane = true; `juggler2`, a second call in a row (inRDMPane back to false, the styles kept) at
414 x the largest height <= 896 that fits (50 less when that is the first call's height); `juggler_back`, Juggler's
sequence with the content size of the maximized window (is it maximized again, or restored at that size?). Then, after clearing what Juggler's sequence set: `grid` (restored windows at
chrome inner sizes W x H, one compact row each), `minimum` (the smallest window Firefox gives) and `grid_check` (a few grid
points again); and, last, `rdm_hostgate` (the core's collection page host-gate-2.html loaded in the tab under Juggler's
sequence, without and then with inRDMPane, each result kept in its own file next to raw.json). Writes OUT/raw.json. The result is valid only when no process of the install folder is left.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
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
import run_retail_capture as launcher  # noqa: E402
import capture_server  # noqa: E402
from native import INPUT, MONITORINFO, POINT, RECT, WINDOWPLACEMENT, api, guarded  # noqa: E402

FORMAT = "retail-window-geometry/1"
PAGE = HERE / "page.html"
#: The core's collection page, served by scripts/retail/capture_server.py (the page POSTs its result to it).
HOSTGATE_PAGE = HERE.parents[2] / "captures" / "pages" / "host-gate-2.html"
HOSTGATE_TIMEOUT = 120.0
#: The size Juggler is asked for, in CSS pixels, and the fallback height when the work area is too small for it.
RESIZE_WIDTH, RESIZE_HEIGHT, FALLBACK_HEIGHT, FRAME_ALLOWANCE_CSS = 390, 844, 400, 200
HOLD_SECONDS, SETTLE_TIMEOUT, POLL_SECONDS = 1.0, 20.0, 0.25
#: Where the mouse goes, as fractions of the content area.
MOUSE_FRACTIONS = ((0.25, 0.3), (0.5, 0.5), (0.75, 0.7))
#: The one variable the launch sets: Marionette refuses chrome-context script without it (Firefox 159).
MARIONETTE_ENV = {"MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}
CLASS_NAME = "MozillaWindowClass"
DWMWA_EXTENDED_FRAME_BOUNDS, GWL_STYLE, GWL_EXSTYLE, MONITOR_DEFAULTTONEAREST = 9, -16, -20, 2
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x1, 0x8000, 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

#: Juggler's line (juggler/TargetRegistry.js, setViewportSize), with `width` and `height` as arguments.
JUGGLER_RESIZE = (
    "const width = arguments[0], height = arguments[1];\n"
    "const stackRect = gBrowser.selectedBrowser.closest('.browserStack').getBoundingClientRect(); "
    "const toolbarTop = stackRect.y; "
    "window.resizeBy(width - window.innerWidth, height + toolbarTop - window.innerHeight);\n"
    "return true;")
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
#: Juggler's whole sequence, juggler/TargetRegistry.js (setViewportSize) lines 999-1018 as they are, `this` being the
#: tab and `width` / `height` the arguments, in chrome context on the selected browser. Left out: the stealth guard and
#: `inRDMPane = true` (the step `juggler_rdm` sets it) and the wait for the content size (the capture waits itself).
JUGGLER_SEQUENCE = (
    "const width = arguments[0], height = arguments[1];\n"
    "(function () {\n"
    "this._linkedBrowser.style.setProperty('width', width + 'px');\n"
    "this._linkedBrowser.style.setProperty('height', height + 'px');\n"
    "this._linkedBrowser.style.setProperty('box-sizing', 'content-box');\n"
    "this._linkedBrowser.closest('.browserStack').style.setProperty('overflow', 'auto');\n"
    "this._linkedBrowser.closest('.browserStack').style.setProperty('contain', 'size');\n"
    "this._linkedBrowser.closest('.browserStack').style.setProperty('scrollbar-width', 'none');\n"
    "const stackRect = this._linkedBrowser.closest('.browserStack').getBoundingClientRect();\n"
    "const toolbarTop = stackRect.y;\n"
    "this._window.resizeBy(width - this._window.innerWidth, height + toolbarTop - this._window.innerHeight);\n"
    "}).call({_linkedBrowser: gBrowser.selectedBrowser, _window: window});\n"
    "return true;")
#: What the grid and the minimum start from: every style the sequence set removed, inRDMPane back to false.
JUGGLER_CLEAR = (
    "const browser = gBrowser.selectedBrowser, stack = browser.closest('.browserStack');\n"
    "for (const name of ['width', 'height', 'box-sizing']) browser.style.removeProperty(name);\n"
    "for (const name of ['overflow', 'contain', 'scrollbar-width']) stack.style.removeProperty(name);\n"
    "browser.browsingContext.inRDMPane = false;\n"
    "return true;")
RDM_ON = "gBrowser.selectedBrowser.browsingContext.inRDMPane = true; return true;"
RDM_OFF = "gBrowser.selectedBrowser.browsingContext.inRDMPane = false; return true;"
RDM_STATE = "return gBrowser.selectedBrowser.browsingContext.inRDMPane;"
#: Load a URL in the selected tab from the chrome (it does not wait for the page, which may never finish).
LOAD_IN_TAB = ("gBrowser.selectedBrowser.fixupAndLoadURIString(arguments[0], "
               "{triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal()}); return true;")
LOAD_IN_CONTENT = "window.location.assign(arguments[0]); return true;"
#: Chrome inner size to W x H CSS (a restored window): resizeBy by the difference.
RESIZE_CHROME_INNER = ("const w = arguments[0], h = arguments[1]; window.resizeBy(w - window.innerWidth, h - window.innerHeight); "
                       "return true;")
FRAME_CSS = "return [window.outerWidth - window.innerWidth, window.outerHeight - window.innerHeight];"
CHROME_ROW = """
const s = gBrowser.selectedBrowser.closest('.browserStack').getBoundingClientRect();
return {windowState: window.windowState, sizemode: document.documentElement.getAttribute('sizemode'),
  innerWidth: window.innerWidth, innerHeight: window.innerHeight, outerWidth: window.outerWidth,
  outerHeight: window.outerHeight, devicePixelRatio: window.devicePixelRatio,
  stack: {x: s.x, y: s.y, width: s.width, height: s.height}};"""
CONTENT_ROW = ("return {innerWidth: window.innerWidth, innerHeight: window.innerHeight, outerWidth: window.outerWidth, "
               "outerHeight: window.outerHeight};")
#: Settling of the grid points: held 1 s, at most 10 s.
GRID_TIMEOUT = 10.0
RDM_WAIT_SECONDS = 0.5
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
    """Serves page.html on 127.0.0.1 and nothing else."""

    def __init__(self, page: Path = PAGE) -> None:
        body = page.read_bytes()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                ok = self.path.split("?")[0] == "/"
                self.send_response(200 if ok else 404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body) if ok else 0))
                self.end_headers()
                if ok:
                    self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


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


def find_window(pids: Sequence[int]) -> Optional[int]:
    """The visible top-level window of class MozillaWindowClass owned by one of `pids`."""
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


# --------------------------------------------------------------------------- the steps


class Session:
    """One running browser: its Marionette client and the install folder whose processes are its own."""

    def __init__(self, client: marionette.Client, install_dir: Path, table: launcher.ProcessTable) -> None:
        self.client, self.install_dir, self.table = client, install_dir, table

    def chrome(self, script: str, args: Optional[list] = None) -> Any:
        return self.client.execute_script(script, args, context="chrome")

    def content(self, script: str, args: Optional[list] = None) -> Any:
        return self.client.execute_script(script, args, context="content")

    def hwnd(self) -> int:
        hwnd = find_window(self.table.under(self.install_dir))
        if hwnd is None:
            raise MeasureError("no visible MozillaWindowClass window of the launched firefox")
        return hwnd

    def record(self) -> Dict[str, Any]:
        """The full record of the window as it is now, then a real mouse move over the content and what the page saw."""
        chrome = self.chrome(CHROME_STATE)
        hwnd = self.hwnd()
        native = guarded(lambda: window_record(hwnd))
        api().user32.SetForegroundWindow(hwnd)
        self.content(RESET_EVENTS)
        moves = move_mouse(mouse_points(chrome, native))
        self.client.execute_async_script(TWO_FRAMES, context="content")
        return {"chrome": chrome, "native": native, "input": moves, "content": self.content(CONTENT_STATE)}

    def maximized(self) -> Dict[str, Any]:
        self.chrome("window.maximize(); return true;")

        def read() -> Any:
            return [self.chrome(CHROME_SIZE), self.content(CONTENT_SIZE)]

        _, settled = wait_stable(read, ready=lambda v: v[0][0] is True)
        return dict(self.record(), settled=settled)

    def restored(self, work_rect: Sequence[int], scale: float) -> Dict[str, Any]:
        """Juggler's resize. 390x844 CSS of content when the work area holds it plus a frame, else 390x400."""
        height = RESIZE_HEIGHT if (work_rect[3] - work_rect[1]) / scale >= RESIZE_HEIGHT + FRAME_ALLOWANCE_CSS else FALLBACK_HEIGHT
        self.chrome(JUGGLER_RESIZE, [RESIZE_WIDTH, height])
        _, settled = wait_stable(lambda: self.content("return [window.innerWidth, window.innerHeight];"))
        return dict(self.record(), settled=settled, size_used={"width": RESIZE_WIDTH, "height": height})

    def juggler(self, work_rect: Sequence[int], scale: float) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """(step juggler, step juggler_rdm, size used): Juggler's whole sequence at 390 x height CSS (844, or the largest
        height the work area holds, rounded down to a multiple of 10), then the same window with inRDMPane = true."""
        height = plan.juggler_height(work_rect, scale)
        size = {"width": plan.JUGGLER_WIDTH, "height": height, "asked_height": plan.JUGGLER_HEIGHT,
                "does_not_fit": height != plan.JUGGLER_HEIGHT}
        if height is None:
            skipped = {"skipped": "does_not_fit"}
            return dict(skipped), dict(skipped), size

        first = self.juggler_call(plan.JUGGLER_WIDTH, height)
        self.chrome(RDM_ON)
        self.client.execute_async_script(TWO_FRAMES, context="chrome")
        time.sleep(RDM_WAIT_SECONDS)
        _, settled = wait_stable(self.content_size)
        return first, dict(self.record(), settled=settled), size

    def content_size(self) -> Any:
        return self.content("return [window.innerWidth, window.innerHeight];")

    def juggler_call(self, width: int, height: int) -> Dict[str, Any]:
        """One call of Juggler's whole sequence (as it is, on whatever the window and the styles are now), then the full
        record of the window once the content size holds still."""
        self.chrome(JUGGLER_SEQUENCE, [width, height])
        _, settled = wait_stable(self.content_size)
        return dict(self.record(), settled=settled)

    def juggler_again(self, work_rect: Sequence[int], scale: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """(step juggler2, size used): the second call in a row, right after `juggler_rdm` with inRDMPane put back to false
        and the styles of the first call kept (Juggler does not clear them between calls): 414 x plan.juggler2_height."""
        height = plan.juggler2_height(work_rect, scale)
        size = {"width": plan.JUGGLER2_WIDTH, "height": height, "max_height": plan.JUGGLER2_MAX_HEIGHT}
        self.chrome(RDM_OFF)
        if height is None:
            return {"skipped": "does_not_fit"}, size
        return self.juggler_call(plan.JUGGLER2_WIDTH, height), size

    def juggler_back(self, maximized: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """(step juggler_back, size used): Juggler's sequence with the content size the maximized window gave the page
        (its innerWidth x innerHeight in step `maximized`), to see whether the window goes back to maximized or stays
        restored at that size. Adds `window_state` (the chrome's windowState, sizemode, maximized flag)."""
        content = maximized["content"]
        size = {"width": content["innerWidth"], "height": content["innerHeight"], "from_step": "maximized"}
        step = self.juggler_call(size["width"], size["height"])
        chrome = step["chrome"]
        step["window_state"] = {"windowState": chrome.get("windowState"), "sizemode": chrome.get("sizemode"),
                                "maximized": chrome.get("maximized")}
        return step, size

    def load(self, url: str) -> None:
        """Navigate the selected tab without waiting for the page: from the chrome, else from the page."""
        try:
            self.chrome(LOAD_IN_TAB, [url])
        except Exception:  # noqa: BLE001 - the page's own navigation is the other way in
            self.content(LOAD_IN_CONTENT, [url])

    def rdm_hostgate(self, height: Optional[int], page: Path = HOSTGATE_PAGE, timeout: float = HOSTGATE_TIMEOUT,
                     clock: Callable[[], float] = time.monotonic) -> Tuple[Dict[str, Any], Dict[str, bytes]]:
        """Juggler's sequence again (390 x height), then host-gate-2.html loaded without inRDMPane and then with it. Waits for
        the page's POST (`timeout` s). Returns (the step: per run whether it posted, the seconds, the size and sha256 of the
        result; the result files by name, kept next to raw.json and not in it)."""
        if height is None:
            return {"skipped": "does_not_fit"}, {}
        step: Dict[str, Any] = {"juggler_height": height, "timeout_seconds": timeout}
        files: Dict[str, bytes] = {}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            http, tls, recorder = capture_server.start(page, Path(tmp))
            try:
                self.chrome(JUGGLER_SEQUENCE, [plan.JUGGLER_WIDTH, height])
                wait_stable(lambda: self.content("return [window.innerWidth, window.innerHeight];"))
                for rdm in (False, True):
                    label = "rdm" + str(rdm).lower()
                    self.chrome(RDM_ON if rdm else RDM_OFF)
                    recorder.result_event.clear()
                    started = clock()
                    self.load(f"http://127.0.0.1:{http.server_address[1]}/?{label}")
                    posted = recorder.result_event.wait(timeout)
                    entry: Dict[str, Any] = {"posted": posted, "seconds": round(clock() - started, 1), "bytes": 0, "sha256": None,
                                             "inRDMPane": guarded(lambda: self.chrome(RDM_STATE))}
                    result = Path(tmp) / f"{label}.json"
                    if posted and result.is_file():
                        data = result.read_bytes()
                        entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                        files[f"hostgate-{label[:3]}-{label[3:]}.json"] = data
                    step[f"{label[:3]}_{label[3:]}"] = entry
            finally:
                capture_server.stop(http, tls)
        return step, files

    def clear_juggler(self) -> None:
        """Remove every style Juggler's sequence set and put inRDMPane back to false."""
        self.chrome(JUGGLER_CLEAR)

    def inner_row(self, width: int, height: int) -> Dict[str, Any]:
        """Resize the chrome's inner size to width x height CSS, wait until it holds still (1 s hold, 10 s at most), and
        return one compact row: what the chrome, the page and Windows say."""
        self.chrome(RESIZE_CHROME_INNER, [width, height])

        def read() -> Any:
            chrome = self.chrome(CHROME_ROW)
            return [chrome["innerWidth"], chrome["innerHeight"], chrome["windowState"], chrome["stack"]]

        _, settled = wait_stable(read, timeout=GRID_TIMEOUT)
        hwnd = self.hwnd()
        native = guarded(lambda: window_record(hwnd))
        keep = ("window_rect", "client_rect", "client_origin")
        return {"w": width, "h": height, "settled": settled, "chrome": self.chrome(CHROME_ROW),
                "page": self.content(CONTENT_ROW),
                "native": {k: native[k] for k in keep} if all(k in native for k in keep) else native}

    def grid(self, points: Sequence[Tuple[int, int]], scale: float, work_rect: Sequence[int]) -> List[Dict[str, Any]]:
        """One row per point; a point whose outer size would not fit the work area is a row that says it was skipped."""
        frame = self.chrome(FRAME_CSS)
        rows: List[Dict[str, Any]] = []
        failures = 0
        for width, height in points:
            if not plan.fits(width, height, frame, scale, work_rect):
                rows.append({"w": width, "h": height, "skipped": "does_not_fit", "frame_css": frame})
                continue
            try:
                rows.append(self.inner_row(width, height))
                failures = 0
            except Exception as exc:  # noqa: BLE001 - the row says what failed; the browser being gone ends the grid
                rows.append({"w": width, "h": height, "error": f"{type(exc).__name__}: {exc}"})
                failures += 1
                if failures >= 3:
                    raise
        return rows


def measure(install_dir: Path, dpr: float, launch: str, resolution: Sequence[int], display_result: Dict[str, Any],
            build: Dict[str, Any], mode: str = "display", prefs: Optional[Dict[str, Any]] = None,
            table: Optional[launcher.ProcessTable] = None,
            steps_wanted: Sequence[str] = plan.CORE_STEPS) -> Dict[str, Any]:
    """One launch, measured. Returns the raw record; it never raises for a failure of the browser (`error` says it).
    `maximized` is always taken first (it gives the work area); the other steps are those of `steps_wanted`."""
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
    server, process, client = PageServer(), None, marionette.Client(marionette_port)
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
            session = Session(client, install_dir, table)
            steps = raw["steps"]
            steps["maximized"] = session.maximized()
            native = steps["maximized"]["native"]
            scale = steps["maximized"]["chrome"]["devicePixelRatio"]
            work = native.get("work_rect") or [0, 0, 0, 0]
            # Juggler's sequence runs on the maximized window, as setViewportSize does on a fresh page: the
            # toolbar height it reads (toolbarTop) is the maximized window's. Run 45672953042 ran it after the
            # restored step, from a compact window, and measured that route instead.
            if "juggler" in steps_wanted:
                steps["juggler"], steps["juggler_rdm"], raw["juggler_size_used"] = session.juggler(work, scale)
                if "juggler2" in steps_wanted:
                    steps["juggler2"], raw["juggler2_size_used"] = session.juggler_again(work, scale)
                if "juggler_back" in steps_wanted:
                    steps["juggler_back"], raw["juggler_back_size_used"] = session.juggler_back(steps["maximized"])
                session.clear_juggler()
            if "restored" in steps_wanted:
                steps["restored"] = session.restored(work, scale)
                raw["restored_size_used"] = steps["restored"].pop("size_used")
            if "grid" in steps_wanted:
                steps["grid"] = session.grid(plan.grid_points(), scale, work)
            if "minimum" in steps_wanted:
                steps["minimum"] = session.inner_row(*plan.MINIMUM_ASKED)
            if "grid_check" in steps_wanted:
                steps["grid_check"] = session.grid(plan.GRID_CHECK_POINTS, scale, work)
            if "rdm_hostgate" in steps_wanted:
                steps["rdm_hostgate"], raw["_attachments"] = session.rdm_hostgate((raw.get("juggler_size_used") or {}).get("height"))
            client.delete_session()
        except Exception as exc:  # noqa: BLE001 - the record says what failed
            raw["error"] = f"{type(exc).__name__}: {exc}"
            raw["traceback"] = traceback.format_exc()[-2000:]
        finally:
            client.close()
            left = launcher.end_all(table, install_dir, getattr(process, "pid", None))
            server.close()
    raw["processes_left"] = len(left)
    return raw


def write_raw(raw: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = dict(raw)
    for name, data in (raw.pop("_attachments", None) or {}).items():  # files kept beside raw.json, not in it
        (out_dir / name).write_bytes(data)
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
