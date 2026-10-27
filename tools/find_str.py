import re
import sys

path = sys.argv[1]
pats = [p.encode() for p in (sys.argv[2:] or ["TOD", "videoByTodView", "feplayer", "webPlayerControl"])]

data = open(path, "rb").read()
print(f"file={path} size={len(data)/1048576:.1f}MB")

# ASCII / UTF-8 可见字符串，长度 >= 6
for m in re.finditer(rb"[\x20-\x7e]{6,}", data):
    s = m.group()
    try:
        t = s.decode("ascii")
    except Exception:
        continue
    for p in pats:
        if p in s:
            print(f"  @{m.start():#x}  {t[:160]}")
            break

# UTF-16LE
for m in re.finditer(rb"(?:[\x20-\x7e]\x00){6,}", data):
    t = m.group().decode("utf-16-le", errors="replace")
    for p in pats:
        if p.decode() in t:
            print(f"  @{m.start():#x}  [u16] {t[:160]}")
            break
