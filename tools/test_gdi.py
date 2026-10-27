# -*- coding: utf-8 -*-
"""验证 GDI 绘制在 WorkerW 子窗口上是否可见（不依赖 WM_PAINT，直接 PatBlt）"""
import ctypes
import sys
import time
from ctypes import wintypes as w

u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
                ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON)]


u.CreateWindowExW.restype = w.HWND
u.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
u.DefWindowProcW.restype = LRESULT
u.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
g.CreateSolidBrush.restype = w.HBRUSH
g.CreateSolidBrush.argtypes = [w.DWORD]
u.GetDC.restype = w.HDC
u.GetDC.argtypes = [w.HWND]
u.RedrawWindow.argtypes = [w.HWND, ctypes.c_void_p, w.HRGN, w.UINT]
u.FillRect.argtypes = [w.HDC, ctypes.POINTER(w.RECT), w.HBRUSH]
u.ReleaseDC.argtypes = [w.HWND, w.HDC]
g.DeleteObject.argtypes = [w.HGDIOBJ]


def wndproc(h, msg, wp, lp):
    if msg == 0x0014:
        return 1
    return u.DefWindowProcW(h, msg, wp, lp)


def main(mode="workerw"):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    SW, SH = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    hi = k.GetModuleHandleW(None)
    NAME = "OlvGdiTest"
    proc = WNDPROC(wndproc)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = proc
    wc.hInstance = hi
    wc.lpszClassName = NAME
    u.RegisterClassExW(ctypes.byref(wc))

    pm = u.FindWindowW("Progman", None)
    found = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(c, _):
        b = ctypes.create_unicode_buffer(128)
        u.GetClassNameW(c, b, 128)
        if b.value == "WorkerW":
            found.append(c)
        return True

    u.EnumChildWindows(pm, cb, 0)
    target = found[0] if (mode == "workerw" and found) else pm
    print(f"target={target:#x} ({mode})", flush=True)

    WS_CHILD, WS_VISIBLE, WS_CLIPSIBLINGS, WS_CLIPCHILDREN = 0x40000000, 0x10000000, 0x04000000, 0x02000000
    hwnd = u.CreateWindowExW(0x08000000 | 0x80, NAME, "OlvGdiTest",
                             WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN,
                             0, 0, SW, SH, target, None, hi, None)
    print(f"hwnd={hwnd:#x}", flush=True)
    u.SetWindowPos(hwnd, 0, 0, 0, SW, SH, 0x0040)

    br = g.CreateSolidBrush(0x0000FF00)  # COLORREF 纯绿
    t0 = time.time()
    while time.time() - t0 < 16:
        hdc = u.GetDC(hwnd)
        rc = w.RECT(0, 0, SW, SH)
        u.FillRect(hdc, ctypes.byref(rc), br)
        u.ReleaseDC(hwnd, hdc)
        # 强制 DWM 认为窗口有更新
        u.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0100)  # INVALIDATE|ERASE|UPDATENOW
        time.sleep(0.5)
    u.DestroyWindow(hwnd)
    print("done", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "workerw")
