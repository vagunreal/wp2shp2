#!/usr/bin/env python3
"""从还原档案生成开源发布版源码（opensource/ 目录）。

处理（均不动 recon/ 还原档案）：
1. windll 导入兼容（Linux 下可 import，不崩）
2. 注入 license_manager 开源桩：原版授权/试用模块整体移除，无任何授权检查
3. _show_help / _show_about 文案去除销售联系方式（README 承诺）
"""
import os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble

_TOOLS = os.path.dirname(os.path.abspath(__file__))
# 输出到工作区根下的 opensource/（相对路径，不绑定本机目录）
ROOT = os.path.join(os.path.dirname(_TOOLS) if os.path.basename(_TOOLS) == 'tools' else _TOOLS, 'opensource')

def patch_wp2shp2(text):
    # 1) windll 兼容
    text = text.replace(
        'from ctypes import windll',
        'try:\n    from ctypes import windll\nexcept ImportError:\n    windll = None')
    # 2) license stub（放在 warnings 之后、frozen 初始化之前）
    stub = """warnings.filterwarnings('ignore')

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
"""
    text = text.replace("warnings.filterwarnings('ignore')\n", stub, 1)
    # 3) 文案清理：把原版销售/联系方式文案替换为免费开放文案
    #    （不在此处出现具体联系方式字样，避免公开仓库泄露原版作者信息）
    text = re.sub(r'\\n\\n购买授权请联系微信: \S+',
                  '\\n\\n本软件为免费开放版，可自由使用与分发。', text)
    text = re.sub(r'技术支持微信: \S+', '免费开放版 · 可自由使用与分发', text)
    text = re.sub(r'\\n微信: \S+', '', text)
    return text

def main():
    pmg = assemble.build_pymapgis()
    w2s = patch_wp2shp2(assemble.build_wp2shp2())

    open(os.path.join(ROOT, 'pymapgis.py'), 'w', encoding='utf-8').write(pmg)
    open(os.path.join(ROOT, 'wp2shp2.py'), 'w', encoding='utf-8').write(w2s)

    # 语法验证
    compile(pmg, 'pymapgis.py', 'exec')
    compile(w2s, 'wp2shp2.py', 'exec')
    print('opensource/ 生成完毕:')
    print('  pymapgis.py  %d 行' % len(pmg.splitlines()))
    print('  wp2shp2.py   %d 行' % len(w2s.splitlines()))
    # 附属文件同步（构建脚本/示例）
    for f in ('build_opensource_exe.py',):
        src = os.path.join(os.path.dirname(ROOT), f)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(ROOT, f))
            print('  同步 %s' % f)

if __name__ == '__main__':
    main()