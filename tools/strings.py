#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯 Python 版的 strings：从 PE / 任意二进制里抽 ASCII 与 UTF-16LE 字符串。

用法：
  py -3.12 strings.py <文件> [关键字...]
"""
import re
import sys

ASCII_RE = re.compile(rb"[\x20-\x7e]{4,}")
UTF16_RE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")


def extract(path):
    data = open(path, "rb").read()
    out = set()
    for m in ASCII_RE.finditer(data):
        out.add(m.group().decode("ascii"))
    for m in UTF16_RE.finditer(data):
        out.add(m.group().decode("utf-16-le"))
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    items = extract(sys.argv[1])
    kws = [k.lower() for k in sys.argv[2:]]
    for s in sorted(items):
        if not kws or any(k in s.lower() for k in kws):
            print(s)


if __name__ == "__main__":
    main()
