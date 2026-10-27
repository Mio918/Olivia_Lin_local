#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 PE 里抠出内嵌的 Qt 资源（rcc），把 .ui / .png 等原始文件还原出来。

rcc 结构：magic "qres" + version + treeOffset + dataOffset + nameOffset，
节点 struct Node { uint name; ushort flags; uint childCount; uint dataOffset }，
flags & 1 = 目录，flags & 2 = zlib 压缩。

用法：
  py -3.12 rcc.py <dll> <输出目录> [过滤关键字]
"""
import os
import struct
import sys
import zlib


def find_qres(data):
    out = []
    i = data.find(b"qres")
    while i != -1:
        out.append(i)
        i = data.find(b"qres", i + 1)
    return out


def parse(data, base):
    _ver, tree_off, data_off, name_off = struct.unpack_from(">IIII", data, base + 4)
    tree_off += base
    data_off += base
    name_off += base

    def read_name(off):
        if off == 0xFFFFFFFF:
            return ""
        end = data.index(b"\x00", name_off + off)
        return data[name_off + off:end].decode("utf-8", "replace")

    items = []

    def collect(node_off, path):
        name_idx, flags, child_count, data_offset = struct.unpack_from(">IHII", data, node_off)
        name = read_name(name_idx)
        cur = path + ([name] if name else [])
        if flags & 1:
            off = node_off + 14
            for _ in range(child_count):
                _n, nflags, nchild, _d = struct.unpack_from(">IHII", data, off)
                collect(off, cur)
                off += 14 + (14 * nchild if nflags & 1 else 0)
        else:
            start = data_off + data_offset
            if flags & 2:
                try:
                    raw = zlib.decompressobj().decompress(data[start:start + (1 << 24)])
                except Exception:
                    raw = b""
            else:
                # 未压缩资源没有长度字段，按到末尾截取（够用即可）
                raw = data[start:start + (1 << 24)]
            items.append(("/".join(cur), raw))

    collect(tree_off, [])
    return items


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    dll, dest = sys.argv[1], sys.argv[2]
    kw = sys.argv[3].lower() if len(sys.argv) > 3 else ""
    data = open(dll, "rb").read()
    for base in find_qres(data):
        try:
            items = parse(data, base)
        except Exception as e:
            print(f"[rcc@{base:#x}] 解析失败: {e}")
            continue
        if not items:
            continue
        print(f"[rcc@{base:#x}] {len(items)} 个资源")
        for name, raw in items:
            if kw and kw not in name.lower():
                continue
            clean = name.replace(":/", "").lstrip("/")
            path = os.path.join(dest, clean)
            os.makedirs(os.path.dirname(path) or dest, exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
            print(f"  {name}  {len(raw)}B")


if __name__ == "__main__":
    main()
