"""What each launch of the window-geometry capture measures (pure: no browser, no Windows).

The capture measures the maximized window and what a page reads in it. The restored-window steps (Juggler's resize, the grid of
restored windows) left with the feature they measured. Per job (one display scale):

  launch a (first reached resolution)  `maximized` and the eight ROUND5_STEPS (element fullscreen and F11-style fullscreen from
                                       the maximized window, the iframes, the iframes under two transforms, two popups)
  launch b (same resolution)           the same nine steps
  launch a at every other accepted     `maximized` only
  resolution
  launch p (and q when no display      `maximized`, with layout.css.devPixelsPerPx = the scale, at 100% display scale; the
            launch could be made)      ROUND5_STEPS too when no display launch could be made (p and q are then the pair)
  launch m (every resolution Windows   `maximized` only: the maximized UI height as a function of the work area
            offers, at the job's scale)
  launch m with devPixelsPerPx 0.75    `maximized` only, in the job of scale 1 (CSS screens 2560x1440 and 3840x2160)
  and 0.5
"""
from __future__ import annotations

from typing import List, Sequence

#: The step every a, b, p and q launch takes (the determinism check compares exactly these).
CORE_STEPS = ("maximized",)
#: The steps round 5 adds to the two launches the judge compares (a and b; p and q where no display launch could be made), in
#: the order they run: element fullscreen (a real click on a button) and F11-style window fullscreen from the maximized window
#: (`fullscreen_max_*`, `f11_max_*`), a same-origin and a cross-site iframe (`iframes_max`), the same two origins under two CSS
#: transforms (`iframes_transformed`), and two popups (`popup`, and `popup_pos` with left and top).
#: A step is a full record, or `{"not_possible": <reason>}` (this browser did not allow it); the judge prints the reason.
ROUND5_STEPS = ("fullscreen_max_in", "fullscreen_max_out", "f11_max_in", "f11_max_out", "iframes_max", "iframes_transformed", "popup",
                "popup_pos")
#: The steps the a/b (p/q) determinism check compares.
COMPARED_STEPS = CORE_STEPS + ROUND5_STEPS
MAXIMIZED_ONLY = ("maximized",)

#: devPixelsPerPx of the maximized-only launches of the job of scale 1: CSS screens 2560x1440 and 3840x2160 at 1920x1080.
DEVPX_VALUES = (0.75, 0.5)
#: Launch letters the request may not name: derived (p, q) and the every-resolution launch (m).
RESERVED_LAUNCHES = ("p", "q", "m")


def derived_steps(display_launches_made: bool) -> List[str]:
    """The steps of a derived launch (p, q): the core step; the round 5 steps too when p and q are the pair the judge compares."""
    return list(CORE_STEPS if display_launches_made else COMPARED_STEPS)


def steps_for(launch: str, launches: Sequence[str], first_reached: bool) -> List[str]:
    """The steps of a display launch: the core step, and the round 5 steps on the two launches the judge compares (the first two
    of the request, at the first reached resolution)."""
    steps = list(CORE_STEPS)
    if first_reached and launch in launches[:2]:
        steps += ROUND5_STEPS
    return steps


def devpx_label(value: float) -> str:
    return "%g" % value
