import re

from odoo import fields, models
from odoo.exceptions import UserError


class MachineCopyWizard(models.TransientModel):
    """批量复制机台：以样板机为蓝本，生成 N 台只改编号的新机台。

    编号规则：取样板机编号末尾的数字并依次递增，保持原有位数（M-001 -> M-002）。
    末尾没有数字时追加 -2、-3……
    SN 一律清空：每台实物机台的板件序列号各不相同。
    """

    _name = 'machine.copy.wizard'
    _description = '批量复制机台'

    source_machine_id = fields.Many2one(
        'customer.machine', '样板机台', required=True,
        help='以这台机台为蓝本复制',
    )
    target_partner_id = fields.Many2one(
        'res.partner', '客户',
        help='留空则沿用样板机台的客户；填了就是复制到该客户名下',
    )
    count = fields.Integer('复制数量', default=1, required=True)
    include_sn = fields.Boolean(
        '同时复制 SN', default=False,
        help='默认不复制 SN（每台机台的板件序列号不同）。仅在补录误删数据时才勾选。',
    )

    @staticmethod
    def _next_machine_no(source_no: str, offset: int) -> str:
        """把样板机编号递增 offset，保持原有数字位数。"""
        match = re.search(r'(\d+)(?!.*\d)', source_no or '')
        if not match:
            return '%s-%d' % (source_no, offset + 1)
        raw = match.group(1)
        width = len(raw)
        new_no = str(int(raw) + offset).zfill(width)
        return source_no[:match.start(1)] + new_no + source_no[match.end(1):]

    def action_copy(self):
        self.ensure_one()
        if self.count < 1 or self.count > 200:
            raise UserError('复制数量需要在 1 到 200 之间。')
        source = self.source_machine_id
        partner = self.target_partner_id or source.partner_id

        created = self.env['customer.machine']
        for offset in range(1, self.count + 1):
            new_no = self._next_machine_no(source.machine_no, offset)
            machine = self.env['customer.machine'].create({
                'partner_id': partner.id,
                'machine_no': new_no,
                'template_id': source.template_id.id,
                'installation_address': source.installation_address,
                'monthly_fee': source.monthly_fee,
                'monthly_start_date': source.monthly_start_date,
                'monthly_active': source.monthly_active,
                'note': source.note,
                'line_ids': [(0, 0, {
                    'product_id': line.product_id.id,
                    'sn': line.sn if self.include_sn else False,
                    'is_sn_required': line.is_sn_required,
                    'qty': line.qty,
                    'billing_type': line.billing_type,
                    'extra_price': line.extra_price,
                    'warranty_months': line.warranty_months,
                    'template_line_id': line.template_line_id.id,
                    'note': line.note,
                }) for line in source.line_ids],
            })
            created |= machine

        return {
            'type': 'ir.actions.act_window',
            'name': '已复制 %d 台机台' % len(created),
            'res_model': 'customer.machine',
            'view_mode': 'list,form',
            'domain': [('id', 'in', created.ids)],
            'target': 'current',
        }
