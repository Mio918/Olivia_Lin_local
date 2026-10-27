#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把外部音频/视频导入成林离曲库里的一首曲子。

背景（逆向结论）：
  官方那 129 首曲子，每首在 assets/songlist.dat 里存的是**元数据**，
  真正的画面是 mp4 视频，按时段分三套（videoByTodView: TOD12 / TOD1730 / TOD20），
  播放器就是一个 <video src> 双缓冲，没有 DRM、没有签名校验。
  所以"导入外部曲目"不需要任何破解：准备一份 mp4 丢进 media/ 就行。

这个脚本负责把各种来源整理成合规的 mp4 + 同名 .json 元数据：
  · 纯音频（mp3/wav/flac/m4a/ogg）→ 配一张封面或生成一张文字卡，封装成 mp4
  · 视频（mp4/mkv/webm/mov）      → 直接转封装成 H.264/AAC 的 mp4

用法：
  py -3.12 import_song.py "D:/下载/某首曲子.mp3" --name "夜曲" --title "肖邦" --style Classical
  py -3.12 import_song.py "D:/下载/一批" --style "本地导入"      # 目录整批导入
  先试跑不落盘：加 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v"}
COVER_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def _run(cmd: list[str]) -> tuple[bool, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        return False, (p.stderr or "")[-600:]
    # 成功也要把 stdout 带回去：ffprobe 的结果就靠它
    return True, p.stdout or ""


def _probe(path: str) -> dict:
    """读时长和有没有视频流。"""
    dur, has_video = 0.0, False
    ok, out = _run(["ffprobe", "-v", "error", "-show_entries",
                    "format=duration:stream=codec_type",
                    "-of", "default=nw=1", path])
    if ok:
        for line in out.splitlines():
            if line.startswith("duration="):
                try:
                    dur = float(line.split("=", 1)[1])
                except ValueError:
                    pass
            if line.strip() == "codec_type=video":
                has_video = True
    return {"duration": dur, "has_video": has_video}


def _safe_id(name: str) -> str:
    """文件名只留安全字符：它会变成 URL 的一部分，也是曲目 id。"""
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name, flags=re.UNICODE)
    return s.strip("_") or "song"


def _find_cover(stem: str, src_dir: str) -> str | None:
    for ext in COVER_EXT:
        for cand in (os.path.join(src_dir, stem + ext), os.path.join(MEDIA_DIR, stem + ext)):
            if os.path.isfile(cand):
                return cand
    return None


def _make_text_cover(text: str, out_png: str, w: int = 1280, h: int = 720) -> bool:
    """没有封面就现画一张：深色底 + 居中的曲名，跟应用本身的深色调一致。"""
    safe = text.replace("\\", "").replace(":", "：").replace("'", "").replace("%", "")
    font = r"C\:/Windows/Fonts/msyh.ttc"
    vf = (f"drawbox=c=0x141416:t=fill,"
          f"drawtext=fontfile='{font}':text='{safe}':fontcolor=0xDCD4CA:fontsize=44:"
          f"x=(w-text_w)/2:y=(h-text_h)/2")
    ok, err = _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x141416:s={w}x{h}",
                    "-vf", vf, "-frames:v", "1", out_png])
    if not ok:
        # 字体画不上就退化成纯色，别让整首导入失败
        ok, err = _run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"color=c=0x141416:s={w}x{h}", "-frames:v", "1", out_png])
    return ok


def import_one(src: str, args) -> bool:
    ext = os.path.splitext(src)[1].lower()
    if ext not in AUDIO_EXT | VIDEO_EXT:
        print(f"  跳过（不认识的格式）：{os.path.basename(src)}")
        return False

    stem = os.path.splitext(os.path.basename(src))[0]
    song_id = _safe_id(args.name or stem)
    info = _probe(src)
    dur = int(round(info["duration"]))
    out_mp4 = os.path.join(MEDIA_DIR, song_id + ".mp4")

    if info["has_video"]:
        # 视频：转封装成通用 mp4（H.264/AAC），兼容性最好
        print(f"  视频转封装 -> {os.path.basename(out_mp4)}")
        if not args.dry_run:
            ok, err = _run(["ffmpeg", "-y", "-i", src, "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                            "-movflags", "+faststart", out_mp4])
            if not ok:
                print(f"  失败：{err}")
                return False
    else:
        # 纯音频：配一张封面做成"可视化"画面，播放器才有东西显示
        cover = _find_cover(stem, os.path.dirname(src))
        tmp_cover = None
        if not cover:
            tmp_cover = os.path.join(MEDIA_DIR, "_tmp_cover.png")
            if not args.dry_run and not _make_text_cover(args.name or stem, tmp_cover):
                print("  封面生成失败，改用纯黑画面继续")
                tmp_cover = None
            cover = tmp_cover
        print(f"  音频封装 -> {os.path.basename(out_mp4)}（时长 {dur}s）")
        if not args.dry_run:
            cmd = ["ffmpeg", "-y"]
            if cover:
                cmd += ["-loop", "1", "-i", cover]
            else:
                cmd += ["-f", "lavfi", "-i", "color=c=0x141416:s=1280x720"]
            cmd += ["-i", src, "-shortest",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-r", "2",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_mp4]
            ok, err = _run(cmd)
            if tmp_cover and os.path.isfile(tmp_cover):
                os.remove(tmp_cover)
            if not ok:
                print(f"  失败：{err}")
                return False

    meta = {
        "name": args.name or stem,
        "title": args.title or "本地导入",
        "singer": args.singer,
        "styleType": args.style,
        "mood": args.mood,
        "description": args.description or "从外部导入的曲目。",
        "opinion": args.opinion or "曲目资产没有加密也没有签名校验，自己准备素材就能进曲库。",
        "duration": dur,
    }
    if not args.dry_run:
        os.makedirs(MEDIA_DIR, exist_ok=True)
        with open(os.path.join(MEDIA_DIR, song_id + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"  已导入：{song_id}.mp4 + {song_id}.json")
    else:
        print(f"  [dry-run] 将生成 {song_id}.mp4 + {song_id}.json  ({dur}s)")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="把外部音视频导入林离曲库")
    ap.add_argument("src", help="音视频文件，或包含多个文件的目录")
    ap.add_argument("--name", help="曲名（默认取文件名）")
    ap.add_argument("--title", default="", help="副标题/演奏者信息")
    ap.add_argument("--singer", default="本地导入", help="演唱/演奏者")
    ap.add_argument("--style", default="local", help="风格分类（local = 本地导入）")
    ap.add_argument("--mood", default="", help="氛围标签")
    ap.add_argument("--description", default="", help="曲目简介（会显示在曲目页）")
    ap.add_argument("--opinion", default="", help="林离的乐评（会显示在曲目页）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不落盘")
    args = ap.parse_args()

    if not os.path.isdir(MEDIA_DIR):
        os.makedirs(MEDIA_DIR, exist_ok=True)

    files: list[str] = []
    if os.path.isdir(args.src):
        for fn in sorted(os.listdir(args.src)):
            if os.path.splitext(fn)[1].lower() in AUDIO_EXT | VIDEO_EXT:
                files.append(os.path.join(args.src, fn))
    elif os.path.isfile(args.src):
        files = [args.src]
    else:
        raise SystemExit(f"找不到：{args.src}")

    if not files:
        raise SystemExit("没有可导入的音视频文件")

    print(f"共 {len(files)} 个文件，导入到 {MEDIA_DIR}")
    ok = 0
    for f in files:
        print(f"- {os.path.basename(f)}")
        if import_one(f, args):
            ok += 1
    print(f"\n完成 {ok}/{len(files)}。重启曲库页（或等下一次刷新）即可看到。")


if __name__ == "__main__":
    main()
