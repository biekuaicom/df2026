from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class RepairFaultCategory(models.Model):
    """故障分类：由管理员维护的标准下拉菜单（阶段 5 双轨制的"下拉"一侧）。"""

    _name = 'repair.fault.category'
    _description = '故障分类'
    _order = 'sequence, name'

    name = fields.Char('分类名称', required=True)
    sequence = fields.Integer('排序', default=10)
    active = fields.Boolean('启用', default=True)
    note = fields.Text('说明')

    @api.constrains('name')
    def _check_name_unique(self):
        """故障分类不能重名：下拉里出现两个「电源故障」时，统计口径就废了。"""
        for category in self:
            name = (category.name or '').strip()
            if not name:
                continue
            duplicate = self.search([
                ('id', '!=', category.id),
                ('name', '=ilike', name),
            ], limit=1)
            if duplicate:
                raise ValidationError(
                    '故障分类「%s」已经存在（#%s），请直接使用已有分类。'
                    % (name, duplicate.id))


class RepairCase(models.Model):
    """维修快照：完工时自动生成的原始凭证，不可人工修改。

    只开放 create + read：师傅与普通用户能看到、系统能自动写入，
    但不能改、不能删（管理员保留 crud 以便纠错）。
    """

    _name = 'repair.case'
    _description = '维修快照'
    _order = 'completion_date desc, id desc'

    name = fields.Char('快照编号', readonly=True)
    repair_order_id = fields.Many2one(
        'repair.order', '来源工单', readonly=True, index=True, ondelete='restrict')
    machine_id = fields.Many2one('customer.machine', '机台', readonly=True, index=True)
    machine_template_id = fields.Many2one('machine.base.template', '机型', readonly=True, index=True)
    partner_id = fields.Many2one('res.partner', '客户', readonly=True, index=True)
    machine_no = fields.Char('机台编号', readonly=True)
    technician_id = fields.Many2one('res.users', '维修师傅', readonly=True)
    fault_category_id = fields.Many2one('repair.fault.category', '故障分类', readonly=True, index=True)
    fault_description = fields.Text('故障简述', readonly=True)
    solution = fields.Text('处理方式', readonly=True)
    part_line_ids = fields.One2many('repair.case.line', 'case_id', '所用配件', readonly=True)
    part_summary = fields.Text('配件摘要', readonly=True)
    urgency_level = fields.Char('紧急程度', readonly=True)
    repair_round = fields.Integer('维修轮次', readonly=True)
    completion_date = fields.Datetime('完工时间', readonly=True, index=True)
    experience_id = fields.Many2one(
        'repair.experience', '聚合到经验', readonly=True, index=True,
        help='每日聚合任务处理后会写上；为空表示还没被归并。')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('repair.case') or 'CASE'
        return super().create(vals_list)

    def write(self, vals):
        """快照是历史凭证：非管理员一律不允许修改。"""
        if not self.env.is_admin():
            raise UserError('维修快照是历史凭证，不可人工修改。')
        return super().write(vals)

    def unlink(self):
        if not self.env.is_admin():
            raise UserError('维修快照是历史凭证，不可删除。')
        return super().unlink()


class RepairCaseLine(models.Model):
    _name = 'repair.case.line'
    _description = '快照配件行'

    case_id = fields.Many2one('repair.case', '快照', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', '器件', readonly=True)
    qty = fields.Float('数量', readonly=True, digits=(12, 2))
    sn = fields.Char('SN', readonly=True)
    price_unit = fields.Float('单价', readonly=True)


class RepairExperience(models.Model):
    """故障经验库（双轨制：管理员下拉 + 师傅自由文本）。

    priority：系统聚合默认 50，管理员手动整理/置顶置为 100。
    """

    _name = 'repair.experience'
    _description = '故障经验库'
    _order = 'priority desc, occurrence_count desc, id desc'

    name = fields.Char('经验标题', required=True)
    machine_template_id = fields.Many2one('machine.base.template', '机型', index=True)
    fault_category_id = fields.Many2one('repair.fault.category', '故障分类（下拉）', index=True)
    custom_fault_input = fields.Char(
        '自定义故障（自由文本）',
        help='下拉菜单里没有的罕见故障由师傅自由填写，供管理员后续归并成标准分类。')
    priority = fields.Integer(
        '优先级', default=50,
        help='系统聚合默认 50；管理员手动整理/置顶可改为 100。')
    state = fields.Selection(
        [('draft', '待整理'), ('published', '已整理')], default='draft', index=True)
    summary_content = fields.Text('经验总结')
    occurrence_count = fields.Integer('出现次数', default=0, readonly=True)
    last_seen_date = fields.Datetime('最近出现', readonly=True)
    top_parts = fields.Text('高频器件 TOP', readonly=True)
    active = fields.Boolean('启用', default=True)

    @api.model
    def _cron_aggregate(self):
        """每日聚合：把还没归并的维修快照按【机型 + 故障分类】归档到经验库。"""
        cases = self.env['repair.case'].search([('experience_id', '=', False)], limit=500)
        touched = self.browse()
        for case in cases:
            domain = [('machine_template_id', '=', case.machine_template_id.id)]
            if case.fault_category_id:
                domain.append(('fault_category_id', '=', case.fault_category_id.id))
            else:
                domain.append(('fault_category_id', '=', False))
                domain.append(('custom_fault_input', '=', case.fault_description or False))
            experience = self.search(domain, limit=1)
            if not experience:
                experience = self.create({
                    'name': '%s / %s' % (
                        case.machine_template_id.name or '未分类机型',
                        case.fault_category_id.name or '待整理',
                    ),
                    'machine_template_id': case.machine_template_id.id,
                    'fault_category_id': case.fault_category_id.id,
                    'custom_fault_input': False if case.fault_category_id else case.fault_description,
                    'priority': 50,
                    'state': 'draft',
                })
            experience.write({
                'occurrence_count': experience.occurrence_count + 1,
                'last_seen_date': case.completion_date or fields.Datetime.now(),
                'summary_content': experience.summary_content or (case.fault_description or ''),
            })
            case.write({'experience_id': experience.id})
            touched |= experience
        for experience in touched:
            experience._compute_top_parts()
        return len(cases)

    def _compute_top_parts(self):
        Case = self.env['repair.case']
        for experience in self:
            counts = {}
            for case in Case.search([('experience_id', '=', experience.id)]):
                for line in case.part_line_ids:
                    key = line.product_id.display_name or '?'
                    counts[key] = counts.get(key, 0.0) + line.qty
            top = sorted(counts.items(), key=lambda item: -item[1])[:5]
            experience.top_parts = '\n'.join('%s × %s' % (name, qty) for name, qty in top)
