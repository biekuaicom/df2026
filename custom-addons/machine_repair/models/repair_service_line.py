from odoo import fields, models


class RepairServiceLine(models.Model):
    """给维修服务行加一个单价字段。

    原生 repair.service.line 没有价格（价格取自产品售价），
    但我们的过保维修要按「机台台账里这块板的另计单价」收，
    所以在这里补一个 price_unit，并在生成发票行 / 销售行时带过去。
    """

    _inherit = 'repair.service.line'

    price_unit = fields.Float('单价', digits=(12, 2), help='0 表示按产品售价；否则按此单价收费。')
    board_product_id = fields.Many2one(
        'product.product', '对应电路板', help='仅用于说明：这次收费是为哪块板。')

    def _prepare_repair_service_line_common_vals(self):
        vals = super()._prepare_repair_service_line_common_vals()
        if self.price_unit and not self.repair_id.under_warranty:
            vals['price_unit'] = self.price_unit
        if self.board_product_id:
            vals['name'] = '%s — %s' % (self.product_id.display_name,
                                        self.board_product_id.display_name)
        return vals
