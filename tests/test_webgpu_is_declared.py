"""The WebGPU adapter a page sees is declared, never the host's.

Firefox hands a page the features and limits wgpu reports for the real adapter:
D3D12 on Windows, Vulkan on Linux, and on a Linux machine without a GPU a CPU
adapter. Measured 2026-10-31 with firefox-54 on Linux: isFallbackAdapter true,
an extra "clip-distances" and 14 limits away from retail Firefox 159 for Windows.
Retail's values were the same for an RTX 5080, an Intel iGPU and WARP: constants
of the D3D12 backend, declared once. A D3D10-class GPU has no D3D12 adapter.

The engine (dom/webgpu/StealthWebGPUDeclaration.cpp) refuses a limit list that
misses one of its limits; the names are read from its Adapter.cpp here, so a
Firefox that adds a limit fails in this repo first.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import invisible_core
import invisible_core._retail
from _engine_source import EngineSource
from invisible_core.prefs import _apply_codecs

pytestmark = pytest.mark.unit
_SOURCE = EngineSource.pinned()
GPU_POOL = Path(invisible_core.__file__).parent / "_fpforge" / "data" / "webgl_gpu_pool.json"

#: The retail values live once, in the file the core reads (generated from the retail 159
#: capture by scripts/retail/gen_tables.py): this test holds no copy of them.
RETAIL = invisible_core._retail.table("webgpu")
RETAIL_FEATURES = RETAIL["features"]
RETAIL_LIMITS = {name: value for name, value in RETAIL["limits"]}


def prefs_for(renderer):
    codec = SimpleNamespace(av1_enabled=True, webm_encoder_enabled=True,
                            mediasource_webm=True, mediasource_mp4=True)
    prefs = {}
    _apply_codecs(prefs, SimpleNamespace(codec=codec, gpu=SimpleNamespace(renderer=renderer)))
    return prefs


def limits(prefs):
    return {k: int(v) for k, v in (l.split("=") for l in prefs["zoom.stealth.webgpu.limits"].split(chr(10)))}


def windows_renderers():
    pool = json.loads(GPU_POOL.read_text(encoding="utf-8"))
    return sorted({e["prefs"]["zoom.stealth.webgl.renderer"] for e in pool["win"]})


@pytest.mark.parametrize("renderer", windows_renderers())
def test_every_windows_persona_declares_the_adapter_its_gpu_has(renderer):
    prefs = prefs_for(renderer)
    # every Windows persona has Direct3D 12 since the Direct3D 10 class slots left the pool
    assert prefs["zoom.stealth.webgpu.adapter"] == "d3d12"
    assert prefs["zoom.stealth.webgpu.features"].split(chr(10)) == RETAIL_FEATURES
    assert len(limits(prefs)) == 35


def test_the_limits_are_retail_where_linux_differed():
    # Linux answered 14 of the 35 differently on 2026-10-31 (maxStorageBufferBindingSize 134217728
    # against 2147483644, ...): every limit, not a copied selection, is the retail's.
    got = limits(prefs_for("ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0, D3D11)"))
    assert got == RETAIL_LIMITS


def test_no_feature_is_one_retail_windows_does_not_report():
    # clip-distances is the one the Linux build added.
    assert "clip-distances" not in prefs_for("ANGLE (Intel, Intel(R) HD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)")[
        "zoom.stealth.webgpu.features"]


@pytest.mark.skipif(_SOURCE.unavailable() is not None, reason=_SOURCE.unavailable() or "")
def test_the_limit_names_are_the_engines():
    text = _SOURCE.show("dom/webgpu/Adapter.cpp")
    assert text, f"Adapter.cpp not in {_SOURCE.commit}"
    body = text[text.index("static std::string_view ToJsKey(const Limit limit) {"):]
    body = body[:body.index("\n}\n")]
    engine = set(re.findall(r'return "(\w+)";', body))
    assert len(engine) >= 30, f"ToJsKey moved: {sorted(engine)}"
    assert engine == set(limits(prefs_for("x")))


@pytest.mark.skipif(_SOURCE.unavailable() is not None, reason=_SOURCE.unavailable() or "")
def test_the_engine_reads_the_declaration():
    gate = _SOURCE.show("gfx/thebes/StealthDeclarationGate.cpp") or ""
    for pref in ("zoom.stealth.webgpu.adapter", "zoom.stealth.webgpu.features", "zoom.stealth.webgpu.limits"):
        assert f'"{pref}"' in gate, f"{pref} is not in the engine's declaration gate at {_SOURCE.commit}"
