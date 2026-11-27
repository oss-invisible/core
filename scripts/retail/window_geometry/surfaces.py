"""The pieces of round 5 of the window-geometry capture that need no browser: the scripts, the points the real mouse goes to.

Round 5 adds, to the main window of launches a and b, surfaces besides the page of the window itself:

  * element fullscreen and window (F11-style) fullscreen, from the maximized window;
  * a popup (`window.open` with width and height, and with left and top too) opened by a real click;
  * a same-origin and a cross-site iframe of the main page.

The scripts here run in the page (content context, through `window.wrappedJSObject`: page.js is the page's own script) or in the
chrome. Nothing here starts a browser or calls Windows; measure.py drives them and plan.py names the steps.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

#: Where the real click lands inside a button, as fractions of its rectangle.
CLICK_FRACTION = ((0.5, 0.5),)
#: Where the real mouse goes over an iframe, as fractions of its rectangle (the main page's own three points).
FRAME_FRACTIONS = ((0.25, 0.3), (0.5, 0.5), (0.75, 0.7))
#: Where the real mouse goes over a popup's content: fractions of its CLIENT area, low enough to be under the popup's own bar.
POPUP_FRACTIONS = ((0.25, 0.55), (0.5, 0.7), (0.75, 0.85))

#: `window.open` features of the two popups, and the step each is recorded in.
POPUP_FEATURES = (("popup", "width=500,height=400"), ("popup_pos", "left=100,top=80,width=500,height=400"))

#: Names of the two iframes, in the order they are recorded, and why each is one.
FRAME_NAMES = ("same", "cross")
FRAME_KINDS = {"same": "same-origin (127.0.0.1, the page's own port)",
               "cross": "cross-site (localhost against the page's 127.0.0.1, another port)"}
#: The iframes of step `iframes_transformed`: each origin under each transform, named <origin>_<transform>, run one at a time at
#: the same place (a container fixed at left 10, top 200, 300 x 120 CSS, transform-origin 0 0, holding a 300 x 120 iframe).
TRANSFORMS = (("scale", "scale(1.5)"), ("rotate", "rotate(10deg)"))
TRANSFORMED_NAMES = tuple(f"{origin}_{kind}" for origin in ("cross", "same") for kind, _ in TRANSFORMS)
#: How long to wait, in seconds, for a fullscreen change, a popup or its page, an iframe, a popup to close.
FULLSCREEN_TIMEOUT, POPUP_TIMEOUT, FRAME_TIMEOUT = 15.0, 20.0, 20.0


class StepNotPossible(Exception):
    """A step of round 5 that this browser did not allow; the step is recorded as `not_possible` with this reason."""


# ---------------------------------------------------------------------------- the scripts

#: The rectangle of an element of the main page, CSS pixels from the content's top left (argument: its id).
ELEMENT_RECT = ("const r = document.getElementById(arguments[0]).getBoundingClientRect(); "
                "return {x: r.x, y: r.y, width: r.width, height: r.height};")
#: What the chrome says about fullscreen: window.fullScreen, the window state and the sizes.
FULLSCREEN_CHROME = ("return [window.fullScreen, window.windowState, window.innerWidth, window.innerHeight, "
                     "window.outerWidth, window.outerHeight];")
#: What the page says: is there a fullscreen element, and the viewport.
FULLSCREEN_CONTENT = "return [!!document.fullscreenElement, window.innerWidth, window.innerHeight];"
#: Why the click did not enter fullscreen, when the page's own requestFullscreen was rejected.
FULLSCREEN_ERROR = "return (window.wrappedJSObject || window).__fsError || null;"
#: Leave element fullscreen (the promise is not awaited: the capture waits for the state itself).
EXIT_FULLSCREEN = "const p = document.exitFullscreen(); if (p && p.catch) p.catch(() => {}); return true;"
#: F11-style fullscreen of the window, set from the chrome.
WINDOW_FULLSCREEN_ON = "window.fullScreen = true; return true;"
WINDOW_FULLSCREEN_OFF = "window.fullScreen = false; return true;"

_PAGE = "const w = window.wrappedJSObject || window; "
_POPUP = _PAGE + "const p = w.__popup; "
#: Store what the click on #open opens (arguments: url, features).
SET_POPUP = _PAGE + "w.__popupUrl = arguments[0]; w.__popupFeatures = arguments[1]; w.__popup = null; return true;"
#: The popup is open, its page is loaded and has page.js's recorder.
POPUP_READY = (_POPUP + "try { return !!(p && !p.closed && p.document && p.document.readyState === 'complete' && p.__m); } "
               "catch (e) { return false; }")
POPUP_SIZE = (_POPUP + "return [p.innerWidth, p.innerHeight, p.outerWidth, p.outerHeight, p.screenX, p.screenY];")
POPUP_RESET = _POPUP + "p.__reset(); return true;"
POPUP_STATE = _POPUP + "return p.__state();"
POPUP_CLOSE = _POPUP + "p.close(); return true;"
POPUP_CLOSED = _POPUP + "return !p || p.closed;"

#: Add the two iframes (arguments: same-origin url, cross-site url) and read what loaded.
ADD_FRAMES = _PAGE + "w.__addFrames(arguments[0], arguments[1]); return true;"
FRAMES_HELLO = _PAGE + "return Object.keys(w.__frameHello).sort();"
FRAME_RECTS = ("const r = (id) => { const b = document.getElementById(id).getBoundingClientRect(); "
               "return {x: b.x, y: b.y, width: b.width, height: b.height}; }; "
               "return {same: r('frame-same'), cross: r('frame-cross')};")
ASK_FRAMES = _PAGE + "w.__askFrames(arguments[0]); return true;"
FRAME_RECORDS = _PAGE + "return JSON.stringify(w.__frameRecords);"
REMOVE_FRAMES = _PAGE + "w.__removeFrames(); return true;"
#: The iframes under a transform: add one (arguments: name, url, transform), the container's geometry, ask it for its record
#: with every mouse move it saw (argument: name), remove it.
ADD_TRANSFORMED = _PAGE + "w.__addTransformed(arguments[0], arguments[1], arguments[2]); return true;"
TRANSFORMED_GEOMETRY = _PAGE + "return w.__transformedGeometry();"
ASK_TRANSFORMED = _PAGE + "w.__askTransformed(arguments[0], arguments[1]); return true;"
REMOVE_TRANSFORMED = _PAGE + "w.__removeTransformed(); return true;"
FRAME_HELLO_OF = _PAGE + "return !!w.__frameHello[arguments[0]];"
FRAME_RECORD_OF = _PAGE + "return JSON.stringify(w.__frameRecords[arguments[0]] || null);"


# ---------------------------------------------------------------------------- the points of the real mouse


def region_points(chrome: Dict[str, Any], native: Dict[str, Any], region: Dict[str, float],
                  fractions: Sequence[Tuple[float, float]]) -> List[Tuple[int, int]]:
    """Screen points (device pixels) inside `region`, a rectangle of the page in CSS pixels from the content's top left: the
    browser's rect in the chrome plus the region, scaled by the window's pixel ratio, from the client origin."""
    ox, oy = native["client_origin"]
    browser, scale = chrome["browser"], chrome["devicePixelRatio"]
    return [(round(ox + (browser["x"] + region["x"] + fx * region["width"]) * scale),
             round(oy + (browser["y"] + region["y"] + fy * region["height"]) * scale)) for fx, fy in fractions]


def transformed_points(chrome: Dict[str, Any], native: Dict[str, Any], geometry: Dict[str, Any],
                       fractions: Sequence[Tuple[float, float]] = FRAME_FRACTIONS) -> List[Dict[str, Any]]:
    """Per fraction of the frame (300 x 120 before the transform): the point in CSS pixels of the page (the container's place
    plus the container's matrix applied to the point, transform-origin 0 0) and the screen point in device pixels (the browser's
    rect in the chrome plus that point, scaled by the window's pixel ratio, from the client origin). The point is inside the
    transformed frame by construction (an affine image of a point of the frame), and `inside_rect` says it is inside the
    container's bounding rectangle from getBoundingClientRect too."""
    a, b, c, d, e, f = geometry["matrix"]
    ox, oy = native["client_origin"]
    browser, scale = chrome["browser"], chrome["devicePixelRatio"]
    rect = geometry["rect"]
    out = []
    for fx, fy in fractions:
        u, v = fx * geometry["width"], fy * geometry["height"]
        x, y = geometry["left"] + a * u + c * v + e, geometry["top"] + b * u + d * v + f
        out.append({"fraction": [fx, fy], "css": [x, y],
                    "inside_rect": rect["x"] <= x <= rect["x"] + rect["width"] and rect["y"] <= y <= rect["y"] + rect["height"],
                    "device": [round(ox + (browser["x"] + x) * scale), round(oy + (browser["y"] + y) * scale)]})
    return out


def client_points(native: Dict[str, Any], fractions: Sequence[Tuple[float, float]] = POPUP_FRACTIONS) -> List[Tuple[int, int]]:
    """Screen points (device pixels) at fractions of a window's client area (Windows' client rect from its client origin)."""
    ox, oy = native["client_origin"]
    left, top, right, bottom = native["client_rect"]
    return [(round(ox + fx * (right - left)), round(oy + fy * (bottom - top))) for fx, fy in fractions]


def frame_urls(same_port: int, cross_port: int) -> Dict[str, str]:
    """The pages of the two iframes: the page's own origin, and `localhost` on the other server (another site: a host
    name against an IP, and another port). The url carries the name the frame answers to."""
    return {"same": f"http://127.0.0.1:{same_port}/surface.html?name=same",
            "cross": f"http://localhost:{cross_port}/surface.html?name=cross"}


def transformed_urls(same_port: int, cross_port: int) -> Dict[str, str]:
    """The pages of the iframes under a transform, by name (<origin>_<transform>): the same two origins as `frame_urls`."""
    base = frame_urls(same_port, cross_port)
    return {name: base[name.split("_")[0]].replace(f"name={name.split('_')[0]}", f"name={name}") for name in TRANSFORMED_NAMES}


def popup_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/surface.html?name=popup"
