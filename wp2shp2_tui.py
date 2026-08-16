#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wp2shp2 TUI 命令行版：MapGIS WP/WL/WT 批量转换（无需图形界面）。

复用 wp2shp2.py 的转换引擎（auto_fix_crs / sanitize_gdf / convert_worker），
通过 argparse 全代码驱动，适合脚本化、CI、无显示器环境。

示例:
  python wp2shp2_tui.py --input ./数据 --output ./out --format GeoJSON
  python wp2shp2_tui.py --input ./数据 --output ./out --crs-mode "保持原始状态 (不转换)"
  python wp2shp2_tui.py --input ./数据 --output ./out --name-pattern "*.WL"
"""
import argparse
import concurrent.futures
import logging
import os
import sys
import time

import wp2shp2  # noqa: F401  (复用转换引擎；GUI 依赖 tkinter 仅导入不启动)

FMT_CHOICES = ['ESRI Shapefile', 'GeoJSON', 'FileGDB']
CRS_CHOICES = ['保持原始状态 (不转换)', '智能识别与转换',
               '忽略投影信息 (仅输出数值)', '强制指定为 CGCS2000 (不重投影)']
ENC_CHOICES = ['UTF-8', 'GBK', 'GB2312']


def scan_files(input_dir, pattern=None, recursive=True):
    """扫描目录下 .wp/.wl/.wt 文件（与 GUI 版同规则，支持递归/文件名正则）。"""
    import fnmatch
    hits = []
    walker = os.walk(input_dir) if recursive else [(input_dir, [], os.listdir(input_dir))]
    for root, _dirs, files in walker:
        for f in files:
            if not f.lower().endswith(('.wp', '.wl', '.wt')):
                continue
            if pattern and not fnmatch.fnmatch(f, pattern):
                continue
            hits.append(os.path.join(root, f))
    return sorted(hits)


def run(input_dir, output_dir, fmt='ESRI Shapefile', crs_mode='智能识别与转换',
        encoding='UTF-8', paper_scale=1.0, workers=None, pattern=None, quiet=False):
    """批量转换并返回统计 {success, failed, skipped, total}。"""
    logger = logging.getLogger('MapGIS2SHP')
    if not os.path.isdir(input_dir):
        raise FileNotFoundError('输入目录不存在: %s' % input_dir)
    os.makedirs(output_dir, exist_ok=True)
    files = scan_files(input_dir, pattern=pattern)
    total = len(files)
    if total == 0:
        print('未找到任何 .wp/.wl/.wt 文件: %s' % input_dir)
        return {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}
    settings = {'format': fmt, 'crs_mode': crs_mode,
                'encoding': encoding, 'paper_scale': paper_scale}
    n_workers = workers or min(max(1, (os.cpu_count() or 4) - 2), 8)
    stats = {'success': 0, 'failed': 0, 'skipped': 0}
    t0 = time.time()
    bar = '=' * 60
    if not quiet:
        print(bar)
        print('批量转换开始  文件数=%d  线程=%d  格式=%s  编码=%s' % (total, n_workers, fmt, encoding))
        print('坐标模式: %s' % crs_mode)
        print(bar)
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as ex:
        fut_to_file = {ex.submit(wp2shp2.convert_worker, p, input_dir, output_dir, settings): p
                       for p in files}
        for i, fut in enumerate(concurrent.futures.as_completed(fut_to_file), 1):
            name = os.path.basename(fut_to_file[fut])
            try:
                counted, msg, status = fut.result(timeout=600)
            except Exception as e:
                stats['failed'] += 1
                print('[%3d/%d] ✗ %-28s 异常: %s' % (i, total, name, e))
                continue
            stats[status] += 1
            if not quiet:
                mark = {'success': '✓', 'failed': '✗', 'skipped': '·'}.get(status, '?')
                print('[%3d/%d] %s %-28s %s' % (i, total, mark, name, msg))
    cost = time.time() - t0
    print(bar)
    print('完成: 成功=%d 失败=%d 跳过=%d 共=%d  耗时 %.1fs' %
          (stats['success'], stats['failed'], stats['skipped'], total, cost))
    print('输出目录: %s' % output_dir)
    return {'total': total, **stats, 'seconds': cost}


def main(argv=None):
    p = argparse.ArgumentParser(
        prog='wp2shp2_tui',
        description='MapGIS 转 SHP 批量转换（TUI 命令行版，复用开源还原引擎）')
    p.add_argument('--input', '-i', required=True, help='输入目录（含 .wp/.wl/.wt）')
    p.add_argument('--output', '-o', required=True, help='输出目录')
    p.add_argument('--format', '-f', default='ESRI Shapefile', choices=FMT_CHOICES,
                   help='输出格式（默认 %(default)s）')
    p.add_argument('--crs-mode', '-c', default='智能识别与转换', choices=CRS_CHOICES,
                   help='坐标模式（默认 %(default)s）')
    p.add_argument('--encoding', '-e', default='UTF-8', choices=ENC_CHOICES,
                   help='输出编码（默认 %(default)s）')
    p.add_argument('--paper-scale', type=float, default=1.0,
                   help='图幅比例缩放系数（默认 1.0）')
    p.add_argument('--workers', '-w', type=int, default=None, help='并行线程数（默认 cpu-2）')
    p.add_argument('--name-pattern', default=None,
                   help='文件名过滤（fnmatch，如 *.WL，默认全部）')
    p.add_argument('--quiet', '-q', action='store_true', help='只输出最终统计')
    a = p.parse_args(argv)
    try:
        run(a.input, a.output, fmt=a.format, crs_mode=a.crs_mode, encoding=a.encoding,
            paper_scale=a.paper_scale, workers=a.workers, pattern=a.name_pattern,
            quiet=a.quiet)
        return 0
    except Exception as e:
        print('错误: %s' % e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())