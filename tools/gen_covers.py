#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 media/ 里的每首曲目生成封面图（ffmpeg 从视频抽帧）

曲库/底部播放条显示的封面来自 /catalog 的 iconUrl 字段，原版元数据的
iconUrl 指向已停服的 CDN → 全部空白。这里从本地视频第 3 秒抽一帧存成
media/<nameKey>/<nameKey>.jpg，server._catalog() 扫到同名图就会自动
覆盖 coverUrl / iconUrl，曲库立即有封面。

用法：py -3.12 tools/gen_covers.py [--force]
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(os.path.dirname(HERE), "mockserver", "media")

# 与 server._scan_media 同一条命名规则：<nameKey>_TOD<HHMM>_<view>[_L]
MEDIA_NAME_RE = re.compile(r"^(?P<key>.+?)_TOD(?P<tod>\d{4})_(?P<view>[A-Za-z]+?)(?:_L)?$")


def main():
    force = "--force" in sys.argv
    if not os.path.isdir(MEDIA):
        raise SystemExit(f"找不到媒体目录：{MEDIA}")

    groups: dict = {}
    for root, _dirs, files in os.walk(MEDIA):
        for fn in files:
            stem, ext = os.path.splitext(fn)
            if ext.lower() not in (".mp4", ".webm", ".m4v"):
                continue
            m = MEDIA_NAME_RE.match(stem)
            key = m.group("key") if m else stem
            # 封面优先用 17:30 时段的画面（傍晚光线最上镜）
            tod = int(m.group("tod")) if m else 1730
            groups.setdefault(key, []).append((abs(tod - 1730), os.path.join(root, fn)))

    ok = fail = skip = 0
    for key in sorted(groups):
        out_dir = os.path.join(MEDIA, key)
        out = os.path.join(out_dir, key + ".jpg")
        if os.path.isfile(out) and os.path.getsize(out) > 0 and not force:
            skip += 1
            continue
        os.makedirs(out_dir, exist_ok=True)
        src = sorted(groups[key])[0][1]
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "3", "-i", src,
                 "-frames:v", "1", "-q:v", "3", out],
                capture_output=True, timeout=60,
            )
        except Exception as e:
            print(f"FAIL {key}: {e}")
            fail += 1
            continue
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            ok += 1
            print(f"ok   {key}")
        else:
            fail += 1
            print(f"FAIL {key}")

    print(f"\n完成：生成 {ok}，跳过 {skip}，失败 {fail}")


if __name__ == "__main__":
    main()
