#!/usr/bin/env python3
"""partB.py 与原始字节码的结构化比对（复用 verify_partC 的数据流签名法）。

方法（与 verify_partC.py 相同，已在 partC 22/22 验证）：
- 可达性分析剔除 PyArmor 包裹层（死区 return 包装、完整性桩）
- RETURN/JUMP/POP_TOP 等控制流指令跳过 → 天然豁免「本机 3.12.3 就地
  return」与「exe 内 Python 的分支汇合 JUMP_FORWARD + PyArmor 改写」差异
  （两者语义等价，只是编译/加密产物结构不同）
- 局部变量按槽位编号归一（去掉 __assert_armored__ 占位）
- 元组常量：新采集记录有真值全比对；旧记录按通配
"""
import json, re, sys, types, opcode, dis

# ---- 从 verify_partC.py 复制核心实现 ----
def conv_const(c):
    if isinstance(c, dict):
        if '__code__' in c:
            return ('<code>',)
        if '__bytes__' in c:
            # 单字节 b'\x00' 是源码真实常量（rstrip/包含判断），非 PyArmor blob
            raw = bytes.fromhex(c['__bytes__']) if isinstance(c['__bytes__'], str) else c['__bytes__']
            if raw == b'\x00':
                return raw
            return ('<blob>',)
        if '__tuple__' in c:
            return tuple(conv_const(x) for x in c['__tuple__'])
        if '__obj__' in c:
            return ('<obj>', c['__obj__'])
    return c

class P:
    pass

def rec_to_pseudo(rec):
    p = P()
    p.co_code = bytes.fromhex(rec['code_hex']) if rec['code_hex'] else b''
    p.co_consts = tuple(conv_const(c) for c in rec['consts'])
    p.co_names = tuple(rec['names'])
    p.co_varnames = tuple(rec['varnames'])
    p.co_freevars = tuple(rec['freevars'])
    p.co_cellvars = tuple(rec['cellvars'])
    return p

SKIP = {'NOP', 'CACHE', 'RESUME', 'RESUME_CHECK', 'PRECALL', 'CALL_FUNCTION_EX',
        'POP_EXCEPT', 'RERAISE', 'COPY', 'WITH_EXCEPT_START',
        'END_FOR', 'POP_TOP', 'RETURN_VALUE', 'RETURN_CONST', 'PUSH_NULL',
        'SWAP', 'ROT_TWO', 'ROT_THREE', 'CHECK_EXC_MATCH', 'JUMP_NO_INTERRUPT',
        'JUMP_FORWARD', 'JUMP_BACKWARD',
        'LOAD_ASSERTION_ERROR', 'RAISE_VARARGS', 'BEFORE_WITH', 'SETUP_ANNOTATIONS'}
SKIP = SKIP - {'RETURN_CONST'}
NOARG = {'POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE', 'POP_JUMP_IF_NONE', 'POP_JUMP_IF_NOT_NONE',
         'POP_JUMP_BACKWARD_IF_FALSE', 'POP_JUMP_BACKWARD_IF_TRUE', 'SEND', 'FOR_ITER'}

def disasm(co):
    out = []
    code, ext, i = co.co_code, 0, 0
    CACHE = dis._inline_cache_entries
    pending = []
    while i < len(code):
        op, arg = code[i], code[i + 1]
        nm = opcode.opname[op]
        end = i + 2 * (1 + CACHE[op])
        if op == opcode.opmap['EXTENDED_ARG']:
            pending.append(arg)
            ext = (ext | arg) << 8
            i = end
            continue
        ra = ext | arg
        ext = 0
        pending = []
        out.append([i, nm, ra, end])
        i = end
    return out

def jump_target(ins):
    off, nm, ra, end = ins
    if nm in ('JUMP_FORWARD', 'SEND', 'FOR_ITER') or nm.startswith('POP_JUMP_IF') or nm.startswith('POP_JUMP_BACKWARD'):
        if nm in ('JUMP_BACKWARD',) or nm.startswith('POP_JUMP_BACKWARD'):
            return end - ra * 2
        return end + ra * 2
    return None

def is_armour_const(c):
    return isinstance(c, tuple) and c and c[0] in ('<blob>', '<code>') or (isinstance(c, tuple) and c and c[0] == '<obj>' and c[1] != 'tuple')

def const_tok(c):
    if isinstance(c, tuple) and c and c[0] == '<obj>' and c[1] == 'tuple':
        return ('TUP', '?')
    if isinstance(c, tuple):
        return ('TUP', repr(c))
    return ('C', repr(c))

def signature(co, varmap):
    instrs = disasm(co)
    by_off = {ins[0]: ins for ins in instrs}
    names, consts = co.co_names, co.co_consts
    varnames, fv, cv = co.co_varnames, co.co_freevars, co.co_cellvars
    allv = list(cv) + list(fv)

    def const_at(ins):
        return consts[ins[2]] if ins[2] < len(consts) else None

    live = set()
    roots = []
    resume = next((i for i, ins in enumerate(instrs) if ins[1] == 'RESUME'), 0)
    roots.append(resume)
    for idx, ins in enumerate(instrs):
        if ins[1] == 'PUSH_EXC_INFO':
            roots.append(idx)
    stack = list(roots)
    seen = set()
    while stack:
        idx = stack.pop()
        if idx in seen or idx >= len(instrs):
            continue
        seen.add(idx)
        ins = instrs[idx]
        off, nm, ra, end = ins
        live.add(off)
        t = jump_target(ins)
        if nm in ('JUMP_FORWARD', 'JUMP_BACKWARD', 'JUMP_NO_INTERRUPT'):
            if t in by_off:
                stack.append(instrs.index(by_off[t]))
            continue
        if nm in ('RETURN_VALUE', 'RETURN_CONST', 'RERAISE'):
            continue
        if t is not None and t in by_off:
            stack.append(instrs.index(by_off[t]))
        stack.append(idx + 1)

    dead_roots = set()
    for idx, ins in enumerate(instrs):
        if ins[1] != 'PUSH_EXC_INFO':
            continue
        seq = instrs[idx:idx + 8]
        armour_loads = 0
        real = 0
        for s in seq[1:]:
            if s[1] == 'LOAD_CONST' and is_armour_const(const_at(s)):
                armour_loads += 1
            elif s[1] in ('NOP', 'PUSH_NULL', 'CACHE'):
                continue
            else:
                real += 1
            if armour_loads >= 2:
                break
        if armour_loads >= 2 and real == 0:
            dead_roots.add(idx)
    live2 = set()
    stack = [i for i in roots if not (instrs[i][1] == 'PUSH_EXC_INFO' and i in dead_roots)]
    seen = set()
    while stack:
        idx = stack.pop()
        if idx in seen or idx >= len(instrs):
            continue
        seen.add(idx)
        ins = instrs[idx]
        off, nm, ra, end = ins
        live2.add(off)
        t = jump_target(ins)
        if nm in ('JUMP_FORWARD', 'JUMP_BACKWARD', 'JUMP_NO_INTERRUPT'):
            if t in by_off:
                stack.append(instrs.index(by_off[t]))
            continue
        if nm in ('RETURN_VALUE', 'RETURN_CONST', 'RERAISE'):
            continue
        if t is not None and t in by_off:
            stack.append(instrs.index(by_off[t]))
        stack.append(idx + 1)

    dead_none = set()
    for i, ins in enumerate(instrs):
        if ins[1] == 'LOAD_CONST':
            c = const_at(ins)
            if repr(c) == 'None':
                j = i + 1
                while j < len(instrs) and instrs[j][1] == 'NOP':
                    j += 1
                if j < len(instrs) and instrs[j][1] == 'JUMP_FORWARD':
                    dead_none.add(ins[0])
    out = []
    for ins in instrs:
        off, nm, ra, end = ins
        if off not in live2 or nm in SKIP or off in dead_none:
            continue
        if nm == 'RETURN_CONST':
            c = const_at(ins)
            if repr(c) == 'None':
                continue
            out.append(('LOAD_CONST', const_tok(c)))
        elif nm == 'LOAD_CONST':
            c = const_at(ins)
            if is_armour_const(c) or isinstance(c, types.CodeType) or (isinstance(c, tuple) and c and c[0] == '<code>'):
                continue
            out.append(('LOAD_CONST', const_tok(c)))
        elif nm == 'LOAD_FAST' and varnames[ra] == '__assert_armored__':
            continue
        elif nm in ('LOAD_FAST', 'LOAD_FAST_CHECK', 'STORE_FAST', 'DELETE_FAST', 'LOAD_FAST_AND_CLEAR', 'STORE_FAST_MAYBE_NULL', 'LOAD_FAST_LOAD_FAST'):
            v = varnames[ra] if ra < len(varnames) else '?'
            out.append((nm, varmap.get(v, v)))
        elif nm in ('LOAD_DEREF', 'STORE_DEREF', 'DELETE_DEREF', 'MAKE_CELL'):
            v = allv[ra] if ra < len(allv) else '?'
            out.append((nm, varmap.get(v, v)))
        elif nm == 'LOAD_CLOSURE':
            out.append((nm, allv[ra] if ra < len(allv) else '?'))
        elif nm == 'LOAD_GLOBAL':
            idx2 = ra >> 1
            out.append((nm, names[idx2] if idx2 < len(names) else '?'))
        elif nm == 'LOAD_ATTR':
            idx2 = ra >> 1
            out.append((nm, names[idx2] if idx2 < len(names) else '?'))
        elif nm in ('LOAD_NAME', 'STORE_NAME', 'STORE_ATTR', 'DELETE_ATTR', 'IMPORT_NAME', 'IMPORT_FROM',
                    'STORE_GLOBAL', 'LOAD_FROM_DICT_OR_GLOBALS', 'DELETE_NAME'):
            out.append((nm, names[ra] if ra < len(names) else '?'))
        elif nm == 'COMPARE_OP':
            ci = ra >> 4
            out.append((nm, dis.cmp_op[ci] if ci < len(dis.cmp_op) else str(ci)))
        elif nm == 'CONTAINS_OP':
            out.append((nm, 'not in' if ra & 1 else 'in'))
        elif nm == 'IS_OP':
            out.append((nm, 'is not' if ra & 1 else 'is'))
        elif nm == 'BINARY_OP':
            out.append((nm, opcode._nb_ops[ra][0] if ra < len(opcode._nb_ops) else str(ra)))
        elif nm == 'KW_NAMES':
            c = const_at(ins)
            out.append((nm, const_tok(c)))
        elif nm == 'BUILD_CONST_KEY_MAP':
            out.append((nm, str(ra)))
        elif nm in NOARG:
            out.append((nm, ''))
        else:
            out.append((nm, str(ra)))
    while out and out[-1][0] == 'BUILD_TUPLE' and out[-1][1] == '1':
        out.pop()
    return out

def tok_eq(a, b):
    if a == b:
        return True
    if isinstance(a[1], tuple) and isinstance(b[1], tuple) and a[1] and b[1] and a[1][0] == 'TUP' and b[1][0] == 'TUP':
        if a[1][1] == '?' or b[1][1] == '?':
            return True
    return False

def seq_eq(a, b):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if not tok_eq(x, y):
            return False
    return True

def var_positional_map(co):
    names = [v for v in co.co_varnames if v != '__assert_armored__']
    return {v: 'L%d' % i for i, v in enumerate(names)}

def build_class_src(partb_path):
    """partB → class Reader + get_multipolygons（同 verify_partB.py）"""
    src = open(partb_path, encoding='utf-8').read()
    lines = src.split('\n')
    class_src, gm_lines = [], []
    cur = None
    for ln in lines:
        m = re.match(r'^def (Reader___get_attr|Reader___get_crs|Reader___get_polygons)\((.*)\):', ln)
        if m:
            cur = m.group(1).replace('Reader___', '__')
            class_src.append('    def %s(%s):' % (cur, m.group(2)))
            continue
        if re.match(r'^def get_multipolygons\(lines\):', ln):
            cur = 'GM'
            gm_lines.append(ln)
            continue
        if cur == 'GM':
            if ln.strip() == '':
                cur = None
                continue
            gm_lines.append(ln)
            continue
        if cur:
            if ln.strip() == '':
                class_src.append('')
                continue
            if ln.startswith('    '):
                class_src.append('    ' + ln)
            continue
    return ('class Reader:\n' + '\n'.join(class_src) + '\n\n\ndef get_multipolygons(lines):\n'
            + '\n'.join(gm_lines[1:]) + '\n')

def main():
    partb = sys.argv[1] if len(sys.argv) > 1 else '/zocde/wp2shp2_work/recon/partB.py'
    store = json.load(open('/zocde/wp2shp2_work/all_functions_merged.json', encoding='utf-8'))

    src = build_class_src(partb)
    open('/tmp/partB_verify2.py', 'w', encoding='utf-8').write(src)
    code = compile(src, '<frozen pymapgis>', 'exec')
    mine = {}
    def walk(co):
        for c in co.co_consts:
            if isinstance(c, types.CodeType):
                mine[c.co_qualname] = c
                walk(c)
    walk(code)

    targets = ['pymapgis::Reader.__get_crs', 'pymapgis::Reader.__get_attr',
               'pymapgis::Reader.__get_polygons', 'pymapgis::get_multipolygons']
    ok = bad = 0
    for key in targets:
        rec = store[key]
        q = rec['qualname']
        mco = mine.get(q)
        if mco is None:
            print('MISSING  %s (%s)' % (q, key))
            bad += 1
            continue
        orig = signature(rec_to_pseudo(rec), var_positional_map(rec_to_pseudo(rec)))
        mysig = signature(mco, var_positional_map(mco))
        ov = len([v for v in rec['varnames'] if v != '__assert_armored__'])
        mv = len(mco.co_varnames)
        if seq_eq(orig, mysig) and ov == mv:
            print('PASS     %s  (%d ops, vars %d)' % (q, len(orig), mv))
            ok += 1
        else:
            bad += 1
            print('DIFF     %s  (mine %d vs orig %d ops, vars %d/%d)' % (q, len(mysig), len(orig), mv, ov))
            import difflib
            sm = difflib.SequenceMatcher(None, ['%s %s' % x for x in orig], ['%s %s' % x for x in mysig])
            n = 0
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == 'equal':
                    continue
                n += 1
                if n > 14:
                    print('   ...more')
                    break
                print('   ORIG[%d:%d]=%r' % (i1, i2, orig[i1:i2]))
                print('   MINE[%d:%d]=%r' % (j1, j2, mysig[j1:j2]))
    print('\nPASS=%d DIFF=%d' % (ok, bad))

if __name__ == '__main__':
    main()