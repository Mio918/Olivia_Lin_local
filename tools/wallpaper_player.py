# -*- coding: utf-8 -*-
"""Olivia 桌面壁纸播放器

把视频渲染到 Windows 桌面壁纸层（WorkerW），效果等同 Wallpaper Engine / Lively
的"视频壁纸"：视频铺满桌面，桌面图标和普通窗口仍然叠在上面。

为什么这么做
------------
1. 实测 GDI 绘制的内容完全进不了桌面层的 DWM 合成（绿色填充窗口 0% 可见），
   所以必须走 GPU 渲染通路。
2. 用 mpv 作渲染后端（D3D11 / 硬件解码 / 自带音频同步）。
3. mpv 的 --wid 在此环境会 `unable to create window`；改为：让 mpv 创建自己的
   窗口，抓到 HWND 后立刻 SetParent 到 WorkerW 并全屏。句柄不变，DXGI
   swapchain 继续有效，画面随父窗口一起进入壁纸层。
4. 每次播放重启 mpv（约 0.3 秒），比维护 IPC 长连接更稳。

用法
----
    # 播单个文件
    python wallpaper_player.py "D:\\video.mp4"

    # 播整个目录（循环播放）
    python wallpaper_player.py "C:\\media\\轻音乐" --loop

    # 常驻服务模式：等 HTTP 指令，空闲时完全不占桌面
    python wallpaper_player.py --serve --port 8788
      GET /play?name=<曲目名>        播放曲库中匹配的视频（支持模糊匹配）
      GET /play?file=<绝对路径>      播放指定文件
      GET /stop                      停止，恢复原壁纸
      GET /status                    查询状态
      GET /list                      列出可播放曲目

    --target workerw|progman   挂载点，默认 workerw
    --mute                     静音
    --duration 秒              播放指定时长后退出（调试）
"""
import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from ctypes import wintypes as w
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- Win32 声明 ----
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
user32.FindWindowW.restype = w.HWND
user32.SetParent.argtypes = [w.HWND, w.HWND]
user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, w.UINT]
user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"}
SWP_SHOWWINDOW, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0040, 0x0010, 0x0020
GWL_STYLE = -16
WS_CHILD = 0x40000000
SW_SHOW = 5

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA_ROOT = os.path.join(os.path.dirname(HERE), "mockserver", "media")


def _cls(h):
    b = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(h, b, 128)
    return b.value


def _title(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, b, 256)
    return b.value


def _enum_child(parent):
    out = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(c, _):
        out.append(c)
        return True

    user32.EnumChildWindows(parent, cb, 0)
    return out


def find_mpv():
    for rel in ("mpv/mpv.exe", "mpv/mpv-x86_64/mpv.exe"):
        p = os.path.join(HERE, rel)
        if os.path.isfile(p):
            return p
    d = os.path.join(HERE, "mpv")
    if os.path.isdir(d):
        for root, _, files in os.walk(d):
            if "mpv.exe" in files:
                return os.path.join(root, "mpv.exe")
    return None


def collect_media(path):
    if os.path.isfile(path):
        return [path]
    out = []
    for root, _, names in os.walk(path):
        for n in sorted(names):
            if os.path.splitext(n)[1].lower() in VIDEO_EXT:
                out.append(os.path.join(root, n))
    return out


class WallpaperPlayer:
    """一次播放一个 mpv 进程；停止即销毁窗口，壁纸自动回到游戏原生画面。"""

    def __init__(self, target="workerw", mute=False):
        self.target_mode = target
        self.mute = mute
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        self.sw = user32.GetSystemMetrics(0)
        self.sh = user32.GetSystemMetrics(1)
        self.proc = None
        self.hwnd = None
        self.current = None
        self.lock = threading.Lock()

    def _target_hwnd(self):
        progman = user32.FindWindowW("Progman", None)
        if not progman:
            raise RuntimeError("找不到 Progman")
        if self.target_mode == "progman":
            return progman
        for c in _enum_child(progman):
            if _cls(c) == "WorkerW":
                return c
        return progman

    def _qt_anchor(self):
        """游戏壁纸窗口，作为 z 序锚点，让我们的窗口盖在它上面"""
        progman = user32.FindWindowW("Progman", None)
        for c in _enum_child(progman):
            if "Qt" in _cls(c) and _title(c) == "林离Olivia":
                r = w.RECT()
                user32.GetWindowRect(c, ctypes.byref(r))
                if r.right - r.left >= self.sw:
                    return c
        return 0

    def _kill(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        self.hwnd = None

    def play(self, files):
        if isinstance(files, str):
            files = [files]
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            raise RuntimeError("没有可播放的文件")

        mpv = find_mpv()
        if not mpv:
            raise RuntimeError("找不到 mpv.exe（应位于 tools/mpv/mpv.exe）")

        with self.lock:
            self._kill()
            time.sleep(0.15)  # 等旧窗口彻底消失，避免抓到残留句柄

            cmd = [mpv,
                   "--no-config", "--no-osc", "--no-osd-bar", "--osd-level=0",
                   "--no-input-default-bindings", "--no-border", "--no-window-dragging",
                   "--vo=gpu", "--hwdec=auto-safe",
                   "--really-quiet", "--msg-level=all=error",
                   "--stop-screensaver=no",
                   "--geometry=%dx%d+0+0" % (self.sw, self.sh)]
            if self.mute:
                cmd.append("--mute=yes")
            if len(files) > 1:
                cmd.append("--loop-playlist=inf")
            cmd += files

            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            hwnd = None
            t0 = time.time()
            while time.time() - t0 < 15:
                h = user32.FindWindowW("mpv", None)
                if h:
                    hwnd = h
                    break
                if self.proc.poll() is not None:
                    raise RuntimeError("mpv 启动后立即退出")
                time.sleep(0.02)
            if not hwnd:
                self._kill()
                raise RuntimeError("等不到 mpv 窗口")

            self.hwnd = hwnd
            self._attach()
            self.current = files[0]
            return hwnd

    def _attach(self):
        target = self._target_hwnd()
        style = user32.GetWindowLongW(self.hwnd, GWL_STYLE) & 0xFFFFFFFF
        style = (style | WS_CHILD) & 0xFFFFFFFF
        user32.SetWindowLongW(self.hwnd, GWL_STYLE,
                              ctypes.c_long(style - (1 << 32) if style >> 31 else style).value)
        user32.SetParent(self.hwnd, target)
        # 必须用 HWND_TOP：指定兄弟窗口当锚点在这套桌面层里不生效，会被游戏壁纸盖住
        user32.SetWindowPos(self.hwnd, 0, 0, 0, self.sw, self.sh,
                            SWP_SHOWWINDOW | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        user32.ShowWindow(self.hwnd, SW_SHOW)
        # mpv 起来后还会再调整一次窗口，隔一会儿重复置顶，防止被挤下去
        for _ in range(3):
            time.sleep(0.15)
            user32.SetWindowPos(self.hwnd, 0, 0, 0, self.sw, self.sh, SWP_NOACTIVATE)

    def stop(self):
        with self.lock:
            self._kill()
            self.current = None

    def close(self):
        self.stop()


def catalog(root):
    """曲目索引：nameKey -> 视频路径（同曲多时段优先 TOD1730）"""
    idx = {}
    for p in collect_media(root):
        name = os.path.splitext(os.path.basename(p))[0]
        m = re.match(r"^(.+?)_TOD(\d+)_(NI|WI)_L$", name, re.I)
        key = m.group(1) if m else name
        tod = int(m.group(2)) if m else 9999
        score = 0 if tod == 1730 else (1 if tod == 2000 else (2 if tod == 1200 else 3))
        cur = idx.get(key)
        if cur is None or score < cur[0]:
            idx[key] = (score, p)
    return {k: v[1] for k, v in idx.items()}


def make_handler(player, media_root):
    cat = catalog(media_root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            path = u.path.rstrip("/")

            if path == "/status":
                alive = bool(player.proc and player.proc.poll() is None)
                return self._send({"ok": True,
                                   "playing": player.current if alive else None,
                                   "hwnd": player.hwnd if alive else None})

            if path == "/list":
                return self._send({"ok": True, "items": sorted(cat.keys()), "count": len(cat)})

            if path == "/play":
                if "file" in q:
                    target = q["file"][0]
                    if not os.path.isfile(target):
                        return self._send({"ok": False, "error": "文件不存在"}, 404)
                elif "name" in q:
                    name = q["name"][0]
                    target = cat.get(name)
                    if not target:
                        for k, v in cat.items():
                            if name.lower() in k.lower():
                                target = v
                                break
                    if not target:
                        return self._send({"ok": False, "error": f"曲目未找到: {name}"}, 404)
                else:
                    return self._send({"ok": False, "error": "缺少 name 或 file"}, 400)
                try:
                    hwnd = player.play(target)
                    return self._send({"ok": True, "file": target, "hwnd": hwnd})
                except Exception as e:
                    return self._send({"ok": False, "error": str(e)}, 500)

            if path == "/stop":
                try:
                    player.stop()
                    return self._send({"ok": True})
                except Exception as e:
                    return self._send({"ok": False, "error": str(e)}, 500)

            if path == "/shutdown":
                self._send({"ok": True})
                def _bye():
                    time.sleep(0.3)
                    try:
                        player.close()
                    finally:
                        os._exit(0)
                threading.Thread(target=_bye, daemon=True).start()
                return

            return self._send({"ok": False, "error": "unknown endpoint"}, 404)

    return Handler


def run_serve(player, port, media_root):
    handler = make_handler(player, media_root)
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"壁纸播放器已启动 http://127.0.0.1:{port}  曲库={len(catalog(media_root))} 首", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        player.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media", nargs="?", default=MEDIA_ROOT)
    ap.add_argument("--target", default="workerw", choices=["workerw", "progman"])
    ap.add_argument("--no-loop", action="store_true")
    ap.add_argument("--mute", action="store_true")
    ap.add_argument("--duration", type=float, default=0)
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--stop", action="store_true", help="让正在运行的服务停播并退出")
    ap.add_argument("--port", type=int, default=8788)
    args = ap.parse_args()

    if args.stop:
        import urllib.request
        try:
            # 绕开系统代理，直连本机
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            opener.open(f"http://127.0.0.1:{args.port}/shutdown", timeout=3).read()
            print("已停用，桌面恢复原生壁纸。")
            return 0
        except Exception:
            print("壁纸播放器没有在运行。")
            return 1

    player = WallpaperPlayer(args.target, args.mute)

    if args.serve:
        run_serve(player, args.port, args.media)
        return 0

    media = collect_media(args.media)
    if not media:
        print(f"没有可播放的视频: {args.media}", file=sys.stderr)
        return 1
    files = media[:1] if args.no_loop else media
    print(f"播放 {len(files)} 个文件", flush=True)
    player.play(files)
    print(f"已挂到壁纸层 hwnd={player.hwnd and hex(player.hwnd)} 按 Ctrl+C 结束", flush=True)

    try:
        t0 = time.time()
        while True:
            if player.proc and player.proc.poll() is not None:
                print("mpv 已退出", flush=True)
                break
            if args.duration and time.time() - t0 > args.duration:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        player.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
