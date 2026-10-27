"""Extract embedded serialized FileDescriptorProto blobs (protobuf descriptors) from a PE binary.

Generated protobuf C++ code stores the serialized FileDescriptorProto as a plain
rdata string literal starting with field 1 (name). We locate "<name>.proto" and
walk the top-level wire fields to find the blob end, then parse it.
"""
import re
import sys
from google.protobuf import descriptor_pb2

DLL = sys.argv[1] if len(sys.argv) > 1 else r'C:/Games/steam/steamapps/common/BSide Olivia Lin Test/0.0.9.627/plugins/Studio/NutLiveChat.dll'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'C:/CodeAI/Olivia_Lin/analysis/livechat'
data = open(DLL, 'rb').read()


def read_varint(buf, off):
    r, s = 0, 0
    while True:
        b = buf[off]
        off += 1
        r |= (b & 0x7F) << s
        if not b & 0x80:
            return r, off
        s += 7
        if s > 63:
            raise ValueError('varint too long')


def parse_fields(buf, off, max_field=30):
    end = off
    while end < len(buf):
        try:
            tag, pos = read_varint(buf, end)
        except IndexError:
            break
        fnum, wt = tag >> 3, tag & 7
        if fnum == 0 or fnum > max_field:
            break
        if wt == 0:
            _, pos = read_varint(buf, pos)
        elif wt == 1:
            pos += 8
        elif wt == 2:
            ln, pos = read_varint(buf, pos)
            if ln > len(buf) - pos or ln > 20_000_000:
                break
            pos += ln
        elif wt == 5:
            pos += 4
        else:
            break
        end = pos
    return end


all_protos = sorted(set(m.group() for m in re.finditer(rb'[a-zA-Z0-9_]{2,40}\.proto', data)))
print('proto file refs:', all_protos)

for name in all_protos:
    if name in (b'descriptor.proto', b'any.proto', b'wrappers.proto', b'type.proto',
                b'source_context.proto', b'placeholder.proto', b'google.proto'):
        continue  # well-known types shipped with protobuf itself
    needle = b'\x0a' + bytes([len(name)]) + name
    idxs = [m.start() for m in re.finditer(re.escape(needle), data)]
    for st in idxs:
        for start in range(st, st - 8, -1):
            end = parse_fields(data, start)
            blob = data[start:end]
            try:
                fdp = descriptor_pb2.FileDescriptorProto.FromString(blob)
            except Exception:
                continue
            if fdp.name.endswith('.proto') and (fdp.message_type or fdp.enum_type):
                out = OUT + '/' + fdp.name.replace('.proto', '') + '.fdp'
                open(out, 'wb').write(blob)
                print('parsed', fdp.name, 'at', start, 'len', end - start,
                      'msgs:', [m.name for m in fdp.message_type],
                      'enums:', [e.name for e in fdp.enum_type], '->', out)
                break
