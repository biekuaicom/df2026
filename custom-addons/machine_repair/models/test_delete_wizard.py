import base64
import json
import logging
import os
from datetime import datetime

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import config

_logger = logging.getLogger(__name__)

# 备份时一并导出的子表：(表名, 关联字段)
CHILD_TABLES = [
    ('repair_service_line', 'repair_id'),
    ('repair_case', 'repair_order_id'),
    ('machine_repair_video', 'order_id'),
    ('stock_move', 'repair_id'),
    ('account_move', 'repair_order_id'),
]


class MachineRepairTestDelete(models.TransientModel):
    """管理员专用：把测试工单「作废 + 删除」，删除前自动导出 JSON 备份。

    为什么要单独做这个：Odoo 核心禁止删除已完工维修单
    （`repair.order._unlink_except_confirmed` 会拒绝），但测试期造出来的单子必须能清掉。
    这里把「备份 → 取消 → 删除」封成一个带二次确认的向导，避免管理员直接动 SQL。
    """

    _name = 'machine.repair.test.delete'
    _description = '作废并删除测试工单'

    order_ids = fields.Many2many('repair.order', string='要删除的工单', readonly=True)
    order_summary = fields.Char('工单', compute='_compute_order_summary')
    backup_dir = fields.Char('备份目录', compute='_compute_order_summary')
    confirmed = fields.Boolean('我已确认这些是测试数据，删除后不可恢复')

    @api.depends('order_ids')
    def _compute_order_summary(self):
        backup_dir = self._backup_dir()
        for wizard in self:
            wizard.order_summary = '、'.join(
                '%s（%s）' % (order.name, order.state_label(order.state))
                for order in wizard.order_ids) or '（没有选中工单）'
            wizard.backup_dir = backup_dir

    # ---------------------------------------------------------------- 工具
    @api.model
    def _backup_dir(self):
        """备份目录：默认 <data_dir>/test-order-backups，可用系统参数覆盖。"""
        configured = self.env['ir.config_parameter'].sudo().get_str(
            'machine_repair.test_order_backup_dir')
        path = configured or os.path.join(config['data_dir'], 'test-order-backups')
        return path

    @api.model
    def _json_rows(self, table, column, value):
        self.env.cr.execute(
            "select coalesce(json_agg(t), '[]'::json) from %s t where %s = %%s" % (table, column),
            (value,))
        return self.env.cr.fetchone()[0]

    def _backup(self, order):
        """导出一张工单的全部可读数据 + 视频文件，返回备份目录。"""
        folder = self._backup_dir()
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        base = '%s-%s-%s' % (order.name.replace('/', '-'), order.id, stamp)

        payload = {'backup_at': stamp, 'order': self._json_rows('repair_order', 'id', order.id)}
        for table, column in CHILD_TABLES:
            payload[table] = self._json_rows(table, column, order.id)
        cases = [row['id'] for row in payload.get('repair_case') or []]
        payload['repair_case_line'] = []
        if cases:
            self.env.cr.execute(
                'select coalesce(json_agg(t), \'[]\'::json) from repair_case_line t where case_id in %s',
                (tuple(cases),))
            payload['repair_case_line'] = self.env.cr.fetchone()[0]
        self.env.cr.execute(
            "select coalesce(json_agg(json_build_object('author', author_id, 'date', date, "
            "'body', body)), '[]'::json) from mail_message "
            "where model = 'repair.order' and res_id = %s", (order.id,))
        payload['messages'] = self.env.cr.fetchone()[0]

        with open(os.path.join(folder, base + '.json'), 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

        # 视频二进制单独存文件（数据库里是 filestore + base64，JSON 不方便还原）
        for video in order.video_ids:
            data = video.video or b''
            raw = base64.b64decode(data) if isinstance(data, str) else bytes(data or b'')
            name = (video.name or 'video-%s.mp4' % video.id).replace('/', '_').replace('\\', '_')
            with open(os.path.join(folder, '%s-video-%s-%s' % (base, video.id, name)), 'wb') as fh:
                fh.write(raw)
        return folder

    def _purge(self, order):
        """删除一张工单及其附属数据（已完工单据 Odoo 不让走 ORM，这里按管理员意图直删）。"""
        self.env['repair.case'].search([('repair_order_id', '=', order.id)]).unlink()
        self.env['machine.repair.video'].search([('order_id', '=', order.id)]).unlink()
        self.env['mail.message'].search(
            [('model', '=', 'repair.order'), ('res_id', '=', order.id)]).unlink()
        self.env['ir.attachment'].search(
            [('res_model', '=', 'repair.order'), ('res_id', '=', order.id)]).unlink()
        self.env.cr.execute(
            "delete from mail_followers where res_model = 'repair.order' and res_id = %s",
            (order.id,))
        self.env.cr.execute(
            "delete from mail_activity where res_model = 'repair.order' and res_id = %s",
            (order.id,))
        # 未完工的先正常作废（走原生逻辑，把相关的库存移动也取消掉）
        if order.state not in ('done', 'secondary_completed', 'cancel'):
            try:
                order.action_repair_cancel()
            except UserError:
                pass
        self.env.cr.execute('delete from repair_order where id = %s', (order.id,))

    # ---------------------------------------------------------------- 动作
    def action_confirm(self):
        if not self.env.user.has_group('base.group_system'):
            raise UserError('只有管理员可以删除工单。')
        if not self.confirmed:
            raise UserError('请先勾选「我已确认这些是测试数据」。')
        if not self.order_ids:
            raise UserError('没有选中要删除的工单。')

        kept_names, backup_dir = [], ''
        try:
            for order in self.order_ids:
                backup_dir = self._backup(order)
                kept_names.append('%s(#%s)' % (order.name, order.id))
                _logger.warning('管理员 %s 删除工单 %s（已备份到 %s）',
                                self.env.user.login, order.name, backup_dir)
                self._purge(order)
            self.env.invalidate_all()
        except Exception as exc:  # noqa: BLE001
            raise UserError('删除失败，已回滚：%s' % str(exc)[:300]) from exc

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '已作废并删除',
                'message': '%s 张工单已删除（%s）。备份在：%s'
                           % (len(kept_names), '、'.join(kept_names), backup_dir),
                'type': 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
