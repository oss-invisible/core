"""What each launch of the window-geometry capture measures, and which points of the grids fit (pure: no browser, no Windows).

Round 2 of the capture, per job (one display scale):

  launch a (first reached resolution)  maximized, restored, juggler, juggler_rdm, and at the scales of GRID_DPRS also
                                       `grid` (restored windows at GRID_WIDTHS x GRID_HEIGHTS) and `minimum`
  launch b (same resolution)           the same four steps, and at GRID_DPRS `grid_check` (GRID_CHECK_POINTS again)
  launch m (every resolution Windows   `maximized` only: the maximized UI height as a function of the work area
            offers, at the job's scale)
  launch p (and q when no display      the four steps with layout.css.devPixelsPerPx = the scale, at 100% display scale
            launch could be made)
  launch m with devPixelsPerPx 0.75    `maximized` only, in the job of scale 1 (CSS screens 2560x1440 and 3840x2160)
  and 0.5
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

#: The four steps every a, b, p and q launch takes (the determinism check compares exactly these).
CORE_STEPS = ("maximized", "restored", "juggler", "juggler_rdm")
#: Steps that are not part of the a/b determinism check.
GRID_STEPS = ("grid", "minimum", "grid_check")
MAXIMIZED_ONLY = ("maximized",)

GRID_DPRS = (1.0, 1.25)
GRID_WIDTHS = (500, 640, 800, 1000, 1100, 1200, 1280, 1300, 1366, 1400, 1440, 1500, 1536, 1600, 1700, 1800, 1900)
GRID_HEIGHTS = (450, 500, 600, 650, 672, 700, 720, 750, 768, 800, 816, 850, 864, 900, 950, 1000)
#: Launch b repeats these points of launch a's grid; the judge wants every one equal to launch a's row.
GRID_CHECK_POINTS = ((500, 450), (1280, 672), (1366, 768), (1536, 816), (1900, 1000), (800, 600))
#: The chrome inner size asked to find the minimum window.
MINIMUM_ASKED = (100, 100)

#: devPixelsPerPx of the maximized-only launches of the job of scale 1: CSS screens 2560x1440 and 3840x2160 at 1920x1080.
DEVPX_VALUES = (0.75, 0.5)
#: Launch letters the request may not name: derived (p, q) and the every-resolution launch (m).
RESERVED_LAUNCHES = ("p", "q", "m")

#: Juggler's size in CSS pixels. The frame allowance is what a restored window needs above and around the content
#: (chrome 73 CSS at most 85, plus the borders) with some slack; a smaller work area gets the largest height that fits.
JUGGLER_WIDTH, JUGGLER_HEIGHT = 390, 844
JUGGLER_FRAME_CSS, JUGGLER_MIN_HEIGHT = 120, 200


def grid_points() -> List[Tuple[int, int]]:
    return [(w, h) for w in GRID_WIDTHS for h in GRID_HEIGHTS]


def fits(width: int, height: int, frame_css: Sequence[float], scale: float, work_rect: Sequence[int]) -> bool:
    """True when a window whose chrome inner size is width x height CSS (outer = inner + frame_css) fits the work area."""
    outer_w, outer_h = (width + frame_css[0]) * scale, (height + frame_css[1]) * scale
    return outer_w <= work_rect[2] - work_rect[0] and outer_h <= work_rect[3] - work_rect[1]


def juggler_height(work_rect: Sequence[int], scale: float) -> Optional[int]:
    """844 when the work area holds it plus the frame; else the largest height that fits, rounded down to a multiple of
    10; None when even JUGGLER_MIN_HEIGHT does not fit (the step is then skipped and says so)."""
    available = (work_rect[3] - work_rect[1]) / scale - JUGGLER_FRAME_CSS
    if available >= JUGGLER_HEIGHT:
        return JUGGLER_HEIGHT
    height = int(math.floor(available / 10.0)) * 10
    return height if height >= JUGGLER_MIN_HEIGHT else None


def steps_for(dpr: float, launch: str, launches: Sequence[str], first_reached: bool) -> List[str]:
    """The steps of a display launch: the four, and the grid steps on the first launch(es) of the first reached resolution."""
    steps = list(CORE_STEPS)
    if first_reached and float(dpr) in GRID_DPRS:
        if launch == launches[0]:
            steps += ["grid", "minimum"]
        elif len(launches) > 1 and launch == launches[1]:
            steps += ["grid_check"]
    return steps


def devpx_label(value: float) -> str:
    return "%g" % value
