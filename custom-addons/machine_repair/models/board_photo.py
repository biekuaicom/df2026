from odoo import api, fields, models


class MachineBoardPhoto(models.Model):
    """电路板照片：跟随二维码保存，用于身份确认与维修追踪。

    规则（业务要求）：
      * 每块板至少要有 **正面 + 反面** 两张（`kind` = front / back），
        没有这两张不允许把工单发布到大厅；
      * 有了正反面之后，可以继续拍任意多张**局部**特写（detail）。
    """

    _name = 'machine.board.photo'
    _description = '电路板照片'
    _order = 'qr_id, kind, sequence, id'

    qr_id = fields.Many2one(
        'machine.board.qr', '电路板二维码', required=True, ondelete='cascade', index=True)
    order_id = fields.Many2one(
        'repair.order', '拍摄工单', ondelete='set null', index=True)
    kind = fields.Selection(
        [('front', '正面'), ('back', '反面'), ('detail', '局部特写')],
        string='类型', required=True, default='front', index=True)
    image = fields.Image('照片', max_width=1600, max_height=1600, required=True)
    sequence = fields.Integer('排序', default=10)
    note = fields.Char('说明')
    taken_by = fields.Many2one('res.users', '拍摄人', default=lambda self: self.env.user, readonly=True)
    taken_at = fields.Datetime('拍摄时间', default=fields.Datetime.now, readonly=True)
    board_label = fields.Char(related='qr_id.name', string='二维码', readonly=True)

    def name_get(self):
        labels = dict(self._fields['kind'].selection)
        result = []
        for photo in self:
            result.append((photo.id, '%s · %s' % (photo.qr_id.name or '',
                                                  labels.get(photo.kind, photo.kind))))
        return result
