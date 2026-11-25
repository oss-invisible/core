"""The chrome height above the page on Firefox 159 is a function of the UI
density and the scale; the maximized height declared to the engine stays the
Firefox 151 one until MAXIMIZED_UI_FROM_DENSITY is switched on."""
import json
from pathlib import Path

import pytest

from invisible_core import constants
from invisible_core._fpforge import generate_profile
from invisible_core.constants import (
    MIN_INNER_CSS,
    WINDOW_FRAME_BY_DPR,
    chrome_height_device,
    declared_maximized_ui,
    density_reference_height,
    ui_density,
)
from invisible_core.prefs import translate_profile_to_prefs

_RUNS = "GitHub runs oss-invisible/core 45657528859, 45672953042, 45679625406"

_SCREEN_TABLE = Path(constants.__file__).parent / "_fpforge" / "data" / "cpt_screen_given_class_tier.json"


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
    assert constants.AUTO_COMPACT_BELOW_CSS == 720


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
    assert frame.restored_position == 4


@pytest.mark.unit
def test_the_smallest_main_window():
    assert MIN_INNER_CSS == (500, 120)


def _persona_screens():
    table = json.loads(_SCREEN_TABLE.read_text(encoding="utf-8"))["table"]
    seen = set()
    for rows in table.values():
        for row in rows:
            v = row["value"]
            seen.add((v["w"], v["h"], v["dpr"]))
    return sorted(seen)


@pytest.mark.unit
@pytest.mark.parametrize("w,h,dpr", _persona_screens())
def test_the_declared_height_is_the_published_one_while_the_gate_is_off(w, h, dpr):
    assert constants.MAXIMIZED_UI_FROM_DENSITY is False
    p = generate_profile(seed=11, pin={"screen.width": w, "screen.height": h, "screen.dpr": dpr})
    screen = p.screen
    assert declared_maximized_ui(screen.frame, screen.css_avail_height) == screen.frame.maximized_ui
    prefs = translate_profile_to_prefs(p, timezone="America/New_York")
    assert prefs["zoom.stealth.screen.maximized_ui"] == screen.frame.maximized_ui


@pytest.mark.unit
def test_the_prefs_of_many_seeds_carry_the_published_maximized_height():
    for seed in range(60):
        p = generate_profile(seed=seed)
        prefs = translate_profile_to_prefs(p, timezone="America/New_York")
        assert prefs["zoom.stealth.screen.maximized_ui"] == p.screen.frame.maximized_ui
        for name in ("ui_normal", "ui_compact", "restored_position"):
            assert "zoom.stealth.screen." + name not in prefs


@pytest.mark.unit
@pytest.mark.parametrize("dpr,avail_css,expected", [
    (1.0, 1032, 85),
    (1.25, 816, 106),
    (1.5, 1032, 127),
    (2.0, 1032, 170),
    (2.0, 516, 146),
    (1.5, 672, 109),     # a 1920x1080 panel at 150%: availHeight 672 CSS
    (1.0, 719, 73),
])
def test_with_the_gate_on_the_declared_height_is_the_density_rule(monkeypatch, dpr, avail_css, expected):
    monkeypatch.setattr(constants, "MAXIMIZED_UI_FROM_DENSITY", True)
    assert declared_maximized_ui(WINDOW_FRAME_BY_DPR[dpr], avail_css) == expected


@pytest.mark.unit
def test_with_the_gate_on_a_generated_profile_declares_the_density_height(monkeypatch):
    monkeypatch.setattr(constants, "MAXIMIZED_UI_FROM_DENSITY", True)
    p = generate_profile(seed=7, pin={"screen.width": 2048, "screen.height": 1280, "screen.dpr": 1.25})
    prefs = translate_profile_to_prefs(p, timezone="America/New_York")
    assert prefs["zoom.stealth.screen.maximized_ui"] == 106
    # The viewport follows the declared height: (1220 - 106) / 1.25.
    from invisible_core import context_geometry
    assert context_geometry(p)["viewport"]["height"] == 891
