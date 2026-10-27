import sys,struct
sys.path.insert(0,'.')
from pe import PE
import capstone
def dump(path, start, end, base=None):
    p=PE(path); sec=p.sec('.text')
    code=p.d[sec['roff']:sec['roff']+sec['rsz']]
    md=capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64); md.detail=True
    i=start-sec['va']
    for ins in md.disasm(code[i:end-sec['va']], p.imagebase+start):
        extra=""
        for op in ins.operands:
            if op.type==capstone.x86.X86_OP_MEM and op.mem.base==capstone.x86.X86_REG_RIP:
                tgt=ins.address+ins.size+op.mem.disp
                rva=tgt-p.imagebase
                off=p.rva2off(rva)
                if off is not None:
                    s=p.d[off:off+80].split(b'\x00')[0]
                    try: txt=s.decode('ascii')
                    except: txt=""
                    if txt and all(32<=ord(c)<127 for c in txt[:40]):
                        extra=f"  ; \"{txt[:70]}\""
        if ins.mnemonic=='call' and ins.op_str.startswith('0x'):
            t=int(ins.op_str,16)-p.imagebase
            extra+=f"  ; call rva={hex(t)}"
        print(f"{ins.address-p.imagebase:08x}  {ins.mnemonic:8s} {ins.op_str}{extra}")
if __name__=='__main__':
    dump(sys.argv[1], int(sys.argv[2],16), int(sys.argv[3],16))
