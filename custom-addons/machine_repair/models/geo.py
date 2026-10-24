"""坐标系转换与高德地图链接工具。

手机浏览器/WebView 的 GPS 坐标是 WGS-84；高德/腾讯地图上取到的经纬度是 GCJ-02。
两者在国内相差约 300~600 米，所以：
  * 存库统一保留手机上报的 WGS-84；
  * 画到高德瓦片地图、打开高德链接时转成 GCJ-02；
  * 工作位置允许选择坐标系，算距离前统一转成 WGS-84。
"""
import math
from urllib.parse import quote

# GCJ-02 使用 Krasovsky 1940 椭球参数
_A = 6378245.0
_EE = 0.00669342162296594323


def out_of_china(lat, lng):
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat, lng):
    """WGS-84 -> GCJ-02；国外坐标原样返回。"""
    lat, lng = float(lat), float(lng)
    if out_of_china(lat, lng):
        return lat, lng
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + d_lat, lng + d_lng


def gcj02_to_wgs84(lat, lng):
    """GCJ-02 -> WGS-84（迭代反解，精度优于 1 米）。"""
    lat, lng = float(lat), float(lng)
    if out_of_china(lat, lng):
        return lat, lng
    w_lat, w_lng = lat, lng
    for _ in range(4):
        g_lat, g_lng = wgs84_to_gcj02(w_lat, w_lng)
        w_lat += lat - g_lat
        w_lng += lng - g_lng
    return w_lat, w_lng


def amap_marker_url(lat, lng, name='', coord_system='wgs84'):
    """生成高德地图标记链接（坐标会自动转成 GCJ-02）。"""
    lat, lng = float(lat), float(lng)
    if coord_system == 'gcj02':
        g_lat, g_lng = lat, lng
    else:
        g_lat, g_lng = wgs84_to_gcj02(lat, lng)
    label = quote(name or '位置')
    return ('https://uri.amap.com/marker?position=%.7f,%.7f&name=%s'
            '&coordinate=gaode&callnative=0' % (g_lng, g_lat, label))
