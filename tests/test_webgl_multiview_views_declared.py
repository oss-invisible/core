"""MAX_VIEWS_OVR is declared wherever OVR_multiview2 is.

It was not, so getParameter(ext.MAX_VIEWS_OVR) answered the host's: 4 on
Windows (ANGLE), 6 on Linux (Mesa). The host gate found it on 2026-10-31 as a
difference between hosts at the same seed. Retail Firefox 159 for Windows
answers 4, ANGLE's D3D11 constant.
"""
from __future__ import annotations

import pytest

from invisible_core._webgl_personas import _gpu_pool

pytestmark = pytest.mark.unit

MAX_VIEWS_OVR = 0x9631


def personas():
    """The Windows personas as the core hands them out (the extension lists are the retail table's)."""
    return _gpu_pool()


@pytest.mark.parametrize("i", range(len(personas())))
def test_a_persona_with_multiview_declares_its_views(i):
    prefs = personas()[i]["prefs"]
    params = dict(e.split("|", 1) for e in prefs["zoom.stealth.webgl.int_params"].split(",") if "|" in e)
    if "OVR_multiview2" in prefs.get("zoom.stealth.webgl2.extensions", ""):
        assert params.get(str(MAX_VIEWS_OVR)) == "4"
    else:
        assert str(MAX_VIEWS_OVR) not in params
