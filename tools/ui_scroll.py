#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向指定窗口发送滚轮/按键（模拟真实输入）。

用法：py -3.12 ui_scroll.py "<窗口标题关键字>" <滚动格数，正=下滚，负=上滚>
"""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

MOUSEEVENTF_WHEEL = 0x0800


def find_window(keyword):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if keyword.lower() in buf.value.lower():
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else (None, None)


def main():
    kw = sys.argv[1]
    notches = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    hwnd, title = find_window(kw)
    if not hwnd:
        print("没找到窗口")
        return
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    cx = (r.left + r.right) // 2
    cy = (r.top + r.bottom) // 2

    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    user32.SetCursorPos(cx, cy)
    time.sleep(0.2)
    step = 120 if notches > 0 else -120
    for _ in range(abs(notches)):
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, step, 0)
        time.sleep(0.12)
    print(f"已在「{title}」上滚动 {notches} 格")


if __name__ == "__main__":
    main()
