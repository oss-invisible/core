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
def test_the_frame_table_is_built_from_the_functions_and_matches_the_measurements():
    assert FRAME_SCALES == SCALES  # what the persona pool samples today
    for scale, frame in WINDOW_FRAME_BY_DPR.items():
        dpi = wf.dpi_of_scale(scale)
        assert frame.border == wf.border(dpi)
        assert frame.popup_client_top == wf.popup_client_top(dpi)
        assert frame.popup_ui == wf.popup_ui(scale)
        assert frame.ui_normal == chrome_height_device(scale, "normal")
        assert frame.ui_compact == chrome_height_device(scale, "compact")
        assert frame.popup_position == 4
    for scale in SCALES:  # the measured ones
        frame = WINDOW_FRAME_BY_DPR[scale]
        assert (frame.border, frame.popup_client_top, frame.popup_ui) == (
            MEASURED_BORDER[scale], MEASURED_POPUP_CLIENT_TOP[scale], MEASURED_POPUP_UI[scale])


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
@pytest.mark.parametrize("scale", wf.WINDOWS_SCALES)
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


# --- the accepted scales: exactly the ones Windows offers ---------------------
#
# The list is read from the 26100 settings binaries (SettingsHandlers_Display.dll
# 10.0.26100.8117, RVA 0x59488; RetailCaptures analysis/window-geometry/metrics-dpi/scale-list/).

@pytest.mark.unit
def test_the_accepted_scales_are_the_ones_windows_offers():
    assert wf.WINDOWS_SCALE_PERCENTS == (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)
    assert wf.WINDOWS_SCALES == (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)
    assert tuple(WINDOW_FRAME_BY_DPR) == wf.WINDOWS_SCALES
    assert set(FRAME_SCALES) <= set(WINDOW_FRAME_BY_DPR)  # the sampled pool is a subset, never a limit
    assert [wf.dpi_of_scale(s) for s in wf.WINDOWS_SCALES] == [
        96, 120, 144, 168, 192, 216, 240, 288, 336, 384, 432, 480]


@pytest.mark.unit
@pytest.mark.parametrize("scale", wf.WINDOWS_SCALES)
def test_every_accepted_scale_has_the_frame_from_the_formulas(scale):
    dpi = wf.dpi_of_scale(scale)
    _, _, border, cap, top = METRICS_BY_DPI[dpi]            # the metrics-dpi table
    frame = WINDOW_FRAME_BY_DPR[scale]
    assert (frame.border, frame.popup_client_top) == (border, top) and wf.caption(dpi) == cap
    assert frame.popup_ui == top + wf.popup_toolbar(scale)
    assert frame.ui_normal == chrome_height_device(scale, "normal")
    assert frame.ui_compact == chrome_height_device(scale, "compact")
    assert frame.ui_compact < frame.ui_normal


@pytest.mark.unit
@pytest.mark.parametrize("scale", wf.WINDOWS_SCALES)
def test_the_popup_toolbar_is_the_nav_bar_and_border_rounded_once_at_every_accepted_scale(scale):
    from fractions import Fraction
    from math import floor

    au_per_dev = floor(60 / scale + 0.5)
    border_au = max(au_per_dev, floor(60 / au_per_dev) * au_per_dev)
    expected = floor(Fraction(40 * 60 + border_au, au_per_dev) + Fraction(1, 2))
    assert wf.popup_toolbar(scale) == expected


@pytest.mark.unit
@pytest.mark.parametrize("scale", wf.WINDOWS_SCALES)
def test_every_accepted_scale_has_prefs_equal_to_the_functions(scale):
    width, height = (3840, 2160) if scale <= 3.5 else (7680, 4320)  # a panel that offers the scale
    p = generate_profile(seed=5, pin={"screen.width": width, "screen.height": height, "screen.dpr": scale})
    prefs = translate_profile_to_prefs(p, timezone="America/New_York")
    dpi = wf.dpi_of_scale(scale)
    assert prefs["zoom.stealth.screen.frame_border"] == wf.border(dpi)
    assert prefs["zoom.stealth.screen.popup_client_top"] == wf.popup_client_top(dpi)
    assert prefs["zoom.stealth.screen.popup_ui"] == wf.popup_ui(scale)
    density = "compact" if 36 / p.screen.css_avail_height > 0.05 else "normal"
    assert prefs["zoom.stealth.screen.maximized_ui"] == chrome_height_device(scale, density)


@pytest.mark.unit
@pytest.mark.parametrize("scale", [1.33, 1.1, 1.8, 0.75, 2.75, 3.25, 5.5])
def test_a_scale_windows_does_not_offer_has_no_frame(scale):
    assert scale not in WINDOW_FRAME_BY_DPR
    p = generate_profile(seed=5, pin={"screen.dpr": scale})
    with pytest.raises(ValueError, match="Windows does not offer"):
        p.screen.frame


# --- which of the scales a screen offers --------------------------------------
#
# Rule: the effective resolution at a scale stays at least 800x600 (long side,
# short side). RetailCaptures analysis/window-geometry/metrics-dpi/scale-list/
# README.md "Which scales a monitor offers" and scripts/scale_model.py (win32kfull
# DxgkEngQueryWin32InfoPlatform RVA 0x22f7b0, dxgkrnl CalculateMinMaxOverride
# RVA 0x5f298, build 26100.9974); its model output is the expectations below.

@pytest.mark.unit
@pytest.mark.parametrize("w,h,top,offered", [
    (1920, 1080, 175, (100, 125, 150, 175)),
    (1600, 1200, 200, (100, 125, 150, 175, 200)),
    (1366, 768, 125, (100, 125)),
    (2560, 1440, 225, (100, 125, 150, 175, 200, 225)),
    (3840, 2160, 350, (100, 125, 150, 175, 200, 225, 250, 300, 350)),
    (1280, 720, 100, (100,)),
    (640, 480, 100, (100,)),
])
def test_the_scales_a_screen_offers_are_the_model_of_the_binaries(w, h, top, offered):
    assert wf.max_offered_scale(w, h) == top
    assert wf.offered_scales(w, h) == offered
    assert wf.max_offered_scale(h, w) == top and wf.offered_scales(h, w) == offered  # portrait


@pytest.mark.unit
@pytest.mark.parametrize("w,h", [(1920, 1080), (1600, 1200), (2560, 1440), (3840, 2160), (1366, 768)])
def test_the_bar_is_800_by_600_effective_pixels(w, h):
    top = wf.max_offered_scale(w, h)
    long_side, short_side = max(w, h), min(w, h)
    assert long_side * 100 // top >= 800 and short_side * 100 // top >= 600
    above = [p for p in wf.WINDOWS_SCALE_PERCENTS if p > top]
    assert all(long_side * 100 // p < 800 or short_side * 100 // p < 600 for p in above)


# The GitHub runner's own records (RetailCaptures captures/firefox-159.0/github-runner/*/*/meta.json,
# display.*.dpi min_rel / max_rel from DisplayConfigGetDeviceInfo type -3, recommended 100%):
# resolution -> highest offered percent, SCALES[max_rel] of the 12-entry table (max_rel 0/0/1/1/0/1/2/1/2/2/4/3/3
# in the order below; scale_model.py's runner check reports 13 records, 0 mismatches).
RUNNER_RESOLUTIONS = {
    (640, 480): 100, (800, 600): 100, (1024, 768): 125, (1152, 864): 125, (1280, 720): 100,
    (1280, 800): 125, (1280, 1024): 150, (1366, 768): 125, (1440, 900): 150, (1600, 900): 150,
    (1600, 1200): 200, (1680, 1050): 175, (1920, 1080): 175,
}


@pytest.mark.unit
@pytest.mark.parametrize("res,top", sorted(RUNNER_RESOLUTIONS.items()))
def test_the_runner_resolutions_give_the_recorded_maximum(res, top):
    assert wf.max_offered_scale(*res) == top


@pytest.mark.unit
@pytest.mark.parametrize("w,h,scale,offered", [
    (1920, 1080, 1.75, True), (1920, 1080, 2.0, False), (1600, 1200, 2.0, True),
    (1366, 768, 1.5, False), (3840, 2160, 3.5, True), (3840, 2160, 4.0, False),
])
def test_a_scale_the_screen_does_not_offer_is_refused(w, h, scale, offered):
    p = generate_profile(seed=7, pin={"screen.width": w, "screen.height": h, "screen.dpr": scale})
    assert wf.offers_scale(scale, w, h) is offered
    if offered:
        assert p.screen.frame == WINDOW_FRAME_BY_DPR[scale]
    else:
        with pytest.raises(ValueError, match="does not offer %d%% on a %dx%d screen" % (scale * 100, w, h)):
            p.screen.frame
        with pytest.raises(ValueError, match="does not offer"):
            translate_profile_to_prefs(p, timezone="America/New_York")


@pytest.mark.unit
def test_the_persona_pool_only_draws_screens_at_scales_windows_offers():
    """Every (screen, scale) of cpt_screen_given_class_tier.json is offered by Windows: the
    refusal never breaks a seed. Known-bad if the table gains e.g. 1920x1080 at 200%."""
    import json
    from importlib import resources

    table = json.loads((resources.files("invisible_core._fpforge") / "data"
                        / "cpt_screen_given_class_tier.json").read_text(encoding="utf-8"))["table"]
    pairs = {(e["value"]["w"], e["value"]["h"], e["value"]["dpr"]) for rows in table.values() for e in rows}
    assert len(pairs) == 15
    not_offered = sorted(c for c in pairs if not wf.offers_scale(c[2], c[0], c[1]))
    assert not_offered == []


@pytest.mark.unit
@pytest.mark.parametrize("seed", range(0, 400, 7))
def test_a_drawn_persona_always_has_a_frame(seed):
    assert generate_profile(seed=seed).screen.frame

