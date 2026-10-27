import sys,struct,math,collections
sys.path.insert(0,'.')
from pe import PE
def ent(b):
    if not b: return 0
    c=collections.Counter(b); n=len(b)
    return -sum(v/n*math.log2(v/n) for v in c.values())
def info(path):
    p=PE(path); d=p.d
    e=struct.unpack('<I',d[0x3c:0x40])[0]
    nsec=struct.unpack('<H',d[e+6:e+8])[0]
    optsize=struct.unpack('<H',d[e+20:e+22])[0]
    opt=e+24
    magic=struct.unpack('<H',d[opt:opt+2])[0]
    pe32plus = magic==0x20b
    # data dirs
    dd = opt + (112 if pe32plus else 96)
    nrva = struct.unpack('<I', d[opt+(108 if pe32plus else 92):][:4])[0]
    dirs=[struct.unpack('<II', d[dd+i*8:dd+i*8+8]) for i in range(min(nrva,16))]
    # imports
    imp_rva, imp_sz = dirs[1]
    imps=[]
    if imp_rva:
        off=p.rva2off(imp_rva)
        while off:
            ent_desc=d[off:off+20]
            if len(ent_desc)<20 or ent_desc==b'\x00'*20: break
            oft,ts,fc,name_rva,ft = struct.unpack('<IIIII', ent_desc)
            if name_rva==0 and oft==0: break
            no=p.rva2off(name_rva)
            dll=d[no:d.find(b'\x00',no)].decode('latin1') if no else '?'
            fns=[]
            thunk = oft or ft
            to=p.rva2off(thunk)
            while to and len(d[to:to+8])==8:
                v=struct.unpack('<Q', d[to:to+8])[0] if pe32plus else struct.unpack('<I',d[to:to+4])[0]
                if v==0: break
                if v & (1<<63 if pe32plus else 1<<31):
                    fns.append(f"#{v & 0xffff}")
                else:
                    fo=p.rva2off(v & 0x7fffffff)
                    if fo: fns.append(d[fo+2:d.find(b'\x00',fo+2)].decode('latin1'))
                to += 8 if pe32plus else 4
            imps.append((dll,fns))
            off+=20
    clr = dirs[14][0] if len(dirs)>14 else 0
    print(f"== {path.split('/')[-1]}  size={len(d)}  pe32plus={pe32plus}  imagebase={hex(p.imagebase)}  clr={bool(clr)}")
    for s in p.sections:
        raw=d[s['roff']:s['roff']+s['rsz']]
        print(f"   {s['name']:8s} va={hex(s['va']):>10s} vsz={hex(s['vsz']):>9s} rawsz={hex(s['rsz']):>9s} entropy={ent(raw[:200000]):.2f}")
    for dll,fns in imps:
        print(f"   IMP {dll} ({len(fns)}) : {', '.join(fns[:14])}{' ...' if len(fns)>14 else ''}")
if __name__=='__main__':
    for a in sys.argv[1:]: info(a)
