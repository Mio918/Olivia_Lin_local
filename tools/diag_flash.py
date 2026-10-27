# -*- coding: utf-8 -*-
"""闪现诊断 v2：顶层+WorkerW 子窗口全枚举，观察 stop->play 全程窗口状态"""
import ctypes
import threading
import time
import urllib.request
from ctypes import wintypes as w

u = ctypes.WinDLL("user32")
u.EnumChildWindows.argtypes = [w.HWND, ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM), w.LPARAM]

samples = []
stop_flag = threading.Event()


def snapshot():
    found = {}

    def add(h, depth):
        cls = ctypes.create_unicode_buffer(128)
        u.GetClassNameW(h, cls, 128)
        c = cls.value
        if c in ("mpv", "WorkerW") or "Qt683" in c:
            txt = ctypes.create_unicode_buffer(64)
            u.GetWindowTextW(h, txt, 64)
            r = w.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            vis = u.IsWindowVisible(h)
            key = ("  " * depth) + (c if c != "Qt683QWindowIcon" else "game:" + (txt.value or "?"))
            wr = (r.right - r.left, r.bottom - r.top, r.left, r.top)
            # 同类窗口去重，保留可见优先
            if key not in found or (vis and not found[key][0]):
                found[key] = (vis, wr)

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def top(h, _):
        add(h, 0)
        progman = h if (ctypes.create_unicode_buffer(64).value == "Progman") else 0
        return True

    # 枚举所有顶层窗口；对 Progman 递归子窗口
    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(h, _):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, cls, 64)
        if cls.value == "Progman":
            add(h, 0)

            @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
            def c1(hc, _2):
                add(hc, 1)
                cl = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(hc, cl, 64)
                if cl.value == "WorkerW" or cl.value == "SHELLDLL_DefView":
                    u.EnumChildWindows(hc, c1, 0)
                return True

            u.EnumChildWindows(h, c1, 0)
        else:
            add(h, 0)
        return True

    u.EnumWindows(cb, 0)
    return found


def sample_loop():
    while not stop_flag.is_set():
        samples.append((time.time(), snapshot()))
        time.sleep(0.03)


def api(path):
    try:
        return urllib.request.urlopen("http://127.0.0.1:8787" + path, timeout=8).read()
    except Exception as e:
        return str(e).encode()


th = threading.Thread(target=sample_loop)
th.start()
time.sleep(0.5)
t0 = time.time()
print(">>> stop")
api("/wallpaper/stop")
time.sleep(2.5)
print(">>> play")
api("/wallpaper/play?name=Solo_Lyphard_Melody&t=0")
time.sleep(4)
stop_flag.set()
th.join()

prev = None
for t, f in samples:
    ts = t - t0
    if ts < -0.3:
        continue
    line = f"t={ts:5.2f}  " + " | ".join(
        f"{k.strip()}{'+' if k.startswith('  ') else ''} v={int(v)} {wr[0]}x{wr[1]}@{wr[2]},{wr[3]}"
        for k, (v, wr) in sorted(f.items())
    )
    if line.split("  ", 1)[1] != (prev or "").split("  ", 1)[1] if prev else True:
        print(line)
    prev = line
