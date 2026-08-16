# MapGIS 转 SHP 矢量数据批量转换工具

批量将 MapGIS 6.x/7.x 明码地理数据文件（`.wp` 区 / `.wl` 线 / `.wt` 点）转换为通用
GIS 矢量格式（SHP / GeoJSON / FileGDB），并提供坐标系识别与修复（CGCS2000）。
免费开放，供地理信息学习者与从业者自由使用、传播与修改。

## 功能

- **批量转换**：选择目录一次性转换全部 WP/WL/WT 文件，多线程并行
- **坐标修复**：四种模式
  - 智能识别与转换：自动识别投影并重投影至 CGCS2000（推荐）
  - 保持原始状态：不改坐标，尽量保留 MapGIS 原始投影定义
  - 忽略投影信息：仅导出坐标数值，不带坐标系定义
  - 强制指定 2000：直接将结果标记为 CGCS2000，不做重投影
- **输出格式**：ESRI Shapefile / GeoJSON / FileGDB
- **输出编码**：UTF-8 / GBK 可选
- **图幅比例**：支持按标准图幅比例处理
- **转换报告**：自动生成转换报告与滚动日志

## 使用

### 直接运行预编译版（Windows x64）

解压后双击 `wp2shp2.exe` 即可，无需安装任何依赖。

### 从源码运行（图形界面）

```bash
pip install -r requirements.txt
python wp2shp2.py
```

### 从源码运行（TUI 命令行，无图形界面）

`wp2shp2_tui.py` 复用同一转换引擎，适合脚本化 / CI / 无显示器环境：

```bash
python wp2shp2_tui.py --input 数据目录 --output 输出目录 --format GeoJSON
python wp2shp2_tui.py -i 数据目录 -o 输出目录 --crs-mode "保持原始状态 (不转换)"
python wp2shp2_tui.py -i 数据目录 -o 输出目录 --name-pattern "*.WL" -q
```

参数：`--format`（ESRI Shapefile/GeoJSON/FileGDB）、`--crs-mode`（四模式同 GUI）、
`--encoding`（UTF-8/GBK/GB2312）、`--paper-scale`、`--workers`、`--name-pattern`。
完整说明见 `python wp2shp2_tui.py --help`。

> ⚠️ 编码注意：SHP 的 `--encoding` 决定 dbf 字段编码；GeoJSON 规范要求 UTF-8，
> 若以 GBK 导出 GeoJSON，属性中文会按 GBK 字节写入（原版行为一致）。建议
> GeoJSON 一律使用 `--encoding UTF-8`。

## 构建（PyInstaller）

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name wp2shp2 wp2shp2.py
```

如需捆绑 PROJ/GDAL 数据目录（用于脱离本机环境的绿色版），参考 PyInstaller
`--add-data` 把 `proj/`、`gdal/` 数据目录加入包内，程序启动时会自动设置
`PROJ_LIB` / `GDAL_DATA`。

## 源码结构

| 文件 | 说明 |
|---|---|
| `wp2shp2.py` | 主程序（tkinter 图形界面、任务调度、日志），无授权检查 |
| `wp2shp2_tui.py` | TUI 命令行版（复用同一转换引擎，无图形界面） |
| `pymapgis.py` | MapGIS 明码文件解析引擎（WP/WL/WT → GeoDataFrame） |

## 技术说明

本仓库的源码由发行版二进制逐指令还原重建（原开发环境源码已遗失），核心解析
逻辑与发行版行为一致。原版的授权/试用模块在开源时已整体移除，本版本无任何
授权检查（见 `wp2shp2.py` 顶部 `_LicenseStub`）。

### 还原与验证方法

- 从发行版 exe 解包 PyInstaller 归档，采集 PyArmor 加密字节码（`code_hex`）与常量表
- 逐函数重写源码后，用「数据流签名 + 可达性分析」与原始字节码比对
  （剔除 PyArmor 包裹层，局部变量按槽位归一）；`Reader` 7 个方法、模块级函数、
  主程序 22 个类方法全部比对通过
- 用真实 MapGIS 6.x/7.x 数据（28 个 .WP/.WL/.WT 文件）做了解析与转换回归
- 还原过程的全部脚本与中间产物归档在 `tools/`（见下文）

### 已知边界（与原版行为一致）

1. 属性表引用缺失记录的区文件（如样例数据中的 `中低纬度冻结层水.WP`）会在
   `__get_polygons` 的 `loc` 处抛 `KeyError`，该文件转换失败、其余文件不受影响
   —— 这是原版（含其上游 leecugb/pymapgis）的固有行为，非本重建引入。
2. 由多条线段拼成的多面区文件在 `get_multipolygons` 排序处可能抛
   `TypeError: sort() takes no positional arguments`——原版字节码中
   `polys.sort(<lambda>, reverse=True)` 将 key 函数作为位置参数传入，
   Python 的 `list.sort` 不接受位置参数。本仓库按字节码忠实还原该调用；
   如需修复可改为 `polys.sort(key=lambda p: p.area, reverse=True)`。
3. 本机 Python 3.12.3 与发行版内部 Python 3.12.x 存在编译器差异（分支尾部
   就地返回 vs 统一返回、分支极性等），语义等价，已由验证脚本归一处理。

### 无授权版本与「免验证版」的关系

本源码为「真开源」版本：明文、可编译、可再分发。此前发布的免验证二进制版
（在加密层外注入授权桩）功能与此一致，两者转换结果相同。

## 致谢与上游来源

`pymapgis.py` 基于 [leecugb/pymapgis](https://github.com/leecugb/pymapgis)（MapGIS
明码矢量文件开源解析库）修改增强，在此修改基础上有如下改动：日志化调试输出、
GBK 解码容错、空表保护等。**上游仓库未附带开源许可证**，本仓库按"注明出处 +
合理使用"方式发布其衍生代码；如上游作者对本仓库的发布方式有异议，欢迎提
issue 协商。

## 许可

MIT License —— 可自由使用、修改、再分发（含商用），请随副本保留许可声明。

## 文件清单

| 文件 | 说明 |
|---|---|
| `wp2shp2.py` | 主程序（tkinter 图形界面、任务调度、日志），无授权检查 |
| `pymapgis.py` | MapGIS 明码文件解析引擎（WP/WL/WT → GeoDataFrame） |
| `requirements.txt` | 运行依赖（geopandas / shapely / pyproj / pandas / numpy / fiona） |
| `tools/` | 还原过程脚本归档（解包、字节码采集、比对验证、组装、重建 exe） |

> `tools/` 目录为还原与验证工具归档：`verify_partB2.py` / `verify_partC.py`
> （字节码逐函数比对）、`assemble.py`（骨架+chapter 组装源码）、
> `make_opensource.py`（生成开源版补丁）、`build_opensource_exe.py`
> （用原版 bootloader 重建去 PyArmor 的 exe）、`jsondis*.py`（反汇编渲染）、
> `pyz_rebuild.py`（PYZ 归档重建）。字节码采集等中间步骤不在公开仓库内。

## 许可

MIT License —— 可自由使用、修改、再分发（含商用），请随副本保留许可声明。

## 免责声明

本项目为对公开分发二进制的研究性重建，仅用于学习与数据转换用途，请勿用于
任何商业销售或授权规避场景。
