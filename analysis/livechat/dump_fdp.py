"""Dump extracted .fdp (FileDescriptorProto) as readable .proto text."""
import sys
from google.protobuf import descriptor_pb2


def tname(f):
    if f.type_name:
        return ('.' if f.type_name.startswith('.') else '') + f.type_name
    return {1: 'double', 2: 'float', 3: 'int64', 4: 'uint64', 5: 'int32', 6: 'fixed64',
            7: 'fixed32', 8: 'bool', 9: 'string', 10: 'group', 11: 'message',
            12: 'bytes', 13: 'uint32', 14: 'enum', 15: 'sfixed32', 16: 'sfixed64',
            17: 'sint32', 18: 'sint64'}[f.type]


def dump(fdp_path):
    fdp = descriptor_pb2.FileDescriptorProto.FromString(open(fdp_path, 'rb').read())
    out = ['syntax "%s";' % (fdp.syntax or 'proto2'),
           'package %s;' % fdp.package,
           'file: %s' % fdp.name, '']
    for e in fdp.enum_type:
        out.append('enum %s {' % e.name)
        for v in e.value:
            out.append('  %s = %d;' % (v.name, v.number))
        out += ['}', '']

    def msg(m, indent=''):
        out.append(indent + 'message %s {' % m.name)
        for f in m.field:
            lbl = {1: 'optional', 2: 'required', 3: 'repeated'}.get(f.label, '')
            oneof = (' // oneof: ' + m.oneof_decl[f.oneof_index].name) if f.HasField('oneof_index') else ''
            out.append('  %s%s %s %s = %d;%s' % (indent, lbl, tname(f), f.name, f.number, oneof))
        for nt in m.nested_type:
            msg(nt, indent + '  ')
        out.extend([indent + '}', ''])

    for m in fdp.message_type:
        msg(m)
    return '\n'.join(out)


for p in sys.argv[1:]:
    print('=' * 30, p)
    print(dump(p))
