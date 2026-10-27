# -*- coding: utf-8 -*-
"""PoC: 在桌面壁纸层(WorkerW)下创建一个子窗口并填充颜色，验证可见性"""
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
    _fields_ = [
        ("cbSize", w.UINT),
        ("style", w.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", w.HANDLE),
        ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
        ("hIconSm", w.HICON),
    ]


user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM), w.LPARAM]
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, w.UINT]


def cls(h):
    b = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(h, b, 512)
    return b.value


def find_wallpaper_w():
    """找到承载壁纸的 WorkerW（Progman 的子窗口）"""
    progman = user32.FindWindowW("Progman", None)
    found = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(h, _):
        if cls(h) == "WorkerW":
            found.append(h)
        return True

    user32.EnumChildWindows(progman, cb, 0)
    return progman, (found[0] if found else None)


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", w.HDC), ("fErase", w.BOOL), ("rcPaint", w.RECT),
                ("fRestore", w.BOOL), ("fReserve", w.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


def run(color=0x0000FF):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    SW, SH = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    progman, workerw = find_wallpaper_w()
    print(f"Progman={progman:#x} WorkerW={workerw:#x} screen={SW}x{SH}")
    if not workerw:
        print("!! 没有 WorkerW，退回 Progman")
        workerw = progman

    hinst = kernel32.GetModuleHandleW(None)
    NAME = "OliviaWallpaperHost"

    def wndproc(h, msg, wp, lp):
        if msg == 0x000F:  # WM_PAINT
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(h, ctypes.byref(ps))
            rc = w.RECT(0, 0, SW, SH)
            br = gdi32.CreateSolidBrush(color)
            user32.FillRect(hdc, ctypes.byref(rc), br)
            gdi32.DeleteObject(br)
            user32.EndPaint(h, ctypes.byref(ps))
            return 0
        if msg == 0x0014:  # WM_ERASEBKGND
            return 1
        if msg == 0x0002:  # WM_DESTROY
            return 0
        return user32.DefWindowProcW(h, msg, wp, lp)

    proc = WNDPROC(wndproc)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.style = 0
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.hbrBackground = None
    wc.lpszClassName = NAME
    if not user32.RegisterClassExW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err != 1410:  # class already exists
            print("RegisterClassEx failed", err)
    print("hinst", hinst)

    WS_CHILD, WS_VISIBLE, WS_CLIPCHILDREN, WS_CLIPSIBLINGS = 0x40000000, 0x10000000, 0x02000000, 0x04000000
    hwnd = user32.CreateWindowExW(
        0x08000000 | 0x00000080,  # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        NAME, "OliviaWallpaperVideo",
        WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
        0, 0, SW, SH,
        workerw, None, hinst, None)
    print(f"hwnd={hwnd:#x} err={ctypes.get_last_error()}")
    if not hwnd:
        return 1

    # 置顶（在兄弟窗口里最上面）
    HWND_TOP = 0
    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, SW, SH, 0x0004 | 0x0040)  # SWP_NOZORDER? no -> use 0
    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, SW, SH, 0x0040)  # SWP_SHOWWINDOW
    user32.UpdateWindow(hwnd)
    print("窗口已创建，观察 12 秒…（截图验证）")
    time.sleep(12)
    user32.DestroyWindow(hwnd)
    print("done")
    return 0


if __name__ == "__main__":
    c = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x0000FF
    sys.exit(run(c))
