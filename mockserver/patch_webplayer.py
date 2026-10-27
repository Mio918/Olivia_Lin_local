#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 webplayer.dat 打补丁：去掉桌面壁纸层的斜向 UID 水印

背景
----
桌面上那层 5% 白、-30° 铺满的 "10000" 水印不在 feapp（曲库页面，已打补丁、
DOM 里连 watermark-overlay 都没有），而是来自**另一个 CEF 视图**：原生加载
webplayer.dat 的那个 webview。它的 App.vue 里有独立的 WatermarkOverlay，
uid 取自页面 URL 的 ?uid= 参数——原生创建视图时带上了 ?uid=10000，于是
整个桌面壁纸被铺满斜向水印。

修法
----
往 webplayer/index.html 的 <head> 注入一条 CSS，把 .watermark-overlay
整个隐藏。不动任何业务代码——这个 webview 同时承担原生播放器的画面输出
（PlaybackView 的双 video 交叉淡入），必须保持原样。

用法：
  只生成不安装（安全，安装目录零写入）：
    py -3.12 patch_webplayer.py
  确认后安装（先把原文件备份成 webplayer.dat.bak.<时间戳>）：
    py -3.12 patch_webplayer.py --apply
  回滚：
    py -3.12 patch_webplayer.py --restore
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import time
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE_DIR, "dist")
DEFAULT_SRC = (
    r"C:/Games/steam/steamapps/common/BSide Olivia Lin Test/0.0.9.627"
    r"/resources/webplayer.dat"
)

# 与 feapp 补丁同一套兜底样式：即使未来前端更新改了组件，这条 CSS 也在
WATERMARK_CSS = (
    "<style>/* olv-patch: hide uid watermark overlay */\n"
    ".watermark-overlay{display:none!important}\n"
    "</style>\n"
)


def injected_index(html: str) -> str:
    if "olv-patch: hide uid watermark overlay" in html:
        return html  # 已经打过，不叠加
    if "<head>" in html:
        return html.replace("<head>", "<head>\n" + WATERMARK_CSS, 1)
    return WATERMARK_CSS + html


def build(src: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "webplayer.dat")
    with zipfile.ZipFile(src, "r") as zin:
        names = zin.namelist()
        if "index.html" not in names:
            raise SystemExit("webplayer.dat 结构异常：根目录没有 index.html")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "index.html":
                    data = injected_index(data.decode("utf-8")).encode("utf-8")
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                zout.writestr(zi, data)
    print(f"已生成补丁包：{out}  ({os.path.getsize(out) / 1024:.1f} KB, {len(names)} 个条目)")
    return out


def apply_patch(src: str, patched: str) -> None:
    if not os.path.exists(src):
        raise SystemExit(f"找不到原文件：{src}")
    cands = sorted(glob.glob(f"{src}.bak.*"))
    if cands:
        print(f"已有原始备份：{cands[0]}（跳过重复备份）")
    else:
        bak = f"{src}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(src, bak)
        print(f"已备份原文件：{bak}")
    shutil.copy2(patched, src)
    print(f"已写入：{src}")
    print("提示：请确认应用已完全退出再启动，否则文件可能被占用。")


def restore(src: str) -> None:
    cands = sorted(glob.glob(f"{src}.bak.*"))
    if not cands:
        raise SystemExit("没找到备份文件，无法回滚")
    shutil.copy2(cands[0], src)
    print(f"已回滚：{cands[0]} -> {src}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC, help="原 webplayer.dat 路径")
    ap.add_argument("--out", default=DIST, help="补丁包输出目录")
    ap.add_argument("--apply", action="store_true", help="写回安装目录（先备份）")
    ap.add_argument("--restore", action="store_true", help="用备份回滚")
    args = ap.parse_args()

    if args.restore:
        restore(args.src)
        return

    if not os.path.exists(args.src):
        raise SystemExit(f"找不到 {args.src}，用 --src 指定")

    # 始终以原始备份为构建源，避免重复 apply 叠加注入
    origin = args.src
    cands = sorted(glob.glob(f"{args.src}.bak.*"))
    if cands:
        origin = cands[0]
        print(f"以原始备份为源：{origin}")

    patched = build(origin, args.out)
    if args.apply:
        apply_patch(args.src, patched)
    else:
        print("未写入安装目录。确认无误后加 --apply 再执行一次即可。")


if __name__ == "__main__":
    main()
