# MapGIS 转 SHP 矢量数据批量转换工具

> **重要声明（请先读）**：本仓库是**破解版**——把原程序的加密去掉后重建出的源码，
> **不是原作者的版本**：原程序来自网上流传的资源，原作者不详。
> 本文档与全部代码均由 AI 撰写（GLM 5.3、DeepSeek V4 Flash）。

把 MapGIS 6.x/7.x 的 `.wt`（点）/ `.wp`（区）/ `.wl`（线）文件转成 `.shp`
等常见矢量格式（SHP / GeoJSON / FileGDB），并做坐标系识别与修复（CGCS2000）。
原版的授权与试用限制已移除。

## 功能

- **批量转换**：选择目录一次性转换全部 WP/WL/WT 文件，多线程并行
- **坐标修复**：四种模式
  - 智能识别与转换：自动识别投影并重投影至 CGCS2000
  - 保持原始状态：不改坐标，尽量保留 MapGIS 原始投影定义
  - 忽略投影信息：仅导出坐标数值，不带坐标系定义
  - 强制指定 2000：直接将结果标记为 CGCS2000，不做重投影
- **输出格式**：ESRI Shapefile / GeoJSON / FileGDB
- **输出编码**：UTF-8 / GBK 可选
- **图幅比例**：支持按标准图幅比例处理
- **转换报告**：自动生成转换报告与滚动日志

## 使用

### 下载预编译成果包（Windows）

到 [Releases](https://github.com/vagunreal/wp2shp2/releases) 下载最新的
`成果包.zip`，解压即可：

- `wp2shp2.exe` —— 破解版图形界面，双击即用，无需安装任何环境
- `wp2shp2_tui.bat` —— TUI 命令行启动器：把数据目录拖到 bat 上松开，
  或命令行 `wp2shp2_tui.bat -i 输入目录 -o 输出目录`
- 包内全部为**相对路径**，解压到任意位置都能直接使用

### 图形界面（GUI，源码运行）

```bash
pip install -r requirements.txt
python wp2shp2.py
```

### 命令行（TUI，无图形界面）

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

## 构建（PyInstaller，Windows x64 预编译版）

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
| `wp2shp2.py` | 主程序（tkinter 图形界面、任务调度、日志），已移除授权检查 |
| `wp2shp2_tui.py` | 命令行版（复用同一转换引擎，无图形界面） |
| `pymapgis.py` | MapGIS 明码文件解析引擎（WP/WL/WT → GeoDataFrame） |
| `requirements.txt` | 运行依赖（geopandas / shapely / pyproj / pandas / numpy / fiona） |
| `tools/` | 还原与验证脚本归档（见下文） |

## 技术说明

本仓库的源码是对分发版二进制进行**反向还原**后重写：原版为 PyInstaller +
PyArmor 打包，本仓库从加密字节码逐函数还原为可编译源码，行为与原版一致。
原版的授权/试用模块已整体移除（见 `wp2shp2.py` 顶部 `_LicenseStub`）。

### 还原与验证方法

- 从分发版 exe 解包 PyInstaller 归档，采集 PyArmor 加密字节码（`code_hex`）与常量表
- 逐函数重写源码后，用「数据流签名 + 可达性分析」与原始字节码比对
  （剔除 PyArmor 包裹层，局部变量按槽位归一）；`Reader` 7 个方法、模块级函数、
  主程序 22 个类方法全部比对通过
- 用真实 MapGIS 6.x/7.x 数据（28 个 .WP/.WL/.WT 文件）做了解析与转换回归
- 还原与验证的脚本归档在 `tools/`：`verify_partB2.py` / `verify_partC.py`
  （字节码逐函数比对）、`assemble.py` / `make_opensource.py`（源码组装）、
  `build_opensource_exe.py` / `pyz_rebuild.py`（去 PyArmor 重建 exe）、
  `jsondis.py`（反汇编渲染）

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

## 来源与上游

- 原程序：从网上流传的百度网盘资源下载的分发版二进制（作者未知）。
- `pymapgis.py` 的解析逻辑源自 [leecugb/pymapgis](https://github.com/leecugb/pymapgis)
  （MapGIS 明码矢量文件开源解析库）及其在分发版中的魔改版本。上游仓库未附带
  开源许可证，本仓库按"注明出处 + 合理使用"方式发布衍生代码。
- 原版界面与试用文案中的联系人与微信号已全部删除，本仓库不含任何原作者的
  个人信息与联系方式；请勿在任何场景冒充作者或声称拥有本软件版权。

## 许可

MIT License —— 可自行使用、修改、再分发，请随副本保留许可声明。

## 免责声明

本项目是对公开分发二进制的研究性重建，仅用于学习与数据转换用途。