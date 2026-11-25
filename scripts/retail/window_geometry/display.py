"""The display of a runner: its resolution and its per-monitor scale, set for real (ctypes only).

The scale is set with the DisplayConfig device-info types -3 (get: min / current / max, in steps relative to the
recommended scale) and -4 (set), the technique every "SetDPI" tool uses. Windows offers no documented call for it,
so nothing here is trusted: after every change the effective DPI of the primary monitor is read back
(GetDpiForMonitor) and `dpi_reached` says whether the asked scale is the one in force. Nothing here raises for a
scale that cannot be set.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes as w
from typing import Any, Dict, List, Optional, Tuple

from native import DEVMODEW, LUID, LONG, POINT, api, guarded

#: The scales Windows steps through (percent); the recommended one is somewhere in the list.
DPI_VALUES = [100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500]
GET_DPI_SCALE, SET_DPI_SCALE = -3, -4
ENUM_CURRENT_SETTINGS = -1
DM_PELSWIDTH, DM_PELSHEIGHT = 0x80000, 0x100000
QDC_ONLY_ACTIVE_PATHS = 2
MDT_EFFECTIVE_DPI = 0
MONITOR_DEFAULTTOPRIMARY = 1
PER_MONITOR_AWARE_V2 = -4
SETTLE_SECONDS = 3.0


class _Header(ctypes.Structure):
    _fields_ = [("type", LONG), ("size", w.UINT), ("adapterId", LUID), ("id", w.UINT)]


class _ScaleGet(ctypes.Structure):
    _fields_ = [("header", _Header), ("minScaleRel", LONG), ("curScaleRel", LONG), ("maxScaleRel", LONG)]


class _ScaleSet(ctypes.Structure):
    _fields_ = [("header", _Header), ("scaleRel", LONG)]


def percent(dpr: float) -> int:
    return int(round(float(dpr) * 100))


def relative_step(values: List[int], minimum: int, wanted: int) -> Optional[int]:
    """The relative step (to the recommended scale) that gives `wanted` percent, or None when out of range.
    The recommended scale sits at index -minimum of `values`."""
    return values.index(wanted) + minimum if wanted in values else None


def enable_dpi_awareness() -> bool:
    """Make this process per-monitor aware, so every size it reads is in device pixels."""
    return bool(api().user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(PER_MONITOR_AWARE_V2)))


def _mode(a: Any, index: int) -> Optional[DEVMODEW]:
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    return dm if a.user32.EnumDisplaySettingsW(None, index, ctypes.byref(dm)) else None


def current_resolution() -> List[int]:
    dm = _mode(api(), ENUM_CURRENT_SETTINGS)
    return [dm.dmPelsWidth, dm.dmPelsHeight] if dm else []


def offered_resolutions() -> List[List[int]]:
    a, seen, index = api(), set(), 0
    while True:
        dm = _mode(a, index)
        if dm is None:
            return sorted([list(s) for s in seen])
        seen.add((dm.dmPelsWidth, dm.dmPelsHeight))
        index += 1


def set_resolution(width: int, height: int) -> Dict[str, Any]:
    """Try to change the resolution (dynamic: not written to the registry); report what happened."""
    a = api()
    dm = _mode(a, ENUM_CURRENT_SETTINGS) or DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    dm.dmPelsWidth, dm.dmPelsHeight, dm.dmFields = width, height, DM_PELSWIDTH | DM_PELSHEIGHT
    code = a.user32.ChangeDisplaySettingsExW(None, ctypes.byref(dm), None, 0, None)
    time.sleep(SETTLE_SECONDS)
    now = current_resolution()
    return {"requested": [width, height], "code": code, "ok": code == 0 and now == [width, height], "current": now}


def effective_dpi() -> Optional[int]:
    """GetDpiForMonitor(MDT_EFFECTIVE_DPI) of the primary monitor."""
    a = api()
    monitor = a.user32.MonitorFromPoint(POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
    x, y = w.UINT(), w.UINT()
    return x.value if a.shcore.GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, ctypes.byref(x), ctypes.byref(y)) == 0 else None


def _primary_source(a: Any) -> Tuple[LUID, int, int]:
    """(adapter, source id, number of active paths) of the first active path."""
    paths, modes = w.UINT(), w.UINT()
    code = a.user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(paths), ctypes.byref(modes))
    if code or not paths.value:
        raise OSError(f"GetDisplayConfigBufferSizes returned {code} with {paths.value} paths")
    path_buf = (ctypes.c_ubyte * (72 * paths.value))()  # DISPLAYCONFIG_PATH_INFO is 72 bytes
    mode_buf = (ctypes.c_ubyte * (64 * modes.value))()  # DISPLAYCONFIG_MODE_INFO is 64 bytes
    code = a.user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(paths), path_buf, ctypes.byref(modes), mode_buf, None)
    if code:
        raise OSError(f"QueryDisplayConfig returned {code}")
    adapter = LUID.from_buffer_copy(bytes(path_buf[0:8]))  # sourceInfo.adapterId
    return adapter, int.from_bytes(bytes(path_buf[8:12]), "little"), paths.value


def set_dpi_percent(wanted: int) -> Dict[str, Any]:
    """Set the scale of the primary monitor to `wanted` percent. Never raises; the dict says what happened."""
    out: Dict[str, Any] = {"requested_percent": wanted, "supported": False}
    try:
        a = api()
        adapter, source, paths = _primary_source(a)
        out["active_paths"] = paths
        get = _ScaleGet()
        get.header = _Header(GET_DPI_SCALE, ctypes.sizeof(_ScaleGet), adapter, source)
        out["get_code"] = a.user32.DisplayConfigGetDeviceInfo(ctypes.byref(get))
        if out["get_code"]:
            out["reason"] = f"DisplayConfigGetDeviceInfo(-3) returned {out['get_code']}"
            return out
        out.update(supported=True, min_rel=get.minScaleRel, cur_rel=get.curScaleRel, max_rel=get.maxScaleRel,
                   recommended_percent=DPI_VALUES[-get.minScaleRel] if -get.minScaleRel < len(DPI_VALUES) else None)
        step = relative_step(DPI_VALUES, get.minScaleRel, wanted)
        if step is None or step > get.maxScaleRel:
            out["reason"] = f"{wanted}% is outside the steps this monitor offers (relative {get.minScaleRel}..{get.maxScaleRel})"
            return out
        out["set_rel"] = step
        setter = _ScaleSet()
        setter.header = _Header(SET_DPI_SCALE, ctypes.sizeof(_ScaleSet), adapter, source)
        setter.scaleRel = step
        out["set_code"] = a.user32.DisplayConfigSetDeviceInfo(ctypes.byref(setter))
        if out["set_code"]:
            out["reason"] = f"DisplayConfigSetDeviceInfo(-4) returned {out['set_code']}"
    except Exception as exc:  # noqa: BLE001 - recorded, never raised
        out["reason"] = f"{type(exc).__name__}: {exc}"
    time.sleep(SETTLE_SECONDS)
    return out


def apply(dpr: float, resolution: Optional[Tuple[int, int]]) -> Dict[str, Any]:
    """Set the resolution (when given) and then the scale `dpr`, and read the scale back."""
    result: Dict[str, Any] = {"dpr": dpr, "offered_resolutions": guarded(offered_resolutions)}
    if resolution is not None:
        result["resolution"] = guarded(lambda: set_resolution(*resolution))
    result["dpi"] = set_dpi_percent(percent(dpr))
    result["effective_dpi"] = effective_dpi()
    result["resolution_now"] = current_resolution()
    result["dpi_reached"] = result["effective_dpi"] == round(96 * dpr)
    if not result["dpi_reached"]:
        result["reason"] = result["dpi"].get("reason") or f"effective DPI is {result['effective_dpi']}, not {round(96 * dpr)}"
    return result

