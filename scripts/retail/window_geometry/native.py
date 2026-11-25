"""The Win32 structures and prototypes the capture scripts share (ctypes only).

Structures are plain ctypes and import everywhere; `api()` loads the DLLs and is called on Windows only.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from types import SimpleNamespace
from typing import Any, List

LONG = ctypes.c_int32  # a Win32 LONG is 32 bits on every platform


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]

    def list(self) -> List[int]:
        return [self.left, self.top, self.right, self.bottom]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", w.DWORD)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", w.UINT), ("flags", w.UINT), ("showCmd", w.UINT), ("ptMinPosition", POINT),
                ("ptMaxPosition", POINT), ("rcNormalPosition", RECT)]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", w.DWORD), ("HighPart", LONG)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", w.WCHAR * 32), ("dmSpecVersion", w.WORD), ("dmDriverVersion", w.WORD),
                ("dmSize", w.WORD), ("dmDriverExtra", w.WORD), ("dmFields", w.DWORD),
                ("dmPositionX", LONG), ("dmPositionY", LONG), ("dmDisplayOrientation", w.DWORD),
                ("dmDisplayFixedOutput", w.DWORD), ("dmColor", ctypes.c_int16), ("dmDuplex", ctypes.c_int16),
                ("dmYResolution", ctypes.c_int16), ("dmTTOption", ctypes.c_int16), ("dmCollate", ctypes.c_int16),
                ("dmFormName", w.WCHAR * 32), ("dmLogPixels", w.WORD), ("dmBitsPerPel", w.DWORD),
                ("dmPelsWidth", w.DWORD), ("dmPelsHeight", w.DWORD), ("dmDisplayFlags", w.DWORD),
                ("dmDisplayFrequency", w.DWORD), ("dmICMMethod", w.DWORD), ("dmICMIntent", w.DWORD),
                ("dmMediaType", w.DWORD), ("dmDitherType", w.DWORD), ("dmReserved1", w.DWORD),
                ("dmReserved2", w.DWORD), ("dmPanningWidth", w.DWORD), ("dmPanningHeight", w.DWORD)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", _INPUTUNION)]


class LOGFONTW(ctypes.Structure):
    _fields_ = [("lfHeight", LONG), ("lfWidth", LONG), ("lfEscapement", LONG), ("lfOrientation", LONG),
                ("lfWeight", LONG), ("lfItalic", ctypes.c_ubyte), ("lfUnderline", ctypes.c_ubyte),
                ("lfStrikeOut", ctypes.c_ubyte), ("lfCharSet", ctypes.c_ubyte), ("lfOutPrecision", ctypes.c_ubyte),
                ("lfClipPrecision", ctypes.c_ubyte), ("lfQuality", ctypes.c_ubyte),
                ("lfPitchAndFamily", ctypes.c_ubyte), ("lfFaceName", w.WCHAR * 32)]


class NONCLIENTMETRICSW(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("iBorderWidth", LONG), ("iScrollWidth", LONG), ("iScrollHeight", LONG),
                ("iCaptionWidth", LONG), ("iCaptionHeight", LONG), ("lfCaptionFont", LOGFONTW),
                ("iSmCaptionWidth", LONG), ("iSmCaptionHeight", LONG), ("lfSmCaptionFont", LOGFONTW),
                ("iMenuWidth", LONG), ("iMenuHeight", LONG), ("lfMenuFont", LOGFONTW),
                ("lfStatusFont", LOGFONTW), ("lfMessageFont", LOGFONTW), ("iPaddedBorderWidth", LONG)]


class RTL_OSVERSIONINFOW(ctypes.Structure):
    _fields_ = [("dwOSVersionInfoSize", w.ULONG), ("dwMajorVersion", w.ULONG), ("dwMinorVersion", w.ULONG),
                ("dwBuildNumber", w.ULONG), ("dwPlatformId", w.ULONG), ("szCSDVersion", w.WCHAR * 128)]


def api() -> SimpleNamespace:
    """user32, kernel32, dwmapi, shcore and ntdll with the prototypes this package calls."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    hwnd, ptr = w.HWND, ctypes.POINTER

    def proto(fn: Any, res: Any, *args: Any) -> None:
        fn.restype, fn.argtypes = res, list(args)

    proto(user32.EnumWindows, w.BOOL, ctypes.WINFUNCTYPE(w.BOOL, hwnd, w.LPARAM), w.LPARAM)
    proto(user32.GetWindowThreadProcessId, w.DWORD, hwnd, ptr(w.DWORD))
    proto(user32.GetClassNameW, ctypes.c_int, hwnd, w.LPWSTR, ctypes.c_int)
    proto(user32.IsWindowVisible, w.BOOL, hwnd)
    proto(user32.GetWindowRect, w.BOOL, hwnd, ptr(RECT))
    proto(user32.GetClientRect, w.BOOL, hwnd, ptr(RECT))
    proto(user32.ClientToScreen, w.BOOL, hwnd, ptr(POINT))
    proto(user32.GetWindowPlacement, w.BOOL, hwnd, ptr(WINDOWPLACEMENT))
    proto(user32.GetDpiForWindow, w.UINT, hwnd)
    proto(user32.GetWindowLongPtrW, ctypes.c_ssize_t, hwnd, ctypes.c_int)
    proto(user32.MonitorFromWindow, w.HANDLE, hwnd, w.DWORD)
    proto(user32.MonitorFromPoint, w.HANDLE, POINT, w.DWORD)
    proto(user32.GetMonitorInfoW, w.BOOL, w.HANDLE, ptr(MONITORINFO))
    proto(user32.SetForegroundWindow, w.BOOL, hwnd)
    proto(user32.SendInput, w.UINT, w.UINT, ptr(INPUT), ctypes.c_int)
    proto(user32.GetCursorPos, w.BOOL, ptr(POINT))
    proto(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
    proto(user32.GetSystemMetricsForDpi, ctypes.c_int, ctypes.c_int, w.UINT)
    proto(user32.SystemParametersInfoForDpi, w.BOOL, w.UINT, w.UINT, ctypes.c_void_p, w.UINT, w.UINT)
    proto(user32.GetDpiForSystem, w.UINT)
    proto(user32.ChangeDisplaySettingsExW, w.LONG, w.LPCWSTR, ptr(DEVMODEW), hwnd, w.DWORD, ctypes.c_void_p)
    proto(user32.EnumDisplaySettingsW, w.BOOL, w.LPCWSTR, w.DWORD, ptr(DEVMODEW))
    proto(user32.GetDisplayConfigBufferSizes, w.LONG, w.UINT, ptr(w.UINT), ptr(w.UINT))
    proto(user32.QueryDisplayConfig, w.LONG, w.UINT, ptr(w.UINT), ctypes.c_void_p, ptr(w.UINT), ctypes.c_void_p,
          ctypes.c_void_p)
    proto(user32.DisplayConfigGetDeviceInfo, w.LONG, ctypes.c_void_p)
    proto(user32.DisplayConfigSetDeviceInfo, w.LONG, ctypes.c_void_p)
    proto(user32.OpenInputDesktop, w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    proto(user32.GetUserObjectInformationW, w.BOOL, w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ptr(w.DWORD))
    proto(user32.CloseDesktop, w.BOOL, w.HANDLE)
    proto(user32.SetProcessDpiAwarenessContext, w.BOOL, ctypes.c_void_p)
    proto(kernel32.WTSGetActiveConsoleSessionId, w.DWORD)
    proto(kernel32.ProcessIdToSessionId, w.BOOL, w.DWORD, ptr(w.DWORD))
    proto(dwm.DwmIsCompositionEnabled, ctypes.c_long, ptr(w.BOOL))
    proto(dwm.DwmGetWindowAttribute, ctypes.c_long, hwnd, w.DWORD, ctypes.c_void_p, w.DWORD)
    proto(shcore.GetDpiForMonitor, ctypes.c_long, w.HANDLE, ctypes.c_int, ptr(w.UINT), ptr(w.UINT))
    proto(ntdll.RtlGetVersion, ctypes.c_long, ptr(RTL_OSVERSIONINFOW))
    return SimpleNamespace(user32=user32, kernel32=kernel32, dwm=dwm, shcore=shcore, ntdll=ntdll)


def guarded(fn: Any) -> Any:
    """`fn()`, or {"error": ...} when it raises: a probe never ends the run."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - recorded, never raised
        return {"error": f"{type(exc).__name__}: {exc}"}
