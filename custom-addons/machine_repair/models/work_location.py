import math

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .geo import amap_marker_url, gcj02_to_wgs84, wgs84_to_gcj02

# 地球平均半径（米）。只用于“是否在围栏内”的粗略判断，
# 工位级别的距离误差远小于 GPS 本身的漂移。
EARTH_RADIUS_M = 6371000.0


class MachineRepairWorkLocation(models.Model):
    """工作位置（电子围栏）：师傅的上班地点，或客户现场。

    管理端在「机台维修 → 位置监控 → 工作位置」里维护，并把人分配进来。
    手机端只上报经纬度，是否在范围内由**服务端**算 —— 客户端改不了判定结果。
    """

    _name = 'machine_repair.work_location'
    _description = '工作位置（定位围栏）'
    _order = 'sequence, name'
    _rec_name = 'name'

    name = fields.Char('名称', required=True, help='如：一号车间、XX 纺织厂')
    sequence = fields.Integer('排序', default=10)
    partner_id = fields.Many2one('res.partner', '关联客户', help='客户现场可填，自有厂区可留空')
    address = fields.Char('地址说明', help='仅作展示，不参与距离计算')
    latitude = fields.Float('纬度', digits=(10, 7), required=True)
    longitude = fields.Float('经度', digits=(10, 7), required=True)
    coord_system = fields.Selection(
        [('wgs84', 'WGS-84（手机 GPS）'), ('gcj02', 'GCJ-02（高德/腾讯地图）')],
        string='坐标系', default='gcj02', required=True,
        help='手机 GPS 上报的是 WGS-84；从高德/腾讯/百度地图上复制的经纬度通常是 GCJ-02。'
             '选错会让距离偏差约 300~600 米。')
    radius = fields.Float('范围半径（米）', digits=(8, 1), default=150.0, required=True,
                          help='人员离中心点不超过这个距离就算「在工作位置」。')
    user_ids = fields.Many2many(
        'res.users', 'machine_repair_work_location_users_rel',
        'location_id', 'user_id', string='在此工作的人员')
    user_count = fields.Integer('人员数', compute='_compute_user_count')
    active = fields.Boolean('启用', default=True)
    note = fields.Text('备注')
    map_url = fields.Char('高德地图链接', compute='_compute_map_url')

    _radius_positive = models.Constraint(
        'CHECK(radius > 0)',
        '范围半径必须大于 0 米。',
    )

    @api.depends('user_ids')
    def _compute_user_count(self):
        for place in self:
            place.user_count = len(place.user_ids)

    @api.depends('latitude', 'longitude', 'coord_system', 'name')
    def _compute_map_url(self):
        for place in self:
            if place.latitude or place.longitude:
                place.map_url = amap_marker_url(
                    place.latitude, place.longitude, place.name or '工作位置',
                    place.coord_system)
            else:
                place.map_url = False

    def _wgs84_coords(self):
        """统一转成 WGS-84，用于和手机 GPS 坐标算距离。"""
        self.ensure_one()
        if self.coord_system == 'gcj02':
            return gcj02_to_wgs84(self.latitude, self.longitude)
        return self.latitude, self.longitude

    def _gcj02_coords(self):
        """转成 GCJ-02，用于高德瓦片/链接。"""
        self.ensure_one()
        if self.coord_system == 'gcj02':
            return self.latitude, self.longitude
        return wgs84_to_gcj02(self.latitude, self.longitude)

    @api.constrains('latitude', 'longitude')
    def _check_coordinates(self):
        for place in self:
            if not -90.0 <= (place.latitude or 0.0) <= 90.0:
                raise ValidationError('纬度必须在 -90 ~ 90 之间。')
            if not -180.0 <= (place.longitude or 0.0) <= 180.0:
                raise ValidationError('经度必须在 -180 ~ 180 之间。')

    @api.constrains('radius')
    def _check_radius(self):
        for place in self:
            if place.radius <= 0:
                raise ValidationError('范围半径必须大于 0 米。')
            if place.radius > 50000:
                raise ValidationError('范围半径过大（最大 50000 米）。')

    @api.model
    def _distance_m(self, lat1, lng1, lat2, lng2):
        """两点间大圆距离（米）。Haversine 公式，纯 Python，无外部依赖。"""
        lat1, lng1, lat2, lng2 = (float(lat1), float(lng1), float(lat2), float(lng2))
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1)
        a = (math.sin(dphi / 2.0) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2)
        return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))

    def _evaluate_position(self, latitude, longitude):
        """返回 (是否在范围内, 采用的位置记录, 距离, 半径)。

        多个位置时：
          * 落在任意一个围栏内 => inside，取**围栏内最近**的那个用于展示；
          * 都不在 => outside，取**整体最近**的那个。
        """
        places = self.filtered(lambda p: p.active)
        if not places:
            return False, self.browse(), None, None
        inside_place = None
        inside_distance = None
        nearest_place = None
        nearest_distance = None
        for place in places:
            p_lat, p_lng = place._wgs84_coords()
            distance = self._distance_m(latitude, longitude, p_lat, p_lng)
            if nearest_distance is None or distance < nearest_distance:
                nearest_place, nearest_distance = place, distance
            if distance <= place.radius and (inside_distance is None or distance < inside_distance):
                inside_place, inside_distance = place, distance
        if inside_place:
            return True, inside_place, inside_distance, inside_place.radius
        return False, nearest_place, nearest_distance, nearest_place.radius if nearest_place else None
