import re
import collections
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"C:\CodeAI\Olivia_Lin\feapp_unpacked\assets\main-31595bd3.js"
s = open(path, encoding="utf-8", errors="replace").read()

acts = re.findall(r"action:\s*[\"']([A-Za-z0-9_]+)[\"']", s)
print("=== action 列表 ===")
for a, c in collections.Counter(acts).most_common(60):
    print(f"  {a:36s} x{c}")

print("\n=== 含 WebPlayer / View / Create 的标识符 ===")
seen = set()
for m in re.finditer(r"[A-Za-z_$][A-Za-z0-9_$]*(?:WebPlayer|Player)[A-Za-z0-9_$]*", s):
    seen.add(m.group(0))
for m in re.finditer(r"[A-Za-z_$][A-Za-z0-9_$]*(?:CreateView|showView|ShowView)[A-Za-z0-9_$]*", s):
    seen.add(m.group(0))
for x in sorted(seen):
    print("  ", x)
