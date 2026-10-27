import re,sys
def good(s):
    if sum(c.islower() for c in s)<2: return False
    if len(re.findall(r'[A-Z]{3,}', s))>len(s)/2: return False
    return True
p=sys.argv[1]; ml=int(sys.argv[2]) if len(sys.argv)>2 else 6
d=open(p,'rb').read()
out=[]
for m in re.finditer(rb'[\x20-\x7e]{%d,}'%ml, d):
    s=m.group().decode('ascii')
    if good(s): out.append((m.start(),s))
for m in re.finditer((rb'(?:[\x20-\x7e]\x00){%d,}'%ml), d):
    s=m.group().decode('utf-16-le',errors='replace')
    if good(s): out.append((m.start(),s))
for o,s in sorted(out): print(f"{o:08x}\t{s}")
