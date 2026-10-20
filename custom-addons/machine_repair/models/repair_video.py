from odoo import api, fields, models


class MachineRepairVideo(models.Model):
    """维修视频：每张工单最多 3 段，完工前必须有至少 1 段。"""

    _name = 'machine.repair.video'
    _description = '维修视频'
    _order = 'id desc'

    order_id = fields.Many2one('repair.order', '工单', required=True,
                               ondelete='cascade', index=True)
    name = fields.Char('文件名', default='维修视频')
    video = fields.Binary('视频', required=True, attachment=True)
    file_size = fields.Integer('大小（字节）')
    mimetype = fields.Char('文件类型', default='video/mp4')
    duration_sec = fields.Integer('时长（秒）')
    uploaded_by = fields.Many2one('res.users', '上传人',
                                  default=lambda self: self.env.user, readonly=True)
    note = fields.Char('说明')

    def name_get(self):
        return [(video.id, '%s（%s 秒）' % (video.name, video.duration_sec or 0))
                for video in self]
