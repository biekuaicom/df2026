import calendar
from datetime import date, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MachineBillingLine(models.Model):
    """包月计费明细：每台机台每个月的应收金额，供"包月收入统计报表"用。"""

    _name = 'machine.billing.line'
    _description = '包月计费明细'
    _order = 'period_month desc, partner_id, machine_id'

    period_month = fields.Date('账单月份', required=True, index=True)
    partner_id = fields.Many2one('res.partner', '客户', required=True, index=True)
    machine_id = fields.Many2one('customer.machine', '机台', index=True)
    machine_no = fields.Char('机台编号')
    monthly_fee = fields.Float('包月单价', digits=(12, 2))
    total_days = fields.Integer('当月天数')
    active_days = fields.Integer('实际激活天数')
    holiday_days = fields.Integer('放假天数')
    billable_days = fields.Integer('计费天数')
    amount = fields.Float('应收金额', digits=(12, 2))
    invoice_id = fields.Many2one('account.move', '发票', readonly=True)
    note = fields.Char('说明')

    # 向导里有"该月已生成就报错"的粗粒度保护，但机台行是允许人工新增的：
    # 同一台机台同一个月出现两行就会重复计费、重复开票，直接在库层面挡住。
    _uniq_machine_month = models.Constraint(
        'unique(machine_id, period_month)',
        '同一台机台同一个月只能有一条计费明细（否则会重复计费）。',
    )

    @api.model_create_multi
    def create(self, vals_list):
        """先给一句人话，再让数据库约束兜底（并发/导入时靠它）。"""
        for vals in vals_list:
            machine_id = vals.get('machine_id')
            month = vals.get('period_month')
            if not machine_id or not month:
                continue
            duplicate = self.search([('machine_id', '=', machine_id),
                                     ('period_month', '=', month)], limit=1)
            if duplicate:
                raise ValidationError(
                    '机台「%s」在 %s 已经有一条计费明细（#%s），不能重复计费。\n'
                    '要改金额请直接改那条明细，要重算请先删除它。'
                    % (duplicate.machine_no or duplicate.machine_id.display_name,
                       month, duplicate.id))
        return super().create(vals_list)


class MachineMonthlyBillingWizard(models.TransientModel):
    """包月账单批量生成（含放假天数与中途加减机折算）。"""

    _name = 'machine.monthly.billing.wizard'
    _description = '包月账单批量生成'

    period_month = fields.Date('账单月份', required=True, help='选该月任意一天即可')
    partner_ids = fields.Many2many(
        'res.partner', 'machine_billing_wiz_partner_rel', 'wiz_id', 'partner_id',
        string='客户', help='留空 = 所有有包月机台的客户')
    holiday_days = fields.Integer(
        '当月工厂放假天数', default=0,
        help='按当月实际天数比例扣减，不用固定 30 天。')
    create_invoice = fields.Boolean('同时生成应收发票', default=True)
    line_count = fields.Integer('将生成行数', compute='_compute_preview')
    preview_amount = fields.Float('预计金额合计', compute='_compute_preview')

    @api.depends('period_month', 'partner_ids', 'holiday_days')
    def _compute_preview(self):
        for wizard in self:
            rows = wizard._prepare_rows() if wizard.period_month else []
            wizard.line_count = len(rows)
            wizard.preview_amount = round(sum(row['amount'] for row in rows), 2)

    def _month_bounds(self):
        self.ensure_one()
        first = self.period_month.replace(day=1)
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        return first, last

    def _prepare_rows(self):
        """按机台算出应收金额：按月实际天数折算，扣掉放假天数，支持中途加减机。"""
        self.ensure_one()
        month_first, month_last = self._month_bounds()
        total_days = (month_last - month_first).days + 1
        domain = [('monthly_active', '=', True), ('monthly_fee', '>', 0),
                  ('partner_id.customer_type', '!=', 'adhoc')]
        if self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))
        rows = []
        for machine in self.env['customer.machine'].search(domain):
            start = max(month_first, machine.monthly_start_date or month_first)
            end = min(month_last, machine.monthly_end_date or month_last)
            if end < start:
                continue
            active_days = (end - start).days + 1
            billable_days = max(0, active_days - self.holiday_days)
            if not billable_days:
                continue
            amount = round(machine.monthly_fee * billable_days / total_days, 2)
            rows.append({
                'period_month': month_first,
                'partner_id': machine.partner_id.id,
                'machine_id': machine.id,
                'machine_no': machine.machine_no,
                'monthly_fee': machine.monthly_fee,
                'total_days': total_days,
                'active_days': active_days,
                'holiday_days': self.holiday_days,
                'billable_days': billable_days,
                'amount': amount,
                'note': '%s 至 %s，%s/%s 天' % (start, end, billable_days, total_days),
            })
        return rows

    def action_generate(self):
        self.ensure_one()
        if not self.period_month:
            raise UserError('请先选择账单月份。')
        month_first, month_last = self._month_bounds()
        rows = self._prepare_rows()
        if not rows:
            raise UserError('没有可计费的机台：请检查机台是否「包月中」、包月单价是否大于 0、'
                            '以及包月生效/结束日期是否落在该月。')

        BillingLine = self.env['machine.billing.line']
        existing = BillingLine.search([('period_month', '=', month_first)])
        if existing:
            raise UserError('该月的计费明细已存在（%s 行），请先删除或换一个月，避免重复开票。'
                            % len(existing))

        lines = BillingLine.create(rows)
        if self.create_invoice:
            self._create_invoices(lines, month_last)
        return {
            'type': 'ir.actions.act_window',
            'name': '包月计费明细 %s' % month_first.strftime('%Y-%m'),
            'res_model': 'machine.billing.line',
            'view_mode': 'list,form',
            'domain': [('period_month', '=', month_first)],
            'target': 'current',
        }

    def _create_invoices(self, lines, invoice_date):
        by_partner = {}
        for line in lines:
            by_partner.setdefault(line.partner_id, self.env['machine.billing.line'])
            by_partner[line.partner_id] |= line
        for partner, partner_lines in by_partner.items():
            move = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'partner_id': partner.id,
                'invoice_date': invoice_date,
                'invoice_origin': '包月账单 %s' % invoice_date.strftime('%Y-%m'),
                'invoice_line_ids': [(0, 0, {
                    'name': '包月服务费 %s（机台 %s，%s 天，放假 %s 天）' % (
                        invoice_date.strftime('%Y-%m'),
                        line.machine_no or '',
                        line.billable_days,
                        line.holiday_days,
                    ),
                    'quantity': 1.0,
                    'price_unit': line.amount,
                }) for line in partner_lines],
            })
            partner_lines.write({'invoice_id': move.id})
