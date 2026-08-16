#!/usr/bin/env python3
"""最终打包：用重建的明文源码替换归档中的加密模块，去掉 PyArmor，产出 exe。
用法: python3 build_opensource_exe.py <原版exe> <pymapgis.py> <wp2shp2.py> <输出exe>
"""
import struct, os, sys, zlib, marshal

sys.path.insert(0, '/zocde/wp2shp2_work')
import pyz_rebuild

SRC, PMG_SRC, W2S_SRC, DST = sys.argv[1:5]

# ---------- 1. 编译重建源码 ----------
pmg_code = compile(open(PMG_SRC, encoding='utf-8').read(), 'pymapgis.py', 'exec')
w2s_code = compile(open(W2S_SRC, encoding='utf-8').read(), 'wp2shp2.py', 'exec')

# ---------- 2. 提取原 exe 的 CArchive ----------
size = os.path.getsize(SRC)
f = open(SRC, 'rb')
f.seek(size - 88)
magic, lengthofPackage, toc_off, tocLen, pyver, pylibname = struct.unpack('!8siiii64s', f.read(88))
pkg_base = size - lengthofPackage
bootloader = open(SRC, 'rb').read(pkg_base)
f.seek(pkg_base + toc_off)
toc_data = f.read(tocLen)

entries = []
pos = 0
while pos < len(toc_data):
    (entry_len,) = struct.unpack('!I', toc_data[pos:pos+4])
    if entry_len == 0:
        break
    entry = toc_data[pos+4:pos+entry_len]
    ofs, dlen, ulen, flag, typ = struct.unpack('!IIIBc', entry[:14])
    name = entry[14:].rstrip(b'\x00').decode(errors='replace')
    f.seek(pkg_base + ofs)
    raw = f.read(dlen)
    entries.append([typ.decode(errors='replace'), raw, ulen, flag, name])
    pos += entry_len

# 找 PYZ 条目
pyz_idx = next(i for i, e in enumerate(entries) if e[4] == 'PYZ.pyz')
pyz_raw = entries[pyz_idx][1]
open('/tmp/orig.pyz', 'wb').write(pyz_raw)

# ---------- 3. 重建 PYZ：替换 pymapgis，删 license_manager / pyarmor ----------
new_pyz = '/tmp/new.pyz'
pyz_rebuild.rebuild('/tmp/orig.pyz', new_pyz,
                    replacements={'pymapgis': pmg_code},
                    remove_names=['license_manager',
                                  'pyarmor_runtime_000000',
                                  'pyarmor_runtime_000000.__init__'])
new_pyz_data = open(new_pyz, 'rb').read()

# 替换 CArchive 中的 PYZ 条目
entries[pyz_idx][1] = new_pyz_data
entries[pyz_idx][2] = len(new_pyz_data)  # ulen
entries[pyz_idx][3] = 0                   # flag: 不压缩

# 替换 wp2shp2 主脚本（去 PyArmor 引导）
w2s_marsh = marshal.dumps(w2s_code)
for e in entries:
    if e[4] == 'wp2shp2':
        e[1] = zlib.compress(w2s_marsh, 9)
        e[2] = len(w2s_marsh)
        e[3] = 1
        break

# 删除 pyarmor 运行时条目
entries = [e for e in entries if not e[4].startswith('pyarmor_runtime_000000')]

# ---------- 4. 重写 CArchive ----------
out = bytearray(bootloader)
new_toc = bytearray()
for typ, raw, ulen, flag, name in entries:
    ofs = len(out) - pkg_base
    out += raw
    nameb = name.encode('utf-8')
    body = struct.pack('!IIIBc', ofs, len(raw), ulen, flag, typ.encode()) + nameb + b'\x00'
    entry_len = ((len(body) + 4 + 15) // 16) * 16
    pad = entry_len - 4 - len(body)
    new_toc += struct.pack('!I', entry_len) + body + b'\x00' * pad

toc_pos = len(out) - pkg_base
out += new_toc
total_len = len(out) - pkg_base + 88
cookie = struct.pack('!8sIIII64s', b'MEI\x0c\x0b\x0a\x0b\x0e', total_len, toc_pos, len(new_toc), pyver, pylibname)
out += cookie

open(DST, 'wb').write(bytes(out))
print('OK: %s (%d 字节, %d 条目; 原 %d 字节)' % (DST, len(out), len(entries), size))
