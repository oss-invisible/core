"""What each launch of the window-geometry capture measures, and which points of the grids fit (pure: no browser, no Windows).

Round 5 of the capture, per job (one display scale):

  launch a (first reached resolution)  the six CORE_STEPS and the fifteen ROUND5_STEPS (fullscreen, popups, iframes, the
                                       repeated Juggler call), and at the scales of GRID_DPRS also `grid` (restored windows at
                                       GRID_WIDTHS x GRID_HEIGHTS) and `minimum`
  launch b (same resolution)           the same twenty-one steps, and at GRID_DPRS `grid_check` (GRID_CHECK_POINTS again)
  launches a and b at HOSTGATE_DPRS    last, `rdm_hostgate`
  launch m (every resolution Windows   `maximized` only: the maximized UI height as a function of the work area
            offers, at the job's scale)
  launch p (and q when no display      the six CORE_STEPS with layout.css.devPixelsPerPx = the scale, at 100% display scale; the
            launch could be made)      ROUND5_STEPS too when no display launch could be made (p and q are then the pair)
  launch m with devPixelsPerPx 0.75    `maximized` only, in the job of scale 1 (CSS screens 2560x1440 and 3840x2160)
  and 0.5
  launch n (round 7, scale 2 only)      `maximized` and `iframes_max` on the first panel of NORMAL_DENSITY_PANELS that the display
                                       takes at scale 2 and whose work area is tall enough for the normal UI density
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

#: The steps every a, b, p and q launch takes (the determinism check compares exactly these). In the browser they run
#: in this order except `restored`, which runs after the Juggler steps: maximized, juggler, juggler_rdm, (inRDMPane
#: back to false, styles kept) juggler2, juggler_back, (styles cleared) restored.
CORE_STEPS = ("maximized", "restored", "juggler", "juggler_rdm", "juggler2", "juggler_back")
#: The steps round 5 adds to the two launches the judge compares (a and b; p and q where no display launch could be made), in
#: the order they run: element fullscreen (a real click on a button) and F11-style window fullscreen from the maximized window
#: (`fullscreen_max_*`, `f11_max_*`), a same-origin and a cross-site iframe (`iframes_max`), the same two origins under two CSS transforms (`iframes_transformed`), two popups (`popup`, and
#: `popup_pos` with left and top), Juggler's first call again at exactly its size (`juggler_repeat`, right after
#: `juggler_rdm`, inRDMPane back to false), Juggler's second call again at exactly its size (`juggler2_repeat`), the same fullscreen
#: steps from the window Juggler left (`fullscreen_res_*`, `f11_res_*`) and the iframes there (`iframes_juggler`).
#: A step is a full record, or `{"skipped": "does_not_fit"}` (Juggler's size did not fit the work area), or
#: `{"not_possible": <reason>}` (this browser did not allow it); the judge prints the reason.
ROUND5_STEPS = ("fullscreen_max_in", "fullscreen_max_out", "f11_max_in", "f11_max_out", "iframes_max", "iframes_transformed", "popup",
                "popup_pos",
                "juggler_repeat", "juggler2_repeat", "fullscreen_res_in", "fullscreen_res_out", "f11_res_in", "f11_res_out", "iframes_juggler")
#: The steps the a/b (p/q) determinism check compares.
COMPARED_STEPS = CORE_STEPS + ROUND5_STEPS
#: Steps that are not part of the a/b determinism check.
GRID_STEPS = ("grid", "minimum", "grid_check", "rdm_hostgate")
MAXIMIZED_ONLY = ("maximized",)

GRID_DPRS = (1.0, 1.25)
#: Scale of the `rdm_hostgate` step (launches a and b, last): host-gate-2.html in the tab without and then with inRDMPane.
HOSTGATE_DPRS = (1.0,)
GRID_WIDTHS = (500, 640, 800, 1000, 1119, 1120, 1200, 1280, 1366, 1440, 1536, 1600, 1700, 1800, 1900)
GRID_HEIGHTS = (450, 500, 600, 672, 700, 719, 720, 768, 800, 816, 864, 900, 950, 1000)
#: Launch b repeats these points of launch a's grid; the judge wants every one equal to launch a's row.
GRID_CHECK_POINTS = ((500, 450), (1280, 672), (1366, 768), (1536, 816), (1900, 1000), (800, 600))
#: The chrome inner size asked to find the minimum window.
MINIMUM_ASKED = (100, 100)

#: devPixelsPerPx of the maximized-only launches of the job of scale 1: CSS screens 2560x1440 and 3840x2160 at 1920x1080.
DEVPX_VALUES = (0.75, 0.5)
#: Launch letters the request may not name: derived (p, q) and the every-resolution launch (m).
RESERVED_LAUNCHES = ("p", "q", "m", "n")

#: Launch n: the maximized window at a display scale where the work area is tall enough for the NORMAL UI density (every
#: scale-2 panel of the request, 1600x1200 among them, is short in CSS px and gets the compact bar). The scales it runs at, the
#: role it carries in the output, its two steps (the maximized probe with mozInnerScreenX/Y, innerHeight/outer and
#: screen.availHeight; and the frames of `iframes_max` with their early readings; no restored-window step), and the panels asked,
#: in this order: the first one the display sets (read back from EnumDisplaySettings) and at which the scale is in force is
#: the one measured, and a panel whose work area is too short for the normal density is never asked.
NORMAL_DENSITY_DPRS = (2.0,)
NORMAL_DENSITY_ROLE = "normal density at scale 2"
NORMAL_DENSITY_STEPS = ("maximized", "iframes_max")
NORMAL_DENSITY_PANELS = ((3840, 2160), (2560, 1600), (2560, 1440))
#: The taskbar (CSS px at scale 1, invisible_core TASKBAR_PX) and the reference height from which the density is normal
#: (the height at which invisible_core ui_density turns from compact to normal, AUTO_COMPACT_REFERENCE_TABSTRIP_CSS /
#: AUTO_COMPACT_THRESHOLD = 36 / 0.05, browser.js gUIDensity); a test holds both equal to the core's.
TASKBAR_CSS, NORMAL_DENSITY_FROM_CSS = 48, 720
#: Juggler's size in CSS pixels. The frame allowance is what a restored window needs above and around the content
#: (chrome 73 CSS at most 85, plus the borders) with some slack; a smaller work area gets the largest height that fits.
JUGGLER_WIDTH, JUGGLER_HEIGHT = 390, 844
JUGGLER_FRAME_CSS, JUGGLER_MIN_HEIGHT = 120, 200
#: The second Juggler call in a row (step `juggler2`): a phone of another size, 414 wide and at most 896 high, and
#: when it asks for the very height of the first call it is JUGGLER2_GAP lower, so the two calls always differ.
JUGGLER2_WIDTH, JUGGLER2_MAX_HEIGHT, JUGGLER2_GAP = 414, 896, 50


def grid_points() -> List[Tuple[int, int]]:
    return [(w, h) for w in GRID_WIDTHS for h in GRID_HEIGHTS]


def fits(width: int, height: int, frame_css: Sequence[float], scale: float, work_rect: Sequence[int]) -> bool:
    """True when a window whose chrome inner size is width x height CSS (outer = inner + frame_css) fits the work area."""
    outer_w, outer_h = (width + frame_css[0]) * scale, (height + frame_css[1]) * scale
    return outer_w <= work_rect[2] - work_rect[0] and outer_h <= work_rect[3] - work_rect[1]


def juggler_height(work_rect: Sequence[int], scale: float, wanted: int = JUGGLER_HEIGHT) -> Optional[int]:
    """`wanted` (844) when the work area holds it plus the frame; else the largest height that fits, rounded down to a
    multiple of 10; None when even JUGGLER_MIN_HEIGHT does not fit (the step is then skipped and says so)."""
    available = (work_rect[3] - work_rect[1]) / scale - JUGGLER_FRAME_CSS
    if available >= wanted:
        return wanted
    height = int(math.floor(available / 10.0)) * 10
    return height if height >= JUGGLER_MIN_HEIGHT else None


def juggler2_height(work_rect: Sequence[int], scale: float) -> Optional[int]:
    """The height of the second call: the largest <= 896 that fits by the rule of `juggler_height`; when that is the
    first call's height, 50 less; None when the first call's does not fit either (the step is then skipped)."""
    first = juggler_height(work_rect, scale)
    second = juggler_height(work_rect, scale, JUGGLER2_MAX_HEIGHT)
    if first is None or second is None:
        return None
    return first - JUGGLER2_GAP if second == first else second


def work_height_css(panel: Sequence[int], scale: float) -> float:
    """screen.availHeight, in CSS px, of a maximized-window display of this panel: the panel's height less the taskbar
    (TASKBAR_CSS * scale device px), over the scale."""
    return (panel[1] - TASKBAR_CSS * scale) / scale


def qualifies_for_normal_density(panel: Sequence[int], scale: float) -> bool:
    """True when the work area is at least NORMAL_DENSITY_FROM_CSS tall in CSS px (the density rule of Firefox 159)."""
    return work_height_css(panel, scale) >= NORMAL_DENSITY_FROM_CSS


def normal_density_panels(scale: float) -> List[Tuple[int, int]]:
    """The panels of NORMAL_DENSITY_PANELS that qualify at this scale, in the order they are asked (at 2: 3840x2160, 2560x1600;
    2560x1440 leaves 672 CSS px and is not asked)."""
    return [panel for panel in NORMAL_DENSITY_PANELS if qualifies_for_normal_density(panel, scale)]


def derived_steps(display_launches_made: bool) -> List[str]:
    """The steps of a derived launch (p, q): the core steps; the round 5 steps too when p and q are the pair the judge compares."""
    return list(CORE_STEPS if display_launches_made else COMPARED_STEPS)


def steps_for(dpr: float, launch: str, launches: Sequence[str], first_reached: bool) -> List[str]:
    """The steps of a display launch: the core steps, the round 5 steps on the two launches the judge compares (the first two
    of the request, at the first reached resolution), and the grid steps on the first launch(es) of the first reached resolution."""
    steps = list(CORE_STEPS)
    if first_reached and launch in launches[:2]:
        steps += ROUND5_STEPS
    if first_reached and float(dpr) in GRID_DPRS:
        if launch == launches[0]:
            steps += ["grid", "minimum"]
        elif len(launches) > 1 and launch == launches[1]:
            steps += ["grid_check"]
    if first_reached and float(dpr) in HOSTGATE_DPRS and launch in launches[:2]:
        steps.append("rdm_hostgate")  # last: it leaves the tab on another page
    return steps


def devpx_label(value: float) -> str:
    return "%g" % value
