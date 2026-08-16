# pymapgis.py —— 由字节码忠实还原（开源重建版）
# 模块级结构 + 异常类 + Reader 生命周期方法（已逐指令核对）

__version__ = '1.0'

import struct
import pyproj
import os
import re
import logging
import pandas as pd
import geopandas as gpd
import numpy as np
import shapely
import shapely.ops
import datetime
import warnings

logger = logging.getLogger('MapGIS2SHP')


class Reader:
    # 方法顺序与原版类体一致：
    # __init__, __get_crs, __get_attr, __get_points, __get_lines,
    # __get_polygons, __get_geopandas, to_file, __len__, __str__,
    # __del__, __enter__, __exit__

    # === 以下方法体由还原脚本从字节码生成，见 recon/ 目录 ===
    def __init__(self, filepath):
        self.f = open(filepath, 'rb')
        type_dict = {'WMAP`D22': 'POINT', 'WMAP`D23': 'POLYGON', 'WMAP`D21': 'LINE'}
        type = self.f.read(8).decode('gbk', errors='ignore')
        if type not in ('WMAP`D22', 'WMAP`D23', 'WMAP`D21'):
            raise InvalidFileError()
        self.shapeType = type_dict[type]
        logger.debug('文件标识: %s', struct.unpack('1i', self.f.read(4))[0])
        data_start = struct.unpack('1i', self.f.read(4))[0]
        self.f.seek(data_start)
        self.head_1 = self.f.read(10)
        self.head_2 = self.f.read(10)
        self.head_3 = self.f.read(10)
        self.head_4 = self.f.read(10)
        self.head_5 = self.f.read(10)
        self.head_6 = self.f.read(10)
        self.head_7 = self.f.read(10)
        self.head_8 = self.f.read(10)
        self.head_9 = self.f.read(10)
        self.head_10 = self.f.read(10)
        self.filepath = filepath
        if type == 'WMAP`D22':
            start, vol = struct.unpack('2i', self.head_3[:-2])
            self.__get_attr(start)
            self.__get_points()
        elif type == 'WMAP`D21':
            start, vol = struct.unpack('2i', self.head_3[:-2])
            self.__get_attr(start)
            self.__get_lines()
        else:
            start, vol = struct.unpack('2i', self.head_10[:-2])
            self.__get_attr(start)
            self.__get_polygons()
        self.__get_geopandas()

    def __get_crs(self):
        self.f.seek(109)
        self.pro = ord(self.f.read(1))
        pro_dict = {5: 'tmerc', 1: 'utm', 2: 'aea', 3: 'lcc'}
        elli = ord(self.f.read(1))
        self.f.seek(143)
        self.sc = struct.unpack('1d', self.f.read(8))[0]
        ellip = {
            1: '+ellps=krass +towgs84=15.8,-154.4,-82.3,0,0,0,0 +units=m +no_d',
            2: '+a=6378140 +b=6356755.288157528',
            7: '+datum=WGS84',
            9: '+ellps=WGS72',
            10: '+ellps=aust_SA +towgs84=-117.808,-51.536,137.784,0.303,0.446,0.234,-0.29',
            11: '+ellps=aust_SA +towgs84=-134,-48,149,0,0,0,0',
            16: '+ellps=krass',
            116: '+ellps=clrk80 +towgs84=-166,-15,204,0,0,0,0',
            'cgcs2000': '+ellps=GRS80',
        }
        if (elli not in ellip.keys()) or (self.sc == 0):
            self.sc = 1
            self.crs = ''
            warnings.warn(self.filepath + ':  no invalid crs detected')
            return
        if self.pro == 5:
            self.sc = self.sc / 1000
            self.f.seek(151)
            cl = struct.unpack('1d', self.f.read(8))[0]
            cl = int(str(cl).split('.')[0][:-4]) + int(str(cl).split('.')[0][-4:-2]) / 60.0 + int(str(cl).split('.')[0][-2:]) / 60.0 / 60
            self.crs = pyproj.CRS('+proj=tmerc' + ' +lat_0=0 +lon_0=' + str(cl) + ' +k=1 +x_0=500000 +y_0=0 ' + ellip[elli] + ' +units=m +no_defs')
        elif self.pro == 0:
            self.crs = pyproj.CRS('+proj=longlat ' + ellip[elli] + ' +no_defs')
        elif (self.pro == 2 or self.pro == 3):
            self.sc = self.sc / 1000
            self.f.seek(151)
            cl = struct.unpack('1d', self.f.read(8))[0]
            cl = int(str(cl).split('.')[0][:-4]) + int(str(cl).split('.')[0][-4:-2]) / 60.0 + int(str(cl).split('.')[0][-2:]) / 60.0 / 60
            self.f.seek(175)
            lat0 = struct.unpack('1d', self.f.read(8))[0]
            lat_0 = int(str(lat0).split('.')[0][:-4]) + int(str(lat0).split('.')[0][-4:-2]) / 60.0 + int(str(lat0).split('.')[0][-2:]) / 60.0 / 60
            lat1 = struct.unpack('1d', self.f.read(8))[0]
            lat_1 = int(str(lat1).split('.')[0][:-4]) + int(str(lat1).split('.')[0][-4:-2]) / 60.0 + int(str(lat1).split('.')[0][-2:]) / 60.0 / 60
            lat2 = struct.unpack('1d', self.f.read(8))[0]
            lat_2 = int(str(lat2).split('.')[0][:-4]) + int(str(lat2).split('.')[0][-4:-2]) / 60.0 + int(str(lat2).split('.')[0][-2:]) / 60.0 / 60
            x_0 = struct.unpack('1d', self.f.read(8))[0]
            y_0 = struct.unpack('1d', self.f.read(8))[0]
            self.crs = pyproj.CRS('+proj=' + pro_dict[self.pro] + ' +lat_0=' + str(lat_0) + ' +lon_0=' + str(cl) + ' +lat_1=' +
                                  str(lat_1) + ' +lat_2=' + str(lat_2) + ' +x_0=' + str(x_0) + ' +y_0=' + str(y_0) + ' ' + ellip[elli] + ' +units=m +no_defs')

    def __get_attr(self, start):
        self.f.seek(start)
        self.f.read(2)
        self.f.read(4)
        self.f.read(6)
        offset = struct.unpack('1i', self.f.read(4))[0]
        logger.debug('Attribute Offset: %s', offset)
        self.f.read(4)
        self.f.read(4)
        self.f.read(128)
        self.f.read(128)
        self.f.read(40)
        self.f.read(2)
        fields_n = struct.unpack('1h', self.f.read(2))[0]
        num = struct.unpack('1i', self.f.read(4))[0]
        leng = struct.unpack('1h', self.f.read(2))[0]
        self.f.read(18)
        field_names = []
        types = []
        nums = []
        offs = []
        lens = []
        for i in range(fields_n):
            temp = self.f.read(20)
            try:
                temp_ = temp.decode('gbk').strip('\x00')
            except UnicodeDecodeError:
                temp_ = temp.decode('gbk', errors='ignore').strip('\x00')
            field_names.append(temp_)
            types.append(ord(self.f.read(1)))
            offs.append(struct.unpack('1i', self.f.read(4)))
            self.f.read(2)
            lens.append(struct.unpack('1h', self.f.read(2)))
            self.f.read(1)
            self.f.read(1)
            self.f.read(2)
            nums.append(struct.unpack('1h', self.f.read(2)))
            self.f.read(4)
        temp = np.array(types)
        mask = np.ones(len(types), dtype=bool)
        field_names = np.array(field_names)[mask]
        field_type_dict = {0: 'string', 1: 'byte', 2: 'short integer', 3: 'integer', 4: 'float', 5: 'double', 6: 'date', 7: 'time'}
        offs = [i[0] for i in offs]
        k1 = offs.copy()
        k1.append(leng)
        length = np.array([i[1] - i[0] for i in zip(k1[:-1], k1[1:])])[mask]
        self.fields = list(zip(field_names, [field_type_dict.get(i, 'string') for i in np.array(types)[mask]], length))
        self.f.read(leng)
        self.data = []
        for i in range(num - 1):
            a = self.f.read(leng)
            attr = []
            for j in range(offs.__len__()):
                if not mask[j]:
                    continue
                if j < offs.__len__() - 1:
                    if types[j] == 4:
                        attr.append(struct.unpack('1f', a[offs[j]:offs[j + 1]])[0])
                    elif types[j] == 3:
                        attr.append(struct.unpack('1i', a[offs[j]:offs[j + 1]])[0])
                    elif types[j] == 2:
                        attr.append(struct.unpack('1h', a[offs[j]:offs[j + 1]])[0])
                    elif types[j] == 1:
                        attr.append(ord(a[offs[j]:offs[j + 1]]))
                    elif types[j] == 5:
                        attr.append(struct.unpack('1d', a[offs[j]:offs[j + 1]])[0])
                    elif types[j] == 6:
                        temp = a[offs[j]:offs[j + 1]]
                        year = struct.unpack('1h', temp[:2])[0]
                        month = temp[2]
                        day = temp[3]
                        try:
                            date_val = datetime.date(year, month, day)
                        except (ValueError, OverflowError):
                            date_val = f'{year:04d}-{month:02d}-{day:02d}'
                        attr.append(date_val)
                    elif types[j] == 7:
                        temp = a[offs[j]:offs[j + 1]]
                        try:
                            time_val = datetime.time(temp[0], temp[1], *(lambda x: (np.int64(np.floor(x)), np.int64(1000000 * (x - np.floor(x)))))(struct.unpack('1d', temp[2:])[0]))
                        except (ValueError, OverflowError):
                            time_val = None
                        attr.append(time_val)
                    else:
                        temp = a[offs[j]:offs[j + 1]]
                        temp2 = temp.rstrip(b'\x00')
                        if b'\x00' in temp2:
                            attr.append(temp.hex().upper())
                        else:
                            try:
                                attr.append(temp2.decode('gbk'))
                            except UnicodeDecodeError:
                                attr.append(temp.hex().upper())
                else:
                    if types[j] == 4:
                        attr.append(struct.unpack('1f', a[offs[j]:])[0])
                    elif types[j] == 3:
                        attr.append(struct.unpack('1i', a[offs[j]:])[0])
                    elif types[j] == 2:
                        attr.append(struct.unpack('1h', a[offs[j]:])[0])
                    elif types[j] == 1:
                        attr.append(ord(a[offs[j]:]))
                    elif types[j] == 5:
                        attr.append(struct.unpack('1d', a[offs[j]:])[0])
                    elif types[j] == 6:
                        temp = a[offs[j]:]
                        year = struct.unpack('1h', temp[:2])[0]
                        month = temp[2]
                        day = temp[3]
                        try:
                            date_val = datetime.date(year, month, day)
                        except (ValueError, OverflowError):
                            date_val = f'{year:04d}-{month:02d}-{day:02d}'
                        attr.append(date_val)
                    elif types[j] == 7:
                        temp = a[offs[j]:]
                        try:
                            time_val = datetime.time(temp[0], temp[1], *(lambda x: (np.int64(np.floor(x)), np.int64(1000000 * (x - np.floor(x)))))(struct.unpack('1d', temp[2:])[0]))
                        except (ValueError, OverflowError):
                            time_val = None
                        attr.append(time_val)
                    else:
                        temp = a[offs[j]:]
                        temp2 = temp.rstrip(b'\x00')
                        if b'\x00' in temp2:
                            attr.append(temp.hex().upper())
                        else:
                            try:
                                attr.append(temp2.decode('gbk'))
                            except UnicodeDecodeError:
                                attr.append(temp.hex().upper())
            self.data.append(attr)
        self.data = pd.DataFrame(self.data, columns=field_names)

    def __get_points(self):
        self.__get_crs()
        start, vol = struct.unpack('2i', self.head_1[:-2])
        self.f.seek(start)
        self.f.read(93)
        self.coords = []
        for i in range(int(vol / 93) - 1):
            self.f.read(1)  # 1 label
            self.f.read(2)
            self.f.read(4)
            self.coords.append(struct.unpack('2d', self.f.read(16)))
            self.f.read(70)
        self.coords = np.array(self.coords)
        self.geom = [shapely.geometry.Point(xy * self.sc) for xy in self.coords]

    def __get_lines(self):
        self.__get_crs()
        start, vol = struct.unpack('2i', self.head_1[:-2])
        self.f.seek(start)
        k = vol / 57
        self.f.read(57)
        points = []
        points_off = []
        for i in range(int(k) - 1):
            self.f.read(10)
            points.append(struct.unpack('1i', self.f.read(4))[0])
            points_off.append(struct.unpack('1i', self.f.read(4))[0])
            self.f.read(39)
        start, vol = struct.unpack('2i', self.head_2[:-2])
        self.coords = []
        for i in range(int(k) - 1):
            self.f.seek(start + points_off[i])
            self.coords.append(struct.unpack('%sd' % (points[i] * 2), self.f.read(points[i] * 16)))
        self.geom = [shapely.geometry.LineString(np.array(i).reshape(-1, 2) * self.sc) for i in self.coords]

    def __get_polygons(self):
        self.__get_crs()
        start, vol = struct.unpack('2i', self.head_1[:-2])
        self.f.seek(start)
        k = vol / 57
        self.f.read(57)
        points = []
        points_off = []
        for i in range(int(k) - 1):
            self.f.read(10)
            points.append(struct.unpack('1i', self.f.read(4))[0])
            points_off.append(struct.unpack('1i', self.f.read(4))[0])
            self.f.read(39)
        start, vol = struct.unpack('2i', self.head_2[:-2])
        self.coords = []
        for i in range(int(k) - 1):
            self.f.seek(start + points_off[i])
            self.coords.append(struct.unpack('%sd' % (points[i] * 2), self.f.read(points[i] * 16)))
        geom_ = [shapely.geometry.LineString(np.array(i).reshape(-1, 2) * self.sc) for i in self.coords]
        start, vol = struct.unpack('2i', self.head_4[:-2])
        self.f.seek(start)
        self.f.read(24)
        temp = []
        for i in range(int(vol / 24.0 - 1)):
            temp.append(struct.unpack('4i', self.f.read(16)))
            self.f.read(8)
        temp = np.array(temp)
        if temp.size == 0:
            self.data = self.data.iloc[0:0]
            self.geom = []
            return
        temp = np.hstack((temp, np.arange(temp.__len__()).reshape((-1, 1))))
        self.data = self.data.loc[np.array(list(set(temp[:, 2:4].flatten()) - {0})) - 1]
        self.geom = []
        for i in set(temp[:, 2:4].flatten()) - {0}:
            mask = (temp[:, 2] == i) | (temp[:, 3] == i)
            x = temp[mask]
            mask_ = x[:, 2] == i
            kk = x[mask_]
            t = kk[:, 0].copy()
            kk[:, 0] = kk[:, 1]
            kk[:, 1] = t
            x[mask_] = kk
            if x.__len__() == 1:
                poly = list(geom_[x[0][-1]].coords)
                if len(poly) >= 3:
                    if poly[0] != poly[-1]:
                        poly.append(poly[0])
                    if len(poly) >= 4:
                        poly_ = shapely.geometry.Polygon(poly)
                        if not poly_.is_valid:
                            poly_ = poly_.buffer(0)
                        self.geom.append(poly_)
                    else:
                        self.geom.append(shapely.geometry.Polygon())
                else:
                    self.geom.append(shapely.geometry.Polygon())
            else:
                m = [geom_[ii[-1]] for ii in x]
                self.geom.append(get_multipolygons(m))

    def __get_geopandas(self):
        self.geodataframe = gpd.GeoDataFrame(self.data, crs=self.crs, geometry=self.geom)
        self.bbox = np.array([
            self.geodataframe.bounds.minx.min() if not self.geodataframe.empty else 0,
            self.geodataframe.bounds.miny.min() if not self.geodataframe.empty else 0,
            self.geodataframe.bounds.maxx.max() if not self.geodataframe.empty else 0,
            self.geodataframe.bounds.maxy.max() if not self.geodataframe.empty else 0,
        ])


    def to_file(self, filepath, **kwargs):
        self.geodataframe.to_file(filepath, **kwargs)

    def __len__(self):
        return self.geom.__len__()

    def __str__(self):
        return (f"mapgis file Reader\n"
                f"{self.__len__()} feature"
                f"{(lambda x: '' if x == 1 else 's')(self.__len__())}"
                f" (type {self.shapeType})")

    def __del__(self):
        try:
            self.f.close()
        except (IOError, OSError, AttributeError):
            pass

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.__del__()


class InvalidFileError(BaseException):
    def __init__(self):
        pass

    def __str__(self):
        return "can not detect the file's geometry type"


class InvalidDirectoryError(BaseException):
    def __init__(self):
        pass


class TopoError(BaseException):
    def __init__(self):
        pass

    def __str__(self):
        return 'topo error in this wp file'


def get_multipolygons(lines):
    """
    通过底层 GEOS 引擎快速重构面要素。
    使用基于包含关系的嵌套算法，正确区分：
    - 洞(hole): 小多边形完全在大多边形内部 → 从大多边形中减去
    - 岛(island): 小多边形与大多边形相邻但不包含 → 保留为 MultiPolygon 的独立部分
    """
    try:
        res = shapely.ops.polygonize_full(lines)
        if len(res) == 4:
            polygons, cut_edges, dangles, invalid = res
        else:
            polygons = shapely.ops.polygonize(lines)
            cut_edges, dangles, invalid = [], [], []
    except Exception as e:
        logger.debug('polygonize_full 降级处理: %s', e)
        polygons = shapely.ops.polygonize(lines)
        cut_edges, dangles, invalid = [], [], []
    polys = []
    if hasattr(polygons, 'geoms'):
        polys.extend(list(polygons.geoms))
    else:
        polys.extend(list(polygons))
    leftover = []
    for part in (cut_edges, dangles, invalid):
        if hasattr(part, 'geoms'):
            leftover.extend(list(part.geoms))
        else:
            leftover.extend(list(part))
    if leftover:
        merged = shapely.ops.linemerge(leftover)
        parts = []
        if isinstance(merged, shapely.geometry.LineString):
            parts.append(merged)
        elif hasattr(merged, 'geoms'):
            parts.extend(merged.geoms)
        for line in parts:
            try:
                coords = list(line.coords)
                if len(coords) >= 3:
                    if coords[0] != coords[-1]:
                        coords.append(coords[0])
                    poly_ = shapely.geometry.Polygon(coords)
                    if not poly_.is_valid:
                        poly_ = poly_.buffer(0)
                    if not poly_.is_empty and poly_.area > 0:
                        polys.append(poly_)
            except Exception as e:
                logger.debug('游离线段闭合失败: %s', e)
    if not polys:
        return shapely.geometry.Polygon()
    if len(polys) == 1:
        return polys[0]
    polys.sort(lambda p: p.area, reverse=True)
    out_polys = []
    for i, poly in enumerate(polys):
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area <= 0:
            continue
        nested = False
        for j, big in enumerate(out_polys):
            try:
                point = poly.representative_point()
                if big.geom_type == 'Polygon':
                    shell = shapely.geometry.Polygon(big.exterior.coords)
                    if shell.contains(point):
                        new_big = big.difference(poly)
                        if not new_big.is_valid:
                            new_big = new_big.buffer(0)
                        out_polys[j] = new_big
                        nested = True
                        break
                elif big.geom_type == 'MultiPolygon':
                    for g in big.geoms:
                        shell = shapely.geometry.Polygon(g.exterior.coords)
                        if not shell.contains(point):
                            continue
                        new_big = big.difference(poly)
                        if not new_big.is_valid:
                            new_big = new_big.buffer(0)
                        out_polys[j] = new_big
                        nested = True
                        break
                    if nested:
                        break
            except Exception as e:
                logger.debug('包含关系判断异常: %s', e)
        if nested:
            continue
        out_polys.append(poly)
    if len(out_polys) == 0:
        return shapely.geometry.Polygon()
    if len(out_polys) == 1:
        result = out_polys[0]
        if not result.is_valid:
            result = result.buffer(0)
        return result
    flat = []
    for item in out_polys:
        if item.geom_type == 'Polygon':
            flat.append(item)
        elif item.geom_type == 'MultiPolygon':
            flat.extend(list(item.geoms))
    try:
        result = shapely.geometry.MultiPolygon(flat)
        if not result.is_valid:
            result = result.buffer(0)
        return result
    except:
        return shapely.ops.unary_union(flat)
