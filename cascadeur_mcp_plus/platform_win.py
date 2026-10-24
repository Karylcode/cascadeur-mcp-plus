"""Win32 window capture, minimize state and restore for Cascadeur (Windows only).

ui.py imports this module only when sys.platform is "win32" (T55); on macOS and Linux nothing
here is loaded and ui.py uses Qt's own platform calls instead. Everything must run on
Cascadeur's GUI thread, like the rest of ui.py.

The capture goes through PrintWindow and GetDIBits on the window's device context. It
deliberately avoids QQuickWindow.grabWindow(): forcing an extra Qt Quick render pass crashed
Cascadeur, whose 3D viewport renders with its own OpenGL code. The restore posts a message to
the window procedure instead of sending it, so the window procedure runs from Cascadeur's
normal event loop rather than re-entering Qt from inside a dispatcher callback.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtGui import QImage

from .errors import ToolError

_PW_CLIENTONLY_RENDERFULLCONTENT = 0x1 | 0x2
_WM_SYSCOMMAND = 0x0112
_SC_RESTORE = 0xF120

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def is_minimized(window) -> bool:
    """Whether the window is minimized at the OS level (Win32 IsIconic)."""
    return bool(_user32.IsIconic(int(window.winId())))


def restore(window) -> bool:
    """Un-minimize the window; returns whether it was minimized. Returns immediately."""
    hwnd = int(window.winId())
    was_minimized = bool(_user32.IsIconic(hwnd))
    if was_minimized:
        # Posted, not sent: the window procedure then runs from Cascadeur's normal event loop
        # instead of re-entering Qt from inside this dispatcher callback.
        _user32.PostMessageW(hwnd, _WM_SYSCOMMAND, _SC_RESTORE, 0)
    return was_minimized


def capture_client(window) -> QImage:
    """Capture the window's client area through the OS compositor (PrintWindow)."""
    hwnd = int(window.winId())
    rect = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise ToolError(f"Window {window.title()!r} has no visible client area")
    window_dc = _user32.GetWindowDC(hwnd)
    memory_dc = _gdi32.CreateCompatibleDC(window_dc)
    bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = _gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not _user32.PrintWindow(hwnd, memory_dc, _PW_CLIENTONLY_RENDERFULLCONTENT):
            raise ToolError("PrintWindow failed")
        buffer = ctypes.create_string_buffer(width * height * 4)
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        _gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
        return QImage(buffer.raw, width, height, width * 4, QImage.Format_RGB32).copy()
    finally:
        _gdi32.SelectObject(memory_dc, previous)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(memory_dc)
        _user32.ReleaseDC(hwnd, window_dc)
