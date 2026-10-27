# -*- coding: utf-8 -*-
"""探测当前桌面壁纸层（WorkerW）结构与显示器信息"""
import ctypes
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM), w.LPARAM]
user32.EnumChildWindows.argtypes = [w.HWND, ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM), w.LPARAM]
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [w.HWND]
user32.GetParent.argtypes = [w.HWND]
user32.GetWindow.argtypes = [w.HWND, w.UINT]


def cls(h):
    b = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(h, b, 512)
    return b.value


def title(h):
    b = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(h, b, 512)
    return b.value


def rect(h):
    r = w.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def kids(h, depth=0):
    out = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(c, _):
        out.append(c)
        return True

    user32.EnumChildWindows(h, cb, 0)
    return out


# 显示器
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
u32 = ctypes.windll.user32
print("screen:", u32.GetSystemMetrics(0), "x", u32.GetSystemMetrics(1))
dc = u32.GetDC(0)
print("dc size:", ctypes.windll.gdi32.GetDeviceCaps(dc, 8), "x", ctypes.windll.gdi32.GetDeviceCaps(dc, 10))
u32.ReleaseDC(0, dc)

progman = user32.FindWindowW("Progman", None)
print("Progman:", progman, rect(progman) if progman else None)

tops = []


@ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
def top_cb(h, _):
    tops.append(h)
    return True


user32.EnumWindows(top_cb, 0)

print("\n=== Progman 子窗口 ===")
for c in kids(progman):
    print(f"  {c:#x} {cls(c):28s} vis={user32.IsWindowVisible(c)} rect={rect(c)} title={title(c)!r}")

print("\n=== 顶层 WorkerW ===")
for h in tops:
    if cls(h) == "WorkerW":
        print(f"  {h:#x} rect={rect(h)} vis={user32.IsWindowVisible(h)} parent={user32.GetParent(h):#x}")
        for c in kids(h):
            print(f"      child {c:#x} {cls(c):28s} rect={rect(c)} title={title(c)!r}")

print("\n=== 含 Qt 窗口的祖先链 ===")
for h in tops:
    if "Qt" in cls(h) or title(h) == "林离Olivia":
        print(f"  top {h:#x} {cls(h)} title={title(h)!r} rect={rect(h)}")
        p = user32.GetParent(h)
        chain = []
        while p:
            chain.append(f"{p:#x}/{cls(p)}")
            p = user32.GetParent(p)
        print("     ancestors:", " <- ".join(chain))
