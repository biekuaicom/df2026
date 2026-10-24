from odoo import fields, models

# 位置数据属于敏感信息：只有「位置监控管理员」或系统管理员能看/改。
LOCATION_GROUPS = ('machine_repair.group_machine_repair_location_manager,'
                   'base.group_system')


class ResUsers(models.Model):
    _inherit = 'res.users'

    machine_repair_location_enabled = fields.Boolean(
        '启用位置上报', default=True, groups=LOCATION_GROUPS,
        help='关闭后该账号的手机端不再上报位置（管理端保留历史记录）。')
    machine_repair_location_ids = fields.Many2many(
        'machine_repair.work_location', 'machine_repair_work_location_users_rel',
        'user_id', 'location_id', string='工作位置', groups=LOCATION_GROUPS,
        help='分配给该人员的工作位置；可多个，落在任意一个围栏内都算在岗。')
