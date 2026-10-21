from odoo import api, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    """让"在机型模板里内联新建电路板"直接落成合规的板件，并禁止重名。

    客户端无法在视图 context 里写 ref()，所以用布尔标志传递意图：
    视图里给 product_id 加 context="{'machine_repair_board_default': True}"，
    quick-create 打开的产品表单就会带上它，这里据此补齐类别 / 追溯 / 可库存。
    """

    _inherit = 'product.template'

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('machine_repair_board_default'):
            board_categ = self.env.ref('machine_repair.product_category_board', raise_if_not_found=False)
            for vals in vals_list:
                if not vals.get('categ_id') and board_categ:
                    vals['categ_id'] = board_categ.id
                vals.setdefault('tracking', 'serial')
                vals.setdefault('is_storable', True)
        elif self.env.context.get('machine_repair_component_default'):
            comp_categ = self.env.ref('machine_repair.product_category_component', raise_if_not_found=False)
            for vals in vals_list:
                if not vals.get('categ_id') and comp_categ:
                    vals['categ_id'] = comp_categ.id
                vals.setdefault('tracking', 'none')
                vals.setdefault('is_storable', True)
        return super().create(vals_list)

    @api.constrains('name', 'categ_id')
    def _check_unique_name_in_repair_category(self):
        """「专属电路板 / 通用电子元件」类目内不允许重名。

        以前在「机型标准模板」里内联新建、或在「机型专属电路板」列表点新建，
        都能反复建出同名产品（库里实测出现过两份 DDMP / KBD / PB），
        结果机型模板和扫码选板件时分不清该用哪一条。
        同一个名字只允许存在一条记录：要新建先归档旧记录，或者改用已有记录。
        比较范围按类目标志位（is_board / is_component）算，不是按单个类目算。
        """
        for tmpl in self:
            categ = tmpl.categ_id
            if categ.is_board:
                kind, domain = '电路板', [('categ_id.is_board', '=', True)]
            elif categ.is_component:
                kind, domain = '电子元件', [('categ_id.is_component', '=', True)]
            else:
                continue
            name = (tmpl.name or '').strip()
            if not name:
                continue
            # 按"标志位"而不是按单个类目比较：万一以后又建了第二个勾了
            # 「专属电路板」的类目，跨这两个类目也不会漏掉重名。
            duplicate = self.search(
                [('id', '!=', tmpl.id)] + domain + [('name', '=ilike', name)], limit=1)
            if duplicate:
                raise ValidationError(
                    '同名的%s「%s」已经存在（#%s）。\n'
                    '请直接使用已有记录；如果确实是另一种%s，请换一个名称。'
                    % (kind, name, duplicate.id, kind))
