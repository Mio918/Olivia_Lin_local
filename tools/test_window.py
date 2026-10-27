# -*- coding: utf-8 -*-
"""基础验证：普通顶层窗口 GDI 填充绿色，看截图能否抓到。逐步升级挂载方式。"""
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
gdi32.CreateSolidBrush.restype = w.HBRUSH
gdi32.CreateSolidBrush.argtypes = [w.DWORD]


def run(mode):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    SW, SH = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    hinst = kernel32.GetModuleHandleW(None)
    NAME = f"OlvTest_{mode}"
    color = 0x0000FF00  # COLORREF 0x00BBGGRR -> 纯绿

    def wndproc(h, msg, wp, lp):
        if msg == 0x000F:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(h, ctypes.byref(ps))
            rc = w.RECT(0, 0, SW, SH)
            br = gdi32.CreateSolidBrush(color)
            user32.FillRect(hdc, ctypes.byref(rc), br)
            gdi32.DeleteObject(br)
            user32.EndPaint(h, ctypes.byref(ps))
            return 0
        if msg == 0x0014:
            return 1
        return user32.DefWindowProcW(h, msg, wp, lp)

    proc = WNDPROC(wndproc)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.lpszClassName = NAME
    user32.RegisterClassExW(ctypes.byref(wc))

    WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
    hwnd = user32.CreateWindowExW(0x08000000, NAME, NAME, WS_POPUP | WS_VISIBLE,
                                  0, 0, 800, 600, None, None, hinst, None)
    print(f"[{mode}] hwnd={hwnd:#x} err={ctypes.get_last_error()}", flush=True)

    if mode in ("progman", "workerw"):
        progman = user32.FindWindowW("Progman", None)
        target = progman
        if mode == "workerw":
            found = []

            @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
            def cb(c, _):
                b = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(c, b, 256)
                if b.value == "WorkerW":
                    found.append(c)
                return True

            user32.EnumChildWindows(progman, cb, 0)
            target = found[0] if found else progman
        ok = user32.SetParent(hwnd, target)
        print(f"  SetParent({target:#x}) -> {ok} err={ctypes.get_last_error()}", flush=True)
        user32.SetWindowPos(hwnd, 0, 0, 0, SW, SH, 0x0040)
        user32.UpdateWindow(hwnd)
    else:
        user32.SetWindowPos(hwnd, -1, 0, 0, 800, 600, 0x0040)  # HWND_TOPMOST
        user32.UpdateWindow(hwnd)

    time.sleep(14)
    user32.DestroyWindow(hwnd)
    print(f"[{mode}] done", flush=True)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "topmost")
