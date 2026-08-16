# wp2shp2.py —— 由字节码忠实还原（开源重建版，已移除授权模块）
"""MapGIS 转 SHP 矢量数据批量转换与坐标修复软件 V1.0
主程序 - 增强版
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import time
import logging
import logging.handlers
import concurrent.futures
import multiprocessing
import queue
import datetime
import geopandas as gpd
import numpy as np
import shapely
import shapely.validation
from shapely.affinity import scale
import re
try:
    from ctypes import windll
except ImportError:
    windll = None
import pymapgis as pmg
import warnings

warnings.filterwarnings('ignore')

# ---- 开源版授权桩 ----
# 原版的授权/试用模块（license_manager，PyArmor 加密）在开源时整体移除，
# 本版本无任何授权检查、无试用限制、全功能开放。以下桩保持原版调用点
# 不变（get_license_info/update_trial_count/TRIAL_LIMIT），返回最高权限。
class _LicenseStub:
    TRIAL_LIMIT = 1 << 30

    def get_license_info(self):
        return ('Professional', 0)

    def update_trial_count(self, count):
        pass


license_manager = _LicenseStub()

if getattr(sys, 'frozen', False):
    _base_path = sys._MEIPASS
    _proj_lib = os.path.join(_base_path, 'proj')
    if os.path.exists(_proj_lib):
        os.environ['PROJ_LIB'] = _proj_lib
    _gdal_data = os.path.join(_base_path, 'gdal')
    if os.path.exists(_gdal_data):
        os.environ['GDAL_DATA'] = _gdal_data

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
logger = logging.getLogger('MapGIS2SHP')
logger.setLevel(logging.DEBUG)
_fh = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, 'conversion.log'),
    maxBytes=2097152, backupCount=5, encoding='utf-8')
_fh.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
logger.addHandler(_fh)


# === 模块级函数（由还原脚本生成，见 recon/partC.py）===

def sanitize_gdf(gdf, target_format, encoding='utf-8'):
    """清理 GeoDataFrame, 确保兼容目标格式"""
    if gdf.empty:
        return gdf
    try:
        gdf.geometry = gdf.geometry.apply(lambda x: x if x is None or x.is_valid else shapely.validation.make_valid(x))
    except Exception as e:
        logger.warning('几何修复异常: %s', e)
    if target_format == 'ESRI Shapefile':
        rename_map, used_names = {}, set()
        for col in gdf.columns:
            if col == 'geometry':
                continue
            new_name = str(col)
            try:
                while len(new_name.encode(encoding, 'ignore')) > 10:
                    new_name = new_name[:-1]
            except (UnicodeDecodeError, UnicodeEncodeError):
                new_name = new_name[:3]
            candidate, suffix_num = new_name, 1
            while candidate in used_names:
                suffix = f'_{suffix_num}'
                while len((new_name + suffix).encode(encoding, 'ignore')) > 10:
                    new_name = new_name[:-1]
                candidate = new_name + suffix
                suffix_num += 1
            used_names.add(candidate)
            rename_map[col] = candidate
        gdf = gdf.rename(columns=rename_map)
    return gdf


def auto_fix_crs(gdf, filename, mode='Auto', paper_scale=1.0):
    """坐标处理逻辑"""
    if mode in ('Keep Original', '保持原始状态 (不转换)', '原样输出'):
        return gdf, '(保持原样)'
    if mode == '忽略投影信息 (仅输出数值)':
        gdf.crs = None
        return gdf, '(已移除投影)'
    try:
        bounds = gdf.total_bounds
        max_x, center_x = bounds[2], (bounds[0] + bounds[2]) / 2
    except Exception as e:
        logger.error('%s 边界获取失败: %s', filename, e)
        return gdf, '[错误] 无法获取边界'
    note = ''
    if max_x < 100000 and paper_scale != 1.0:
        gdf.geometry = gdf.geometry.apply(lambda g: scale(g, xfact=paper_scale, yfact=paper_scale, origin=(0, 0)))
        max_x *= paper_scale
        note += f' (缩放x{paper_scale})'
    if mode == '强制指定为 CGCS2000 (不重投影)':
        gdf.set_crs('EPSG:4490', allow_override=True, inplace=True)
        return gdf, '(强制指定为 CGCS2000)'
    crs_target = None
    if 70 < center_x < 145:
        crs_target = 'EPSG:4490'
    elif max_x > 1000000:
        zone = int(str(int(max_x))[:2])
        lon = zone * 6 - 3 if 13 <= zone <= 23 else (zone * 3 if 24 <= zone <= 45 else 105)
        crs_target = f'+proj=tmerc +lat_0=0 +lon_0={lon} +k=1 +x_0={zone * 1000000 + 500000} +y_0=0 +ellps=GRS80 +units=m +no_defs'
    if crs_target:
        gdf.set_crs(crs_target, allow_override=True, inplace=True)
        if mode in ('智能识别与转换', 'Auto Fix'):
            try:
                gdf = gdf.to_crs('EPSG:4490')
                note += ' -> CGCS2000'
            except Exception as e:
                logger.warning('%s 重投影失败: %s', filename, e)
                note += ' (重投影失败)'
    return gdf, note


def convert_worker(wp_path, src_root, dst_root, settings):
    """单文件转换 (子进程/线程)"""
    import logging
    logging.getLogger('fiona').setLevel(logging.ERROR)
    if getattr(sys, 'frozen', False):
        logging.getLogger('MapGIS2SHP').setLevel(logging.WARNING)
    name = os.path.splitext(os.path.basename(wp_path))[0]
    rel = os.path.relpath(wp_path, src_root)
    out_dir = os.path.join(dst_root, os.path.dirname(rel))
    os.makedirs(out_dir, exist_ok=True)
    out_format = settings.get('format', 'ESRI Shapefile')
    ext_map = {'ESRI Shapefile': '.shp', 'GeoJSON': '.json', 'FileGDB': '.gdb'}
    out_path = os.path.join(out_dir, f'{name}{ext_map.get(out_format, ".shp")}')
    try:
        with pmg.Reader(wp_path) as reader:
            gdf = reader.geodataframe
            if gdf.empty:
                return True, f'[跳过] 空文件: {name}', 'skipped'
            gdf, note = auto_fix_crs(gdf, name, mode=settings['crs_mode'], paper_scale=settings['paper_scale'])
            gdf = sanitize_gdf(gdf, out_format, encoding=settings['encoding'])
            driver = {'ESRI Shapefile': 'ESRI Shapefile', 'GeoJSON': 'GeoJSON', 'FileGDB': 'OpenFileGDB'}.get(out_format, 'ESRI Shapefile')
            engine = 'fiona' if getattr(sys, 'frozen', False) else None
            import fiona
            with fiona.Env():
                if driver == 'OpenFileGDB':
                    gdf.to_file(out_path, driver=driver, layer=name, encoding=settings['encoding'], engine=engine)
                else:
                    gdf.to_file(out_path, driver=driver, encoding=settings['encoding'], engine=engine)
            return True, f'[成功] {name} ({len(gdf)}个要素) {note}', 'success'
    except pmg.InvalidFileError:
        return False, f'[失败] {name}: 无法识别的文件格式', 'failed'
    except Exception as e:
        try:
            err = str(e)
        except UnicodeDecodeError:
            err = repr(e)
        return False, f'[失败] {name}: {err}', 'failed'


class MapGISProConverterApp:
    VERSION = 'V1.0.0'
    APP_TITLE = 'MapGIS 转 SHP 矢量数据批量转换与坐标修复软件'

    # === 类方法（由还原脚本生成，见 recon/partC.py）===

    def __init__(self, root):
        self.root = root
        self.root.geometry('900x750')
        self.root.minsize(800, 600)
        try:
            ico = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'app.ico')
            if os.path.exists(ico):
                self.root.iconbitmap(ico)
        except Exception:
            pass
        self.input_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.fmt_var = tk.StringVar(value='ESRI Shapefile')
        self.crs_var = tk.StringVar(value='智能识别与转换')
        self.enc_var = tk.StringVar(value='UTF-8')
        self.scale_var = tk.DoubleVar(value=1.0)
        self.worker_var = tk.IntVar(value=max(1, min((os.cpu_count() or 4) - 2, 8)))
        self.progress_var = tk.DoubleVar()
        self.file_count_var = tk.StringVar(value='')
        self.progress_text = tk.StringVar(value='就绪')
        self.status_var = tk.StringVar()
        self._refresh_auth_status()
        self.log_queue = queue.Queue()
        self.is_converting = False
        self.cancel_requested = False
        self._build_menu()
        self._build_ui()
        self.root.after(100, self._pump_log)
        self.input_dir.trace_add('write', self._on_input_changed)

    def _refresh_auth_status(self):
        """刷新授权信息与 UI 标题/状态栏"""
        self.level, self.trial_count = license_manager.get_license_info()
        remaining = max(0, license_manager.TRIAL_LIMIT - self.trial_count)
        title = f'{self.APP_TITLE} {self.VERSION}'
        if self.level == 'Trial':
            title += f' - 试用版 (剩余{remaining}次)'
            self.status_var.set(f'试用版 | 剩余 {remaining} 次')
        else:
            title += f' - {self.level} (已激活)'
            self.status_var.set(f'已授权: {self.level}')
        self.root.title(title)

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label='导出日志...', command=self._export_log)
        file_menu.add_separator()
        file_menu.add_command(label='退出', command=self.root.quit)
        menubar.add_cascade(label='文件', menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label='使用说明', command=self._show_help)
        help_menu.add_separator()
        help_menu.add_command(label='关于', command=self._show_about)
        menubar.add_cascade(label='帮助', menu=help_menu)
        self.root.config(menu=menubar)

    def _build_ui(self):
        frm_path = ttk.LabelFrame(self.root, text=' 1. 路径设置 ', padding=15)
        frm_path.pack(fill='x', padx=15, pady=(10, 5))
        ttk.Label(frm_path, text='输入路径:').grid(row=0, column=0, sticky='w')
        ttk.Entry(frm_path, textvariable=self.input_dir, width=58).grid(row=0, column=1, padx=5)
        ttk.Button(frm_path, text='浏览', command=self._browse_in).grid(row=0, column=2)
        ttk.Label(frm_path, textvariable=self.file_count_var, foreground='#2980b9').grid(row=0, column=3, pady=(10, 0))
        ttk.Label(frm_path, text='输出路径:').grid(row=1, column=0, sticky='w', pady=(10, 0))
        ttk.Entry(frm_path, textvariable=self.output_dir, width=58).grid(row=1, column=1, padx=5, pady=(10, 0))
        ttk.Button(frm_path, text='浏览', command=self._browse_out).grid(row=1, column=2, pady=(10, 0))
        frm_param = ttk.LabelFrame(self.root, text=' 2. 转换参数 ', padding=15)
        frm_param.pack(fill='x', padx=15, pady=5)
        ttk.Label(frm_param, text='导出格式:').grid(row=0, column=0, sticky='w')
        ttk.Combobox(frm_param, textvariable=self.fmt_var, values=['ESRI Shapefile', 'GeoJSON', 'FileGDB'], state='readonly', width=15).grid(row=0, column=1, padx=5, sticky='w')
        ttk.Label(frm_param, text='坐标模式:').grid(row=0, column=2, sticky='w', padx=(20, 0))
        ttk.Combobox(frm_param, textvariable=self.crs_var, values=['保持原始状态 (不转换)', '智能识别与转换', '忽略投影信息 (仅输出数值)', '强制指定为 CGCS2000 (不重投影)'], state='readonly', width=18).grid(row=0, column=3, padx=5, sticky='w')
        ttk.Label(frm_param, text='字符编码:').grid(row=1, column=0, sticky='w', padx=(20, 0))
        ttk.Combobox(frm_param, textvariable=self.enc_var, values=['UTF-8', 'GBK', 'GB2312'], state='readonly', width=15).grid(row=1, column=1, padx=5, sticky='w', pady=(10, 0))
        ttk.Label(frm_param, text='并行进程:').grid(row=1, column=2, sticky='w', padx=(20, 0), pady=(10, 0))
        ttk.Spinbox(frm_param, from_=1, to=16, textvariable=self.worker_var, width=5).grid(row=1, column=3, padx=5, sticky='w', pady=(10, 0))
        ttk.Label(frm_param, text='缩放系数:').grid(row=2, column=0, sticky='w', padx=(20, 0))
        ttk.Entry(frm_param, textvariable=self.scale_var, width=18).grid(row=2, column=1, padx=5, sticky='w', pady=(10, 0))
        if self.level in ('Trial', 'Personal'):
            ttk.Label(frm_param, text='(当前版本仅支持 SHP 导出)', foreground='#e74c3c').grid(row=2, column=2, columnspan=2, sticky='w', padx=(20, 0), pady=(10, 0))
        prog_frame = ttk.Frame(self.root, padding=(15, 5))
        prog_frame.pack(fill='x')
        ttk.Progressbar(prog_frame, variable=self.progress_var, maximum=100).pack(fill='x', pady=(5, 2))
        status_frame = ttk.Frame(prog_frame)
        status_frame.pack(fill='x')
        ttk.Label(status_frame, textvariable=self.progress_text).pack(side='left')
        ttk.Label(status_frame, textvariable=self.status_var, foreground='#3498db').pack(side='right')
        btn_frame = ttk.Frame(self.root, padding=5)
        btn_frame.pack()
        self.btn_start = ttk.Button(btn_frame, text='启动转换', command=self._start)
        self.btn_start.pack(side='left', padx=10, ipadx=30, ipady=8)
        self.btn_cancel = ttk.Button(btn_frame, text='停止转换', command=self._cancel, state='disabled')
        self.btn_cancel.pack(side='left', padx=10, ipadx=20, ipady=8)
        log_frame = ttk.LabelFrame(self.root, text=' 执行日志 ', padding=5)
        log_frame.pack(fill='both', expand=True, padx=15, pady=(5, 10))
        self.txt_log = tk.Text(log_frame, bg='#1e1e1e', fg='#dcdcdc', font=('Consolas', 9), state='disabled')
        self.txt_log.pack(side='left', fill='both', expand=True)
        self.txt_log.tag_configure('success', foreground='#2ecc71')
        self.txt_log.tag_configure('failed', foreground='#e74c3c')
        self.txt_log.tag_configure('skipped', foreground='#f39c12')
        self.txt_log.tag_configure('info', foreground='#3498db')
        scrollbar = ttk.Scrollbar(log_frame, orient='vertical', command=self.txt_log.yview)
        scrollbar.pack(side='right', fill='y')
        self.txt_log.config(yscrollcommand=scrollbar.set)

    def _browse_in(self):
        path = filedialog.askdirectory(title='选择输入目录')
        if path:
            self.input_dir.set(path)

    def _browse_out(self):
        path = filedialog.askdirectory(title='选择输出目录')
        if path:
            self.output_dir.set(path)

    def _on_input_changed(self, *_):
        path = self.input_dir.get()
        if path and os.path.isdir(path):
            n = len(self._scan(path))
            self.file_count_var.set(f'发现 {n} 个文件')
        else:
            self.file_count_var.set('')

    def _scan(self, d):
        return [os.path.join(root, f) for root, dirs, files in os.walk(d) for f in files if f.lower().endswith(('.wp', '.wt', '.wl'))]

    def _log(self, msg, tag):
        self.log_queue.put((msg, tag))

    def _pump_log(self):
        try:
            while True:
                msg, tag = self.log_queue.get_nowait()
                self.txt_log.config(state='normal')
                self.txt_log.insert(tk.END, f'{msg}\n', tag or ())
                self.txt_log.see(tk.END)
                self.txt_log.config(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self._pump_log)

    def _export_log(self):
        path = filedialog.asksaveasfilename(title='导出日志', defaultextension='.txt', filetypes=[('文本文件', '*.txt'), ('所有文件', '*.*')])
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(self.txt_log.get('1.0', tk.END))
                messagebox.showinfo('成功', f'日志已导出到:\n{path}')
            except OSError as e:
                messagebox.showerror('错误', f'导出失败: {e}')

    def _show_help(self):
        messagebox.showinfo('使用说明', '1. 选择包含 MapGIS 文件 (.wp/.wl/.wt) 的输入目录\n2. 选择输出目录\n3. 根据需要调整参数 (默认即可)\n4. 点击"启动转换"\n\n坐标模式说明:\n- 智能识别与转换: 自动识别投影并转为 CGCS2000 (推荐)\n- 保持原始状态: 不改坐标，尝试保留 MapGIS 原始投影\n- 忽略投影信息: 仅导出坐标数值，不带任何坐标系定义\n- 强制指定 2000: 直接将结果设为 CGCS2000，不进行重投影\n\n本软件为免费开放版，可自由使用与分发。')

    def _show_about(self):
        messagebox.showinfo('关于', f'{self.APP_TITLE}\n版本: {self.VERSION}\n授权: {self.level}\n\n支持格式: MapGIS 6.x/7.x WP/WL/WT\n输出格式: SHP / GeoJSON / FileGDB\n\n免费开放版 · 可自由使用与分发')

    def _validate(self):
        input_dir, output_dir = self.input_dir.get(), self.output_dir.get()
        if not input_dir or not os.path.isdir(input_dir):
            messagebox.showwarning('输入错误', '请选择有效的输入路径。')
            return False
        if not output_dir:
            messagebox.showwarning('输入错误', '请选择输出路径。')
            return False
        try:
            os.makedirs(output_dir, exist_ok=True)
            if os.path.normpath(input_dir) == os.path.normpath(output_dir):
                messagebox.showwarning('路径冲突', '输入路径和输出路径不能相同。')
                return False
        except OSError as e:
            messagebox.showerror('路径错误', f'无法创建输出目录:\n{e}')
            return False
        if not self._scan(input_dir):
            messagebox.showinfo('提示', '输入目录中未找到 MapGIS 文件 (.wp/.wl/.wt)。')
            return False
        self._refresh_auth_status()
        if self.level == 'Trial' and self.trial_count >= license_manager.TRIAL_LIMIT:
            messagebox.showwarning('额度用尽', '试用次数已用完。\n请联系卖家购买正式版。\n微信: yingli100')
            return False
        if self.level in ('Trial', 'Personal') and self.fmt_var.get() != 'ESRI Shapefile':
            messagebox.showwarning('权限不足', f'当前【{self.level}】版本仅支持 SHP 格式。\n请联系卖家升级专业版。')
            return False
        return True

    def _start(self):
        if self.is_converting:
            return
        if not self._validate():
            return
        self.is_converting = True
        self.cancel_requested = False
        self.btn_start.config(state='disabled')
        self.btn_cancel.config(state='normal')
        self.progress_var.set(0)
        self.txt_log.config(state='normal')
        self.txt_log.delete('1.0', tk.END)
        self.txt_log.config(state='disabled')
        settings = {'format': self.fmt_var.get(), 'crs_mode': self.crs_var.get(), 'encoding': self.enc_var.get(), 'paper_scale': self.scale_var.get()}
        threading.Thread(target=self._run, args=(settings,), daemon=True).start()

    def _cancel(self):
        if self.is_converting:
            self.cancel_requested = True
            self._log('>>> 正在停止, 等待当前任务完成...', 'info')
            self.btn_cancel.config(state='disabled')

    def _run(self, settings):
        input_dir, output_dir = self.input_dir.get(), self.output_dir.get()
        files = self._scan(input_dir)
        total = len(files)
        t0 = time.time()
        self._log(f'{"======================================================="}', 'info')
        self._log(f'  任务启动 | {total} 个文件 | {time.strftime("%H:%M:%S")}', 'info')
        self._log(f'  格式: {settings["format"]} | 坐标: {settings["crs_mode"]}', 'info')
        self._log(f'{"======================================================="}', 'info')
        logger.info('开始转换: %d 个文件, %s -> %s', total, input_dir, output_dir)
        stats = {'success': 0, 'failed': 0, 'skipped': 0}
        report_lines = []
        workers = 1 if total <= 5 else self.worker_var.get()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                fut_to_file = {executor.submit(convert_worker, f, input_dir, output_dir, settings): f for f in files}
                for i, fut in enumerate(concurrent.futures.as_completed(fut_to_file)):
                    if self.cancel_requested:
                        self._log('>>> 转换已取消', 'info')
                        for ft in fut_to_file:
                            ft.cancel()
                        break
                    if self.level == 'Trial' and self.trial_count + stats['success'] >= license_manager.TRIAL_LIMIT:
                        self._log('[拦截] 试用额度已耗尽, 后续转换终止。', 'failed')
                        break
                    try:
                        counted, msg, status = fut.result(timeout=300)
                        stats[status] += 1
                        if counted and self.level == 'Trial':
                            license_manager.update_trial_count(self.trial_count + stats['success'])
                        self._log(msg, status)
                        report_lines.append(msg)
                    except concurrent.futures.TimeoutError:
                        fname = os.path.basename(fut_to_file[fut])
                        self._log(f'[超时] {fname}: 转换超过5分钟, 已跳过', 'failed')
                        stats['failed'] += 1
                        report_lines.append(f'[超时] {fname}')
                    except Exception as e:
                        fname = os.path.basename(fut_to_file[fut])
                        self._log(f'[错误] {fname}: {e}', 'failed')
                        stats['failed'] += 1
                        report_lines.append(f'[错误] {fname}: {e}')
                        logger.error('转换异常 %s: %s', fname, e, exc_info=True)
                    done = i + 1
                    self.progress_var.set(done / total * 100)
                    self.progress_text.set(f'进度: {done}/{total}')
        except Exception as e:
            self._log(f'[致命错误] {e}', 'failed')
            logger.critical('转换引擎异常: %s', e, exc_info=True)
        elapsed = time.time() - t0
        summary = f'\n{"======================================================="}\n  任务完成 | 耗时 {elapsed:.1f}s\n  成功: {stats["success"]} | 失败: {stats["failed"]} | 跳过: {stats["skipped"]}\n{"======================================================="}'
        self._log(summary, 'info')
        logger.info('转换完成: 成功=%d 失败=%d 跳过=%d 耗时=%.1fs', stats['success'], stats['failed'], stats['skipped'], elapsed)
        try:
            report_path = os.path.join(output_dir, f'转换报告_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt')
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write('MapGIS 转换报告\n')
                f.write(f'时间: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n')
                f.write(f'源目录: {input_dir}\n')
                f.write(f'输出目录: {output_dir}\n')
                f.write(f'成功: {stats["success"]} | 失败: {stats["failed"]} | 跳过: {stats["skipped"]}\n')
                f.write(f'耗时: {elapsed:.1f}s\n')
                f.write('--------------------------------------------------\n')
                f.write('\n'.join(report_lines))
            self._log(f'报告已保存: {report_path}', 'info')
        except OSError as e:
            logger.warning('报告保存失败: %s', e)
        self.is_converting = False
        self.progress_text.set('就绪')
        self.root.after(0, self._refresh_auth_status)
        self.root.after(0, lambda: self.btn_start.config(state='normal'))
        self.root.after(0, lambda: self.btn_cancel.config(state='disabled'))
        messagebox.showinfo('完成', f'任务结束!\n\n成功: {stats["success"]} 个\n失败: {stats["failed"]} 个\n跳过: {stats["skipped"]} 个\n耗时: {elapsed:.1f} 秒')


if __name__ == '__main__':
    multiprocessing.freeze_support()
    try:
        windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    app = MapGISProConverterApp(root)
    root.mainloop()
