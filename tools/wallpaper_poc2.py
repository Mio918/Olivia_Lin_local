# -*- coding: utf-8 -*-
"""PoC v2: 模仿游戏自身的做法——顶层窗口 SetParent 到 Progman，插在游戏壁纸窗口之上"""
import ctypes
import sys
import time
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", w.HDC), ("fErase", w.BOOL), ("rcPaint", w.RECT),
                ("fRestore", w.BOOL), ("fReserve", w.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


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
user32.SetParent.argtypes = [w.HWND, w.HWND]
user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, w.UINT]
user32.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
gdi32.CreateSolidBrush.restype = w.HBRUSH


def cls(h):
    b = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(h, b, 512)
    return b.value


def title(h):
    b = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(h, b, 512)
    return b.value


def sub(h):
    out = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(c, _):
        out.append(c)
        return True

    user32.EnumChildWindows(h, cb, 0)
    return out


def run(color=0x0000FF):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    SW, SH = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    progman = user32.FindWindowW("Progman", None)

    # 找游戏壁纸窗口（全屏的 Qt 窗口）
    anchor = None
    for c in sub(progman):
        if "Qt" in cls(c) and title(c) == "林离Olivia":
            r = w.RECT()
            user32.GetWindowRect(c, ctypes.byref(r))
            if r.right - r.left >= SW:
                anchor = c
                break
    print(f"Progman={progman:#x} anchor(Qt壁纸)={anchor and hex(anchor)} screen={SW}x{SH}", flush=True)

    hinst = kernel32.GetModuleHandleW(None)
    NAME = "OliviaWallpaperHost"

    def wndproc(h, msg, wp, lp):
        if msg == 0x000F:  # WM_PAINT
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(h, ctypes.byref(ps))
            rc = w.RECT()
            user32.GetClientRect(h, ctypes.byref(rc))
            br = gdi32.CreateSolidBrush(color)
            user32.FillRect(hdc, ctypes.byref(rc), br)
            gdi32.DeleteObject(br)
            user32.EndPaint(h, ctypes.byref(ps))
            return 0
        if msg == 0x0014:  # WM_ERASEBKGND
            return 1
        return user32.DefWindowProcW(h, msg, wp, lp)

    proc = WNDPROC(wndproc)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.lpszClassName = NAME
    if not user32.RegisterClassExW(ctypes.byref(wc)):
        e = ctypes.get_last_error()
        if e != 1410:
            print("RegisterClassEx failed", e, flush=True)

    WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
    WS_CHILD, WS_CLIPCHILDREN, WS_CLIPSIBLINGS = 0x40000000, 0x02000000, 0x04000000
    hwnd = user32.CreateWindowExW(
        0x08000000 | 0x00000080, NAME, "OliviaWallpaperVideo",
        WS_POPUP | WS_VISIBLE,
        0, 0, SW, SH, None, None, hinst, None)
    print(f"hwnd(顶层)={hwnd:#x} err={ctypes.get_last_error()}", flush=True)
    if not hwnd:
        return 1

    if not user32.SetParent(hwnd, progman):
        print("SetParent failed", ctypes.get_last_error(), flush=True)
    # 加子窗口样式，保证参与父窗口绘制
    GWL_STYLE = -16
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]
    style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    style = ((style & ~WS_POPUP) | WS_CHILD | WS_CLIPSIBLINGS | WS_CLIPCHILDREN) & 0xFFFFFFFF
    user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(style - (1 << 32) if style >> 31 else style).value)

    # 插到 anchor 之上（anchor 在 DefView 之下，所以不会盖住桌面图标）
    after = anchor if anchor else 0  # HWND_TOP=0
    user32.SetWindowPos(hwnd, after, 0, 0, SW, SH, 0x0040)  # SWP_SHOWWINDOW
    user32.UpdateWindow(hwnd)
    r = w.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    print(f"最终 rect={r.left},{r.top},{r.right - r.left}x{r.bottom - r.top} 观察 15 秒", flush=True)
    time.sleep(15)
    user32.DestroyWindow(hwnd)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    c = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x0000FF
    sys.exit(run(c))
