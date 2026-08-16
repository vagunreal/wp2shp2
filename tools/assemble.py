#!/usr/bin/env python3
"""组装最终源码：骨架 + partA/partB/partC → pymapgis.py / wp2shp2.py"""
import re, sys

RECON = '/zocde/wp2shp2_work/recon/'

def read(p):
    return open(RECON + p, encoding='utf-8').read()

def indent(text, n):
    pad = ' ' * n
    return '\n'.join(pad + l if l.strip() else l for l in text.split('\n'))

def extract_funcs(text):
    """从 partX.py 里按 '# === 名称' 注释切段"""
    chunks = {}
    parts = re.split(r'(?m)^# === (.+?) ===\s*$', text)
    for i in range(1, len(parts), 2):
        title, body = parts[i], parts[i + 1].strip('\n')
        chunks[title.strip()] = body
    return chunks

def method_name_from_title(title):
    """从切段标题提取原版 co_name：'Reader.__get_attr (line 156)' -> '__get_attr'"""
    t = re.sub(r'\s*\(line \d+\)$', '', title).strip()
    return t.split('.', 1)[1] if '.' in t else t

def as_class_method(body, method_name):
    """把平铺函数体改为类内方法（缩进 4），方法名用原版 co_name（如 __init__ / _refresh_auth_status）"""
    lines = body.split('\n')
    m = re.match(r'^def \w+(\(.*)$', lines[0])
    if m:
        lines[0] = 'def %s%s' % (method_name, m.group(1))
    return indent('\n'.join(lines), 4)

def build_pymapgis():
    skel = read('pymapgis_skeleton.py')
    a = extract_funcs(read('partA.py'))
    b = extract_funcs(read('partB.py'))

    def get(names):
        for n in names:
            if n in a:
                return a.pop(n)
            if n in b:
                return b.pop(n)
        raise KeyError(names)

    methods = []
    for key, indent_n in [
        ('Reader.__init__ (line 20)', 4),
        ('Reader.__get_crs (line 59)', 4),
        ('Reader.__get_attr (line 156)', 4),
        ('Reader.__get_points (line 342)', 4),
        ('Reader.__get_lines (line 357)', 4),
        ('Reader.__get_polygons (line 382)', 4),
        ('Reader.__get_geopandas (line 455)', 4),
    ]:
        body = get([key, key.replace(' (line', '(line')])
        methods.append(as_class_method(body, method_name_from_title(key)))
    marker = '    # === 以下方法体由还原脚本从字节码生成，见 recon/ 目录 ==='
    head = skel.split(marker)[0]
    tail = skel.split(marker)[1]
    # tail 里包含 to_file 等已还原方法 + 注释行，去掉占位注释行
    tail_lines = [l for l in tail.split('\n')
                  if not l.strip().startswith('# Reader___') and not l.strip().startswith('# def get_multipolygons')]
    out = head + marker + '\n' + '\n\n'.join(methods) + '\n' + '\n'.join(tail_lines)
    # get_multipolygons 追加到文件末尾
    gmp = get(['get_multipolygons (line 518)'])
    out = out.rstrip() + '\n\n\n' + gmp + '\n'
    return out

def build_wp2shp2():
    skel = read('wp2shp2_skeleton.py')
    c = extract_funcs(read('partC.py'))

    funcs = []
    for name in ('sanitize_gdf', 'auto_fix_crs', 'convert_worker'):
        for k in c:
            if k.startswith(name):
                funcs.append(c.pop(k))
                break

    methods = []
    order = ['__init__', '_refresh_auth_status', '_build_menu', '_build_ui', '_browse_in',
             '_browse_out', '_on_input_changed', '_scan', '_log', '_pump_log', '_export_log',
             '_show_help', '_show_about', '_validate', '_start', '_cancel', '_run']
    for name in order:
        for k in c:
            if k.split('(')[0].split(' ')[0] in (name, 'MapGISProConverterApp.' + name, 'App__' + name):
                methods.append(as_class_method(c.pop(k), method_name_from_title(k)))
                break

    m1 = '    # === 类方法（由还原脚本生成，见 recon/partC.py）==='
    out = skel.replace(m1 + '\n    # __init__ / _refresh_auth_status / _build_menu / _build_ui / _browse_in /\n    # _browse_out / _on_input_changed / _scan / _log / _pump_log / _export_log /\n    # _show_help / _show_about / _validate / _start / _cancel / _run',
                       m1 + '\n\n' + '\n\n'.join(methods))
    f1 = '# === 模块级函数（由还原脚本生成，见 recon/partC.py）==='
    out = out.replace(f1 + '\n# sanitize_gdf / auto_fix_crs / convert_worker',
                      f1 + '\n\n' + '\n\n\n'.join(funcs))
    return out

if __name__ == '__main__':
    pmg = build_pymapgis()
    open(RECON + 'pymapgis.py', 'w', encoding='utf-8').write(pmg)
    print('pymapgis.py:', len(pmg.split(chr(10))), '行')
    w2s = build_wp2shp2()
    open(RECON + 'wp2shp2.py', 'w', encoding='utf-8').write(w2s)
    print('wp2shp2.py:', len(w2s.split(chr(10))), '行')
    compile(pmg, 'pymapgis.py', 'exec')
    compile(w2s, 'wp2shp2.py', 'exec')
    print('语法验证通过')
