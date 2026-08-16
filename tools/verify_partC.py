#!/usr/bin/env python3
"""partC.py 与原始字节码的结构化比对 v2。
- 用可达性分析剔除 PyArmor 包裹层（死区 return 包装、完整性桩）
- 局部变量按槽位编号归一（_var_var_N → 语义名不影响比对）
- 元组常量：新采集记录有真值全比对；旧记录(类方法)按通配
"""
import json, re, sys, types, opcode, dis

def conv_const(c):
    if isinstance(c, dict):
        if '__code__' in c:
            return ('<code>',)
        if '__bytes__' in c:
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
JUMPS = {'JUMP_FORWARD', 'JUMP_BACKWARD', 'POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE',
         'POP_JUMP_IF_NONE', 'POP_JUMP_IF_NOT_NONE', 'POP_JUMP_BACKWARD_IF_FALSE',
         'POP_JUMP_BACKWARD_IF_TRUE', 'SEND', 'FOR_ITER'}

def disasm(co):
    """[(off, opname, ra, end)] with EXTENDED_ARG folded"""
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
        for a in pending:
            ra |= 0
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
        return ('TUP', '?')          # 旧记录：元组内容丢失 → 通配
    if isinstance(c, tuple):
        return ('TUP', repr(c))       # 新记录/我方：真元组
    return ('C', repr(c))

def signature(co, varmap):
    instrs = disasm(co)
    by_off = {ins[0]: ins for ins in instrs}
    names, consts = co.co_names, co.co_consts
    varnames, fv, cv = co.co_varnames, co.co_freevars, co.co_cellvars
    allv = list(cv) + list(fv)

    def const_at(ins):
        return consts[ins[2]] if ins[2] < len(consts) else None

    # --- 可达性 ---
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

    # armor 完整性桩（PUSH_EXC_INFO 起，紧跟 marker 调用）→ 剔除
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
    # 从 live 里剔除这些桩的路径（简单法：去掉仅由死根引入的连续段）
    # 重新可达：只用活根
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

    # 标记 armor return 包装: LOAD_CONST None; NOP*; JUMP_FORWARD(->桩)
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
    last_armour = False
    for ins in instrs:
        off, nm, ra, end = ins
        if off not in live2 or nm in SKIP or off in dead_none:
            if nm in SKIP and nm not in ('PUSH_NULL',):
                pass
            continue
        if nm == 'RETURN_CONST':
            c = const_at(ins)
            if repr(c) == 'None':
                continue
            out.append(('LOAD_CONST', const_tok(c)))
        elif nm == 'LOAD_CONST':
            c = const_at(ins)
            if is_armour_const(c) or isinstance(c, types.CodeType) or (isinstance(c, tuple) and c and c[0] == '<code>'):
                last_armour = True
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
        if nm != 'LOAD_CONST' or not is_armour_const(const_at(ins)):
            last_armour = False
        if nm == 'BUILD_TUPLE' and last_armure_ok(out):
            pass
    # 修正：BUILD_TUPLE 紧跟 armor-load（被剔除）→ 它是 armor 桩的，剔除
    cleaned = []
    prev_armour = False
    for tok in out:
        if tok[0] == 'BUILD_TUPLE' and prev_armour:
            prev_armour = False
            continue
        cleaned.append(tok)
        prev_armour = False
        if tok[0] == 'LOAD_CONST' and isinstance(tok[1], tuple) and tok[1][0] in ('<obj>', '<blob>', '<code>'):
            prev_armour = True
    # armor const_tok 不会出现（上面 continue 了），重算 prev_armour 意义不大；
    # 改为：删除位于末尾的孤立 BUILD_TUPLE 1（尾桩残留）
    while cleaned and cleaned[-1][0] == 'BUILD_TUPLE' and cleaned[-1][1] == '1':
        cleaned.pop()
    return cleaned

def last_armure_ok(out):
    return False

def tok_eq(a, b):
    if a == b:
        return True
    # 元组通配：('TUP','?') 对 ('TUP', 任何)
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

def assemble(partc_path):
    src = open(partc_path, encoding='utf-8').read()
    lines = src.split('\n')
    head, methods = [], []
    cur = None
    for ln in lines:
        m = re.match(r'^def App__(\w+)\((.*)$', ln)
        if m:
            name = m.group(1)
            if name == '_init__':
                name = '__init__'
            cur = '    def %s(%s' % (name, m.group(2))
            methods.append(cur)
            continue
        if cur is not None:
            if ln.strip() == '':
                methods.append('')
            elif ln.startswith('    '):
                methods.append('    ' + ln)
            else:
                cur = None
                head.append(ln)
        else:
            head.append(ln)
    while methods and methods[-1] == '':
        methods.pop()
    return 'class MapGISProConverterApp:\n' + '\n'.join(methods) + '\n' + '\n'.join(head)

def main():
    partc = sys.argv[1] if len(sys.argv) > 1 else 'recon/partC.py'
    store = json.load(open('all_functions_merged.json', encoding='utf-8'))
    # 新采集记录优先（含真元组常量）
    try:
        new = json.load(open(os.environ.get('W2S_NEW_CAPTURE') or 'all_functions_c.json', encoding='utf-8'))
        for k, v in new.items():
            if v.get('code_hex'):
                store[k] = v
    except Exception as e:
        print('warn: no new capture', e)
    src = assemble(partc)
    open('/tmp/wp2shp2_assembled.py', 'w', encoding='utf-8').write(src)
    code = compile(src, '<frozen wp2shp2>', 'exec')
    mine = {}
    def walk(co):
        for c in co.co_consts:
            if isinstance(c, types.CodeType):
                mine[c.co_qualname] = c
                walk(c)
    walk(code)

    ok = bad = 0
    for k in sorted(store):
        if not k.startswith('wp2shp2::'):
            continue
        q = k.split('::', 1)[1]
        if q in ('<module>', 'MapGISProConverterApp'):
            continue
        rec = store[k]
        if not rec.get('code_hex'):
            continue
        if q.startswith('MapGISProConverterApp.'):
            mco = mine.get(q)
        else:
            mco = mine.get(q)
        if mco is None:
            print('MISSING  %s' % q)
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
                if n > 12:
                    print('   ...more')
                    break
                print('   ORIG[%d:%d]=%r  MINE[%d:%d]=%r' % (i1, i2, orig[i1:i2], j1, j2, mysig[j1:j2]))
    print('\nPASS=%d DIFF=%d' % (ok, bad))

if __name__ == '__main__':
    main()
