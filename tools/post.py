#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""往指定窗口直接投递鼠标消息（不依赖前台焦点）。

为什么需要它：这个应用常驻桌面壁纸层，实际使用时窗口往往被浏览器等程序盖住，
SetForegroundWindow 会被系统的前台锁拒绝，物理点击就全打到别人身上了。
PostMessage 直接把消息投给目标 HWND，绕过焦点与 Z 序。

用法：
  py -3.12 post.py <hwnd_hex> move  x y
  py -3.12 post.py <hwnd_hex> click x y
  py -3.12 post.py <hwnd_hex> dbl   x y
坐标是该窗口的客户区坐标。
"""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
MK_LBUTTON = 0x0001


def lp(x, y):
    return (y << 16) | (x & 0xFFFF)


def move(h, x, y):
    user32.PostMessageW(h, WM_MOUSEMOVE, 0, lp(x, y))


def click(h, x, y, dbl=False):
    move(h, x, y)
    time.sleep(0.06)
    user32.PostMessageW(h, WM_LBUTTONDOWN, MK_LBUTTON, lp(x, y))
    time.sleep(0.08)
    user32.PostMessageW(h, WM_LBUTTONUP, 0, lp(x, y))
    if dbl:
        time.sleep(0.04)
        user32.PostMessageW(h, WM_LBUTTONDBLCLK, MK_LBUTTON, lp(x, y))
        time.sleep(0.08)
        user32.PostMessageW(h, WM_LBUTTONUP, 0, lp(x, y))


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    hwnd = int(sys.argv[1], 16)
    act = sys.argv[2]
    x, y = int(sys.argv[3]), int(sys.argv[4])
    if act == "move":
        move(hwnd, x, y)
    elif act == "click":
        click(hwnd, x, y)
    elif act == "dbl":
        click(hwnd, x, y, dbl=True)
    else:
        raise SystemExit("act 只能是 move/click/dbl")
    print(f"{act} -> {hwnd:#x} ({x},{y})")
