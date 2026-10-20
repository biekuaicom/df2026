from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

# 方案：普通 7 天 / 紧急 3 天 / 加急 1 天
URGENCY_DAYS = {'normal': 7, 'urgent': 3, 'emergency': 1}
# 看板/列表置顶用的数值排序：越小越靠前
URGENCY_SEQ = {'emergency': 1, 'urgent': 2, 'normal': 3}


class RepairOrder(models.Model):
    """在原生 repair.order 上扩展：三级紧急度、抢单 / 退单 / 超时接力、师傅绑定、修改计数。

    复用原生能力：客户、产品、配件行、序列号/批次出入库、开票、报价单。
    只补"调度"这一层，不重造工单与库存逻辑。
    """

    _inherit = 'repair.order'

    # ---------------------------------------------------------------- 状态扩展
    state = fields.Selection(
        selection_add=[
            ('pending_claim', '待抢单'),
            ('timeout_alert', '超时接力'),
            ('secondary_completed', '接力完工'),
        ],
        ondelete={
            'pending_claim': 'set draft',
            'timeout_alert': 'set draft',
            'secondary_completed': 'set done',
        },
    )

    # ---------------------------------------------------------------- 调度字段
    urgency_level = fields.Selection(
        [('normal', '普通（7 天）'), ('urgent', '紧急（3 天）'), ('emergency', '加急（1 天）')],
        string='紧急程度', default='normal', required=True, tracking=True, index=True,
        help='决定计划完成期限；管理员可随时改紧急度，也可直接改期限。',
    )
    urgency_days = fields.Integer('时限（天）', compute='_compute_urgency', store=True)
    urgency_sequence = fields.Integer(
        '紧急度排序', compute='_compute_urgency', store=True,
        help='加急=1、紧急=2、普通=3；用于看板与列表把加急单置顶。',
    )
    deadline_datetime = fields.Datetime(
        '计划完成期限', compute='_compute_urgency', store=True, readonly=False, tracking=True,
        help='按紧急度自动推算（普通 7 天 / 紧急 3 天 / 加急 1 天），管理员可手动覆盖。',
    )
    technician_id = fields.Many2one(
        'res.users', '维修师傅', tracking=True, index=True, copy=False,
        help='抢单后绑定；退单或超时接力会清空。',
    )
    claimed_at = fields.Datetime('抢单时间', readonly=True, copy=False)
    relay_count = fields.Integer('接力次数', default=0, readonly=True, copy=False)
    relay_from_id = fields.Many2one('repair.order', '接力来源', readonly=True, copy=False)
    relay_reason = fields.Text('退单 / 接力原因', copy=False)
    modify_count = fields.Integer(
        '当天修改次数', default=0, readonly=True, copy=False,
        help='阶段 4 使用：师傅完工后当天可修改的次数上限（默认 3 次）。',
    )
    modify_count_date = fields.Date('修改计数日期', readonly=True, copy=False)

    is_overdue = fields.Boolean('已超期', compute='_compute_claim_state')
    deadline_state = fields.Selection(
        [('ok', '正常'), ('overdue', '超期')],
        string='期限状态', compute='_compute_claim_state',
        help='看板徽标用；字段驱动，不需要在模板里做条件判断。',
    )
    claimable = fields.Boolean('可抢单', compute='_compute_claim_state')

    # ---------------------------------------------------------------- 阶段 4：返修识别
    machine_id = fields.Many2one(
        'customer.machine', '客户机台', tracking=True, index=True, copy=False,
        help='关联客户机台台账；用于返修识别、单台履历报表。',
    )
    previous_repair_count = fields.Integer('历史维修次数', compute='_compute_rework')
    repair_round = fields.Integer('维修轮次', compute='_compute_rework')
    is_rework = fields.Boolean('返修', compute='_compute_rework', store=False)

    # 阶段 4：完工后修改限制
    MODIFY_LIMIT = 3

    # 阶段 5：故障信息（快照来源）+ 双轨制录入
    fault_category_id = fields.Many2one('repair.fault.category', '故障分类', tracking=True)
    fault_description = fields.Text('故障简述')
    solution = fields.Text('处理方式')

    # 二维码主线：扫板件二维码即可带出客户与机台（包月与外来维修同一套流程）
    board_qr_id = fields.Many2one(
        'machine.board.qr', '电路板二维码', tracking=True, index=True, copy=False,
        help='扫板件二维码：系统据此带出客户、机台与板件型号。',
    )
    scan_code = fields.Char(
        '扫码录入',
        help='扫码枪扫出来的是整段内容（MR1;QR=...）。贴到这里再点「识别二维码」，'
             '系统会解析出客户、机台、板件；库里没有就自动建档（原厂码或系统生成码均可）。',
    )
    qr_warranty_state = fields.Selection(
        related='board_qr_id.warranty_state', string='二维码保修状态', readonly=True,
        help='来自板件二维码：保修中则本单自动勾「保修内（不计价）」。',
    )
    extra_charge_price = fields.Float(
        '另计维修单价', digits=(12, 2),
        help='过保维修的收费单价。选机台时自动从台账「另计单价」带出；外来维修可手工填。',
    )
    warranty_months = fields.Selection(
        [(str(month), '%s 个月' % month) for month in range(1, 13)],
        string='本次保修月数',
        help='这次收费维修后，该板件的保修重新起算多少个月。'
             '选机台时自动从台账「保修月数」带出；外来维修请手工填。',
    )

    def _apply_extra_charges(self):
        """把机台账里「另计费」的板件带成**维修服务行**（不是配件出库）。

        业务口径：过保 = 重新维修 + 重新计时 + 重新收费，不涉及换新板，
        所以这里生成的是服务行（产品=电路板维修费，单价=台账另计单价），
        开票时按该单价计入，库存不发生任何变动。
        """
        ServiceLine = self.env['repair.service.line']
        service_product = self.env.ref(
            'machine_repair.product_repair_service_fee', raise_if_not_found=False)
        for order in self:
            if not order.machine_id:
                continue
            extra_lines = order.machine_id.line_ids.filtered(
                lambda line: line.billing_type == 'extra' and line.product_id
            )
            if not extra_lines:
                continue
            # 单上还没有单价/保修月数时，用台账里的值兜底
            first = extra_lines[0]
            vals = {}
            if not order.extra_charge_price and first.extra_price:
                vals['extra_charge_price'] = first.extra_price
            if not order.warranty_months and first.warranty_months:
                vals['warranty_months'] = first.warranty_months
            if vals:
                order.write(vals)
            price = order.extra_charge_price or first.extra_price or 0.0
            months = int(order.warranty_months or first.warranty_months or 12)
            added = []
            for line in extra_lines:
                exists = order.repair_service_line_ids.filtered(
                    lambda s: s.board_product_id == line.product_id)
                if exists:
                    if not exists[0].price_unit and price:
                        exists[0].price_unit = price
                    continue
                ServiceLine.create({
                    'repair_id': order.id,
                    'product_id': service_product.id,
                    'board_product_id': line.product_id.id,
                    'quantity': 1.0,
                    'price_unit': price,
                    'description': '过保维修：%s（保修重新起算 %s 个月）'
                                   % (line.product_id.display_name, months),
                })
                added.append(line.product_id.display_name)
            if added:
                order.message_post(
                    body='已按机台台账带出另计维修：%s，单价 %s，保修重新起算 %s 个月。'
                         % ('、'.join(added), price, months))
        return True

    def action_load_extra_parts(self):
        """手动再拉一次另计维修服务行（台账改价 / 加板件 / 外来维修时用）。"""
        self._apply_extra_charges()
        return True

    def action_resolve_scan(self):
        """解析扫码内容：优先找已有二维码，找不到就建档（外来维修现场补码）。"""
        self.ensure_one()
        code = (self.scan_code or '').strip()
        if not code:
            raise UserError('请先用扫码枪扫一下，或手工粘贴二维码内容。')
        board_qr = self.env['machine.board.qr'].find_or_create_by_code(
            code,
            partner=self.partner_id or None,
            machine=self.machine_id or None,
            product=self.product_id or None,
        )
        self.board_qr_id = board_qr.id
        vals = {'scan_code': False}
        if board_qr.partner_id and not self.partner_id:
            vals['partner_id'] = board_qr.partner_id.id
        if board_qr.machine_id:
            vals['machine_id'] = board_qr.machine_id.id
        # 保修期内 -> 自动按「保修内不计价」处理（开票时配件价格归零）
        vals['under_warranty'] = board_qr.warranty_state == 'active'
        self.write(vals)
        self.message_post(body='扫码识别为 %s（来源：%s）。'
                          % (board_qr.name, dict(board_qr._fields['source'].selection).get(
                              board_qr.source, board_qr.source)))
        return True

    @api.onchange('board_qr_id')
    def _onchange_board_qr(self):
        if self.board_qr_id:
            if self.board_qr_id.partner_id and not self.partner_id:
                self.partner_id = self.board_qr_id.partner_id
            if self.board_qr_id.machine_id:
                self.machine_id = self.board_qr_id.machine_id
            if self.board_qr_id.product_id:
                self.product_id = self.board_qr_id.product_id
            self.under_warranty = self.board_qr_id.warranty_state == 'active'

    def action_create_board_qr(self):
        """外来维修现场建码 / 原厂码缺失时补码：按本单的客户+机台+板件生成一张新码。"""
        self.ensure_one()
        if self.board_qr_id:
            raise UserError('本工单已经绑定了二维码 %s。' % self.board_qr_id.name)
        if not self.partner_id:
            raise UserError('请先选择客户，再生成二维码。')
        self.board_qr_id = self.env['machine.board.qr'].create({
            'partner_id': self.partner_id.id,
            'machine_id': self.machine_id.id,
            'product_id': self.product_id.id,
            'source': 'generated',
        })
        self.message_post(body='已生成电路板二维码 %s。' % self.board_qr_id.name)
        return True

    # ---------------------------------------------------------------- 计算
    @api.depends('urgency_level', 'create_date')
    def _compute_urgency(self):
        for order in self:
            order.urgency_days = URGENCY_DAYS.get(order.urgency_level, 7)
            order.urgency_sequence = URGENCY_SEQ.get(order.urgency_level, 3)
            base = order.create_date or fields.Datetime.now()
            order.deadline_datetime = base + timedelta(days=order.urgency_days)

    @api.depends('deadline_datetime', 'state', 'technician_id')
    def _compute_claim_state(self):
        now = fields.Datetime.now()
        for order in self:
            closed = order.state in ('done', 'cancel', 'secondary_completed')
            order.is_overdue = bool(
                order.deadline_datetime and order.deadline_datetime < now and not closed
            )
            order.deadline_state = 'overdue' if order.is_overdue else 'ok'
            order.claimable = order.state in ('pending_claim', 'timeout_alert') and not order.technician_id

    # ---------------------------------------------------------------- 抢单 / 退单 / 接力
    # ---------------------------------------------------------------- 维修过程记录
    error_code = fields.Char('错误代码')
    analysis_note = fields.Text('故障分析判断')
    repair_process = fields.Text('维修过程')
    measure_resistance = fields.Boolean('电阻测量完成', copy=False)
    measure_voltage = fields.Boolean('电压测量完成', copy=False)
    measure_inductance = fields.Boolean('电感测量完成', copy=False)
    measure_capacitance = fields.Boolean('电容测量完成', copy=False)
    measure_note = fields.Text('测量记录')
    video_ids = fields.One2many('machine.repair.video', 'order_id', '维修视频', copy=False)
    video_count = fields.Integer('视频数', compute='_compute_video_count')

    @api.depends('video_ids')
    def _compute_video_count(self):
        for order in self:
            order.video_count = len(order.video_ids)

    # ---------------------------------------------------------------- 状态中文名
    # Odoo 自带的 draft/confirmed/done/cancel 在中文环境里没有翻译，
    # 统一在这里映射，手机端所有显示都走 state_label()。
    STATE_LABELS_ZH = {
        'draft': '新建',
        'confirmed': '维修中',
        'done': '已完工',
        'secondary_completed': '接力完工',
        'pending_claim': '待抢单',
        'timeout_alert': '超时接力',
        'cancel': '已退单',
    }

    @api.model
    def state_label(self, state):
        """状态键 -> 中文名（找不到就用 Odoo 自带的标签兜底）。"""
        label = self.STATE_LABELS_ZH.get(state)
        if label:
            return label
        return dict(self._fields['state'].selection).get(state, state or '')

    def action_publish(self):
        """把新建或超时的工单发布到大厅，等待师傅抢单。

        发布前必须已经绑定二维码，并且这块板拍好了**正面 + 反面**照片
        （业务要求：没有正反面照片不允许进入大厅）。
        """
        for order in self:
            if order.state not in ('draft', 'timeout_alert'):
                raise UserError('只有「新建」或「超时接力」状态的工单可以发布到大厅。')
            if not order.board_qr_id:
                raise UserError('请先扫码识别或生成电路板二维码，再发布工单。')
            if not order.board_qr_id.has_required_photos:
                raise UserError(
                    '发布前必须先拍电路板正面和反面照片（当前：正面 %s 张 / 反面 %s 张）。'
                    '可以在手机 App 里直接拍，或在「电路板二维码」里补拍。'
                    % (order.board_qr_id.front_photo_count, order.board_qr_id.back_photo_count))
            order.write({'state': 'pending_claim'})
            order.message_post(body='已发布到工单大厅，等待抢单。')
        return True

    def action_claim(self):
        """师傅抢单。

        并发安全靠 SQL 层的条件更新实现：只有 technician_id 仍为空、且状态仍是可抢时，
        UPDATE 才会命中一行。rowcount != 1 说明被别人抢走了 —— 不需要 cr.commit()，
        Odoo 的整个请求本身就在一个事务里，中途 commit 反而会破坏原子性。
        """
        self.ensure_one()
        if self.technician_id:
            raise UserError('该工单已被 %s 抢走。' % self.technician_id.name)
        # The ORM defers writes; raw SQL below must see everything already written in
        # this transaction, otherwise the WHERE clause sees stale state. Flushing is the
        # correct fix here - NOT cr.commit(), which would break the request's atomicity.
        self.env.flush_all()
        self.env.cr.execute("""
            UPDATE repair_order
               SET technician_id = %s,
                   claimed_at = (now() AT TIME ZONE 'UTC'),
                   state = 'confirmed',
                   write_uid = %s,
                   write_date = (now() AT TIME ZONE 'UTC')
             WHERE id = %s
               AND technician_id IS NULL
               AND state IN ('pending_claim', 'timeout_alert')
        """, (self.env.uid, self.env.uid, self.id))
        if self.env.cr.rowcount != 1:
            raise UserError('抢单失败：该工单刚刚已被其他师傅抢走，或状态已变化。请刷新后重试。')
        self.invalidate_recordset(['technician_id', 'claimed_at', 'state'])
        self.message_post(body='%s 抢单成功。' % self.env.user.display_name)
        return True

    # ---------------------------------------------------------------- 站内提醒
    def _notify_users(self, users, body):
        """给指定用户发站内提醒。

        走 chatter（message_post + partner_ids）：对方会收到 bus 推送（界面右下角），
        **不需要配置 SMTP**。若要同时发邮件，需先在 设置 → 邮件服务器 配好出站服务器。
        """
        self.ensure_one()
        partners = users.mapped('partner_id').filtered(lambda partner: partner)
        if not partners:
            return
        self.message_post(
            body=body, partner_ids=partners.ids, subtype_xmlid='mail.mt_comment',
        )

    def action_release(self):
        """主动退单：放回大厅，原因写进 relay_reason。"""
        for order in self:
            if order.technician_id and order.technician_id != self.env.user and not self.env.is_admin():
                raise UserError('只有当前维修师傅或管理员可以退单。')
            order.write({
                'technician_id': False,
                'claimed_at': False,
                'state': 'pending_claim',
            })
            order.message_post(body='%s 退单。原因：%s'
                               % (self.env.user.display_name, order.relay_reason or '（未填）'))
        return True

    def action_relay(self):
        """转为超时接力：清空师傅，任何师傅都可以重新抢。"""
        for order in self:
            previous = order.technician_id
            order.write({
                'state': 'timeout_alert',
                'technician_id': False,
                'claimed_at': False,
                'relay_count': order.relay_count + 1,
                'relay_from_id': order.relay_from_id.id or order.id,
            })
            order.message_post(body='转为超时接力（第 %s 次）。原因：%s'
                               % (order.relay_count, order.relay_reason or '（未填）'))
            if previous and previous != self.env.user:
                order._notify_users(
                    previous,
                    '你的工单 %s 已转入「超时接力」，其他师傅可以接手了。' % order.name,
                )
        return True

    def action_secondary_complete(self):
        """接力完工：由接力接手的师傅标记完成。"""
        for order in self:
            order.write({'state': 'secondary_completed'})
            order.message_post(body='接力完工，由 %s 标记。' % self.env.user.display_name)
        self._create_repair_case()
        return True

    # ---------------------------------------------------------------- 阶段 4：返修识别
    @api.depends('machine_id', 'state')
    def _compute_rework(self):
        for order in self:
            if not order.machine_id:
                order.previous_repair_count = 0
                order.repair_round = 1
                order.is_rework = False
                continue
            own_id = order.id if isinstance(order.id, int) else 0
            previous = self.search_count([
                ('machine_id', '=', order.machine_id.id),
                ('id', '!=', own_id),
                ('state', 'in', ('done', 'secondary_completed')),
            ])
            order.previous_repair_count = previous
            order.repair_round = previous + 1
            order.is_rework = previous > 0

    # ---------------------------------------------------------------- 阶段 4：完工后修改限制
    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders.with_context(machine_repair_skip_extra_parts=True)._apply_extra_charges()
        return orders

    def write(self, vals):
        """完工后的修改限制：师傅每天最多 3 次，管理员不限，且每次都留痕。"""
        if not self.env.context.get('machine_repair_skip_modify_limit'):
            protected = self.filtered(
                lambda order: order.state in ('done', 'secondary_completed')
            )
            if protected and not self.env.is_admin():
                today = fields.Date.context_today(self)
                for order in protected:
                    used = order.modify_count if order.modify_count_date == today else 0
                    if used >= order.MODIFY_LIMIT:
                        raise UserError(
                            '该工单今天已修改 %s 次（上限 %s 次），需管理员解锁后方可继续修改。'
                            % (used, order.MODIFY_LIMIT)
                        )
                    vals = dict(vals, modify_count=used + 1, modify_count_date=today)
                    order.message_post(body='%s 修改了已完工工单（今日第 %s 次）。'
                                       % (self.env.user.display_name, used + 1))
        result = super().write(vals)
        # 选了机台就把「另计费」板件带成维修服务行（带标志位防递归）
        if 'machine_id' in vals and not self.env.context.get('machine_repair_skip_extra_parts'):
            self.with_context(machine_repair_skip_extra_parts=True)._apply_extra_charges()
        return result

    def action_reset_modify_count(self):
        """管理员解锁：把当天修改次数清零。"""
        if not self.env.is_admin():
            raise UserError('只有管理员可以重置修改次数。')
        self.with_context(machine_repair_skip_modify_limit=True).write({
            'modify_count': 0, 'modify_count_date': False,
        })
        for order in self:
            order.message_post(body='管理员 %s 重置了该工单的修改次数。' % self.env.user.display_name)
        return True

    # ---------------------------------------------------------------- 阶段 4：SN 强校验
    def _check_videos(self):
        """完工前必须至少上传 1 段维修视频（业务要求）。"""
        for order in self:
            if not order.video_ids:
                raise UserError('请先上传至少 1 段维修视频，再提交完工。')

    def _check_parts_serial_numbers(self):
        """SN 校验已按业务要求取消。

        现在**不再要求任何 SN 码**：板件的身份由「电路板二维码」承担，
        二维码在工单创建/扫码时生成，库存层面也不再按序列号管控。
        保留这个方法名，避免历史调用点报错。
        """
        return True

    def action_repair_done(self):
        self._ensure_repair_lot()
        self._check_parts_serial_numbers()
        self._check_videos()
        result = super().action_repair_done()
        self._refresh_board_warranty()
        self._create_repair_case()
        return result

    def _ensure_repair_lot(self):
        """Odoo 原生要求"序列号管控的被修产品"必须带 lot_id。

        手机端只扫到二维码/原厂 SN，这里按 SN 找（或建）库存序列号后补到工单上，
        否则完工会被 "Serial number is required" 卡住。
        """
        for order in self:
            product = order.product_id
            if not product or product.tracking not in ('lot', 'serial') or order.lot_id:
                continue
            board_qr = order.board_qr_id
            name = (board_qr.sn or board_qr.name) if board_qr else False
            if not name:
                continue
            lot = self.env['stock.lot'].search([
                ('name', '=', name), ('product_id', '=', product.id)], limit=1)
            if not lot:
                lot = self.env['stock.lot'].create({
                    'name': name, 'product_id': product.id,
                    'company_id': order.company_id.id,
                })
            order.lot_id = lot
        return True

    def _refresh_board_warranty(self):
        """过保收费维修完工：该板件保修从完工日重新起算（重新计时 ≠ 换板）。

        条件：本单绑定了二维码，且本单是**收费**维修（没有勾「保修内不计价」）。
        保修月数取本单的「本次保修月数」（选机台时已从台账带出，外来维修可手填）。
        """
        today = fields.Date.context_today(self)
        for order in self:
            board_qr = order.board_qr_id
            if not board_qr or order.under_warranty:
                continue
            months = int(order.warranty_months or board_qr.warranty_months or 12)
            board_qr.write({'warranty_start_date': today, 'warranty_months': months})
            board_qr.message_post(
                body='过保维修完工，保修从 %s 起重新计算 %s 个月（工单 %s）。'
                     % (today, months, order.name))
        return True

    # ---------------------------------------------------------------- 阶段 5：快照
    def _create_repair_case(self):
        """完工时自动生成不可篡改的维修快照（同一工单只生成一次）。"""
        Case = self.env['repair.case']
        for order in self:
            if Case.search_count([('repair_order_id', '=', order.id)]):
                continue
            part_lines = []
            summary = []
            for move in order.move_ids.filtered(lambda m: m.repair_line_type):
                lots = ', '.join(move.move_line_ids.mapped('lot_id.name'))
                part_lines.append((0, 0, {
                    'product_id': move.product_id.id,
                    'qty': move.product_uom_qty,
                    'sn': lots,
                    'price_unit': move.price_unit,
                }))
                summary.append('%s × %s%s' % (
                    move.product_id.display_name, move.product_uom_qty,
                    (' [%s]' % lots) if lots else ''))
            Case.create({
                'repair_order_id': order.id,
                'machine_id': order.machine_id.id,
                'machine_template_id': order.machine_id.template_id.id,
                'partner_id': order.partner_id.id,
                'machine_no': order.machine_id.machine_no,
                'technician_id': order.technician_id.id,
                'fault_category_id': order.fault_category_id.id,
                'fault_description': order.fault_description,
                'solution': order.solution,
                'part_line_ids': part_lines,
                'part_summary': '\n'.join(summary),
                'urgency_level': dict(order._fields['urgency_level'].selection).get(
                    order.urgency_level, order.urgency_level),
                'repair_round': order.repair_round,
                'completion_date': fields.Datetime.now(),
            })
        return True

    # ---------------------------------------------------------------- 定时任务
    @api.model
    def _cron_check_deadlines(self):
        """每小时扫描：已过计划期限、仍在流转中的工单 -> 超时接力。"""
        now = fields.Datetime.now()
        orders = self.search([
            ('state', 'in', ('pending_claim', 'confirmed')),
            ('deadline_datetime', '!=', False),
            ('deadline_datetime', '<', now),
        ])
        for order in orders:
            technician = order.technician_id
            order.write({
                'state': 'timeout_alert',
                'technician_id': False,
                'claimed_at': False,
                'relay_count': order.relay_count + 1,
            })
            order.message_post(body='超过计划完成期限（%s），已自动转入超时接力。'
                               % order.deadline_datetime)
            if technician:
                order._notify_users(
                    technician,
                    '你负责的工单 %s 已超过计划期限（%s），已自动转为「超时接力」。'
                    % (order.name, order.deadline_datetime),
                )
        return len(orders)
