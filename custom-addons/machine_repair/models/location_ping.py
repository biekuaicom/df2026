from datetime import timedelta

from odoo import api, fields, models

from .geo import amap_marker_url

LOCATION_STATES = [
    ('inside', '在工作位置'),
    ('outside', '离开工作位置'),
    ('unassigned', '未分配工作位置'),
]

LOCATION_SOURCES = [
    ('pwa', '手机网页'),
    ('android', 'Android App'),
]


class MachineRepairLocationPing(models.Model):
    """位置上报流水（只追加，不修改）。

    每一条都是手机端上报的一次真实定位；是否在工作位置由服务端按
    `machine_repair.work_location` 的半径算好一并写进来。
    记录只保留最近 N 天（见「定位设置」），由每日 Cron 清理。
    """

    _name = 'machine_repair.location_ping'
    _description = '位置上报记录'
    _order = 'recorded_at desc, id desc'
    _rec_name = 'user_id'

    user_id = fields.Many2one('res.users', '人员', required=True,
                              ondelete='cascade', index=True)
    latitude = fields.Float('纬度', digits=(10, 7), required=True)
    longitude = fields.Float('经度', digits=(10, 7), required=True)
    accuracy = fields.Float('定位精度（米）', digits=(10, 1),
                            help='手机报告的水平精度；越大表示越不可靠。')
    distance = fields.Float('距位置中心（米）', digits=(10, 1))
    distance_text = fields.Char('距位置中心', compute='_compute_distance_text')
    place_id = fields.Many2one('machine_repair.work_location', '工作位置',
                               ondelete='set null', index=True)
    state = fields.Selection(LOCATION_STATES, string='判定结果', required=True,
                             default='outside', index=True)
    source = fields.Selection(LOCATION_SOURCES, string='来源', default='pwa')
    ip = fields.Char('来源 IP')
    recorded_at = fields.Datetime('定位时间', required=True, index=True,
                                  default=fields.Datetime.now)
    note = fields.Char('备注')

    def name_get(self):
        labels = dict(LOCATION_STATES)
        result = []
        for ping in self:
            when = fields.Datetime.context_timestamp(ping, ping.recorded_at)
            result.append((ping.id, '%s · %s · %s' % (
                ping.user_id.name or '', labels.get(ping.state, ping.state),
                when.strftime('%m-%d %H:%M') if when else '')))
        return result

    @api.depends('distance', 'state', 'place_id')
    def _compute_distance_text(self):
        for ping in self:
            if ping.state == 'unassigned' or not ping.place_id:
                ping.distance_text = '—'
            else:
                ping.distance_text = '%d 米' % round(ping.distance or 0)

    def action_open_map(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': amap_marker_url(self.latitude, self.longitude,
                                   self.user_id.name or '位置记录'),
            'target': 'new',
        }

    def _cron_purge_old_pings(self):
        """按「定位设置」里的保留天数清理历史，避免表无限增长。"""
        days = self.env['ir.config_parameter'].sudo().get_int(
            'machine_repair.location_retention_days', 90)
        if days <= 0:
            return 0
        # recorded_at 存的是 UTC naive，cutoff 也必须用 UTC（不能用本机时区）
        cutoff = fields.Datetime.to_datetime(fields.Datetime.now()) - timedelta(days=days)
        old = self.sudo().search([('recorded_at', '<', cutoff)])
        count = len(old)
        if count:
            old.unlink()
        # 快照也算位置记录：长期没上报的人不应继续把最后位置留到永久
        stale_snapshots = self.env['machine_repair.user_location'].sudo().search(
            [('recorded_at', '<', cutoff)])
        if stale_snapshots:
            stale_snapshots.unlink()
        return count
