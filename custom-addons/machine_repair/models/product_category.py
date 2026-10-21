from odoo import fields, models


class ProductCategory(models.Model):
    """给产品分类加标志位，用来区分「专属电路板」与「通用电子元件」。

    方案里写的是"在产品类别上设置必须录入序列号"，但 Odoo 的序列号管控
    （tracking = none / lot / serial）是**产品**上的字段，不在分类上。
    所以这里用分类上的标志位来圈定"哪些产品属于专属电路板 / 通用元件"，
    具体产品的 tracking 仍需逐产品设为 serial（阶段 4 的 SN 校验依赖它）。

    is_component 是补上的对称标志位：以前「通用电子元件」类目只能靠 xmlid 认，
    导致「同名校验」这类规则只能覆盖电路板、漏掉元件。
    """

    _inherit = 'product.category'

    is_board = fields.Boolean(
        string='专属电路板类目',
        help='勾选后，本类目下的产品可被选为「机型模板的专属电路板」；'
             '此类产品应把「追溯」设为「按唯一序列号」。',
    )
    is_component = fields.Boolean(
        string='通用电子元件类目',
        help='勾选后，本类目下的产品视为电阻/电容这类通用元件（不按 SN 管控）。',
    )
