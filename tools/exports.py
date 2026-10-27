import sys,struct,re,collections
sys.path.insert(0,'.')
from pe import PE
def exports(path):
    p=PE(path); d=p.d
    e=struct.unpack('<I',d[0x3c:0x40])[0]
    opt=e+24; pe32plus=struct.unpack('<H',d[opt:opt+2])[0]==0x20b
    dd=opt+(112 if pe32plus else 96)
    exp_rva,exp_sz=struct.unpack('<II', d[dd:dd+8])
    if not exp_rva: return []
    off=p.rva2off(exp_rva)
    _,_,_,_,name_rva,base, nfunc, nname, afunc, aname, aord = struct.unpack('<IIHHIIIIIII', d[off:off+40])
    names=[]
    no=p.rva2off(aname)
    for i in range(nname):
        nr=struct.unpack('<I', d[no+i*4:no+i*4+4])[0]
        so=p.rva2off(nr)
        if so: names.append(d[so:d.find(b'\x00',so)].decode('latin1'))
    return names
path=sys.argv[1]
names=exports(path)
print(f"{path.split('/')[-1]}: {len(names)} exports")
cls=collections.Counter()
for n in names:
    m=re.match(r'\?+(\w+)@(\w+)@@', n)
    if m: cls[f"{m.group(2)}::{m.group(1)}"]+=1
    else:
        m2=re.match(r'\?+(\w+)@@', n)
        cls[m2.group(1) if m2 else n[:40]]+=1
for k,v in cls.most_common(30): print(f"   {k} ({v})")
