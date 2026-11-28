"""The window frame of Windows Firefox 159, as formulas.

Every number a page reads from the frame of a window (the resize border, the
caption, the popup's toolbar, the bar above the content of a main window) is
computed here from the integers of its source, at any scale. Nothing is typed
per scale. The retail measurements (RetailCaptures, window-geometry) are the
tests of these functions, never their input.

Two sources, both read from the retail files, never from a host:

* Windows' non-client metrics, from REA of win32kbase / win32kfull
  10.0.26100.x (RetailCaptures analysis/window-geometry/metrics-dpi/README.md and
  its pure-Python model; the 26100.1 build has the same arithmetic).
* Firefox 159's chrome CSS (browser/omni.ja of retail 159.0, the same files in
  the source tree) and Gecko's device-pixel snapping (analysis/window-geometry/
  popup-frame/README.md and its model; chrome-density/README.md).

This module imports only the standard library: the window-geometry judge loads
constants.py from a checkout with nothing installed.
"""
from __future__ import annotations

from math import floor

# --- Windows non-client metrics (GetSystemMetricsForDpi) --------------------
#
# HKCU\Control Panel\Desktop\WindowMetrics as Windows 11 ships them, negative =
# twips (1/1440 inch): BorderWidth -15, PaddedBorderWidth -60, CaptionHeight
# -330 (metrics-dpi README, claim 13; the runner reads 1, 4, 22 at 96 dpi).
REG_BORDER_WIDTH_TWIPS: int = -15
REG_PADDED_BORDER_WIDTH_TWIPS: int = -60
REG_CAPTION_HEIGHT_TWIPS: int = -330

#: Registry twips to pixels at the 96 dpi base: win32kfull!GetTWIPSMetricById
#: (RVA 0xcef8c, win32kfull 10.0.26100.9974), negative v -> (720 - dpi * v) / 1440.
BASE_DPI: int = 96


def _twips_to_px(twips: int, dpi: int = BASE_DPI) -> int:
    return (720 - dpi * twips) // 1440


#: BorderWidth 1, PaddedBorderWidth 4, CaptionHeight 22 device px at 96 dpi.
BORDER_WIDTH_PX96: int = _twips_to_px(REG_BORDER_WIDTH_TWIPS)
PADDED_BORDER_WIDTH_PX96: int = _twips_to_px(REG_PADDED_BORDER_WIDTH_TWIPS)
CAPTION_HEIGHT_PX96: int = _twips_to_px(REG_CAPTION_HEIGHT_TWIPS)

#: SM_CXFRAME = EngMulDiv(BorderWidth, dpi, 96) + 3: the literal +3 of
#: win32kfull!_ScaleSystemMetricForDPIWithoutCache, idx 0x0e / 0x0f, at
#: RVA 0x209737 (function RVA 0x2096cc).
FRAME_EXTRA_PX: int = 3
#: SM_CXPADDEDBORDER = clamp(EngMulDiv(PaddedBorderWidth, dpi, 96), 0, 100):
#: idx 0x1d, RVA 0x2098e1.
PADDED_BORDER_MAX_PX: int = 100
#: SM_CYCAPTION = SM_CYSIZE + 1, the literal +1 of idx 2, RVA 0x20977a. SM_CYSIZE
#: is max(EngMulDiv(CaptionHeight, dpi, 96), captionFontHeight(dpi) + 2), RVA
#: 0x20988d (metrics-dpi README, claims 6 and 11).
CAPTION_EXTRA_PX: int = 1
#: The "+ 2" of the caption-font clamp, same RVA.
CAPTION_FONT_EXTRA_PX: int = 2

# The caption font: WindowMetrics CaptionFont default, Segoe UI 9 pt. Its pixel
# height at a dpi is the GDI tmHeight (tmAscent + tmDescent) of that font at
# lfHeight = -MulDiv(9, dpi, 72) (CreateFont's documented point-to-height rule).
CAPTION_FONT_PT: int = 9
#: Segoe UI metrics read as data from C:/Windows/Fonts/segoeui.ttf, "Version
#: 5.72", sha256 704EADA12961A27FD271F7D3B09E2066520E758390C1776744EFF430FEE30F6E
#: (head.unitsPerEm, OS/2.usWinAscent, OS/2.usWinDescent; RetailCaptures
#: analysis/window-geometry/metrics-dpi/caption-font/). GDI takes tmAscent and
#: tmDescent of a TrueType font from usWinAscent / usWinDescent.
SEGOE_UI_UNITS_PER_EM: int = 2048
SEGOE_UI_WIN_ASCENT: int = 2210
SEGOE_UI_WIN_DESCENT: int = 514


def muldiv(a: int, b: int, c: int) -> int:
    """win32kbase!EngMulDiv (RVA 0x57700): (|a*b| + |c|/2) / |c| with the sign
    put back, that is round half away from zero."""
    negative = (a < 0) ^ (b < 0) ^ (c < 0)
    q = (abs(a) * abs(b) + abs(c) // 2) // abs(c)
    return -q if negative else q


def dpi_of_scale(scale: float) -> int:
    """The window's dpi at a display scale (scale 1 = 96 dpi): 120 at 125%."""
    return int(floor(BASE_DPI * scale + 0.5))


def border(dpi: int) -> int:
    """The resize border: Firefox's DefaultResizeMargins is SM_CXFRAME(dpi) +
    SM_CXPADDEDBORDER(dpi). 8 / 9 / 11 / 13 at 96 / 120 / 144 / 192 dpi."""
    frame = muldiv(BORDER_WIDTH_PX96, dpi, BASE_DPI) + FRAME_EXTRA_PX
    padded = min(max(muldiv(PADDED_BORDER_WIDTH_PX96, dpi, BASE_DPI), 0), PADDED_BORDER_MAX_PX)
    return frame + padded


def caption_font_height(dpi: int) -> int:
    """GDI tmHeight of the default caption font (Segoe UI 9 pt) at a dpi: the
    ascent and the descent each scaled to the em in pixels, ppem =
    MulDiv(9, dpi, 72), and rounded to nearest. The exact GDI rounding is not
    disassembled (confidence: medium); it cannot change SM_CYCAPTION, because
    the clamp term stays at least 3 px below MulDiv(22, dpi, 96) at every dpi
    from 96 to 480 under any of nearest, ceiling or floor
    (tests/test_window_frame_formulas.py)."""
    ppem = muldiv(CAPTION_FONT_PT, dpi, 72)
    ascent = muldiv(SEGOE_UI_WIN_ASCENT, ppem, SEGOE_UI_UNITS_PER_EM)
    descent = muldiv(SEGOE_UI_WIN_DESCENT, ppem, SEGOE_UI_UNITS_PER_EM)
    return ascent + descent


def caption(dpi: int) -> int:
    """SM_CYCAPTION(dpi) = max(MulDiv(22, dpi, 96), captionFontHeight(dpi) + 2)
    + 1: 23 / 29 / 34 / 45 at 96 / 120 / 144 / 192 dpi."""
    sm_cysize = max(muldiv(CAPTION_HEIGHT_PX96, dpi, BASE_DPI),
                    caption_font_height(dpi) + CAPTION_FONT_EXTRA_PX)
    return sm_cysize + CAPTION_EXTRA_PX


def popup_client_top(dpi: int) -> int:
    """From a popup's window top, border included, to the top of its client
    area: the resize border plus the native caption (a popup has no custom
    titlebar, popup-frame README). 31 / 38 / 45 / 58."""
    return border(dpi) + caption(dpi)


# --- Firefox 159 chrome, CSS px at scale 1 ----------------------------------
#
# Read from the retail 159.0 browser/omni.ja (sha256 e079c395...a610; analysis
# RetailCaptures analysis/window-geometry/chrome-density/README.md; the files
# and lines below are inside that omni.ja). The height is a density attribute
# set by script, not a media query: browser.js gUIDensity switches the window to
# "compact" by itself while browser.uidensity has no user value.

#: Tab strip: --tab-min-height (tabs.css L9, L16) + 2 x --tab-margin-block
#: (tab.tokens.css L145, tabs.css L80-86); #tabbrowser-tabs min-height (tabs.css
#: L50, L189). Normal 32 + 2 x 6 = 44, compact 28 + 2 x 4 = 36.
TABSTRIP_CSS_NORMAL: int = 32 + 2 * 6
TABSTRIP_CSS_COMPACT: int = 28 + 2 * 4

#: Nav bar: --urlbar-height (urlbar/variables.css L14, L31) + 2 x
#: --urlbar-padding-block of 4 (urlbar.tokens.css L26, urlbar.css L197).
#: Normal 32 + 2 x 4 = 40, compact 28 + 2 x 4 = 36.
NAVBAR_CSS_NORMAL: int = 32 + 2 * 4
NAVBAR_CSS_COMPACT: int = 28 + 2 * 4

#: .browserContainer border-block-start, --border-width (content-area.css L53,
#: L70; tokens-shared.css L34), snapped as a border (border_au).
CONTENT_BORDER_CSS: int = 1

#: AppUnitsPerCSSPixel() (gfx/src/nsDeviceContext.cpp).
AU_PER_CSS: int = 60


def _lround_div(numerator: int, denominator: int) -> int:
    """lround(numerator / denominator) for positive integers, half up."""
    return (2 * numerator + denominator) // (2 * denominator)


def apd(scale: float) -> int:
    """App units per device pixel, nsDeviceContext::ComputeAppUnitsPerDevPixel
    ForWidgetScale (gfx/src/nsDeviceContext.cpp L52-55): lround(60 / scale)."""
    return max(1, int(floor(AU_PER_CSS / scale + 0.5)))


def border_au(css_px: int, scale: float) -> int:
    """A CSS border in app units after Servo's snap, floored to whole device
    pixels with a minimum of one (servo/components/style/values/specified/
    border.rs L235-246): max(apd, floor(60 * css / apd) * apd)."""
    a = apd(scale)
    return max(a, (css_px * AU_PER_CSS) // a * a)


def _offset_device(box_css: int, scale: float) -> int:
    """Device px from a client top to the content top for boxes of `box_css`
    CSS px plus the content border. Boxes keep their exact app-unit size and the
    offset the page sees is rounded once on the sum
    (BrowserParent::GetChildProcessOffset -> LayoutDeviceIntPoint::
    FromAppUnitsRounded, dom/ipc/BrowserParent.cpp L2725-2759)."""
    return _lround_div(box_css * AU_PER_CSS + border_au(CONTENT_BORDER_CSS, scale), apd(scale))


def popup_toolbar(scale: float) -> int:
    """A popup's toolbar part, client top to content top: the nav bar alone (a
    popup has no tab strip, and never auto-compacts: browser.js L3325-3327).
    41 / 51 / 61 / 82 at 1 / 1.25 / 1.5 / 2."""
    return _offset_device(NAVBAR_CSS_NORMAL, scale)


def popup_ui(scale: float) -> int:
    """From a popup's window top, border included, to its content top:
    72 / 89 / 106 / 140 at 1 / 1.25 / 1.5 / 2."""
    return popup_client_top(dpi_of_scale(scale)) + popup_toolbar(scale)


def chrome_height_device(scale: float, density: str) -> int:
    """The bar above the content of a main window in device px, from the client
    top to the content top, the same for a maximized and a restored window: tab
    strip, nav bar and content border, rounded once on the sum in app units
    (not term by term: at 1.75 that is 149, per term it was 148)."""
    if density == "compact":
        boxes = TABSTRIP_CSS_COMPACT + NAVBAR_CSS_COMPACT
    elif density == "normal":
        boxes = TABSTRIP_CSS_NORMAL + NAVBAR_CSS_NORMAL
    else:
        raise ValueError("density must be 'normal' or 'compact', not %r" % (density,))
    return _offset_device(boxes, scale)


# --- The display scales Windows offers ----------------------------------------
#
#: Percentages of Settings > System > Display > Scale on Windows 11: an array of
#: twelve uint32 values in the .rdata of SettingsHandlers_Display.dll
#: 10.0.26100.8117 (sha256 8162D2F9A2A2AFC43A8AB7369F099873357505B059505D457DD80D77A14072BA,
#: RVA 0x59488) and in three other 26100 System32 files (RetailCaptures
#: analysis/window-geometry/metrics-dpi/scale-list/). The array is the superset
#: Windows can show; its reader was not disassembled. The core accepts exactly
#: these scales and refuses any other (a 133% screen is a machine no Windows
#: draws).
WINDOWS_SCALE_PERCENTS: tuple = (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)
#: The same as scales (1.0 .. 5.0); every one is exact in binary floating point.
WINDOWS_SCALES: tuple = tuple(percent / 100 for percent in WINDOWS_SCALE_PERCENTS)

# --- Which of those scales a screen offers ------------------------------------
#
#: The smallest effective resolution Windows keeps at a scale: the long side at
#: least 800 and the short side at least 600 (win32kfull.sys 10.0.26100.9974
#: DxgkEngQueryWin32InfoPlatform, RVA 0x22f7b0: the scale collection's 0x320 and
#: 0x258; applied by dxgkrnl.sys 10.0.26100.9974 DpiInternal::
#: CalculateMinMaxOverride, RVA 0x5f298, with unsigned truncating division, the
#: same for portrait and landscape). RetailCaptures analysis/window-geometry/
#: metrics-dpi/scale-list/README.md, "Which scales a monitor offers" (proved by
#: disassembly; the 13 resolutions of the GitHub runner agree).
MIN_EFFECTIVE_LONG_SIDE: int = 800
MIN_EFFECTIVE_SHORT_SIDE: int = 600


def max_offered_scale(width_px: int, height_px: int) -> int:
    """The largest scale, in percent, Windows offers on a screen of this native
    resolution in device px (100 when none passes the bar)."""
    long_side, short_side = max(width_px, height_px), min(width_px, height_px)
    passing = [percent for percent in WINDOWS_SCALE_PERCENTS
               if long_side * 100 // percent >= MIN_EFFECTIVE_LONG_SIDE
               and short_side * 100 // percent >= MIN_EFFECTIVE_SHORT_SIDE]
    return max(passing, default=100)


def offered_scales(width_px: int, height_px: int) -> tuple:
    """Every scale, in percent, Windows offers on this screen: the table from
    100 up to max_offered_scale."""
    top = max_offered_scale(width_px, height_px)
    return tuple(percent for percent in WINDOWS_SCALE_PERCENTS if percent <= top)


def offers_scale(scale: float, width_px: int, height_px: int) -> bool:
    """Whether a display scale (1.25 = 125%) is one Windows offers on this screen."""
    return int(floor(scale * 100 + 0.5)) in offered_scales(width_px, height_px)

