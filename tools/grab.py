#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 PrintWindow 抓取指定窗口内容（不受遮挡影响），输出 BMP。

用法: py -3.12 grab.py <hwnd_hex> <out.bmp> [w] [h]
"""
import ctypes, ctypes.wintypes as wt, struct, sys

u = ctypes.windll.user32
g = ctypes.windll.gdi32
g.CreateCompatibleDC.restype = wt.HDC
g.CreateCompatibleDC.argtypes = [wt.HDC]
g.CreateCompatibleBitmap.restype = wt.HBITMAP
g.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g.SelectObject.restype = wt.HGDIOBJ
g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g.DeleteObject.argtypes = [wt.HGDIOBJ]
g.DeleteDC.argtypes = [wt.HDC]
g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
u.GetDC.restype = wt.HDC
u.GetDC.argtypes = [wt.HWND]
u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]


class BIH(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', wt.LONG),
                ('biYPelsPerMeter', wt.LONG), ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]


def grab(hwnd, path, w, h, flags=2):
    hwnd = wt.HWND(hwnd)
    hdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, flags)
    bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    hdr = struct.pack('<2sIHHI', b'BM', 14 + 40 + w * h * 4, 0, 0, 54)
    bih = struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(hdr + bih + buf.raw)
    u.ReleaseDC(None, hdc)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    return ok


if __name__ == '__main__':
    hwnd = int(sys.argv[1], 16)
    out = sys.argv[2]
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1198
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 906
    print('PrintWindow ok =', grab(hwnd, out, w, h))
