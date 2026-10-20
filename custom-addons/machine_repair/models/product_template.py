from odoo import api, models


class ProductTemplate(models.Model):
    """让"在机型模板里内联新建电路板"直接落成合规的板件。

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
