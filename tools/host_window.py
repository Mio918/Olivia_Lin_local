# -*- coding: utf-8 -*-
"""宿主窗口：创建并挂到桌面壁纸层，输出 hwnd 供外部渲染器嵌入。

用法: python host_window.py <progman|workerw> <seconds>
"""
import ctypes
import sys
import time
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
                ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON)]


user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]


def cls(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def title(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, b, 256)
    return b.value


def run(mode="workerw", secs=30):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    SW, SH = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    hinst = kernel32.GetModuleHandleW(None)
    NAME = "OliviaWallpaperHostW"

    def wndproc(h, msg, wp, lp):
        if msg == 0x0014:  # WM_ERASEBKGND
            return 1
        return user32.DefWindowProcW(h, msg, wp, lp)

    proc = WNDPROC(wndproc)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.lpszClassName = NAME
    user32.RegisterClassExW(ctypes.byref(wc))

    progman = user32.FindWindowW("Progman", None)
    target = progman
    if mode == "workerw":
        found = []

        @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        def cb(c, _):
            if cls(c) == "WorkerW":
                found.append(c)
            return True

        user32.EnumChildWindows(progman, cb, 0)
        if found:
            target = found[0]

    # 找游戏壁纸窗口做 z 序锚点
    anchor = None
    sibs = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb2(c, _):
        sibs.append(c)
        return True

    user32.EnumChildWindows(progman, cb2, 0)
    for c in sibs:
        if "Qt" in cls(c) and title(c) == "林离Olivia":
            r = w.RECT()
            user32.GetWindowRect(c, ctypes.byref(r))
            if r.right - r.left >= SW:
                anchor = c

    WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
    WS_CHILD, WS_CLIPCHILDREN, WS_CLIPSIBLINGS = 0x40000000, 0x02000000, 0x04000000
    if mode == "toplevel":
        style = WS_POPUP | WS_VISIBLE
        target = None
    elif mode == "progman_popup":
        style = WS_POPUP | WS_VISIBLE
    else:
        style = WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN
    hwnd = user32.CreateWindowExW(
        0x08000000 | 0x00000080, NAME, "OliviaWallpaperVideo", style,
        0, 0, SW, SH, target, None, hinst, None)
    if mode == "progman_popup":
        user32.SetParent(hwnd, progman)
    user32.SetWindowPos(hwnd, anchor if anchor else 0, 0, 0, SW, SH, 0x0040)
    print(f"HOST hwnd={hwnd} (0x{hwnd:x}) target={target if target else 0:#x} anchor={anchor and hex(anchor)} size={SW}x{SH}", flush=True)
    print(f"SDL_VIDEO_WINDOWID={hwnd}", flush=True)
    time.sleep(secs)
    user32.DestroyWindow(hwnd)
    print("host closed", flush=True)


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else "workerw"
    s = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    run(m, s)
