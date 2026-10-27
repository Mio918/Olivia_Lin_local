import sys,struct
sys.path.insert(0,'.')
from pe import PE
def scan(path, target_rvas):
    p=PE(path); sec=p.sec('.text')
    code=p.d[sec['roff']:sec['roff']+sec['rsz']]
    out={t:[] for t in target_rvas}
    for i in range(len(code)-7):
        b0=code[i]
        if b0 not in (0x48,0x4c,0x49,0x4d): continue
        if code[i+1] not in (0x8d,0x8b,0x89,0x8c): continue
        if (code[i+2]&0xC7)!=0x05: continue
        disp=struct.unpack('<i',code[i+3:i+7])[0]
        rva_here=sec['va']+i
        tgt=rva_here+7+disp
        if tgt in out: out[tgt].append(rva_here)
    return out,p
if __name__=='__main__':
    path=sys.argv[1]; tg=[int(x,16) for x in sys.argv[2:]]
    r,p=scan(path,tg)
    for t,v in r.items(): print(hex(t), [hex(x) for x in v[:10]])
