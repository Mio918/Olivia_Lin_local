# -*- coding: utf-8 -*-
"""复现播单切歌闪现：UI 状态真实驱动 + 高频连拍，标出系统壁纸帧"""
import ctypes
import json
import subprocess
import threading
import time
import urllib.request
from ctypes import wintypes as w

u = ctypes.WinDLL("user32")
u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]

OUT = "tmp_flash"
import os
os.makedirs(OUT, exist_ok=True)

win_log = []
stop_flag = threading.Event()


def win_loop():
    while not stop_flag.is_set():
        mpv = None
        @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        def cb(h, _):
            global mpv
            cls = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, cls, 64)
            if cls.value == "mpv":
                mpv = h
            return True
        u.EnumWindows(cb, 0)
        # mpv SetParent 后变子窗口，枚举 WorkerW 子窗口
        progman = u.FindWindowW("Progman", None)
        if not mpv and progman:
            @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
            def cb2(hc, _2):
                global mpv
                cls = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(hc, cls, 64)
                if cls.value == "mpv":
                    mpv = hc
                return True
            u.EnumChildWindows(progman, cb2, 0)
        win_log.append((time.time(), mpv))
        time.sleep(0.03)


def probe(js):
    payload = json.dumps({"cmd": "eval", "js": js}).encode()
    req = urllib.request.Request("http://127.0.0.1:8787/probe/cmd", data=payload,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def shot(idx, t):
    subprocess.run(["py", "-3.12", "tools/shot.py"], capture_output=True)
    try:
        os.replace("shot.bmp", f"{OUT}/f{idx:03d}_t{t:.2f}.bmp")
    except Exception:
        pass


def play_song(i):
    probe("(function(){var pin=document.getElementById('app').__vue_app__.config.globalProperties.$pinia;"
          "var pl=pin._s.get('playlist');var cat=pin._s.get('offlineCatalog');"
          "var s=(cat.songs||[" + str(i) + "])[" + str(i) + "];if(!s)return 'no';"
          "pl.playSonglistItem(s);return s.name;})()")


th = threading.Thread(target=win_loop)
th.start()

# 1. 清场，从待机开始
urllib.request.urlopen("http://127.0.0.1:8787/wallpaper/stop", timeout=8).read()
time.sleep(1.5)
print("standby ready")

# 2. 待机 -> 播放 A（复现初次跳转）
t0 = time.time()
play_song(3)
shot(0, time.time() - t0)
for i in range(1, 40):          # 2.4s 连拍
    time.sleep(0.06)
    shot(i, time.time() - t0)
print("phase1 done")

# 3. 播放中 -> 切歌 B（复现播单切换）
t1 = time.time()
idx = 40
shot(idx, time.time() - t1); idx += 1
play_song(4)
for i in range(40):
    time.sleep(0.06)
    shot(idx, time.time() - t1)
    idx += 1
print("phase2 done")
stop_flag.set()
th.join()

# 输出 mpv 窗口存在性时间线（相对各阶段触发点）
base = win_log[0][0]
print("mpv presence timeline (rel to phase1 trigger):")
p1 = [x for x in win_log if x[0] - base < 20]
first = p1[0][0]
for t, m in win_log:
    print(f"  t={t-first:5.2f} mpv={'0x%X' % m if m else '-'}")
