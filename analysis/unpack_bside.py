#!/usr/bin/env python3
"""BSide: Olivia Lin — 资源解包工具

用法:
    python unpack_bside.py <安装目录> [输出目录]

做的事:
  1. 自动挑选版本号最大的子目录（如 0.0.9.627）
  2. 把 resources/*.dat（标准 ZIP）解包到 <输出目录>/<名字>/
  3. 打印 assets/songlist.dat、conf.app.dat 的密文特征（只读，不尝试解密）
  4. 若存在 Wallpaper_* / songlist 等明文素材，仅列出清单与体积

不修改安装目录中的任何文件。
"""
import os
import sys
import zipfile

DATS = [("resources/feapp.dat", "feapp"),
        ("resources/webplayer.dat", "webplayer"),
        ("resources/feplayer.dat", "feplayer")]
PLAINTEXT_DIRS = ["assets/Wallpaper_Presence", "assets/Wallpaper_Ambience"]


def pick_version_root(install):
    cands = [d for d in os.listdir(install)
             if os.path.isdir(os.path.join(install, d)) and d[0].isdigit()]
    if not cands:
        return install
    cands.sort(key=lambda v: [int(x) if x.isdigit() else 0 for x in v.replace("-", ".").split(".")])
    return os.path.join(install, cands[-1])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    install = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.getcwd(), "bside_unpacked")
    root = pick_version_root(install)
    print(f"[*] 版本目录: {root}")

    for rel, name in DATS:
        src = os.path.join(root, rel)
        if not os.path.exists(src):
            print(f"[!] 缺失: {rel}")
            continue
        dst = os.path.join(out, name)
        os.makedirs(dst, exist_ok=True)
        with zipfile.ZipFile(src) as z:
            z.extractall(dst)
            total = sum(i.file_size for i in z.infolist())
            print(f"[+] {rel}: {len(z.namelist())} 条目, 解压 {total/1048576:.1f} MB -> {dst}")

    for rel in PLAINTEXT_DIRS:
        d = os.path.join(root, rel)
        if os.path.isdir(d):
            files = os.listdir(d)
            size = sum(os.path.getsize(os.path.join(d, f)) for f in files)
            print(f"[=] {rel}: {len(files)} 个文件, {size/1048576:.0f} MB (明文)")

    for rel in ["assets/songlist.dat", "conf.app.dat"]:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            with open(p, "rb") as f:
                head = f.read(16)
            n = os.path.getsize(p)
            print(f"[#] {rel}: {n} 字节, 16 字节对齐={n % 16 == 0}, head={head.hex()}  (AES 信封，需运行时取密钥)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
