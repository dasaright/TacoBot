"""Windows presentation boundary: compose all child controls into one buffer.

Page data and widgets stay resident. A page switch changes stacking order;
Windows paints the complete window into a back buffer before presenting it.
There are no paint locks, screenshot overlays, transition timers, or repeated
window-wide invalidations on the input path.
"""
import ctypes
import sys

COMPOSITED = 0x02000000


def windows_api():
    from ctypes import wintypes
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    api.GetAncestor.restype = wintypes.HWND
    suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
    get_style = getattr(api, "GetWindowLong" + suffix)
    set_style = getattr(api, "SetWindowLong" + suffix)
    get_class = getattr(api, "GetClassLong" + suffix)
    get_style.argtypes = get_class.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    get_class.restype = ctypes.c_size_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    api.SetWindowPos.restype = wintypes.BOOL
    return api, get_style, set_style, get_class


def enable_buffered_presentation(root):
    if sys.platform != "win32":
        root.buffered_presentation = False
        return False
    root.update_idletasks()
    api, get_style, set_style, get_class = windows_api()
    hwnd = api.GetAncestor(root.winfo_id(), 2)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    if get_class(hwnd, -26) & (0x20 | 0x40 | 0x80):
        raise RuntimeError("Window class cannot use buffered presentation")
    style = get_style(hwnd, -20)
    ctypes.set_last_error(0)
    result = set_style(hwnd, -20, style | COMPOSITED)
    if not result and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    if not api.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x01 | 0x02 | 0x04 | 0x10 | 0x20):
        raise ctypes.WinError(ctypes.get_last_error())
    if not get_style(hwnd, -20) & COMPOSITED:
        raise RuntimeError("Buffered presentation was not enabled")
    root.buffered_presentation = True
    return True
