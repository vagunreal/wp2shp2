#!/usr/bin/env python3
"""把采集的 JSON 代码记录转成可读反汇编（3.12，正确跳过 inline cache）"""
import json, sys, opcode, dis

MARKER = ('C_ASSERT_ARMORED_INDEX', 'C_ENTER_CO_OBJECT_INDEX', 'C_LEAVE_CO_OBJECT_INDEX')

def const_repr(c, names, consts):
    if isinstance(c, dict):
        if '__code__' in c:
            return '<code %s>' % c['__code__'].get('name', '?')
        if '__bytes__' in c:
            return 'b:%s' % c['__bytes__'][:24]
        if '__obj__' in c:
            return '⟦%s⟧' % c['__obj__']
    return repr(c)[:90]

CACHE_ENTRIES = dis._inline_cache_entries

def decode(rec, out):
    code = bytes.fromhex(rec['code_hex'])
    names = rec['names']
    consts = rec['consts']
    varnames = rec['varnames']
    freevars = rec['freevars']
    cellvars = rec['cellvars']
    i = 0
    ext = 0
    while i < len(code):
        op = code[i]
        arg = code[i+1]
        opname = opcode.opname[op]
        extra = ''
        if op == opcode.opmap['EXTENDED_ARG']:
            ext = (ext | arg) << 8
            out.append((i, 'EXTENDED_ARG', str(arg)))
            i += 2
            continue
        realarg = (ext | arg)
        ext = 0
        if opname in ('LOAD_GLOBAL',):
            idx = realarg >> 1
            nm = names[idx] if idx < len(names) else '?'
            extra = '%s%s' % (nm, ' NULL' if realarg & 1 else '')
        elif opname in ('LOAD_NAME', 'STORE_NAME', 'STORE_GLOBAL', 'DELETE_NAME', 'IMPORT_NAME', 'LOAD_FROM_DICT_OR_GLOBALS'):
            extra = names[realarg] if realarg < len(names) else '?'
        elif opname in ('LOAD_ATTR', 'LOAD_METHOD'):
            idx = realarg >> 1
            extra = names[idx] if idx < len(names) else '?'
            if realarg & 1:
                extra += ' [m]'
        elif opname in ('STORE_ATTR', 'DELETE_ATTR'):
            extra = names[realarg] if realarg < len(names) else '?'
        elif opname in ('LOAD_FAST', 'LOAD_FAST_CHECK', 'LOAD_FAST_AND_CLEAR', 'STORE_FAST', 'DELETE_FAST'):
            extra = varnames[realarg] if realarg < len(varnames) else '?'
        elif opname in ('LOAD_DEREF', 'STORE_DEREF', 'DELETE_DEREF', 'MAKE_CELL'):
            allv = cellvars + freevars
            extra = allv[realarg] if realarg < len(allv) else '?'
        elif opname == 'LOAD_CONST':
            extra = const_repr(consts[realarg] if realarg < len(consts) else None, names, consts)
        elif opname == 'IMPORT_FROM':
            extra = names[realarg] if realarg < len(names) else '?'
        elif opname in ('JUMP_FORWARD', 'POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE', 'POP_JUMP_IF_NONE',
                        'POP_JUMP_IF_NOT_NONE', 'SEND', 'FOR_ITER'):
            extra = '→ %d' % (i + 2 * (1 + ncache) + realarg * 2)
        elif opname in ('JUMP_BACKWARD', 'POP_JUMP_BACKWARD_IF_FALSE', 'POP_JUMP_BACKWARD_IF_TRUE',
                        'POP_JUMP_BACKWARD_IF_NONE', 'POP_JUMP_BACKWARD_IF_NOT_NONE'):
            extra = '→ %d' % (i + 2 * (1 + ncache) - realarg * 2)
        elif opname == 'COMPARE_OP':
            extra = dis.cmp_op[realarg >> 4] if (realarg >> 4) < len(dis.cmp_op) else '?'
        ncache = CACHE_ENTRIES[op]
        out.append((i, opname + ('' if ncache == 0 else ' +%dc' % ncache), extra))
        i += 2 * (1 + ncache)

def render(rec):
    lines = []
    args = rec['varnames'][:rec['argcount'] + rec.get('kwonlyargcount', 0)]
    lines.append('### %s  (line %s)' % (rec['qualname'], rec.get('firstlineno')))
    lines.append('args=%r varnames=%r' % (args, rec['varnames']))
    lines.append('names=%r' % (rec['names'],))
    lines.append('consts=%s' % ', '.join(const_repr(c, rec['names'], rec['consts']) for c in rec['consts']))
    lines.append('freevars=%r cellvars=%r flags=%#x' % (rec['freevars'], rec['cellvars'], rec['flags']))
    out = []
    if rec['code_hex']:
        decode(rec, out)
    for off, name, extra in out:
        lines.append('%5d  %-34s %s' % (off, name, extra))
    return '\n'.join(lines)

if __name__ == '__main__':
    store = json.load(open(sys.argv[1], encoding='utf-8'))
    modfilter = sys.argv[2] if len(sys.argv) > 2 else None
    for k in sorted(store):
        if modfilter and not k.startswith(modfilter):
            continue
        rec = store[k]
        print(render(rec))
        print()
