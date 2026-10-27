# -*- coding: utf-8 -*-
"""桌面壁纸视频播放（由 server.py 托管）

把视频渲染到 Windows 桌面壁纸层（WorkerW），效果等同 Wallpaper Engine：
视频铺满桌面，桌面图标和普通窗口仍然叠在上面。

技术要点
--------
1. GDI 内容进不了桌面层的 DWM 合成（实测绿色填充窗口 0% 可见），必须 GPU 渲染。
2. 用 mpv 做渲染后端（D3D11 / 硬件解码 / 自带音频同步）。
3. mpv 的 --wid 在此环境报 `unable to create window`；改为：让 mpv 建自己的窗口，
   抓到 HWND 后 SetParent 到 WorkerW 并全屏。句柄不变，DXGI swapchain 继续有效。
4. 每次播放重启 mpv（约 0.3 秒），比维护 IPC 长连接稳。
"""
import ctypes
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)

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
user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.EnumChildWindows.argtypes = [w.HWND,
                                    ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM),
                                    w.LPARAM]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.InvalidateRect.argtypes = [w.HWND, ctypes.c_void_p, w.BOOL]
user32.RedrawWindow.argtypes = [w.HWND, ctypes.c_void_p, w.HRGN, w.UINT]

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"}
SWP_SHOWWINDOW, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0040, 0x0010, 0x0020
GWL_STYLE = -16
WS_CHILD = 0x40000000
SW_SHOW = 5

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MEDIA_ROOT = os.path.join(HERE, "media")


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
    for rel in (os.path.join(ROOT, "tools", "mpv", "mpv.exe"),
                os.path.join(ROOT, "tools", "mpv", "mpv-x86_64", "mpv.exe")):
        if os.path.isfile(rel):
            return rel
    base = os.path.join(ROOT, "tools", "mpv")
    if os.path.isdir(base):
        for root, _, files in os.walk(base):
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


def catalog(root=None):
    """曲目索引：nameKey -> 视频路径（同一首多时段画面时优先 TOD1730）"""
    root = root or MEDIA_ROOT
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

    @property
    def alive(self):
        return bool(self.proc and self.proc.poll() is None)

    def play(self, files, start=0):
        if isinstance(files, str):
            files = [files]
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            raise RuntimeError("没有可播放的文件")

        mpv = find_mpv()
        if not mpv:
            raise RuntimeError("找不到 mpv.exe（应在 tools/mpv/mpv.exe）")

        with self.lock:
            self._kill()
            time.sleep(0.15)

            cmd = [mpv,
                   "--no-config", "--no-osc", "--no-osd-bar", "--osd-level=0",
                   "--no-input-default-bindings", "--no-border", "--no-window-dragging",
                   "--vo=gpu", "--hwdec=auto-safe",
                   "--really-quiet", "--msg-level=all=error",
                   "--stop-screensaver=no",
                   "--geometry=%dx%d+0+0" % (self.sw, self.sh)]
            if start and start > 0:
                cmd.append("--start=%.1f" % float(start))
            if self.mute:
                cmd.append("--mute=yes")
            if len(files) > 1:
                cmd.append("--loop-playlist=inf")
            cmd += files

            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)

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

    def play_by_name(self, name, start=0):
        cat = catalog()
        target = cat.get(name)
        if not target:
            low = name.lower()
            for k, v in cat.items():
                if low in k.lower():
                    target = v
                    break
        if not target:
            raise FileNotFoundError(f"曲目未找到: {name}")
        return self.play(target, start=start)

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

    def _wake_wallpaper(self):
        """播放窗口销毁后 DWM 可能停在最后一帧甚至黑屏，
        主动让游戏壁纸窗口重绘一次，把原生画面拉回来。"""
        progman = user32.FindWindowW("Progman", None)
        if not progman:
            return
        qt = 0
        ww = 0
        for c in _enum_child(progman):
            k = _cls(c)
            if "Qt" in k and _title(c) == "林离Olivia":
                r = w.RECT()
                user32.GetWindowRect(c, ctypes.byref(r))
                if r.right - r.left >= self.sw:
                    qt = c
            elif k == "WorkerW":
                ww = c
        RDW_INV, RDW_ERASE, RDW_UPNOW, RDW_FRAME, RDW_ALLCHILD = 0x0001, 0x0004, 0x0100, 0x0400, 0x0080
        if qt:
            try:
                # 关键：被完全遮挡时系统会跳过它的渲染，必须显式 SHOW 一次把它叫醒
                user32.ShowWindow(qt, SW_SHOW)
                user32.SetWindowPos(qt, 0, 0, 0, self.sw, self.sh,
                                    SWP_SHOWWINDOW | SWP_FRAMECHANGED | SWP_NOACTIVATE)
                user32.InvalidateRect(qt, None, True)
                user32.RedrawWindow(qt, None, None, RDW_INV | RDW_ERASE | RDW_UPNOW | RDW_FRAME)
            except Exception:
                pass
        if ww:
            try:
                user32.RedrawWindow(ww, None, None, RDW_INV | RDW_ERASE | RDW_ALLCHILD | RDW_UPNOW)
            except Exception:
                pass

    def stop(self):
        with self.lock:
            # 先把播放窗移出屏幕：游戏壁纸从"被完全遮挡"变成"可见"，DWM 才会重绘它。
            # 直接销毁窗口的话，下层窗口收不到重绘通知，桌面会停在最后一帧甚至黑屏。
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
            if self.hwnd:
                try:
                    user32.SetWindowPos(self.hwnd, 0, -20000, -20000, 0, 0,
                                        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                    time.sleep(0.4)
                except Exception:
                    pass
            self._kill()
            for delay in (0.0, 0.3, 0.7):
                time.sleep(delay)
                self._wake_wallpaper()
            self.current = None

    def status(self):
        return {"playing": self.current if self.alive else None,
                "hwnd": self.hwnd if self.alive else None}
