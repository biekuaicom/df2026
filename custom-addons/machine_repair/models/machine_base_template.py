from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MachineBaseTemplate(models.Model):
    """机型标准模板：定义某一型号织机的专属电路板基线。

    阶段 2 的「全局初始化模板 → 复制首台样板机 → 批量复制」都会从这里的 line_ids 取基线。
    """

    _name = 'machine.base.template'
    _description = '机型标准模板'
    _order = 'name'

    name = fields.Char('机型名称', required=True, help='如：喷水织机、剑杆织机')
    code = fields.Char('机型代号', help='内部编号，可留空')
    active = fields.Boolean('启用', default=True)
    note = fields.Text('说明')
    line_ids = fields.One2many(
        'machine.base.template.line', 'template_id', '专属电路板清单',
        help='该机型的专属电路板基线；建立客户机台时会据此复制。',
    )
    line_count = fields.Integer('器件行数', compute='_compute_line_count')

    @api.depends('line_ids')
    def _compute_line_count(self):
        for template in self:
            template.line_count = len(template.line_ids)

    @api.constrains('name')
    def _check_name_unique(self):
        """机型名称不允许重名：两个「GTMAX-2.0」会让建机台时选错基线。"""
        for template in self:
            name = (template.name or '').strip()
            if not name:
                continue
            duplicate = self.search([
                ('id', '!=', template.id),
                ('name', '=ilike', name),
            ], limit=1)
            if duplicate:
                raise ValidationError(
                    '机型名称「%s」已经存在（#%s）。\n'
                    '请直接在已有模板上增删电路板，不要重复建机型。'
                    % (name, duplicate.id))


class MachineBaseTemplateLine(models.Model):
    """模板里的器件行。

    只有「专属电路板类目」（product.category.is_board = True）下的产品可以选进来。
    is_sn_required 表示该板件必须按唯一序列号出入库，阶段 4 的完工出库会据此强制校验 SN。
    注意：序列号管控本身是**产品**上的「追溯」字段（tracking = serial），不在分类上，
    所以产品还需逐个把追溯设为「按唯一序列号」。
    """

    _name = 'machine.base.template.line'
    _description = '机型模板器件行'
    _order = 'sequence, id'

    template_id = fields.Many2one(
        'machine.base.template', '机型模板', required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer('序号', default=10)
    product_id = fields.Many2one(
        'product.product', '器件', required=True, ondelete='restrict',
        domain=[('categ_id.is_board', '=', True)],
        help='只能选择「专属电路板类目」下的产品',
    )
    name = fields.Char('器件名称', related='product_id.name', readonly=True)
    is_sn_required = fields.Boolean(
        '需 SN / 二维码管控', default=True,
        help='勾选表示该板件必须按序列号出入库；对应产品要把「追溯」设为「按唯一序列号」。',
    )
    qty = fields.Float('标准数量', default=1.0, digits=(12, 2))
    note = fields.Text('备注')

    _uniq_template_product = models.Constraint(
        'unique(template_id, product_id)',
        '同一个机型模板里，同一个器件只能出现一次。',
    )
