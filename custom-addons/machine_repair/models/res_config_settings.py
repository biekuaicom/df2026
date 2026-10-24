from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    mr_location_enabled = fields.Boolean(
        '启用手机端位置上报', default=True,
        config_parameter='machine_repair.location_enabled',
        help='总开关。关闭后所有账号都不再记录新位置，历史记录保留。')
    mr_location_interval = fields.Integer(
        '上报间隔（秒）', default=60,
        config_parameter='machine_repair.location_interval',
        help='手机端在这个间隔内至少上报一次；移动超过下面的距离会立即上报。')
    mr_location_min_distance = fields.Float(
        '移动多少米立即上报', default=30.0, digits=(8, 1),
        config_parameter='machine_repair.location_min_distance')
    mr_location_stale_minutes = fields.Integer(
        '超过多少分钟视为失联', default=15,
        config_parameter='machine_repair.location_stale_minutes',
        help='管理端看板用：最后上报时间超过该值显示为「失联」。')
    mr_location_retention_days = fields.Integer(
        '位置记录保留天数', default=90,
        config_parameter='machine_repair.location_retention_days',
        help='每日 Cron 清理更早的上报流水；0 表示永不清理（不建议）。')
