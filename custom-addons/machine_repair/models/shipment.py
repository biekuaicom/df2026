from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import BinaryBytes, BinaryValue


def _photo_value(value):
    """包装照片统一成 Odoo 20 的 BinaryValue（Image 字段不接受裸 bytes）。"""
    if isinstance(value, BinaryValue):
        return value
    return BinaryBytes(value)


class MachineRepairShipment(models.Model):
    """维修发货单：一次寄回给客户的板件，可以同时包含多张工单。

    业务规则：
      * **一个快递单号可以寄多块板** —— 所以多张工单可以合并到同一张发货单；
      * 必须扫/填快递单号，并至少上传 1 张包装照片，才能确认发货；
      * 确认发货后：工单显示「已发货」，工单详情里可以查到快递单号与包装照片；
      * 活期客户**首次维修**的板件，保修从发货（快递）时间起算（见 repair.order）。
    """

    _name = 'machine.repair.shipment'
    _description = '维修发货单'
    _order = 'ship_date desc, id desc'
    _inherit = ['mail.thread']
    _rec_name = 'name'

    name = fields.Char('发货单号', required=True, copy=False, readonly=True,
                       default='/', index=True)
    ship_date = fields.Datetime('发货时间', default=fields.Datetime.now,
                                required=True, tracking=True)
    user_id = fields.Many2one(
        'res.users', '发货人', default=lambda self: self.env.user, readonly=True, tracking=True)
    tracking_no = fields.Char(
        '快递单号', required=True, index=True, tracking=True,
        help='扫快递单条码或手工填写；同一个单号可以对应多张工单（多块板）。')
    carrier = fields.Char('快递公司')
    order_ids = fields.One2many('repair.order', 'shipment_id', '工单')
    order_count = fields.Integer('工单数', compute='_compute_counts')
    order_names = fields.Char('工单号', compute='_compute_counts')
    partner_names = fields.Char('客户', compute='_compute_counts')
    machine_numbers = fields.Char('机台', compute='_compute_counts')
    photo_ids = fields.One2many('machine.shipment.photo', 'shipment_id', '包装照片')
    photo_count = fields.Integer('包装照片数', compute='_compute_counts')
    note = fields.Text('备注')
    company_id = fields.Many2one(
        'res.company', '公司', default=lambda self: self.env.company, readonly=True)

    @api.depends('order_ids', 'photo_ids')
    def _compute_counts(self):
        for shipment in self:
            orders = shipment.order_ids
            shipment.order_count = len(orders)
            shipment.photo_count = len(shipment.photo_ids)
            shipment.order_names = '、'.join(orders.mapped('name')[:12])
            shipment.partner_names = '、'.join(
                dict.fromkeys(orders.mapped('partner_id.display_name')))[:120]
            shipment.machine_numbers = '、'.join(
                [number for number in orders.mapped('machine_id.machine_no') if number])[:120]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'machine.repair.shipment') or '/'
        return super().create(vals_list)

    def unlink(self):
        for shipment in self:
            if shipment.order_ids:
                raise UserError(
                    '发货单 %s 已经关联了 %s 张工单，不能删除（删掉就查不到快递单号和包装照片了）。'
                    % (shipment.name, len(shipment.order_ids)))
        return super().unlink()

    def action_view_orders(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id(
            'machine_repair.action_repair_hall')
        action.update({
            'name': '发货单 %s 的工单' % self.name,
            'domain': [('shipment_id', '=', self.id)],
            'context': {},
        })
        return action

    def action_apply_shipping(self):
        """手工补录的发货单：按本单发货时间起算首次维修的保修，并给工单留痕。"""
        for shipment in self:
            if not shipment.order_ids:
                raise UserError('这张发货单还没有工单，先把要寄的工单加进来。')
            orders = shipment.order_ids.filtered(
                lambda order: order.state in ('done', 'secondary_completed'))
            if not orders:
                raise UserError('这张发货单里的工单都还没完工，不能发货。')
            orders._start_warranty_from_shipment(shipment.ship_date)
            for order in orders:
                order.message_post(
                    body='已发货：快递单号 %s（发货单 %s）。'
                         % (shipment.tracking_no, shipment.name))
            shipment.message_post(body='已补记发货：%s' % '、'.join(orders.mapped('name')))
        return True

    @api.model
    def _ship_orders(self, orders, tracking_no, carrier=None, note=None, photos=None):
        """把若干张已完工工单合并成一张发货单（手机端/批量按钮都走这里）。

        :param orders: repair.order recordset（必须已完工、且还没发过货）
        :param tracking_no: 快递单号（字符串）
        :param photos: [{'image': bytes, 'name': str}] 包装照片
        """
        tracking_no = (tracking_no or '').strip()
        if not tracking_no:
            raise UserError('请先扫描或填写快递单号。')
        orders = orders.exists()
        if not orders:
            raise UserError('请先选择要发货的工单。')
        done_states = ('done', 'secondary_completed')
        not_done = orders.filtered(lambda order: order.state not in done_states)
        if not_done:
            raise UserError('只有已完工的工单才能发货：%s 还没完工。'
                            % '、'.join(not_done.mapped('name')))
        shipped = orders.filtered(lambda order: order.shipment_id)
        if shipped:
            raise UserError('这些工单已经发过货了：%s'
                            % '、'.join('%s（%s）' % (order.name, order.shipment_id.tracking_no)
                                       for order in shipped))
        photos = photos or []
        if not photos:
            raise UserError('请先拍照上传包装照片（至少 1 张），再确定发货。')

        shipment = self.create({
            'tracking_no': tracking_no,
            'carrier': carrier or False,
            'note': note or False,
            'order_ids': [(6, 0, orders.ids)],
        })
        # 照片单独建：手机端传的是**原始字节**（BinaryBytes），走 o2m 命令容易被当成 base64
        Photo = self.env['machine.shipment.photo']
        for index, photo in enumerate(photos, start=1):
            data = photo.get('image')
            if not data:
                continue
            Photo.create({
                'shipment_id': shipment.id,
                'image': _photo_value(data),
                'name': photo.get('name') or '包装照片',
                'sequence': index * 10,
            })
        orders._mark_shipped(shipment)
        shipment.message_post(
            body='已发货：快递单号 %s，共 %s 张工单 / %s 张包装照片。'
                 % (shipment.tracking_no, len(orders), len(photos)))
        return shipment


class MachineShipmentPhoto(models.Model):
    """发货包装照片（可以多张：外箱、内包装、板件叠加等）。"""

    _name = 'machine.shipment.photo'
    _description = '发货包装照片'
    _order = 'shipment_id, sequence, id'

    shipment_id = fields.Many2one(
        'machine.repair.shipment', '发货单', required=True, ondelete='cascade', index=True)
    image = fields.Image('照片', max_width=1600, max_height=1600, required=True)
    name = fields.Char('说明')
    sequence = fields.Integer('排序', default=10)
    taken_by = fields.Many2one(
        'res.users', '上传人', default=lambda self: self.env.user, readonly=True)
    taken_at = fields.Datetime('上传时间', default=fields.Datetime.now, readonly=True)
    tracking_no = fields.Char(related='shipment_id.tracking_no', string='快递单号', readonly=True)
