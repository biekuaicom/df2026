from odoo import api, fields, models
from odoo.exceptions import UserError


class CustomerMachine(models.Model):
    """客户机台台账：客户现场每一台实物织机一条记录。

    阶段 2 的两条业务路径：
      1. 从机型模板生成「1 号样板机」—— 表单上的「从模板载入器件基线」按钮
      2. 以样板机为蓝本批量复制同客户其他机台（只改编号）—— 「批量复制机台」向导
    """

    _name = 'customer.machine'
    _description = '客户机台档案'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'partner_id, machine_no'
    _rec_name = 'machine_no'

    partner_id = fields.Many2one('res.partner', '客户', required=True, tracking=True, index=True)
    # 注意：related 字段默认 readonly=True（Odoo 源码 fields.py: attrs['readonly'] = attrs.get('readonly', True)），
    # 必须显式写 readonly=False 才会装上 inverse（见 setup_related：not (self.readonly or 目标字段只读)）。
    # 有 inverse 才能在机台表单上直接改客户类型，并把值写回 res.partner.customer_type ——
    # 客户类型是客户档案上的属性，同一客户的机台共用；改客户档案时这里的值也会自动跟着重算。
    customer_type = fields.Selection(
        related='partner_id.customer_type', string='客户类型', store=True, index=True,
        readonly=False,
        help='跟随客户档案。直接在这里改会同步回客户（该客户名下所有机台统一按这个类型处理）：'
             '包月用户按机台收包月费；活期用户不包月、所有器件一律按「另计费」。')
    machine_no = fields.Char('机台编号', required=True, tracking=True, help='如：M-001')
    template_id = fields.Many2one('machine.base.template', '机型模板', required=True, tracking=True)
    machine_model = fields.Char('机型', related='template_id.name', store=False, readonly=True)
    installation_address = fields.Char('安装地址')
    monthly_fee = fields.Float('包月单价', digits=(12, 2), tracking=True)
    monthly_start_date = fields.Date('包月生效日期', tracking=True)
    monthly_active = fields.Boolean(
        '包月中', default=True, tracking=True,
        help='用于阶段 6 判断该机台当月是否参与计费（中途加/减机按天数折算）。',
    )
    monthly_end_date = fields.Date('包月结束日期', tracking=True, help='中途减机时填写，供阶段 6 折算使用')
    line_ids = fields.One2many('customer.machine.line', 'machine_id', '器件基线')
    line_count = fields.Integer('器件数', compute='_compute_line_count')
    note = fields.Text('备注')
    active = fields.Boolean('启用', default=True)

    _uniq_partner_machine_no = models.Constraint(
        'unique(partner_id, machine_no)',
        '同一客户下机台编号不能重复。',
    )

    @api.depends('line_ids')
    def _compute_line_count(self):
        for machine in self:
            machine.line_count = len(machine.line_ids)

    extra_parts_amount = fields.Float(
        '另计配件合计', compute='_compute_extra_parts_amount', digits=(12, 2),
        help='器件基线里「另计费」那些行的金额合计（不含在包月单价内）。')

    @api.depends('line_ids.extra_subtotal', 'line_ids.billing_type')
    def _compute_extra_parts_amount(self):
        for machine in self:
            machine.extra_parts_amount = sum(
                line.extra_subtotal for line in machine.line_ids if line.billing_type == 'extra'
            )

    @api.onchange('template_id')
    def _onchange_template_id(self):
        """选了机型模板就自动把该模板的专属电路板带进来，不用再手点按钮。"""
        if not self.template_id:
            return
        if self.line_ids:
            return  # 已有基线就不动，避免覆盖手工调整
        # 活期用户没有包月，带进来的器件一律是「另计费」
        billing_type = 'extra' if self.partner_id.customer_type == 'adhoc' else 'monthly'
        self.line_ids = [fields.Command.create({
            'product_id': tline.product_id.id,
            'is_sn_required': tline.is_sn_required,
            'qty': tline.qty,
            'billing_type': billing_type,
            'template_line_id': tline.id,
            'note': tline.note,
        }) for tline in self.template_id.line_ids]

    def action_load_template_lines(self):
        """手动补做一次基线载入（模板后来加了板件、或想重建时用）。"""
        for machine in self:
            if not machine.template_id:
                raise UserError('请先选择机型模板。')
            if machine.line_ids:
                raise UserError('该机台已有器件基线。如确需重建，请先删除现有行。')
            billing_type = 'extra' if machine.partner_id.customer_type == 'adhoc' else 'monthly'
            commands = []
            for tline in machine.template_id.line_ids:
                commands.append((0, 0, {
                    'product_id': tline.product_id.id,
                    'is_sn_required': tline.is_sn_required,
                    'qty': tline.qty,
                    'billing_type': billing_type,
                    'template_line_id': tline.id,
                    'note': tline.note,
                }))
            machine.write({'line_ids': commands})
        return True

    # ---------------------------------------------------------------- 客户类型（包月 / 活期）
    # 活期用户：不包月 + 器件全部另计。界面上把包月字段置灰，这里再把数据兜住，
    # 避免从导入、批量复制、手机端等入口漏进来「活期却带着包月单价」的脏数据。
    ADHOC_MONTHLY_RESET = {
        'monthly_fee': 0.0,
        'monthly_start_date': False,
        'monthly_end_date': False,
        'monthly_active': False,
    }

    def _is_adhoc(self):
        return self.partner_id.customer_type == 'adhoc'

    @api.onchange('partner_id')
    def _onchange_partner_customer_type(self):
        if self.partner_id.customer_type != 'adhoc':
            return
        self.monthly_fee = 0.0
        self.monthly_start_date = False
        self.monthly_end_date = False
        self.monthly_active = False
        for line in self.line_ids:
            line.billing_type = 'extra'

    def _normalize_adhoc(self):
        """活期机台：清掉包月字段，器件行强制「另计费」。"""
        adhoc = self.filtered(lambda machine: machine._is_adhoc())
        if not adhoc:
            return
        vals = {}
        for fname, empty in self.ADHOC_MONTHLY_RESET.items():
            if any(machine[fname] != empty for machine in adhoc):
                vals[fname] = empty
        if vals:
            adhoc.with_context(machine_repair_adhoc_sync=True).write(vals)
        lines = adhoc.line_ids.filtered(lambda line: line.billing_type != 'extra')
        if lines:
            lines.with_context(machine_repair_adhoc_sync=True).write({'billing_type': 'extra'})

    @api.model_create_multi
    def create(self, vals_list):
        machines = super().create(vals_list)
        machines._normalize_adhoc()
        return machines

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get('machine_repair_adhoc_sync'):
            self._normalize_adhoc()
        return result


class CustomerMachineLine(models.Model):
    """机台上的实际器件（从模板复制而来，可逐台填写 SN）。"""

    _name = 'customer.machine.line'
    _description = '机台器件基线'
    _order = 'machine_id, id'

    machine_id = fields.Many2one('customer.machine', '机台', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one(
        'product.product', '器件', required=True, ondelete='restrict',
        domain=[('categ_id.is_board', '=', True)],
        help='只能选择「专属电路板类目」下的产品',
    )
    name = fields.Char('器件名称', related='product_id.name', readonly=True)
    sn = fields.Char(
        'SN / 二维码',
        help='专属电路板的唯一序列号。批量复制机台时会被清空 —— 每台机器的板件序列号不同。',
    )
    is_sn_required = fields.Boolean('需 SN 管控', default=True)
    qty = fields.Float('数量', default=1.0, digits=(12, 2))

    # 计费口径：这块板件是含在包月单价里，还是每次维修另计
    billing_type = fields.Selection(
        [('monthly', '含在包月内'), ('extra', '另计费')],
        string='计费方式', default='monthly', required=True,
        help='含在包月内：不额外收钱；另计费：维修更换时按「另计单价」收费。',
    )
    extra_price = fields.Float('另计单价', digits=(12, 2))
    extra_subtotal = fields.Float(
        '另计小计', compute='_compute_extra_subtotal', digits=(12, 2))
    warranty_months = fields.Selection(
        [(str(month), '%s 个月' % month) for month in range(1, 13)],
        string='保修月数', default='12',
        help='仅「另计费」的板件需要：过保维修收费后，保修从完工日**重新起算**多少个月。'
             '注意这是重新计时，不是换新板。',
    )

    @api.depends('billing_type', 'extra_price', 'qty')
    def _compute_extra_subtotal(self):
        for line in self:
            if line.billing_type == 'extra':
                line.extra_subtotal = (line.extra_price or 0.0) * (line.qty or 0.0)
            else:
                line.extra_subtotal = 0.0

    # ---------------------------------------------------------------- 活期用户：器件只能另计
    def _force_adhoc_extra(self):
        to_fix = self.filtered(
            lambda line: line.machine_id.partner_id.customer_type == 'adhoc'
            and line.billing_type != 'extra')
        if to_fix:
            to_fix.with_context(machine_repair_adhoc_sync=True).write({'billing_type': 'extra'})

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._force_adhoc_extra()
        return lines

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get('machine_repair_adhoc_sync'):
            self._force_adhoc_extra()
        return result

    template_line_id = fields.Many2one(
        'machine.base.template.line', '来源模板行', ondelete='set null', readonly=True,
    )
    qr_id = fields.Many2one(
        'machine.board.qr', '电路板二维码', ondelete='set null', copy=False, readonly=True,
        help='按业务规则：台账录入时不建码；等真的发工单扫码时才建，建好后回填到这里。',
    )
    note = fields.Text('备注')
