"""A popup's client top is what retail Firefox 159.0 on Windows 11 measures.

popup_client_top is the device px from a popup window's top, border included,
to the top of its client area (client_origin_y minus window_top of a window.open
popup). The retail capture, round 5 (GitHub run oss-invisible/core 45952171443,
catalogued in the private RetailCaptures repo at commit 4477c39,
captures/WINDOW_GEOMETRY_POPUP_FRAME.csv, launches a and b identical, in both
placements) reads 31 / 38 / 45 / 58 at scales 1 / 1.25 / 1.5 / 2. The core
cannot read the private repo at test time, so the numbers are constants here;
the CSV is their source.

Known-bad before: the field and the pref did not exist.
"""
import pytest

from invisible_core.constants import WINDOW_FRAME_BY_DPR
from invisible_core._fpforge import generate_profile
from invisible_core.prefs import translate_profile_to_prefs

MEASURED_POPUP_CLIENT_TOP = {1.0: 31, 1.25: 38, 1.5: 45, 2.0: 58}


@pytest.mark.unit
@pytest.mark.parametrize("scale", sorted(MEASURED_POPUP_CLIENT_TOP))
def test_the_declared_popup_client_top_is_the_retail_159_measurement(scale):
    frame = WINDOW_FRAME_BY_DPR[scale]
    assert frame.popup_client_top == MEASURED_POPUP_CLIENT_TOP[scale], (
        "popup_client_top at scale %s is %r; retail Firefox 159.0 measures %d (run 45952171443)"
        % (scale, getattr(frame, "popup_client_top", None), MEASURED_POPUP_CLIENT_TOP[scale]))


@pytest.mark.unit
@pytest.mark.parametrize("scale", sorted(MEASURED_POPUP_CLIENT_TOP))
def test_a_generated_profile_declares_the_measured_popup_client_top(scale):
    p = generate_profile(seed=7, pin={"screen.width": 2048, "screen.height": 1280,
                                      "screen.dpr": scale})
    prefs = translate_profile_to_prefs(p, timezone="America/New_York")
    assert prefs.get("zoom.stealth.screen.popup_client_top") == MEASURED_POPUP_CLIENT_TOP[scale], (
        "zoom.stealth.screen.popup_client_top at %s is %r; retail Firefox 159.0 measures %d"
        % (scale, prefs.get("zoom.stealth.screen.popup_client_top"),
           MEASURED_POPUP_CLIENT_TOP[scale]))
