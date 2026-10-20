from odoo import models


class IrHttp(models.AbstractModel):
    """放宽 X-Frame-Options，让**同源**的手机端页面可以内嵌 Odoo 网页客户端（讨论模块）。

    Odoo 的 web 客户端（/odoo/... 与 /web）默认返回 `X-Frame-Options: DENY`，
    浏览器会直接拒绝把它放进 iframe（手机上表现为"网页无法打开"）。
    这里在响应发出前把它改成 `SAMEORIGIN`：只允许**同源**页面嵌套，
    其它站点依旧无法嵌入（安全性不变）。
    """

    _inherit = 'ir.http'

    @classmethod
    def _post_dispatch(cls, response):
        # 注意：基类 _post_dispatch 只就地处理 request.dispatcher，**不返回 response**。
        # 所以这里不能用返回值，必须用传进来的这个 response 对象。
        super()._post_dispatch(response)
        try:
            headers = getattr(response, 'headers', None)
            if headers is not None and str(headers.get('X-Frame-Options', '')).upper() == 'DENY':
                headers['X-Frame-Options'] = 'SAMEORIGIN'
        except Exception:  # noqa: BLE001  绝不因为改头导致请求失败
            pass
        return response
