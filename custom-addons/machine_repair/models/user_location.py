from odoo import api, fields, models

from .geo import amap_marker_url
from .location_ping import LOCATION_SOURCES, LOCATION_STATES


class MachineRepairUserLocation(models.Model):
    """每人一条的「最新位置」快照。

    手机端每次上报都会用事务同时写流水 + 快照，所以管理端列表/看板
    只读这一张表就很轻；历史仍然完整保存在 `machine_repair.location_ping`。
    写操作只由控制器（sudo）完成，管理端只读，避免手工改位置。
    """

    _name = 'machine_repair.user_location'
    _description = '人员最新位置'
    _order = 'recorded_at desc, id desc'
    _rec_name = 'user_id'

    user_id = fields.Many2one('res.users', '人员', required=True,
                              ondelete='cascade', index=True)
    latitude = fields.Float('纬度', digits=(10, 7), required=True)
    longitude = fields.Float('经度', digits=(10, 7), required=True)
    accuracy = fields.Float('定位精度（米）', digits=(10, 1))
    distance = fields.Float('距位置中心（米）', digits=(10, 1))
    distance_text = fields.Char('距位置中心', compute='_compute_distance_text')
    place_id = fields.Many2one('machine_repair.work_location', '工作位置',
                               ondelete='set null')
    state = fields.Selection(LOCATION_STATES, string='当前状态',
                             required=True, default='outside')
    source = fields.Selection(LOCATION_SOURCES, string='来源', default='pwa')
    recorded_at = fields.Datetime('最后上报', required=True, index=True,
                                  default=fields.Datetime.now)
    age_minutes = fields.Integer('距上次上报（分钟）', compute='_compute_age')
    is_stale = fields.Boolean('已失联', compute='_compute_age')

    _one_per_user = models.Constraint(
        'UNIQUE (user_id)',
        '每个人员只能保留一条最新位置记录。',
    )

    @api.depends('distance', 'state', 'place_id')
    def _compute_distance_text(self):
        for record in self:
            if record.state == 'unassigned' or not record.place_id:
                record.distance_text = '—'
            else:
                record.distance_text = '%d 米' % round(record.distance or 0)

    @api.depends('recorded_at')
    def _compute_age(self):
        minutes = self.env['ir.config_parameter'].sudo().get_int(
            'machine_repair.location_stale_minutes', 15)
        now = fields.Datetime.now()
        for record in self:
            if not record.recorded_at:
                record.age_minutes = 0
                record.is_stale = False
                continue
            age = (now - record.recorded_at).total_seconds() / 60.0
            record.age_minutes = max(0, int(age))
            record.is_stale = age > max(1, minutes)

    def name_get(self):
        labels = dict(LOCATION_STATES)
        result = []
        for record in self:
            result.append((record.id, '%s · %s' % (
                record.user_id.name or '', labels.get(record.state, record.state))))
        return result

    def action_open_map(self):
        """在网页端直接打开高德地图（坐标自动转 GCJ-02）。"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': amap_marker_url(self.latitude, self.longitude,
                                   self.user_id.name or '人员位置'),
            'target': 'new',
        }
