"""The chrome height above the page on Firefox 159 is a function of the UI
density and the scale, and the maximized height declared to the engine is the
density rule's value for the persona's work area."""
import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.constants import (
    WINDOW_FRAME_BY_DPR,
    chrome_height_device,
    declared_maximized_ui,
    density_reference_height,
    ui_density,
)
from invisible_core.prefs import translate_profile_to_prefs

_RUNS = "GitHub runs oss-invisible/core 45657528859, 45672953042, 45679625406"

@pytest.mark.unit
@pytest.mark.parametrize("scale,density,expected", [
    (1.0, "normal", 85), (1.25, "normal", 106), (1.5, "normal", 127),
    (1.0, "compact", 73), (1.25, "compact", 91), (1.5, "compact", 109), (2.0, "compact", 146),
    (0.75, "normal", 64), (0.5, "normal", 43),
])
def test_the_chrome_height_matches_the_live_retail_measurements(scale, density, expected):
    got = chrome_height_device(scale, density)
    assert got == expected, "%s density at %s: measured %d on retail 159.0 (%s), got %d" % (
        density, scale, expected, _RUNS, got)


@pytest.mark.unit
def test_the_normal_chrome_height_at_200_percent_is_the_models_value():
    # No live point: the runner offers no 200% screen with availHeight >= 720 CSS.
    assert chrome_height_device(2.0, "normal") == 170


@pytest.mark.unit
def test_an_unknown_density_is_refused():
    with pytest.raises(ValueError):
        chrome_height_device(1.0, "touch")


@pytest.mark.unit
@pytest.mark.parametrize("height,density", [
    (719, "compact"), (719.2, "compact"), (720, "normal"), (1032, "normal"),
])
def test_the_density_rule_switches_below_720_css(height, density):
    assert ui_density(height) == density


@pytest.mark.unit
def test_the_density_reference_height():
    # Maximized: the larger of availHeight and the chrome innerHeight.
    assert density_reference_height(1032, 700, True) == 1032
    assert density_reference_height(600, 700, True) == 700
    # Restored: the chrome innerHeight alone.
    assert density_reference_height(1032, 700, False) == 700
    assert density_reference_height(600, 1000, False) == 1000


@pytest.mark.unit
@pytest.mark.parametrize("scale", sorted(WINDOW_FRAME_BY_DPR))
def test_the_frame_table_is_computed_from_the_function(scale):
    frame = WINDOW_FRAME_BY_DPR[scale]
    assert frame.ui_normal == chrome_height_device(scale, "normal")
    assert frame.ui_compact == chrome_height_device(scale, "compact")


def _declared(w, h, dpr):
    p = generate_profile(seed=11, pin={"screen.width": w, "screen.height": h, "screen.dpr": dpr})
    return p, translate_profile_to_prefs(p, timezone="America/New_York")


@pytest.mark.unit
@pytest.mark.parametrize("w,h,dpr,avail_css,maximized_ui,old,viewport_h", [
    # availHeight 672 CSS is below 720: compact. The Firefox 151 value was 127.
    (1920, 1080, 1.5, 672, 109, 127, 599),
    # availHeight 816 CSS: normal. The Firefox 151 value was 107.
    (1920, 1080, 1.25, 816, 106, 107, 816 - 85),
    # availHeight 1032 CSS: normal. The Firefox 151 value was 169.
    (3840, 2160, 2.0, 1032, 170, 169, 1032 - 85),
])
def test_the_maximized_height_is_the_density_rules_for_the_work_area(
        w, h, dpr, avail_css, maximized_ui, old, viewport_h):
    p, prefs = _declared(w, h, dpr)
    assert p.screen.css_avail_height == avail_css
    got = prefs["zoom.stealth.screen.maximized_ui"]
    assert got == maximized_ui, "%sx%s at %s: maximized_ui %d, the Firefox 151 value was %d" % (
        w, h, dpr, got, old)
    assert got == declared_maximized_ui(p.screen.frame, avail_css)
    # The viewport is the work area minus that chrome, in CSS px.
    vh = p.screen.viewport[1]
    assert vh == viewport_h, "%sx%s at %s: viewport height %d (with the old %d it was %d)" % (
        w, h, dpr, vh, old, p.screen._css(p.screen.avail_device_height - old))


@pytest.mark.unit
@pytest.mark.parametrize("dpr,avail_css,expected", [
    (1.0, 1032, 85), (1.25, 816, 106), (1.5, 1032, 127), (2.0, 1032, 170),
    (1.5, 672, 109), (1.0, 719, 73), (1.0, 720, 85), (2.0, 516, 146),
])
def test_declared_maximized_ui_is_the_density_rule(dpr, avail_css, expected):
    assert declared_maximized_ui(WINDOW_FRAME_BY_DPR[dpr], avail_css) == expected

@pytest.mark.unit
@pytest.mark.parametrize("w,h,dpr", [(1920, 1080, 1.5), (3840, 2160, 2.0), (1920, 1080, 1.0)])
def test_only_the_maximized_bar_is_declared_to_the_engine(w, h, dpr):
    p, prefs = _declared(w, h, dpr)
    got = prefs["zoom.stealth.screen.maximized_ui"]
    assert got == declared_maximized_ui(p.screen.frame, p.screen.css_avail_height) and type(got) is int
    for gone in ("ui_normal", "ui_compact", "compact_below_css", "restored_position",
                 "min_inner_width_css", "min_inner_height_css"):
        assert "zoom.stealth.screen." + gone not in prefs, gone
