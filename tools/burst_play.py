#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高频截屏：捕捉「点播放 → 壁纸切换」间隙的帧序列，定位默认壁纸闪现的窗口期

用法：py -3.12 tools/burst_play.py
流程：抓 1s 待机基线 → probe 触发播放 catalog 第一首 → 再抓 4s → 存帧
输出：tmp_burst/ 目录下按时间编号的 JPEG + 帧差摘要
"""
import io
import json
import os
import sys
import threading
import time
import urllib.request

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "tmp_burst")
BASE = "http://127.0.0.1:8787"

frames = []          # (t, Image)
stop_flag = threading.Event()


def grabber(interval=0.08):
    from PIL import ImageGrab
    t0 = time.time()
    while not stop_flag.is_set():
        try:
            im = ImageGrab.grab()
            frames.append((time.time() - t0, im))
        except Exception:
            pass
        time.sleep(interval)


def trigger():
    js = ("(function(){var gp=document.getElementById('app').__vue_app__"
          ".config.globalProperties;var pin=gp.$pinia;"
          "var pl=pin._s.get('playlist');var cat=pin._s.get('offlineCatalog');"
          "var s=(cat.songs||[])[0];if(!s)return 'no-song';"
          "pl.playSonglistItem(s);return 'played:'+s.name;})()")
    req = urllib.request.Request(
        BASE + "/probe/cmd",
        data=json.dumps({"cmd": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"})
    print("trigger:", urllib.request.urlopen(req, timeout=5).read().decode()[:80])


def main():
    os.makedirs(OUT, exist_ok=True)
    # 清旧帧
    for f in os.listdir(OUT):
        os.remove(os.path.join(OUT, f))

    th = threading.Thread(target=grabber, daemon=True)
    th.start()
    time.sleep(1.0)          # 待机基线
    trigger()
    time.sleep(4.0)          # 播放启动窗口
    stop_flag.set()
    th.join(timeout=1)

    print(f"captured {len(frames)} frames")
    if not frames:
        return
    base_img = frames[0][1].convert("RGB").resize((320, 180))
    prev_small = None
    summary = []
    for i, (t, im) in enumerate(frames):
        small = im.convert("RGB").resize((320, 180))
        if prev_small is None:
            d = 999
        else:
            diff = ImageChops.difference(prev_small, small)
            bbox = diff.getbbox()
            h = diff.histogram()
            d = sum(v * (j % 256 > 24) for j, v in enumerate(h) if j % 256 > 24)
        summary.append((t, d))
        prev_small = small
        if i % 3 == 0 or d > 800:   # 存关键帧
            im.convert("RGB").save(os.path.join(OUT, f"f{i:03d}_t{t:.2f}_d{int(d)}.jpg"),
                                   quality=72)
    # 摘要：找到差异大的时间段
    print("t(s)  diff")
    for t, d in summary:
        bar = "#" * min(60, d // 40)
        print(f"{t:5.2f} {d:7d} {bar}")


if __name__ == "__main__":
    main()
