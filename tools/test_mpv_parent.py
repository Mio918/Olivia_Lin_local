# -*- coding: utf-8 -*-
"""测试：启动 mpv 独立窗口，抓到后 SetParent 到壁纸层（WorkerW）"""
import ctypes
import os
import struct
import subprocess
import sys
import time
from ctypes import wintypes as w

u = ctypes.WinDLL("user32", use_last_error=True)
u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
u.SetParent.argtypes = [w.HWND, w.HWND]
u.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, w.UINT]
u.GetWindowLongW.restype = ctypes.c_long
u.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]
u.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
u.FindWindowW.restype = w.HWND

MPV = r"C:\CodeAI\Olivia_Lin\tools\mpv\mpv.exe"
SHOT = r"C:\CodeAI\Olivia_Lin\tools\shot.py"


def cls(h):
    b = ctypes.create_unicode_buffer(128)
    u.GetClassNameW(h, b, 128)
    return b.value


def find_workerw():
    pm = u.FindWindowW("Progman", None)
    f = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(c, _):
        if cls(c) == "WorkerW":
            f.append(c)
        return True

    u.EnumChildWindows(pm, cb, 0)
    return (f[0] if f else pm), pm


def shot(tag):
    out = os.path.join(os.environ.get("TEMP", "."), f"mpv_{tag}.bmp")
    subprocess.run([sys.executable, SHOT, out], capture_output=True)
    d = open(out, "rb").read()
    off = struct.unpack("<I", d[10:14])[0]
    ww = struct.unpack("<i", d[18:22])[0]
    hs = struct.unpack("<i", d[22:26])[0]
    bpp = struct.unpack("<H", d[28:30])[0]
    h = abs(hs)
    top = hs < 0
    stride = ((ww * bpp // 8) + 3) // 4 * 4
    ps = bpp // 8
    sig = []
    for y in range(80, h - 80, 160):
        for x in range(80, ww - 80, 240):
            row = y if top else h - 1 - y
            i = off + row * stride + x * ps
            sig.append((d[i], d[i + 1], d[i + 2]))
    return sig, out


def main():
    video = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "workerw"
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    SW, SH = u.GetSystemMetrics(0), u.GetSystemMetrics(1)

    base, _ = shot("base")
    proc = subprocess.Popen([MPV, "--no-config", "--no-osc", "--no-border", "--vo=gpu", video],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"mpv pid={proc.pid}", flush=True)

    hwnd = None
    t0 = time.time()
    while time.time() - t0 < 12:
        h = u.FindWindowW("mpv", None)
        if h:
            hwnd = h
            break
        time.sleep(0.05)
    print(f"mpv hwnd={hwnd and hex(hwnd)} 等待={time.time() - t0:.2f}s alive={proc.poll()}", flush=True)
    if not hwnd:
        proc.kill()
        return 1

    workerw, pm = find_workerw()
    target = pm if mode == "progman" else workerw
    GWL_STYLE = -16
    style = u.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    style = (style | 0x40000000) & 0xFFFFFFFF
    u.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(style - (1 << 32) if style >> 31 else style).value)
    ok = u.SetParent(hwnd, target)
    print(f"SetParent({target:#x}) -> {ok} err={ctypes.get_last_error()}", flush=True)
    u.SetWindowPos(hwnd, 0, 0, 0, SW, SH, 0x0040 | 0x0020)

    time.sleep(6)
    after, path = shot("after")
    diff = sum(1 for a, b in zip(base, after) if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > 20)
    print(f"变化 {diff}/{len(base)}  截图={path}", flush=True)
    time.sleep(4)
    proc.kill()
    try:
        proc.wait(timeout=3)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
