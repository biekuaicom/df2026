from odoo import fields, models


class ProductCategory(models.Model):
    """给产品分类加一个标志位，用来区分「专属电路板」与其他配件。

    方案里写的是"在产品类别上设置必须录入序列号"，但 Odoo 的序列号管控
    （tracking = none / lot / serial）是**产品**上的字段，不在分类上。
    所以这里用分类上的一个标志位来圈定"哪些产品属于专属电路板"，
    具体产品的 tracking 仍需逐产品设为 serial（阶段 4 的 SN 校验依赖它）。
    """

    _inherit = 'product.category'

    is_board = fields.Boolean(
        string='专属电路板类目',
        help='勾选后，本类目下的产品可被选为「机型模板的专属电路板」；'
             '此类产品应把「追溯」设为「按唯一序列号」。',
    )
