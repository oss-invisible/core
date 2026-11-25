"""What the machine is when a capture runs: Windows build, session, compositor, desktop, frame metrics (ctypes only).

Every probe is guarded: a call that fails is recorded as {"error": ...} and never ends the run.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as w
from typing import Any, Dict, Iterable, Optional

from native import NONCLIENTMETRICSW, RTL_OSVERSIONINFOW, api, guarded

#: (name, GetSystemMetrics index)
METRICS = (("SM_CXFRAME", 32), ("SM_CYFRAME", 33), ("SM_CXPADDEDBORDER", 92), ("SM_CYCAPTION", 4))
SPI_GETNONCLIENTMETRICS = 0x29
UOI_NAME = 2
GENERIC_READ = 0x80000000
RUNNER_VARIABLES = ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_ENVIRONMENT")
DPIS = (96, 120, 144, 192)


def os_build() -> Dict[str, Any]:
    info = RTL_OSVERSIONINFOW()
    info.dwOSVersionInfoSize = ctypes.sizeof(info)
    api().ntdll.RtlGetVersion(ctypes.byref(info))
    out: Dict[str, Any] = {"major": info.dwMajorVersion, "minor": info.dwMinorVersion, "build": info.dwBuildNumber}
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
        out["ubr"] = winreg.QueryValueEx(key, "UBR")[0]
        out["product"] = winreg.QueryValueEx(key, "ProductName")[0]
    return out


def session() -> Dict[str, Any]:
    a, mine = api(), w.DWORD()
    a.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(mine))
    return {"active_console_session": a.kernel32.WTSGetActiveConsoleSessionId(), "this_process_session": mine.value}


def composition() -> Dict[str, Any]:
    enabled = w.BOOL()
    code = api().dwm.DwmIsCompositionEnabled(ctypes.byref(enabled))
    return {"hresult": code, "enabled": bool(enabled.value)}


def input_desktop() -> Dict[str, Any]:
    a = api()
    handle = a.user32.OpenInputDesktop(0, False, GENERIC_READ)
    if not handle:
        return {"error": f"OpenInputDesktop failed, last error {ctypes.get_last_error()}"}
    try:
        name, needed = ctypes.create_unicode_buffer(256), w.DWORD()
        a.user32.GetUserObjectInformationW(handle, UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(needed))
        return {"name": name.value}
    finally:
        a.user32.CloseDesktop(handle)


def frame_metrics(dpis: Iterable[int]) -> Dict[str, Any]:
    a, out = api(), {}
    for dpi in dpis:
        row: Dict[str, Any] = {name: a.user32.GetSystemMetricsForDpi(index, dpi) for name, index in METRICS}
        ncm = NONCLIENTMETRICSW()
        ncm.cbSize = ctypes.sizeof(ncm)
        ok = a.user32.SystemParametersInfoForDpi(SPI_GETNONCLIENTMETRICS, ncm.cbSize, ctypes.byref(ncm), 0, dpi)
        row["nonclient"] = ({"iBorderWidth": ncm.iBorderWidth, "iPaddedBorderWidth": ncm.iPaddedBorderWidth,
                             "iCaptionHeight": ncm.iCaptionHeight} if ok else {"error": "SystemParametersInfoForDpi failed"})
        out[str(dpi)] = row
    return out


def collect(current_dpi: Optional[int] = None) -> Dict[str, Any]:
    """The whole record. `current_dpi` (the effective DPI of the primary monitor) is added to the dpi list."""
    dpis = list(DPIS) + ([current_dpi] if current_dpi and current_dpi not in DPIS else [])
    return {"os": guarded(os_build), "session": guarded(session), "dwm_composition": guarded(composition),
            "input_desktop": guarded(input_desktop), "frame_metrics_by_dpi": guarded(lambda: frame_metrics(dpis)),
            "system_dpi": guarded(lambda: {"value": api().user32.GetDpiForSystem()}),
            "runner": {name: os.environ.get(name) for name in RUNNER_VARIABLES}}
