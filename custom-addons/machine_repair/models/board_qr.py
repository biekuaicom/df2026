import base64
import hashlib
import hmac
import io
import secrets
from datetime import date

import qrcode

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import BinaryBytes


class MachineBoardQr(models.Model):
    """电路板二维码：一块板件的身份档案。

    设计原则（按实际业务调整过）：
      * 前期**不**在机型模板 / 客户机台台账里建码 —— 那两处只做结构，录入越少越好；
      * **只有真正产生维修业务时**（发布工单）才扫码录入或生成二维码；
      * 二维码要能反查：客户信息、包月时间、板件型号、创建时间、**保修是否到期**；
      * 客户 / 机台等信息发生变化时，**在该二维码上留痕**（继承 mail.thread，字段带 tracking，
        再加一条明确说明变更来源的文字记录）。

    两种来源：
      * oem       —— 沿用板子上的原厂二维码 / 条码
      * generated —— 原厂信息模糊或缺失时由系统生成（默认）

    二维码内容（扫码枪/手机可直接读出）：
      MR1;QR=..;H=..;CID=..;CNAME=..;MODEL=..;PDT=..;MS=..;ME=..;WAR=..;QID=..;PID=..;S=..
      H=身份摘要（客户+创建时间+板件型号 的 sha1 前 6 位），S=整段内容的校验码
    """

    _name = 'machine.board.qr'
    _description = '电路板二维码'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'name'

    name = fields.Char('二维码编号', required=True, copy=False, index=True, readonly=True)
    source = fields.Selection(
        [('oem', '原厂二维码 / 条码'), ('generated', '系统生成')],
        string='来源', default='generated', required=True, tracking=True,
    )
    digest = fields.Char('身份摘要', compute='_compute_digest', store=True, readonly=True,
                         help='客户信息 + 创建时间 + 板件型号 的摘要，用来核对身份来源。')

    partner_id = fields.Many2one('res.partner', '客户', index=True, tracking=True)
    machine_id = fields.Many2one('customer.machine', '机台', index=True, tracking=True)
    machine_no = fields.Char('机台编号', related='machine_id.machine_no', store=True, readonly=True)
    product_id = fields.Many2one(
        'product.product', '电路板型号', tracking=True,
        domain=[('categ_id.is_board', '=', True)],
    )
    sn = fields.Char('原厂 SN / 条码', help='扫原厂二维码得到的内容；系统生成的码可以为空。')

    created_on = fields.Date('创建时间', default=fields.Date.context_today, required=True,
                             readonly=True, tracking=True)
    warranty_start_date = fields.Date(
        '保修起算日', default=fields.Date.context_today, tracking=True,
        help='默认等于创建时间；换板（另计费配件）完工时，会重置为换板日。')

    # 包月时间（从机台带出，可手工调整；用于反查该板件属于哪段包月服务）
    monthly_start_date = fields.Date('包月开始', tracking=True)
    monthly_end_date = fields.Date('包月结束', tracking=True)

    # 保修
    warranty_months = fields.Integer('保修月数', default=12, tracking=True)
    warranty_end_date = fields.Date('保修到期日', compute='_compute_warranty',
                                    store=True, readonly=False, tracking=True,
                                    help='默认 = 创建时间 + 保修月数，可手工改。')
    warranty_state = fields.Selection(
        [('unknown', '未设置'), ('active', '保修中'), ('expired', '已过保')],
        string='保修状态', compute='_compute_warranty_state', store=False,
    )

    payload = fields.Text('二维码内容', compute='_compute_payload', store=True, readonly=True)
    qr_image = fields.Binary('二维码图片', compute='_compute_qr_image', readonly=True)
    photo_ids = fields.One2many('machine.board.photo', 'qr_id', '电路板照片')
    front_photo_count = fields.Integer('正面照片数', compute='_compute_photo_counts')
    back_photo_count = fields.Integer('反面照片数', compute='_compute_photo_counts')
    has_required_photos = fields.Boolean(
        '正反面齐全', compute='_compute_photo_counts',
        help='必须有至少一张正面和一张反面，才允许把工单发布到大厅。')
    machine_line_id = fields.Many2one(
        'customer.machine.line', '对应机台账板件', ondelete='set null', readonly=True)
    repair_order_ids = fields.One2many('repair.order', 'board_qr_id', '相关工单', readonly=True)
    repair_count = fields.Integer('维修次数', compute='_compute_repair_count')
    state = fields.Selection(
        [('active', '有效'), ('scrapped', '已报废')], default='active', required=True, tracking=True)
    note = fields.Text('备注')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, readonly=True)

    _uniq_name = models.Constraint('unique(name)', '二维码编号不能重复。')

    @api.constrains('sn')
    def _check_sn_unique(self):
        """原厂 SN / 条码不能重复建档。

        扫码建档是按「编号」或「原厂 SN」反查记录的；同一个 SN 存成两块板之后，
        反查一次命中两条，扫码会直接报“二维码重复、无法确定是哪一块”。
        """
        for board_qr in self:
            sn = (board_qr.sn or '').strip()
            if not sn:
                continue
            duplicate = self.search([
                ('id', '!=', board_qr.id),
                ('sn', '=', sn),
            ], limit=1)
            if duplicate:
                raise ValidationError(
                    '原厂 SN / 条码「%s」已经被 %s 使用，不能重复建档。\n'
                    '同一块板请直接用 %s 继续维修。'
                    % (sn, duplicate.name, duplicate.name))

    # ---------------------------------------------------------------- 生成
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self._next_code()
            if vals.get('machine_id'):
                machine = self.env['customer.machine'].browse(vals['machine_id'])
                # 注意不能只 setdefault：传进来的可能是显式的 False
                if not vals.get('monthly_start_date'):
                    vals['monthly_start_date'] = machine.monthly_start_date
                if not vals.get('monthly_end_date'):
                    vals['monthly_end_date'] = machine.monthly_end_date
        return super().create(vals_list)

    @api.model
    def _next_code(self):
        seq = self.env['ir.sequence'].next_by_code('machine.board.qr') or '1'
        return 'QR-%s' % seq

    @staticmethod
    def _scrub(text):
        return (text or '').replace(';', ' ').replace('=', ' ').replace('\n', ' ').strip()[:40]

    @api.model
    def _qr_secret(self):
        """签名密钥：首次使用时自动生成并存在系统参数里（备份数据库即备份密钥）。

        注意 Odoo 20 的 ir.config_parameter 已经没有 get_param/set_param，
        改成了按类型的 get_str / set_str / get_int / set_bool 等。
        """
        ICP = self.env['ir.config_parameter'].sudo()
        secret = ICP.get_str('machine_repair.qr_secret')
        if not secret:
            secret = secrets.token_hex(24)
            ICP.set_str('machine_repair.qr_secret', secret)
        return secret

    @api.model
    def _sign(self, body):
        """HMAC-SHA256 前 10 位。

        码里是**明文**（任何扫码器都能读出客户/机台/保修），
        但这一段签名只有本系统算得出来：改一个字符就验不过，防伪靠它。
        """
        return hmac.new(self._qr_secret().encode('utf-8'),
                        body.encode('utf-8'), hashlib.sha256).hexdigest()[:10]

    @staticmethod
    def _legacy_checksum(body):
        return hashlib.sha1(body.encode('utf-8')).hexdigest()[:8]

    @api.depends('partner_id', 'created_on', 'product_id')
    def _compute_digest(self):
        for board_qr in self:
            raw = '%s|%s|%s' % (board_qr.partner_id.id or 0, board_qr.created_on or '',
                                board_qr.product_id.id or 0)
            board_qr.digest = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:6].upper()

    @api.depends('created_on', 'warranty_start_date', 'warranty_months')
    def _compute_warranty(self):
        for board_qr in self:
            start = board_qr.warranty_start_date or board_qr.created_on
            if not start or not board_qr.warranty_months:
                board_qr.warranty_end_date = board_qr.warranty_end_date or False
                continue
            month_index = start.month - 1 + board_qr.warranty_months
            year = start.year + month_index // 12
            month = month_index % 12 + 1
            day = min(start.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                                  31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
            board_qr.warranty_end_date = date(year, month, day)

    @api.depends('warranty_end_date')
    def _compute_warranty_state(self):
        today = fields.Date.context_today(self)
        for board_qr in self:
            if not board_qr.warranty_end_date:
                board_qr.warranty_state = 'unknown'
            elif board_qr.warranty_end_date < today:
                board_qr.warranty_state = 'expired'
            else:
                board_qr.warranty_state = 'active'

    @api.depends('name', 'digest', 'partner_id', 'created_on', 'product_id',
                 'monthly_start_date', 'monthly_end_date', 'warranty_end_date')
    def _compute_payload(self):
        for board_qr in self:
            body = ';'.join([
                'MR1',
                'QR=%s' % (board_qr.name or ''),
                'H=%s' % (board_qr.digest or ''),
                'CID=%s' % (board_qr.partner_id.id or ''),
                'CNAME=%s' % self._scrub(board_qr.partner_id.display_name),
                'MODEL=%s' % self._scrub(board_qr.product_id.display_name),
                'PDT=%s' % (board_qr.created_on or ''),
                'MS=%s' % (board_qr.monthly_start_date or ''),
                'ME=%s' % (board_qr.monthly_end_date or ''),
                'WAR=%s' % (board_qr.warranty_end_date or ''),
                'QID=%s' % (board_qr.id if isinstance(board_qr.id, int) else ''),
                'PID=%s' % (board_qr.product_id.id or ''),
            ])
            board_qr.payload = '%s;S=%s' % (body, self._sign(body))

    def _compute_qr_image(self):
        for board_qr in self:
            value = board_qr.payload or board_qr.name or ''
            if not value:
                board_qr.qr_image = False
                continue
            image = qrcode.make(value)
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            board_qr.qr_image = BinaryBytes(buffer.getvalue())

    @api.depends('repair_order_ids')
    def _compute_repair_count(self):
        for board_qr in self:
            board_qr.repair_count = len(board_qr.repair_order_ids)

    @api.depends('photo_ids', 'photo_ids.kind')
    def _compute_photo_counts(self):
        for board_qr in self:
            board_qr.front_photo_count = len(
                board_qr.photo_ids.filtered(lambda photo: photo.kind == 'front'))
            board_qr.back_photo_count = len(
                board_qr.photo_ids.filtered(lambda photo: photo.kind == 'back'))
            board_qr.has_required_photos = (
                board_qr.front_photo_count > 0 and board_qr.back_photo_count > 0)

    def history_payload(self, limit=5):
        """给手机端用的维修履历：最近几张工单 + 照片统计。"""
        self.ensure_one()
        orders = self.repair_order_ids.sorted('create_date', reverse=True)[:limit]
        Order = self.env['repair.order']
        return {
            'repairs': [{
                'name': order.name,
                'date': order.create_date.strftime('%Y-%m-%d') if order.create_date else '',
                'state': Order.state_label(order.state),
                'technician': order.technician_id.name or '',
                'fault': (order.fault_description or '')[:40],
                'invoiced': bool(order.invoice_ids),
            } for order in orders],
            'photos': {
                'front': len(self.photo_ids.filtered(lambda photo: photo.kind == 'front')),
                'back': len(self.photo_ids.filtered(lambda photo: photo.kind == 'back')),
                'detail': len(self.photo_ids.filtered(lambda photo: photo.kind == 'detail')),
            },
        }

    # ---------------------------------------------------------------- 解析 / 扫描
    @api.model
    def decode_payload(self, payload):
        payload = (payload or '').strip()
        if not payload.startswith('MR1;'):
            raise ValidationError('这不是本系统的电路板二维码：%s' % payload[:40])
        parts = dict(item.split('=', 1) for item in payload.split(';') if '=' in item)
        checksum = parts.pop('S', '')
        body = payload.rsplit(';S=', 1)[0]
        # 新码用 HMAC 签名；老码（sha1）也兼容
        if checksum != self._sign(body) and checksum != self._legacy_checksum(body):
            raise ValidationError('二维码校验失败，内容可能被篡改或不是本系统生成。')
        return parts

    @api.model
    def find_by_code(self, code):
        """只查不建：给手机端扫码预览用（避免扫错码就留下垃圾档案）。"""
        code = (code or '').strip()
        if not code:
            return self.browse()
        if code.startswith('MR1;'):
            try:
                parsed = self.decode_payload(code)
                code = parsed.get('QR') or code
            except ValidationError:
                pass
        return self.search(['|', ('name', '=', code), ('sn', '=', code)], limit=1)

    @api.model
    def find_or_create_by_code(self, code, partner=None, machine=None, product=None, origin=None):
        """扫到的内容 → 定位或创建二维码；信息有变化就在该码上留痕。"""
        code = (code or '').strip()
        if not code:
            raise UserError('请先扫描或填写二维码内容。')
        parsed = {}
        if code.startswith('MR1;'):
            parsed = self.decode_payload(code)
            code = parsed.get('QR') or code
        candidates = self.search(['|', ('name', '=', code), ('sn', '=', code)])
        if len(candidates) > 1:
            raise UserError(
                '扫码命中 %s 条记录（%s），二维码重复。请先到「电路板二维码」里合并或报废多余记录，'
                '否则无法确定维修的是哪一块板。'
                % (len(candidates), '、'.join(candidates.mapped('name'))))

        if candidates:
            board_qr = candidates
            changes = []
            if partner and board_qr.partner_id != partner:
                changes.append('客户：%s → %s'
                               % (board_qr.partner_id.display_name or '（空）', partner.display_name))
            if machine and board_qr.machine_id != machine:
                changes.append('机台：%s → %s'
                               % (board_qr.machine_id.machine_no or '（空）', machine.machine_no))
            if changes:
                board_qr.write({
                    'partner_id': partner.id if partner else board_qr.partner_id.id,
                    'machine_id': machine.id if machine else board_qr.machine_id.id,
                })
                board_qr.message_post(
                    body='扫码信息变更 —— %s%s'
                         % ('；'.join(changes), ('（来源工单 %s）' % origin) if origin else ''))
            return board_qr

        create_vals = {
            'name': code if code.startswith('QR-') else False,
            'sn': False if code.startswith('QR-') else code,
            'source': 'generated' if code.startswith('QR-') else 'oem',
            'partner_id': partner.id if partner else False,
            'machine_id': machine.id if machine else False,
            'product_id': product.id if product else False,
        }
        # 只有扫码内容里确实带了这些信息才传，否则让 create()/compute 去推导
        # （传空值会阻止保修日期自动计算）
        for key, field in (('MS', 'monthly_start_date'), ('ME', 'monthly_end_date'),
                           ('WAR', 'warranty_end_date')):
            if parsed.get(key):
                create_vals[field] = parsed[key]
        board_qr = self.create(create_vals)
        # 如果这台机台上正好有同型号板件且还没挂码，顺手把台账行关联起来（不强制）
        if machine and product:
            line = self.env['customer.machine.line'].search([
                ('machine_id', '=', machine.id),
                ('product_id', '=', product.id),
                ('qr_id', '=', False),
            ], limit=1)
            if line:
                line.qr_id = board_qr.id
                board_qr.machine_line_id = line.id
        return board_qr

    def action_mark_scrapped(self):
        for board_qr in self:
            board_qr.state = 'scrapped'
        return True
