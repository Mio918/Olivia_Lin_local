# -*- coding: utf-8 -*-
"""关键判定：把 ffplay(SDL2/D3D 渲染) 的窗口 SetParent 到桌面壁纸层，看画面能否显示。

用法: python test_sdl.py <video> <workerw|progman>
"""
import ctypes
import os
import struct
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes as w

u = ctypes.WinDLL("user32", use_last_error=True)
u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
u.SetParent.argtypes = [w.HWND, w.HWND]
u.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, w.UINT]
u.GetWindowLongW.restype = ctypes.c_long
u.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]

WS_CHILD = 0x40000000
GWL_STYLE = -16


def cls(h):
    b = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(h, b, 256)
    return b.value


def title(h):
    b = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(h, b, 256)
    return b.value


def find_workerw():
    pm = u.FindWindowW("Progman", None)
    found = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(c, _):
        if cls(c) == "WorkerW":
            found.append(c)
        return True

    u.EnumChildWindows(pm, cb, 0)
    return pm, (found[0] if found else pm)


def screenshot():
    out = os.path.join(tempfile.gettempdir(), "sdl_test.bmp")
    subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "shot.py"), out],
                   capture_output=True)
    data = open(out, "rb").read()
    off = struct.unpack("<I", data[10:14])[0]
    ww = struct.unpack("<i", data[18:22])[0]
    hs = struct.unpack("<i", data[22:26])[0]
    bpp = struct.unpack("<H", data[28:30])[0]
    h = abs(hs)
    top = hs < 0
    stride = ((ww * bpp // 8) + 3) // 4 * 4
    ps = bpp // 8

    def px(x, y):
        row = y if top else h - 1 - y
        i = off + row * stride + x * ps
        return (data[i], data[i + 1], data[i + 2])

    return ww, h, px, out


def stats(px, ww, h):
    """统计颜色分布，判断画面是否变化"""
    sig = []
    for y in range(100, h - 100, 200):
        for x in range(100, ww - 100, 300):
            sig.append(px(x, y))
    return sig


def main():
    video = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "workerw"
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    SW, SH = u.GetSystemMetrics(0), u.GetSystemMetrics(1)

    ffplay = os.path.join(os.path.dirname(shutil_which("ffmpeg") or ""), "ffplay.exe")
    if not os.path.isfile(ffplay):
        ffplay = "ffplay"

    proc = subprocess.Popen(
        [ffplay, "-noborder", "-loop", "0", "-loglevel", "error",
         "-window_title", "OLV_SDL_TEST", "-x", str(SW), "-y", str(SH), video],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"ffplay pid={proc.pid}", flush=True)

    hwnd = None
    for _ in range(60):
        time.sleep(0.3)
        h = u.FindWindowW(None, "OLV_SDL_TEST")
        if h:
            hwnd = h
            break
    if not hwnd:
        # 退而求其次：找 SDL_app 类窗口
        found = []

        @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        def cb(c, _):
            if cls(c) in ("SDL_app",):
                found.append(c)
            return True

        u.EnumWindows(cb, 0)
        hwnd = found[0] if found else None
    print(f"ffplay hwnd={hwnd and hex(hwnd)} class={cls(hwnd) if hwnd else None}", flush=True)
    if not hwnd:
        proc.kill()
        return 1

    pm, target = (u.FindWindowW("Progman", None), None) if mode == "progman" else find_workerw()
    target = pm if mode == "progman" else target
    print(f"target={target:#x} ({mode})", flush=True)

    # 截图基线
    ww, hh, px, path = screenshot()
    base = stats(px, ww, hh)
    print("基线采样:", base[:4], flush=True)

    style = u.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    style = (style | WS_CHILD) & 0xFFFFFFFF
    u.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(style - (1 << 32) if style >> 31 else style).value)
    u.SetParent(hwnd, target)
    u.SetWindowPos(hwnd, 0, 0, 0, SW, SH, 0x0040 | 0x0020)  # SHOWWINDOW|FRAMECHANGED
    print("已挂载，等待 5 秒后截图…", flush=True)
    time.sleep(5)

    ww, hh, px, path = screenshot()
    after = stats(px, ww, hh)
    print("挂载后采样:", after[:4], flush=True)
    changed = sum(1 for a, b in zip(base, after) if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > 20)
    print(f"变化采样点: {changed}/{len(base)}", flush=True)
    print(f"截图: {path}", flush=True)

    time.sleep(6)
    proc.kill()
    return 0


def shutil_which(name):
    from shutil import which
    return which(name)


if __name__ == "__main__":
    sys.exit(main())
