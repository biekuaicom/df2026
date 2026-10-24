from odoo import fields, models


class ResPartner(models.Model):
    """客户类型：包月用户 / 活期用户。

    包月用户：机台按月收包月费（monthly_fee + 生效日期 + 包月中），
              机台器件基线可以选「含在包月内」或「另计费」。
    活期用户：不包月，机台上不填包月单价/生效日期/包月中（界面置灰），
              器件基线**全部**按「另计费」处理；建工单时可以改本次保修月数，
              首次维修的板件保修从发货（快递）时间起算。
    """

    _inherit = 'res.partner'

    customer_type = fields.Selection(
        [('monthly', '包月用户'), ('adhoc', '活期用户')],
        string='客户类型', default='monthly', required=True,
        help='包月用户：机台按月收包月费；活期用户：不包月，所有器件按「另计费」逐次收费。',
    )

    def write(self, vals):
        result = super().write(vals)
        if 'customer_type' in vals:
            # 客户类型改了：名下机台要把包月字段清掉、器件强制「另计费」。
            # 机台上的 customer_type 是 related 字段，改客户不会走机台的 write，
            # 所以必须在这里主动同步一次（见 customer.machine._normalize_adhoc）。
            machines = self.env['customer.machine'].search(
                [('partner_id', 'in', self.ids)])
            if machines:
                machines._normalize_adhoc()
        return result
