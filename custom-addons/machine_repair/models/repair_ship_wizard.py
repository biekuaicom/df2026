from odoo import api, fields, models
from odoo.exceptions import UserError


class RepairShipWizard(models.TransientModel):
    """后台批量发货：勾几张已完工工单 → 填/扫快递单号 → 传包装照片 → 合并成一张发货单。"""

    _name = 'machine.repair.ship.wizard'
    _description = '批量发货（合并快递单号）'

    order_ids = fields.Many2many(
        'repair.order', 'machine_ship_wiz_order_rel', 'wizard_id', 'order_id',
        string='要发货的工单',
        domain="[('state', 'in', ['done', 'secondary_completed']), ('shipment_id', '=', False)]")
    order_count = fields.Integer('工单数', compute='_compute_order_count')
    tracking_no = fields.Char('快递单号', required=True)
    carrier = fields.Char('快递公司')
    note = fields.Text('备注')
    photo_ids = fields.One2many(
        'machine.repair.ship.wizard.photo', 'wizard_id', '包装照片',
        help='至少 1 张：外箱 + 内包装都拍上，方便售后核对。')

    @api.depends('order_ids')
    def _compute_order_count(self):
        for wizard in self:
            wizard.order_count = len(wizard.order_ids)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        active_ids = self.env.context.get('active_ids') or []
        if active_ids and self.env.context.get('active_model') == 'repair.order':
            orders = self.env['repair.order'].browse(active_ids).exists().filtered(
                lambda order: order.state in ('done', 'secondary_completed')
                and not order.shipment_id)
            if orders and 'order_ids' in fields_list:
                values['order_ids'] = [(6, 0, orders.ids)]
        return values

    def action_confirm(self):
        self.ensure_one()
        if not self.order_ids:
            raise UserError('请勾选要一起发货的工单。')
        if not self.photo_ids:
            raise UserError('请至少上传 1 张包装照片，再确定发货。')
        photos = [{
            'image': photo.image,
            'name': photo.name or '包装照片',
        } for photo in self.photo_ids if photo.image]
        shipment = self.env['machine.repair.shipment']._ship_orders(
            self.order_ids, self.tracking_no, carrier=self.carrier,
            note=self.note, photos=photos)
        return {
            'type': 'ir.actions.act_window',
            'name': '发货单 %s' % shipment.name,
            'res_model': 'machine.repair.shipment',
            'res_id': shipment.id,
            'view_mode': 'form',
            'target': 'current',
        }


class RepairShipWizardPhoto(models.TransientModel):
    """向导里临时挂的包装照片，确认发货时复制到正式发货单。"""

    _name = 'machine.repair.ship.wizard.photo'
    _description = '批量发货临时照片'

    wizard_id = fields.Many2one(
        'machine.repair.ship.wizard', '发货向导', required=True, ondelete='cascade')
    image = fields.Image('照片', max_width=1600, max_height=1600, required=True)
    name = fields.Char('说明')
