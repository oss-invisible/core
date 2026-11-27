"""A popup's frame is what retail Firefox 159.0 on Windows 11 measures.

popup_ui is the device px from a popup window's top, border included, to its
content top (window.open 'width=500,height=400'). The retail capture, round 5
(GitHub run oss-invisible/core 45952171443, catalogued in the private
RetailCaptures repo at commit 4477c39, captures/WINDOW_GEOMETRY_POPUP_FRAME.csv,
launches a and b identical) reads 72 / 89 / 106 / 140 at scales 1 / 1.25 / 1.5 /
2, and the default popup position 4 device px on both axes at every scale. The
core cannot read the private repo at test time, so the numbers are constants
here; the CSV is their source.

Known-bad before: 72 / 90 / 106 / 139, a model from Firefox 151.
"""
import pytest

from invisible_core.constants import WINDOW_FRAME_BY_DPR
from invisible_core._fpforge import generate_profile
from invisible_core.prefs import translate_profile_to_prefs

# scale -> popup_ui measured on retail Firefox 159.0 (the
# content_top_minus_window_top column of the popup step in the CSV).
MEASURED_POPUP_UI = {1.0: 72, 1.25: 89, 1.5: 106, 2.0: 140}
MEASURED_POPUP_POSITION = 4
OLD_MODEL_POPUP_UI = {1.0: 72, 1.25: 90, 1.5: 106, 2.0: 139}


@pytest.mark.unit
@pytest.mark.parametrize("scale", sorted(MEASURED_POPUP_UI))
def test_the_declared_popup_ui_is_the_retail_159_measurement(scale):
    frame = WINDOW_FRAME_BY_DPR[scale]
    assert frame.popup_ui == MEASURED_POPUP_UI[scale], (
        "popup_ui at scale %s is %d; retail Firefox 159.0 measures %d (run 45952171443); "
        "the old Firefox 151 model said %d"
        % (scale, frame.popup_ui, MEASURED_POPUP_UI[scale], OLD_MODEL_POPUP_UI[scale]))
    assert frame.popup_position == MEASURED_POPUP_POSITION


@pytest.mark.unit
@pytest.mark.parametrize("scale", [1.25, 2.0])
def test_a_generated_profile_declares_the_measured_popup_ui(scale):
    p = generate_profile(seed=7, pin={"screen.width": 2048, "screen.height": 1280,
                                      "screen.dpr": scale})
    prefs = translate_profile_to_prefs(p, timezone="America/New_York")
    assert prefs["zoom.stealth.screen.popup_ui"] == MEASURED_POPUP_UI[scale], (
        "zoom.stealth.screen.popup_ui at %s is %r; retail Firefox 159.0 measures %d; "
        "the old Firefox 151 model said %d"
        % (scale, prefs["zoom.stealth.screen.popup_ui"], MEASURED_POPUP_UI[scale],
           OLD_MODEL_POPUP_UI[scale]))
    assert prefs["zoom.stealth.screen.popup_position"] == MEASURED_POPUP_POSITION
