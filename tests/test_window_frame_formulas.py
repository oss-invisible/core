"""The window frame is computed from its sources; the retail measurements are its tests.

invisible_core.window_frame computes, at any scale, the resize border and the caption
(Windows non-client metrics, REA of win32kbase / win32kfull 10.0.26100.x), the popup's
toolbar and the main window's bar (Firefox 159 CSS, Gecko's snapping). Nothing is typed
per scale. The expected values below are typed here, from the private RetailCaptures repo
(it cannot be read at test time):

* border 8 / 9 / 11 / 13 and popup client top 31 / 38 / 45 / 58 and popup_ui
  72 / 89 / 106 / 140 at scales 1 / 1.25 / 1.5 / 2: captures/WINDOW_GEOMETRY_POPUP_FRAME.csv,
  round 5, GitHub run oss-invisible/core 45952171443 (rounds 6 and 7 identical);
* the main window's bar, captures/WINDOW_GEOMETRY_INDEX.csv: restored_ui 73 (1.0), 91
  (1.25), 109 (1.5), 146 (2.0) compact and 85 normal at 1.0; maximized_ui minus the border
  115 - 9 = 106 (1.25), 138 - 11 = 127 (1.5), 159 - 13 = 146 (2.0); and
  captures/WINDOW_GEOMETRY_GRID.csv, content_top_device 73 / 85 at 1.0 and 91 / 106 at 1.25
  (runs 45657528859, 45672953042, 45679625406);
* the per-dpi table of analysis/window-geometry/metrics-dpi/README.md (model of the
  disassembly, checked at 96 / 120 / 144 / 192 dpi against the runner's own values).
"""
import pytest

from invisible_core import window_frame as wf
from invisible_core._fpforge import generate_profile
from invisible_core.constants import (
    FRAME_SCALES,
    WINDOW_FRAME_BY_DPR,
    chrome_height_device,
    declared_maximized_ui,
)
from invisible_core.prefs import translate_profile_to_prefs

SCALES = (1.0, 1.25, 1.5, 2.0)

MEASURED_BORDER = {1.0: 8, 1.25: 9, 1.5: 11, 2.0: 13}
MEASURED_POPUP_CLIENT_TOP = {1.0: 31, 1.25: 38, 1.5: 45, 2.0: 58}
MEASURED_POPUP_UI = {1.0: 72, 1.25: 89, 1.5: 106, 2.0: 140}
MEASURED_POPUP_TOOLBAR = {1.0: 41, 1.25: 51, 1.5: 61, 2.0: 82}
MEASURED_BAR = {  # (scale, density) -> device px, client top to content top
    (1.0, "normal"): 85, (1.0, "compact"): 73,
    (1.25, "normal"): 106, (1.25, "compact"): 91,
    (1.5, "normal"): 127, (1.5, "compact"): 109,
    (2.0, "compact"): 146,
}

# dpi -> (SM_CXFRAME, SM_CXPADDEDBORDER, border, SM_CYCAPTION, border + caption):
# metrics-dpi/README.md, "Values for every DPI asked".
METRICS_BY_DPI = {
    96: (4, 4, 8, 23, 31), 108: (4, 5, 9, 26, 35), 120: (4, 5, 9, 29, 38),
    132: (4, 6, 10, 31, 41), 144: (5, 6, 11, 34, 45), 168: (5, 7, 12, 40, 52),
    192: (5, 8, 13, 45, 58), 216: (5, 9, 14, 51, 65), 240: (6, 10, 16, 56, 72),
    264: (6, 11, 17, 62, 79), 288: (6, 12, 18, 67, 85), 312: (6, 13, 19, 73, 92),
    336: (7, 14, 21, 78, 99), 360: (7, 15, 22, 84, 106), 384: (7, 16, 23, 89, 112),
    408: (7, 17, 24, 95, 119), 432: (8, 18, 26, 100, 126), 456: (8, 19, 27, 106, 133),
    480: (8, 20, 28, 111, 139),
}


def _per_term_bar(scale, density):
    """The rule this replaces: tab strip and nav bar each rounded half up on their own, plus
    the border floored to whole device px (a bar of 148 at 1.75, where the browser has 149)."""
    tabstrip, navbar = (44, 40) if density == "normal" else (36, 36)
    return int(tabstrip * scale + 0.5) + int(navbar * scale + 0.5) + max(1, int(scale))


@pytest.mark.unit
def test_the_source_integers_are_the_windows_registry_values():
    assert (wf.REG_BORDER_WIDTH_TWIPS, wf.REG_PADDED_BORDER_WIDTH_TWIPS, wf.REG_CAPTION_HEIGHT_TWIPS) \
        == (-15, -60, -330)
    assert (wf.BORDER_WIDTH_PX96, wf.PADDED_BORDER_WIDTH_PX96, wf.CAPTION_HEIGHT_PX96) == (1, 4, 22)
    assert (wf.TABSTRIP_CSS_NORMAL, wf.TABSTRIP_CSS_COMPACT) == (44, 36)
    assert (wf.NAVBAR_CSS_NORMAL, wf.NAVBAR_CSS_COMPACT) == (40, 36)
    assert wf.CONTENT_BORDER_CSS == 1 and wf.AU_PER_CSS == 60


@pytest.mark.unit
def test_muldiv_rounds_half_away_from_zero():
    # win32kbase!EngMulDiv: (|a*b| + |c|/2) / |c|, sign put back.
    assert wf.muldiv(1, 1, 2) == 1 and wf.muldiv(-1, 1, 2) == -1 and wf.muldiv(1, -1, 2) == -1
    assert wf.muldiv(-1, -1, 2) == 1 and wf.muldiv(1, 1, -2) == -1
    assert wf.muldiv(4, 120, 96) == 5 and wf.muldiv(22, 108, 96) == 25  # 4.99.. is 5; 24.75 is 25


@pytest.mark.unit
@pytest.mark.parametrize("dpi", sorted(METRICS_BY_DPI))
def test_the_frame_metrics_follow_the_metrics_dpi_table(dpi):
    frame, padded, border, cap, top = METRICS_BY_DPI[dpi]
    assert wf.muldiv(wf.BORDER_WIDTH_PX96, dpi, 96) + wf.FRAME_EXTRA_PX == frame
    assert min(max(wf.muldiv(wf.PADDED_BORDER_WIDTH_PX96, dpi, 96), 0), 100) == padded
    assert wf.border(dpi) == border
    assert wf.caption(dpi) == cap
    assert wf.popup_client_top(dpi) == top


@pytest.mark.unit
@pytest.mark.parametrize("scale", SCALES)
def test_the_functions_give_the_retail_159_measurements(scale):
    dpi = wf.dpi_of_scale(scale)
    assert wf.border(dpi) == MEASURED_BORDER[scale]
    assert wf.popup_client_top(dpi) == MEASURED_POPUP_CLIENT_TOP[scale]
    assert wf.popup_toolbar(scale) == MEASURED_POPUP_TOOLBAR[scale]
    assert wf.popup_ui(scale) == MEASURED_POPUP_UI[scale], (
        "popup_ui at %s: retail 159.0 measures %d (run 45952171443)" % (scale, MEASURED_POPUP_UI[scale]))


@pytest.mark.unit
@pytest.mark.parametrize("scale,density", sorted(MEASURED_BAR))
def test_the_bar_is_the_retail_159_measurement_at_every_measured_scale(scale, density):
    assert chrome_height_device(scale, density) == MEASURED_BAR[(scale, density)]


@pytest.mark.unit
def test_the_normal_bar_at_200_percent_is_the_formulas_value_not_a_measurement():
    # No live point (no 200% runner screen with availHeight >= 720 CSS): 88 + 80 + 2 = 170 in
    # the per-term form, 5100 AU / 30 = 170 on the sum.
    assert chrome_height_device(2.0, "normal") == 170


@pytest.mark.unit
def test_the_bar_is_rounded_once_on_the_sum_in_app_units():
    # At 1.75 the app unit is 34 (lround(60 / 1.75)); (84 * 60 + 34) / 34 = 149.2 is 149, where
    # rounding the tab strip (77) and the nav bar (70) on their own gave 77 + 70 + 1 = 148.
    assert wf.apd(1.75) == 34
    assert _per_term_bar(1.75, "normal") == 148
    assert chrome_height_device(1.75, "normal") == 149


@pytest.mark.unit
@pytest.mark.parametrize("scale,density", sorted(MEASURED_BAR))
def test_the_per_term_rule_and_the_sum_rule_agree_where_the_bar_was_measured(scale, density):
    assert _per_term_bar(scale, density) == chrome_height_device(scale, density)


@pytest.mark.unit
@pytest.mark.parametrize("scale,expected", [(1.0, 60), (1.25, 48), (1.5, 40), (1.75, 34), (2.0, 30),
                                            (2.25, 27), (2.5, 24), (3.0, 20), (0.5, 120)])
def test_apd_is_60_over_the_scale_rounded(scale, expected):
    assert wf.apd(scale) == expected


@pytest.mark.unit
@pytest.mark.parametrize("scale,expected", [(1.75, 72), (2.25, 91), (2.5, 102), (3.0, 123)])
def test_the_popup_toolbar_between_the_measured_scales_is_the_popup_frame_model(scale, expected):
    # popup-frame/README.md table (model, not measured): 2434/34 = 71.6, 2454/27 = 90.9.
    assert wf.popup_toolbar(scale) == expected


@pytest.mark.unit
def test_the_frame_table_is_built_from_the_functions_over_the_persona_scales():
    assert tuple(WINDOW_FRAME_BY_DPR) == FRAME_SCALES == SCALES
    for scale, frame in WINDOW_FRAME_BY_DPR.items():
        dpi = wf.dpi_of_scale(scale)
        assert frame.border == wf.border(dpi) == MEASURED_BORDER[scale]
        assert frame.popup_client_top == wf.popup_client_top(dpi) == MEASURED_POPUP_CLIENT_TOP[scale]
        assert frame.popup_ui == wf.popup_ui(scale) == MEASURED_POPUP_UI[scale]
        assert frame.ui_normal == chrome_height_device(scale, "normal")
        assert frame.ui_compact == chrome_height_device(scale, "compact")
        assert frame.popup_position == 4


def _prefs(scale, seed=7, panel=(2048, 1280)):
    p = generate_profile(seed=seed, pin={"screen.width": panel[0], "screen.height": panel[1],
                                         "screen.dpr": scale})
    return p, translate_profile_to_prefs(p, timezone="America/New_York")


@pytest.mark.unit
@pytest.mark.parametrize("seed", [3, 7, 11, 42])
@pytest.mark.parametrize("scale", SCALES)
def test_the_written_prefs_are_the_functions_at_each_scale(scale, seed):
    p, prefs = _prefs(scale, seed)
    dpi = wf.dpi_of_scale(scale)
    assert prefs["zoom.stealth.screen.frame_border"] == wf.border(dpi)
    assert prefs["zoom.stealth.screen.popup_client_top"] == wf.popup_client_top(dpi)
    assert prefs["zoom.stealth.screen.popup_ui"] == wf.popup_ui(scale)
    assert prefs["zoom.stealth.screen.popup_ui"] == (
        prefs["zoom.stealth.screen.popup_client_top"] + wf.popup_toolbar(scale))
    density = "compact" if 36 / p.screen.css_avail_height > 0.05 else "normal"
    assert prefs["zoom.stealth.screen.maximized_ui"] == chrome_height_device(scale, density)
    assert prefs["zoom.stealth.screen.maximized_ui"] == declared_maximized_ui(
        p.screen.frame, p.screen.css_avail_height)
    for name in ("frame_border", "popup_client_top", "popup_ui", "maximized_ui"):
        assert type(prefs["zoom.stealth.screen." + name]) is int


@pytest.mark.unit
@pytest.mark.parametrize("density,tabstrip,navbar", [("normal", 44, 40), ("compact", 36, 36)])
@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0])
def test_the_bar_is_the_retail_offset_of_the_browser_element_rounded_once(scale, density, tabstrip, navbar):
    """Retail's content offset is the <browser> element's offset in the chrome, in app units,
    divided by the app units per device pixel and rounded once (FromAppUnitsRounded:
    floor(x / apd + 0.5)). Built here from the raw CSS app units, independently of window_frame:
    tab strip, nav bar and the content border (1 CSS px floored to whole device px, at least
    one device px) summed in app units first (popup-frame README, "Device pixel snapping")."""
    from fractions import Fraction
    from math import floor

    au_per_dev = floor(60 / scale + 0.5)                       # nsDeviceContext: lround(60 / scale)
    border_au = max(au_per_dev, floor(60 / au_per_dev) * au_per_dev)
    offset_au = tabstrip * 60 + navbar * 60 + border_au
    retail = floor(Fraction(offset_au, au_per_dev) + Fraction(1, 2))
    assert chrome_height_device(scale, density) == retail, (
        "%s bar at %s: retail rounds %d AU / %d once to %d" % (density, scale, offset_au, au_per_dev, retail))


# --- the caption-font term of SM_CYCAPTION -----------------------------------
#
# max(MulDiv(22, dpi, 96), captionFontHeight(dpi) + 2) + 1, with the font of the default
# WindowMetrics CaptionFont (Segoe UI 9 pt). Font values read as data from segoeui.ttf
# "Version 5.72", sha256 704EADA12961A27FD271F7D3B09E2066520E758390C1776744EFF430FEE30F6E:
# unitsPerEm 2048, usWinAscent 2210, usWinDescent 514 (RetailCaptures
# analysis/window-geometry/metrics-dpi/caption-font/). The exact GDI rounding is not
# disassembled, so these tests show the margin: under nearest, ceiling and floor rounding
# of ascent and descent the term never reaches MulDiv(22, dpi, 96).

def _font_term(dpi, rounding):
    ppem = (9 * dpi + 36) // 72
    asc, desc = 2210 * ppem, 514 * ppem
    if rounding == "ceil":
        return -(-asc // 2048) + -(-desc // 2048)
    if rounding == "floor":
        return asc // 2048 + desc // 2048
    return (2 * asc + 2048) // 4096 + (2 * desc + 2048) // 4096


@pytest.mark.unit
def test_the_caption_font_source_integers():
    assert (wf.CAPTION_FONT_PT, wf.SEGOE_UI_UNITS_PER_EM, wf.SEGOE_UI_WIN_ASCENT,
            wf.SEGOE_UI_WIN_DESCENT) == (9, 2048, 2210, 514)
    assert wf.CAPTION_FONT_EXTRA_PX == 2 and wf.CAPTION_EXTRA_PX == 1


@pytest.mark.unit
@pytest.mark.parametrize("scale,expected", [(1.0, 16), (1.25, 20), (1.5, 24), (1.75, 28),
                                            (2.0, 32), (2.25, 36), (2.5, 40), (3.0, 48)])
def test_the_caption_font_height_at_the_scales(scale, expected):
    assert wf.caption_font_height(wf.dpi_of_scale(scale)) == expected


@pytest.mark.unit
@pytest.mark.parametrize("dpi", sorted(METRICS_BY_DPI))
@pytest.mark.parametrize("rounding", ["nearest", "ceil", "floor"])
def test_the_caption_font_term_stays_well_below_the_registry_caption(dpi, rounding):
    base = wf.muldiv(wf.CAPTION_HEIGHT_PX96, dpi, 96)
    margin = base - (_font_term(dpi, rounding) + wf.CAPTION_FONT_EXTRA_PX)
    assert margin >= 3, "dpi %d, %s rounding: the font term is only %d px below %d" % (
        dpi, rounding, margin, base)


@pytest.mark.unit
@pytest.mark.parametrize("dpi", sorted(METRICS_BY_DPI))
def test_the_caption_with_the_font_term_is_the_registry_value_plus_one(dpi):
    assert wf.caption(dpi) == wf.muldiv(22, dpi, 96) + 1


@pytest.mark.unit
@pytest.mark.parametrize("scale", SCALES)
def test_the_caption_with_the_font_term_is_the_measured_one(scale):
    dpi = wf.dpi_of_scale(scale)
    assert wf.caption(dpi) == {1.0: 23, 1.25: 29, 1.5: 34, 2.0: 45}[scale]  # runner frame_metrics_by_dpi
    assert wf.border(dpi) + wf.caption(dpi) == MEASURED_POPUP_CLIENT_TOP[scale]


@pytest.mark.unit
def test_the_font_term_is_real_code_and_binds_at_a_tiny_dpi():
    # Not a dpi any window has: it only shows the clamp is computed, not dropped.
    dpi = 12
    assert wf.caption_font_height(dpi) + 2 > wf.muldiv(22, dpi, 96)
    assert wf.caption(dpi) == wf.caption_font_height(dpi) + 2 + 1
