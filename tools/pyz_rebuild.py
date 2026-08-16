#!/usr/bin/env python3
"""PYZ 归档解析与重建：替换指定模块为明文编译代码，可删除条目"""
import struct, marshal, zlib, sys, io, types

def parse(pyz_path):
    data = open(pyz_path, 'rb').read()
    assert data[:4] == b'PYZ\x00'
    pynum, toc_pos = struct.unpack('!iI', data[4:12])
    toc = marshal.loads(data[toc_pos:])
    return data, pynum, toc_pos, toc

def rebuild(pyz_path, out_path, replacements=None, remove_names=None):
    """replacements: {模块名: code对象}；remove_names: [模块名]"""
    replacements = replacements or {}
    remove_names = set(remove_names or ())
    data, pynum, toc_pos, toc = parse(pyz_path)

    out = bytearray()
    out += struct.pack('!4siI', b'PYZ\x00', pynum, 0)  # toc_pos 稍后回填
    new_toc = []
    for name, (typ, pos, length) in toc:
        if name in remove_names:
            continue
        if name in replacements:
            marsh = marshal.dumps(replacements.pop(name))
            blob = zlib.compress(marsh, 9)
            typ = 0
        else:
            blob = data[pos:pos + length]
        new_pos = len(out)
        out += blob
        new_toc.append((name, (typ, new_pos, len(blob))))
    # 未处理的 replacements 报错
    if replacements:
        raise ValueError('PYZ 中找不到模块: %r' % list(replacements))
    toc_bytes = marshal.dumps(new_toc)
    toc_pos_new = len(out)
    out += toc_bytes
    struct.pack_into('!I', out, 8, toc_pos_new)
    open(out_path, 'wb').write(bytes(out))
    return len(new_toc), len(out)

if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    data, pynum, toc_pos, toc = parse(src)
    print('PYZ 条目数:', len(toc))
    # 抽查一个条目验证解压/反序列化
    import types as T
    for name, (typ, pos, length) in toc:
        if name == 'pymapgis':
            blob = data[pos:pos+length]
            raw = zlib.decompress(blob)
            co = marshal.loads(raw)
            print('pymapgis 条目: typ=%s 解压后 %d 字节, code name=%r' % (typ, len(raw), co.co_name))
            break
