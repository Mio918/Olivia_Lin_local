import struct
class PE:
    def __init__(self,path):
        self.path=path
        self.d=open(path,'rb').read()
        d=self.d
        e=struct.unpack('<I',d[0x3c:0x40])[0]
        nsec=struct.unpack('<H',d[e+6:e+8])[0]
        optsize=struct.unpack('<H',d[e+20:e+22])[0]
        self.imagebase=struct.unpack('<Q',d[e+24+24:e+24+32])[0]
        self.sections=[]
        so=e+24+optsize
        for i in range(nsec):
            off=so+i*40
            name=d[off:off+8].rstrip(b'\x00').decode('latin1')
            vsz,va,rsz,roff=struct.unpack('<IIII',d[off+8:off+24])
            self.sections.append(dict(name=name,va=va,vsz=vsz,roff=roff,rsz=rsz))
        self.dir=[]
        # data directories for exports
    def rva2off(self,rva):
        for s in self.sections:
            if s['va']<=rva<s['va']+max(s['vsz'],s['rsz']):
                return s['roff']+(rva-s['va'])
        return None
    def off2rva(self,off):
        for s in self.sections:
            if s['roff']<=off<s['roff']+s['rsz']:
                return s['va']+(off-s['roff'])
        return None
    def sec(self,name):
        for s in self.sections:
            if s['name']==name: return s
