from odoo import api, fields, models
from odoo.exceptions import UserError

# 器件行上「保修月数」用的可选值（与 customer.machine.line.warranty_months 保持一致）
WARRANTY_MONTHS = [(str(month), '%s 个月' % month) for month in range(1, 13)]


class MachineBillingColumnWizard(models.TransientModel):
    """器件基线「整列设置」：一次把计费方式 / 另计单价 / 保修月数刷到所有行。

    整列设置只负责“铺一遍默认值”，铺完之后每一行还可以单独改 —— 这比一行行点
    要快得多，也不会限制个别板件的特殊价格。
    """

    _name = 'machine.billing.column.wizard'
    _description = '器件基线整列设置'

    machine_id = fields.Many2one(
        'customer.machine', '机台', required=True, ondelete='cascade', readonly=True)
    line_count = fields.Integer('器件行数', compute='_compute_line_count')
    is_adhoc = fields.Boolean('活期用户', compute='_compute_is_adhoc')

    set_billing_type = fields.Boolean('设置计费方式', default=True)
    billing_type = fields.Selection(
        [('monthly', '含在包月内'), ('extra', '另计费')],
        string='计费方式', default='extra', required=True)
    set_extra_price = fields.Boolean('设置另计单价', default=True)
    extra_price = fields.Float('另计单价', digits=(12, 2))
    set_warranty = fields.Boolean('设置保修月数', default=True)
    warranty_months = fields.Selection(
        WARRANTY_MONTHS, string='保修月数', default='12', required=True)

    @api.depends('machine_id')
    def _compute_line_count(self):
        for wizard in self:
            wizard.line_count = len(wizard.machine_id.line_ids)

    @api.depends('machine_id')
    def _compute_is_adhoc(self):
        for wizard in self:
            wizard.is_adhoc = wizard.machine_id.partner_id.customer_type == 'adhoc'

    @api.model
    def default_get(self, fields_list):
        """用第一行的现有值做默认值，少手输几次。"""
        values = super().default_get(fields_list)
        machine_id = values.get('machine_id') or self.env.context.get('default_machine_id')
        if machine_id and 'machine_id' in fields_list:
            machine = self.env['customer.machine'].browse(machine_id)
            values.setdefault('machine_id', machine.id)
            first = machine.line_ids[:1]
            if first:
                values.setdefault('billing_type', first.billing_type or 'extra')
                values.setdefault('extra_price', first.extra_price or 0.0)
                values.setdefault('warranty_months', first.warranty_months or '12')
        return values

    def action_apply(self):
        self.ensure_one()
        machine = self.machine_id
        lines = machine.line_ids
        if not lines:
            raise UserError('这台机台还没有器件行。先在「器件基线」里加行，或点「从模板载入器件基线」。')
        if not (self.set_billing_type or self.set_extra_price or self.set_warranty):
            raise UserError('请至少勾选一项要整列设置的内容。')

        vals = {}
        if self.set_billing_type:
            billing_type = self.billing_type
            # 活期用户没有包月，器件只能是另计费
            if machine.partner_id.customer_type == 'adhoc' and billing_type != 'extra':
                billing_type = 'extra'
            vals['billing_type'] = billing_type
        if self.set_extra_price:
            vals['extra_price'] = self.extra_price
        if self.set_warranty:
            vals['warranty_months'] = self.warranty_months
        lines.write(vals)

        parts = []
        if 'billing_type' in vals:
            parts.append('计费方式=%s' % dict(self._fields['billing_type'].selection).get(
                vals['billing_type'], vals['billing_type']))
        if 'extra_price' in vals:
            parts.append('另计单价=%s' % vals['extra_price'])
        if 'warranty_months' in vals:
            parts.append('保修月数=%s 个月' % vals['warranty_months'])
        machine.message_post(body='整列设置器件基线（%s 行）：%s。个别板件可再单独修改。'
                                  % (len(lines), '，'.join(parts)))
        return {'type': 'ir.actions.act_window_close'}
