#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按比例缩小 32 位 BMP（纯标准库，避免依赖 Pillow）。

用法：py -3.12 shrink.py in.bmp out.bmp [factor]
"""
import struct
import sys


def shrink(src_path, dst_path, factor=2):
    src = open(src_path, "rb").read()
    off = struct.unpack("<I", src[10:14])[0]
    w, h = struct.unpack("<ii", src[18:26])
    topdown = h < 0
    h = abs(h)
    px = src[off:]

    nw, nh = max(1, w // factor), max(1, h // factor)
    rows = []
    for y in range(nh):
        sy = y * factor
        base = sy * w * 4
        row = bytearray()
        for x in range(nw):
            p = base + x * factor * 4
            row += px[p:p + 4]
        rows.append(bytes(row))
    if topdown:
        rows.reverse()          # 统一输出自底向上
    body = b"".join(rows)
    size = len(body)
    with open(dst_path, "wb") as f:
        f.write(b"BM")
        f.write(struct.pack("<IHHI", 54 + size, 0, 0, 54))
        f.write(struct.pack("<IiiHHIIiiII", 40, nw, nh, 1, 32, 0, size, 0, 0, 0, 0))
        f.write(body)
    print(f"{nw}x{nh}  {size // 1024} KB")


if __name__ == "__main__":
    shrink(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2)
