"""手机端（PWA）接口：摄像头扫码、完工、拍照、开票、离线补传。

角色约定：
  * 师傅：扫码 → 开单 → 抢单 → **完工** → 拍照留证
  * 会计/管理员：**开票**（师傅无权开票，服务端强制校验）

摄像头只有 HTTPS（或 localhost）才能用：
    https://df2026.213579.com/machine_repair/mobile
"""
import base64
import logging
import math
import time
from datetime import timezone
from urllib.parse import quote
import re

from odoo import fields, http
from odoo.exceptions import UserError
from odoo.http import request
from odoo.tools import BinaryBytes

_logger = logging.getLogger(__name__)

URGENCY_LABELS = {
    'normal': '普通（7 天）',
    'urgent': '紧急（3 天）',
    'emergency': '加急（1 天）',
}

INVOICE_GROUPS = ('account.group_account_invoice', 'account.group_account_manager',
                  'base.group_system', 'base.group_erp_manager')

# 能看位置数据的人：位置监控管理员 + 系统管理员。
# 师傅组没有任何位置模型 ACL，控制器里的写库一律用 sudo，因此读不到别人的位置。
LOCATION_MANAGER_GROUPS = ('machine_repair.group_machine_repair_location_manager',
                           'base.group_system')
LOCATION_SOURCES = ('pwa', 'android')
LOCATION_STATE_LABELS = {
    'inside': '在工作位置',
    'outside': '离开工作位置',
    'unassigned': '未分配工作位置',
    'no_report': '未上报',
}

SERVICE_WORKER_JS = """
const CACHE = 'mr-shell-v2';
const SHELL = ['/machine_repair/mobile',
               '/machine_repair/static/src/js/jsQR.js',
               '/machine_repair/static/src/img/app-192.png'];
self.addEventListener('install', (e) => {
    e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
    e.waitUntil(caches.keys().then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
    )).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
    const req = e.request;
    if (req.method !== 'GET') { return; }              // POST 由页面的离线队列处理
    const url = new URL(req.url);
    if (url.pathname.indexOf('/machine_repair/') !== 0) { return; }
    // 管理员看板/数据每次都要实时，不能走缓存
    if (url.pathname.indexOf('/machine_repair/locations') === 0) { return; }
    // 页面本身用 network-first：改了手机端界面，下次打开必须拿到新页面
    if (req.mode === 'navigate' || url.pathname === '/machine_repair/mobile') {
        e.respondWith(
            fetch(req).then((resp) => {
                const copy = resp.clone();
                caches.open(CACHE).then((c) => c.put(req, copy));
                return resp;
            }).catch(() => caches.match('/machine_repair/mobile'))
        );
        return;
    }
    e.respondWith(
        caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
            return resp;
        }).catch(() => caches.match('/machine_repair/mobile')))
    );
});
"""


class MachineRepairMobile(http.Controller):

    # ------------------------------------------------------------------ 页面
    @http.route('/machine_repair/mobile', type='http', auth='user', sitemap=False)
    def mobile_page(self, **kwargs):
        response = request.render('machine_repair.mobile_app', {
            'user_name': request.env.user.name,
            'csrf_token': request.csrf_token(),
        })
        # 手机上必须每次拿最新的页面（否则改了界面，App 里还是旧缓存）
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    @http.route('/machine_repair/mobile/sw.js', type='http', auth='public', sitemap=False)
    def service_worker(self, **kwargs):
        return request.make_response(SERVICE_WORKER_JS, headers=[
            ('Content-Type', 'text/javascript; charset=utf-8'),
            ('Service-Worker-Allowed', '/machine_repair/mobile/'),
            ('Cache-Control', 'no-cache'),
        ])

    @http.route('/machine_repair/mobile/manifest.json', type='http', auth='public', sitemap=False)
    def manifest(self, **kwargs):
        return request.make_json_response({
            'name': '别快纺织机维修系统',
            'short_name': '别快织机',
            'start_url': '/machine_repair/mobile',
            'display': 'standalone',
            'background_color': '#5B3A8F',
            'theme_color': '#5B3A8F',
            'icons': [
                {'src': '/machine_repair/static/src/img/app-192.png?v=4', 'sizes': '192x192', 'type': 'image/png'},
                {'src': '/machine_repair/static/src/img/app-512.png?v=4', 'sizes': '512x512', 'type': 'image/png'},
            ],
        })

    # ------------------------------------------------------------------ 管理员：位置看板
    @http.route('/machine_repair/locations', type='http', auth='user', sitemap=False)
    def location_dashboard(self, **kwargs):
        """管理员位置看板（独立轻量页，手机也能开）。非管理员直接 403。"""
        if not self._is_location_manager():
            return request.make_response(
                '<!doctype html><html><head><meta charset="utf-8"/>'
                '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
                '<title>无权限</title></head><body style="font-family:sans-serif;'
                'padding:40px;color:#5d5669;line-height:1.8">'
                '<h2 style="color:#b03030">没有查看位置信息的权限</h2>'
                '<p>位置数据只对「机台维修 / 位置监控管理员」和系统管理员开放。</p>'
                '<p><a href="/machine_repair/mobile">返回手机端</a></p>'
                '</body></html>',
                status=403,
                headers=[('Content-Type', 'text/html; charset=utf-8')],
            )
        action = request.env.ref(
            'machine_repair.action_machine_repair_work_location',
            raise_if_not_found=False)
        response = request.render('machine_repair.location_dashboard', {
            'user_name': request.env.user.name,
            'work_location_action_id': action.id if action else False,
        })
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        return response

    @http.route('/machine_repair/locations/data', type='http', auth='user',
                methods=['GET'], sitemap=False)
    def location_dashboard_data(self, **kwargs):
        if not self._is_location_manager():
            return self._json({'ok': False, 'error': 'forbidden'}, status=403)
        response = self._json(self._location_dashboard_payload())
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response

    # ------------------------------------------------------------------ 定位：上报 / 状态
    @http.route('/machine_repair/mobile/location/state', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def location_state(self, **kwargs):
        """手机端启动时问一次：我要不要上报？多久一次？工作位置是什么？

        注意：**不要求分配工作位置**。所有手机端用户（含管理员）登录后都自动上报；
        没有工作位置时只是判定为「未分配」，位置本身照常记录。
        """
        config = self._location_config()
        user = request.env.user.sudo()
        places = self._location_places(user)
        snapshot = request.env['machine_repair.user_location'].sudo().search(
            [('user_id', '=', user.id)], limit=1)
        tracking = bool(config['enabled'] and user.machine_repair_location_enabled)
        reason, message = '', ''
        if not config['enabled']:
            reason, message = 'disabled', '管理员已关闭位置上报'
        elif not user.machine_repair_location_enabled:
            reason, message = 'disabled', '当前账号未启用位置上报，请联系管理员'
        elif not places:
            message = '未分配工作位置，位置仍会记录'
        return self._json({
            'ok': True,
            'tracking': tracking,
            'reason': reason,
            'message': message,
            'interval': config['interval'],
            'min_distance': config['min_distance'],
            'stale_minutes': config['stale_minutes'],
            'server_ts': int(time.time()),
            'user': {'id': user.id, 'name': user.name or ''},
            # 只给名称/半径，不下发坐标 —— 避免师傅之间互相看到精确点位
            'places': [{'id': place.id, 'name': place.name, 'radius': place.radius}
                       for place in places],
            'last': self._snapshot_payload(snapshot),
            'is_location_manager': self._is_location_manager(),
        })

    @http.route('/machine_repair/mobile/location', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def location_report(self, **kwargs):
        return self._location_report_impl(kwargs)

    @http.route('/machine_repair/mobile/location/native', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def location_report_native(self, **kwargs):
        """Android 前台服务用的上报入口（不受 CSRF token 限制）。

        会话 cookie 仍然必需：服务把 WebView 的 session_id 带在 Cookie 头里。
        额外要求自定义头 X-MR-Native，跨站表单无法设置自定义头，
        跨站 fetch 会触发预检（Odoo 不返回 CORS 允许），以此代替 CSRF token。
        """
        headers = request.httprequest.headers
        if headers.get('X-MR-Native') != '1' or headers.get('Origin'):
            return self._json({'ok': False, 'error': 'forbidden'}, status=403)
        user = request.env.user
        if not user or user._is_public():
            return self._json({'ok': False, 'error': 'auth'}, status=401)
        payload = dict(kwargs)
        payload['src'] = 'android'
        return self._location_report_impl(payload, source_default='android')

    def _location_report_impl(self, kwargs, source_default='pwa'):
        """手机端上报一次位置。

        安全要点：
          * 只认登录会话里的用户，**不接受**客户端传 user_id；
          * 是否在范围内完全由服务端按工作位置计算，客户端无法伪造；
          * 写库用 sudo（普通账号没有位置模型 ACL），读的人也仅限管理员。
        """
        try:
            latitude = float(kwargs.get('lat'))
            longitude = float(kwargs.get('lng'))
        except (TypeError, ValueError):
            return self._json({'ok': False, 'error': '缺少有效的经纬度'})
        if (not math.isfinite(latitude) or not math.isfinite(longitude)
                or not -90.0 <= latitude <= 90.0
                or not -180.0 <= longitude <= 180.0):
            return self._json({'ok': False, 'error': '经纬度超出有效范围'})
        try:
            accuracy = float(kwargs.get('accuracy') or 0)
        except (TypeError, ValueError):
            accuracy = 0.0
        if not math.isfinite(accuracy) or accuracy < 0:
            accuracy = 0.0
        accuracy = min(accuracy, 5000.0)
        source = (kwargs.get('src') or source_default).strip().lower()
        if source not in LOCATION_SOURCES:
            source = 'pwa'

        config = self._location_config()
        user = request.env.user.sudo()
        if not config['enabled'] or not user.machine_repair_location_enabled:
            return self._json({
                'ok': True, 'tracking': False, 'reason': 'disabled',
                'message': '位置上报已关闭',
                'interval': config['interval'], 'min_distance': config['min_distance'],
                'server_ts': int(time.time()),
            })
        places = self._location_places(user)
        inside, place, distance, radius = places._evaluate_position(latitude, longitude)
        # 没分配工作位置也照样记录，只是判定为「未分配」
        state = 'inside' if inside else ('outside' if places else 'unassigned')
        now = fields.Datetime.now()
        headers = request.httprequest.headers
        ip = (headers.get('X-Forwarded-For') or '').split(',')[0].strip() \
            or request.httprequest.remote_addr or ''
        ping_vals = {
            'user_id': user.id,
            'latitude': latitude,
            'longitude': longitude,
            'accuracy': accuracy,
            'distance': round(distance, 1) if distance is not None else 0.0,
            'place_id': place.id if place else False,
            'state': state,
            'source': source,
            'ip': ip[:64],
            'recorded_at': now,
        }

        Ping = request.env['machine_repair.location_ping'].sudo()
        previous = Ping.search([('user_id', '=', user.id)],
                               order='recorded_at desc, id desc', limit=1)
        created = False
        if previous and (now - previous.recorded_at).total_seconds() < 8:
            moved = request.env['machine_repair.work_location']._distance_m(
                previous.latitude, previous.longitude, latitude, longitude)
            if moved < 5:
                ping = previous          # 8 秒内几乎没动：只更新快照，不刷流水
            else:
                ping = Ping.create(ping_vals)
                created = True
        else:
            ping = Ping.create(ping_vals)
            created = True

        Snapshot = request.env['machine_repair.user_location'].sudo()
        snapshot = Snapshot.search([('user_id', '=', user.id)], limit=1)
        snapshot_vals = {
            'user_id': user.id,
            'latitude': latitude,
            'longitude': longitude,
            'accuracy': accuracy,
            'distance': ping_vals['distance'],
            'place_id': ping_vals['place_id'],
            'state': state,
            'source': source,
            'recorded_at': now,
        }
        old_state = snapshot.state if snapshot else ''
        if not snapshot:
            Snapshot.create(snapshot_vals)
        elif snapshot.recorded_at <= now:
            snapshot.write(snapshot_vals)
        if old_state and old_state != state:
            _logger.info('位置状态变化：%s %s -> %s（距 %s %.0f 米）',
                         user.login, old_state, state, place.name or '-', distance or 0)

        return self._json({
            'ok': True,
            'tracking': True,
            'received': created,
            'state': state,
            'state_label': LOCATION_STATE_LABELS.get(state, state),
            'inside': inside,
            'distance': round(distance, 1) if distance is not None else None,
            'radius': radius,
            'place': place.name or '',
            'accuracy': accuracy,
            'interval': config['interval'],
            'min_distance': config['min_distance'],
            'stale_minutes': config['stale_minutes'],
            'server_ts': int(time.time()),
        })

    # ------------------------------------------------------------------ 工具
    def _json(self, data, **kwargs):
        return request.make_json_response(data, **kwargs)

    def _can_invoice(self):
        """谁能开票：按"是否真的有权新建发票"判定（会计组/管理员都满足）。

        比硬编码组名可靠 —— 组名在各版本里会变，ACL 才是真正的权限边界。
        """
        try:
            return bool(request.env['account.move'].has_access('create'))
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ 定位（公共工具）
    def _is_location_manager(self):
        """位置数据只给管理员看：所有位置接口/页面都先过这一关。"""
        user = request.env.user
        for xmlid in LOCATION_MANAGER_GROUPS:
            try:
                if user.has_group(xmlid):
                    return True
            except Exception:  # noqa: BLE001 —— 组缺失时不至于 500
                continue
        return False

    def _location_config(self):
        """定位设置（管理员在「机台维修 → 配置 → 定位设置」里改）。"""
        params = request.env['ir.config_parameter'].sudo()
        return {
            'enabled': params.get_bool('machine_repair.location_enabled', True),
            'interval': max(15, min(3600, params.get_int(
                'machine_repair.location_interval', 60))),
            'min_distance': max(5.0, min(5000.0, params.get_float(
                'machine_repair.location_min_distance', 30.0))),
            'stale_minutes': max(1, params.get_int(
                'machine_repair.location_stale_minutes', 15)),
            'retention_days': params.get_int(
                'machine_repair.location_retention_days', 90),
        }

    @staticmethod
    def _timestamp(value):
        """Datetime（Odoo 存的是 UTC naive）→ epoch 秒，前端直接 new Date(ts*1000)。"""
        if not value:
            return None
        value = fields.Datetime.to_datetime(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())

    def _location_places(self, user):
        """该人员被分配且启用中的工作位置（sudo 读：字段对普通账号不可见）。"""
        return user.sudo().machine_repair_location_ids.filtered(lambda place: place.active)

    def _snapshot_payload(self, snapshot):
        if not snapshot:
            return None
        return {
            'lat': snapshot.latitude,
            'lng': snapshot.longitude,
            'accuracy': snapshot.accuracy,
            'distance': snapshot.distance,
            'state': snapshot.state,
            'state_label': LOCATION_STATE_LABELS.get(snapshot.state, snapshot.state),
            'place': snapshot.place_id.name or '',
            'place_id': snapshot.place_id.id or False,
            'radius': snapshot.place_id.radius or None,
            'source': snapshot.source,
            'ts': self._timestamp(snapshot.recorded_at),
        }

    def _location_dashboard_payload(self):
        """管理端看板数据：只返回聚合后的位置，不返回任何密码/无关资料。"""
        env = request.env
        config = self._location_config()
        users = env['res.users'].sudo().search(
            [('share', '=', False), ('active', '=', True)])
        snapshots = env['machine_repair.user_location'].sudo().search(
            [('user_id', 'in', users.ids)])
        snapshot_by_user = {snap.user_id.id: snap for snap in snapshots}
        places = env['machine_repair.work_location'].sudo().search(
            [('active', '=', True)], order='sequence, name')
        now_ts = int(time.time())
        stale_after = max(1, config['stale_minutes']) * 60

        rows = []
        stats = {'total': 0, 'inside': 0, 'outside': 0, 'unassigned': 0,
                 'no_report': 0, 'stale': 0}
        for user in users:
            assigned = self._location_places(user)
            snapshot = snapshot_by_user.get(user.id)
            enabled = bool(user.machine_repair_location_enabled)
            if not assigned and not snapshot:
                continue
            if not enabled and not snapshot:
                continue
            row = {
                'id': user.id,
                'name': user.name or '',
                'login': user.login or '',
                'enabled': enabled,
                'state': 'no_report',
                'state_label': LOCATION_STATE_LABELS['no_report'],
                'place': '',
                'place_id': False,
                'distance': None,
                'accuracy': None,
                'lat': None,
                'lng': None,
                'last_ts': None,
                'age_sec': None,
                'stale': False,
                'source': '',
                'places': assigned.ids,
            }
            if snapshot:
                event_ts = self._timestamp(snapshot.recorded_at) or now_ts
                age = max(0, now_ts - event_ts)
                if assigned:
                    inside, place, distance, _radius = assigned._evaluate_position(
                        snapshot.latitude, snapshot.longitude)
                    state = 'inside' if inside else 'outside'
                else:
                    state, distance = 'unassigned', None
                    place = snapshot.place_id or env['machine_repair.work_location']
                row.update({
                    'lat': snapshot.latitude,
                    'lng': snapshot.longitude,
                    'accuracy': snapshot.accuracy,
                    'distance': None if distance is None else round(distance, 1),
                    'place': place.name or '',
                    'place_id': place.id or False,
                    'state': state,
                    'state_label': LOCATION_STATE_LABELS.get(state, state),
                    'last_ts': event_ts,
                    'age_sec': age,
                    'stale': age > stale_after,
                    'source': snapshot.source or '',
                })
            stats['total'] += 1
            if row['state'] == 'no_report':
                stats['no_report'] += 1
            elif row['stale']:
                stats['stale'] += 1
            elif row['state'] == 'inside':
                stats['inside'] += 1
            elif row['state'] == 'outside':
                stats['outside'] += 1
            else:
                stats['unassigned'] += 1
            rows.append(row)

        rows.sort(key=lambda item: (item['state'] == 'no_report', item['stale'],
                                    item['name']))
        place_rows = [{
            'id': place.id,
            'name': place.name,
            'partner': place.partner_id.name or '',
            'lat': place.latitude,
            'lng': place.longitude,
            'coord_system': place.coord_system,
            'radius': place.radius,
            'user_count': len(place.user_ids),
        } for place in places]

        pings = env['machine_repair.location_ping'].sudo().search(
            [], order='recorded_at desc, id desc', limit=30)
        recent = [{
            'user': ping.user_id.name or '',
            'state_label': LOCATION_STATE_LABELS.get(ping.state, ping.state),
            'distance': (None if ping.state == 'unassigned' or ping.distance is None
                         else round(ping.distance, 1)),
            'lat': ping.latitude,
            'lng': ping.longitude,
            'ts': self._timestamp(ping.recorded_at),
            'source': ping.source or '',
        } for ping in pings]

        return {
            'ok': True,
            'now': now_ts,
            'config': config,
            'stats': stats,
            'users': rows,
            'places': place_rows,
            'recent': recent,
        }

    def _order(self, raw_id):
        try:
            order_id = int(raw_id or 0)
        except (TypeError, ValueError):
            return request.env['repair.order'].browse()
        return request.env['repair.order'].browse(order_id).exists()

    def _qr_payload(self, board_qr):
        if not board_qr:
            return None
        history = board_qr.history_payload()
        try:
            qr_base64 = board_qr.qr_image.to_base64() if board_qr.qr_image else ''
        except Exception:  # noqa: BLE001
            qr_base64 = ''
        return {
            'id': board_qr.id,
            'name': board_qr.name,
            'source': dict(board_qr._fields['source'].selection).get(board_qr.source, board_qr.source),
            'customer': board_qr.partner_id.display_name or '',
            'customer_type': board_qr.partner_id.customer_type or 'monthly',
            'customer_type_label': dict(
                board_qr.partner_id._fields['customer_type'].selection).get(
                    board_qr.partner_id.customer_type, ''),
            'machine': board_qr.machine_no or '',
            'machine_id': board_qr.machine_id.id or False,
            'product': board_qr.product_id.display_name or '',
            'product_id': board_qr.product_id.id or False,
            'created_on': str(board_qr.created_on or ''),
            'monthly_start': str(board_qr.monthly_start_date or ''),
            'monthly_end': str(board_qr.monthly_end_date or ''),
            'warranty_end': str(board_qr.warranty_end_date or ''),
            'warranty_start': str(board_qr.warranty_start_date or ''),
            'warranty_months': board_qr.warranty_months or 0,
            'warranty_state': dict(board_qr._fields['warranty_state'].selection).get(
                board_qr.warranty_state, ''),
            'in_warranty': board_qr.warranty_state == 'active',
            'repair_count': board_qr.repair_count,
            'has_photos': board_qr.has_required_photos,
            'photos': history['photos'],
            'history': history['repairs'],
            'qr_image': qr_base64,
            'photo_list': [{
                'id': photo.id,
                'kind': photo.kind,
                'url': '/machine_repair/mobile/photo/%s' % photo.id,
            } for photo in board_qr.photo_ids],
            'orders': self._open_orders_payload(board_qr),
            'recent_done': self._recent_done_payload(board_qr),
        }

    @staticmethod
    def _open_orders_payload(board_qr):
        """这块板还没完成的工单（待抢单 / 超时接力 / 维修中），供"+"/抢单入口直接操作。"""
        Order = board_qr.env['repair.order']
        result = []
        for order in board_qr.repair_order_ids.filtered(
                lambda o: o.state in ('pending_claim', 'timeout_alert', 'confirmed')):
            result.append({
                'id': order.id,
                'name': order.name,
                'state': Order.state_label(order.state),
                'state_key': order.state,
                'urgency': dict(order._fields['urgency_level'].selection).get(
                    order.urgency_level, order.urgency_level or ''),
                'deadline': order.deadline_datetime.strftime('%m-%d %H:%M')
                            if order.deadline_datetime else '',
                'technician': order.technician_id.name or '',
                'claimable': order.state in ('pending_claim', 'timeout_alert'),
            })
        return result

    @staticmethod
    def _recent_done_payload(board_qr, days=7):
        """一周内刚完工过的提醒。

        业务背景：有些板实际已修好，只是还没交付/寄回，师傅又扫到它就重复开单。
        这里只做**提醒**（不拦截），超过一周就不提示。
        """
        from datetime import timedelta
        from odoo import fields as odoo_fields
        since = odoo_fields.Datetime.now() - timedelta(days=days)
        Case = board_qr.env['repair.case']
        case = Case.search([
            ('repair_order_id.board_qr_id', '=', board_qr.id),
            ('completion_date', '>=', since),
        ], order='completion_date desc', limit=1)
        if case:
            done_at = case.completion_date
            order_name = case.repair_order_id.name
            technician = case.technician_id.name or ''
        else:
            # 没有快照时退回看工单本身的完工状态
            done = board_qr.repair_order_ids.filtered(
                lambda o: o.state in ('done', 'secondary_completed'))[:1]
            if not done:
                return None
            done_at = done.write_date
            order_name = done.name
            technician = done.technician_id.name or ''
        if not done_at or done_at < since:
            return None
        delta = odoo_fields.Datetime.now() - done_at
        return {
            'order': order_name,
            'date': done_at.strftime('%Y-%m-%d %H:%M'),
            'days': max(0, delta.days),
            'technician': technician,
            'within_days': days,
        }

    def _order_payload(self, order):
        # 机台以工单为准；工单上没填时退回二维码上的机台，避免列表/详情显示成空的
        machine = order.machine_id or order.board_qr_id.machine_id
        return {
            'id': order.id,
            'name': order.name,
            'state': self.env['repair.order'].state_label(order.state),
            'state_key': order.state,
            'mine': order.technician_id.id == request.env.user.id,
            'claimable': order.claimable,
            'urgency': dict(order._fields['urgency_level'].selection).get(
                order.urgency_level, order.urgency_level),
            'machine': machine.machine_no or '',
            'partner': order.partner_id.display_name or '',
            'deadline': order.deadline_datetime.strftime('%Y-%m-%d %H:%M')
                        if order.deadline_datetime else '',
            'overdue': order.is_overdue,
            'can_complete': order.state in ('confirmed', 'timeout_alert'),
            'invoiced': request.env['account.move'].search_count([
                ('repair_order_id', '=', order.id), ('move_type', '=', 'out_invoice')]) > 0,
            'parts': ['%s ×%s' % (move.product_id.display_name, move.product_uom_qty)
                      for move in order.move_ids.filtered(lambda m: m.repair_line_type)],
            'services': ['%s ×%s @%s' % (line.product_id.display_name, line.quantity, line.price_unit)
                         for line in order.repair_service_line_ids],
            # 照片挂在二维码上（不是工单附件），必须用二维码的真实状态
            'photos': len(order.board_qr_id.photo_ids),
            'has_photos': order.board_qr_id.has_required_photos,
            'front_photos': order.board_qr_id.front_photo_count,
            'back_photos': order.board_qr_id.back_photo_count,
            'error_code': order.error_code or '',
            'fault': order.fault_description or '',
            # 发货：未发货 / 已发货 + 快递单号，列表里直接能看到
            'shipped': bool(order.shipment_id),
            'tracking_no': order.tracking_no or '',
            'ship_date': order.ship_date.strftime('%Y-%m-%d %H:%M') if order.ship_date else '',
            'can_ship': order.state in ('done', 'secondary_completed') and not order.shipment_id,
            'warranty_months': int(order.warranty_months or 0),
            'warranty_pending': order.warranty_start_pending,
            # 活期客户建单时可以改本次保修月数
            'customer_type': order.partner_id.customer_type or 'monthly',
        }

    # ------------------------------------------------------------------ 扫码 / 建单
    @http.route('/machine_repair/mobile/ping', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def ping(self, **kwargs):
        return self._json({'ok': True, 'user': request.env.user.name,
                           'can_invoice': self._can_invoice()})

    @http.route('/machine_repair/mobile/scan', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def scan(self, **kwargs):
        """扫码结果分三种：

        1. 是本系统的码且库里有  -> 直接带出客户/机台/履历，可立即建单
        2. 是本系统格式但库里没有 -> 提示重新生成（可能被删或跨环境）
        3. 不是本系统的码（快递单/商品条码/原厂码） -> 切到「新板生成二维码」
        """
        code = (kwargs.get('code') or '').strip()
        if not code:
            return self._json({'ok': False, 'error': '没有读到二维码内容'})
        is_ours_format = code.startswith('MR1;')
        try:
            board_qr = request.env['machine.board.qr'].find_by_code(code)
        except Exception as exc:  # noqa: BLE001
            return self._json({'ok': False, 'invalid': True, 'found': False,
                               'switch_to_new': True, 'code': code,
                               'hint': '本系统格式但签名校验失败：%s' % str(exc)[:60]})
        if not board_qr:
            return self._json({
                'ok': False, 'found': False, 'switch_to_new': True, 'code': code,
                'is_ours_format': is_ours_format,
                'hint': ('本系统格式，但库里没有这张码 —— 请重新生成二维码'
                         if is_ours_format else
                         '这不是本系统的二维码/条码，已切到「新板：生成二维码」'),
            })
        return self._json({'ok': True, 'found': True, 'code': code,
                           'qr': self._qr_payload(board_qr)})

    @http.route('/machine_repair/mobile/customers', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def customers(self, **kwargs):
        query = (kwargs.get('q') or '').strip()
        Partner = request.env['res.partner']
        if query:
            partners = Partner.search([('name', 'ilike', query)], limit=20, order='name')
        else:
            machine_partners = request.env['customer.machine'].search([]).mapped('partner_id')
            order_partners = request.env['repair.order'].search([], limit=200).mapped('partner_id')
            ids = (machine_partners | order_partners).ids
            partners = Partner.browse(ids) if ids else Partner.search(
                [('is_company', '=', True)], limit=20, order='name')
        return self._json({'ok': True, 'customers': [
            {'id': partner.id, 'name': partner.display_name,
             'type': partner.customer_type or 'monthly'} for partner in partners]})

    @http.route('/machine_repair/mobile/create_order', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def create_order(self, **kwargs):
        code = (kwargs.get('code') or '').strip()
        urgency = kwargs.get('urgency') or 'normal'
        fault = (kwargs.get('fault') or '').strip()
        try:
            partner_id = int(kwargs.get('partner_id') or 0)
        except (TypeError, ValueError):
            partner_id = 0
        if not code:
            return self._json({'ok': False, 'error': '缺少二维码内容'})
        if urgency not in URGENCY_LABELS:
            urgency = 'normal'
        try:
            BoardQr = request.env['machine.board.qr']
            picked_partner = request.env['res.partner'].browse(partner_id).exists() if partner_id else None
            # 优先用前端传来的 qr_id（生码建单后直接建单时，避免按码名再查一次造成错位）
            board_qr = None
            try:
                qr_id = int(kwargs.get('qr_id') or 0)
            except (TypeError, ValueError):
                qr_id = 0
            if qr_id:
                board_qr = BoardQr.browse(qr_id).exists()
            if not board_qr:
                board_qr = BoardQr.find_by_code(code)
            if not board_qr:
                if not picked_partner:
                    return self._json({'ok': False, 'need_customer': True,
                                       'error': '库里没有这张码，请先选择客户'})
                board_qr = BoardQr.find_or_create_by_code(code, partner=picked_partner)

            # 业务要求：发布到大厅前必须有正反面照片
            if not board_qr.has_required_photos:
                return self._json({
                    'ok': False, 'need_photos': True, 'qr_id': board_qr.id,
                    'qr_name': board_qr.name,
                    'front': board_qr.front_photo_count, 'back': board_qr.back_photo_count,
                    'error': '请先拍电路板正面和反面照片（当前 正 %s / 反 %s 张）'
                             % (board_qr.front_photo_count, board_qr.back_photo_count),
                })

            partner = board_qr.partner_id or picked_partner or None
            machine = board_qr.machine_id or None
            product = board_qr.product_id or None
            def _as_int(value):
                try:
                    return int(value or 0)
                except (TypeError, ValueError):
                    return 0
            # 建单时**直接**用前端选的值（不能只依赖二维码生成时写入的）
            machine = request.env['customer.machine'].browse(_as_int(kwargs.get('machine_id'))).exists() or machine
            product = request.env['product.product'].browse(_as_int(kwargs.get('product_id'))).exists() or product
            vals = {
                'partner_id': partner.id if partner else False,
                'machine_id': machine.id if machine else False,
                'product_id': product.id if product else False,
                'urgency_level': urgency,
                'fault_description': fault or False,
                'error_code': (kwargs.get('error_code') or '')[:120] or False,
                'board_qr_id': board_qr.id,
            }
            # 活期用户建单时可以改「本次保修月数」（首次维修的从发货快递时间起算）
            months = _as_int(kwargs.get('warranty_months'))
            if partner and partner.customer_type == 'adhoc' and 1 <= months <= 12:
                vals['warranty_months'] = str(months)
            order = request.env['repair.order'].create(vals)
            # 二维码上缺的机台/板件顺手补上，保证扫码查得到
            fix = {}
            if machine and not board_qr.machine_id: fix['machine_id'] = machine.id
            if product and not board_qr.product_id: fix['product_id'] = product.id
            if fix: board_qr.write(fix)
            order.action_publish()
            return self._json({'ok': True, 'order_id': order.id, 'order_name': order.name,
                               'urgency': URGENCY_LABELS.get(urgency, urgency),
                               'partner': order.partner_id.display_name or '',
                               'machine': order.machine_id.machine_no or '',
                               'qr_name': board_qr.name})
        except Exception as exc:  # noqa: BLE001
            return self._json({'ok': False, 'error': str(exc)[:160]})

    # ------------------------------------------------------------------ 工单列表 / 抢单
    @http.route('/machine_repair/mobile/my_orders', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def my_orders(self, **kwargs):
        """工单分组（手机端分栏菜单用）。

        「任务工单」分栏：待抢单（大厅）/ 维修中 / 超时接力 / 已完工 —— 看整个车间；
        「我的工单」分栏：维修中 / 超时接力 / 已完工 —— 只看自己抢到的。
        每组都带真实总数（counts），列表本身按组限量，避免一次拉太多。
        """
        order_model = request.env['repair.order']
        user = request.env.user
        DONE = ('done', 'secondary_completed')

        def fetch(domain, order, limit):
            return [self._order_payload(rec)
                    for rec in order_model.search(domain, order=order, limit=limit)]

        def count(domain):
            return order_model.search_count(domain)

        hall_pending = [('state', '=', 'pending_claim'), ('technician_id', '=', False)]
        hall_repairing = [('state', '=', 'confirmed')]
        hall_timeout = [('state', '=', 'timeout_alert')]
        hall_done = [('state', 'in', DONE)]
        mine_repairing = [('technician_id', '=', user.id), ('state', '=', 'confirmed')]
        mine_timeout = [('technician_id', '=', user.id), ('state', '=', 'timeout_alert')]
        mine_done = [('technician_id', '=', user.id), ('state', 'in', DONE)]
        # 消息 → 开票 / 发货 两个分栏的待办清单（所有人都看全厂，不只自己抢的）
        invoice_open = [('state', 'in', DONE), ('invoice_ids', '=', False)]
        ship_open = [('state', 'in', DONE), ('shipment_id', '=', False)]

        groups = {
            'pending': fetch(hall_pending, 'urgency_sequence, deadline_datetime', 50),
            'repairing': fetch(hall_repairing, 'deadline_datetime', 50),
            'timeout': fetch(hall_timeout, 'deadline_datetime', 50),
            'done': fetch(hall_done, 'write_date desc', 30),
            'mine_repairing': fetch(mine_repairing, 'deadline_datetime', 30),
            'mine_timeout': fetch(mine_timeout, 'deadline_datetime', 30),
            'mine_done': fetch(mine_done, 'write_date desc', 20),
            'invoice': fetch(invoice_open, 'write_date desc', 100),
            'ship': fetch(ship_open, 'write_date asc', 100),
        }
        counts = {
            'pending': count(hall_pending),
            'repairing': count(hall_repairing),
            'timeout': count(hall_timeout),
            'done': count(hall_done),
            'mine_repairing': count(mine_repairing),
            'mine_timeout': count(mine_timeout),
            'mine_done': count(mine_done),
            'invoice': count(invoice_open),
            'ship': count(ship_open),
        }
        return self._json({
            'ok': True,
            'can_invoice': self._can_invoice(),
            'groups': groups,
            'counts': counts,
            # 兼容旧版页面（新版按 groups 渲染）
            'mine': groups['mine_repairing'] + groups['mine_timeout'] + groups['mine_done'],
            'pending': groups['pending'],
        })

    @http.route('/machine_repair/mobile/claim', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def claim(self, **kwargs):
        order = self._order(kwargs.get('order_id'))
        if not order:
            return self._json({'ok': False, 'error': '工单不存在'})
        try:
            order.action_claim()
            return self._json({'ok': True, 'order_name': order.name,
                               'technician': request.env.user.name})
        except UserError as exc:
            return self._json({'ok': False, 'error': str(exc)[:120]})

    # ------------------------------------------------------------------ 完工（师傅）
    @http.route('/machine_repair/mobile/complete', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def complete(self, **kwargs):
        order = self._order(kwargs.get('order_id'))
        if not order:
            return self._json({'ok': False, 'error': '工单不存在'})
        if order.state not in ('confirmed', 'timeout_alert'):
            return self._json({'ok': False,
                               'error': '只有已接单/维修中的工单可以完工（当前：%s）'
                                        % dict(order._fields['state'].selection).get(order.state)})
        try:
            # 走原生完工：配件出库、SN 强校验、快照、保修重新计时都在里面
            order.action_repair_done()
            return self._json({'ok': True, 'order_name': order.name, 'state': order.state,
                               'need_invoice': not order.invoice_ids,
                               'can_invoice': self._can_invoice()})
        except UserError as exc:
            return self._json({'ok': False, 'error': str(exc)[:200]})
        except Exception as exc:  # noqa: BLE001
            return self._json({'ok': False, 'error': str(exc)[:200]})

    # ------------------------------------------------------------------ 开票（会计 / 管理员）
    @http.route('/machine_repair/mobile/invoice', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def invoice(self, **kwargs):
        if not self._can_invoice():
            return self._json({'ok': False,
                               'forbidden': True,
                               'error': '开票由会计/管理员操作，师傅账号无权开票'})
        order = self._order(kwargs.get('order_id'))
        if not order:
            return self._json({'ok': False, 'error': '工单不存在'})
        if order.state not in ('done', 'secondary_completed'):
            return self._json({'ok': False, 'error': '只有已完工的工单才能开票'})
        try:
            action = order.action_create_invoice()
            invoice = order.invoice_ids[:1]
            return self._json({'ok': True, 'order_name': order.name,
                               'invoice': invoice.name or '(draft)',
                               'amount': invoice.amount_total})
        except UserError as exc:
            return self._json({'ok': False, 'error': str(exc)[:200]})
        except Exception as exc:  # noqa: BLE001
            return self._json({'ok': False, 'error': str(exc)[:200]})

    # ------------------------------------------------------------------ 发货（快递单号 + 包装照片）
    @http.route('/machine_repair/mobile/ship_confirm', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def ship_confirm(self, **kwargs):
        """多张工单合并成一个快递单号发货（手机端「消息 → 发货」）。

        一次性提交：勾选的工单 + 快递单号 + 多张包装照片（multipart）。
        要求：工单都已完工且没发过货、有单号、至少 1 张照片。
        """
        raw_ids = (kwargs.get('order_ids') or '').strip()
        order_ids = []
        for chunk in re.split(r'[,\s]+', raw_ids):
            if not chunk:
                continue
            try:
                order_ids.append(int(chunk))
            except (TypeError, ValueError):
                continue
        if not order_ids:
            return self._json({'ok': False, 'error': '请先勾选要发货的工单'})
        tracking_no = (kwargs.get('tracking_no') or '').strip()
        if not tracking_no:
            return self._json({'ok': False, 'error': '请先扫描或填写快递单号'})
        carrier = (kwargs.get('carrier') or '').strip()
        note = (kwargs.get('note') or '').strip()

        uploads = request.httprequest.files.getlist('photo')
        if not uploads:
            return self._json({'ok': False, 'error': '请先拍照上传包装照片（至少 1 张）'})
        photos = []
        for upload in uploads:
            data = upload.read()
            if not data:
                continue
            if len(data) > 12 * 1024 * 1024:
                return self._json({'ok': False, 'error': '单张照片不能超过 12MB，请重拍'})
            photos.append({
                'image': BinaryBytes(data),
                'name': upload.filename or '包装照片',
            })
        if not photos:
            return self._json({'ok': False, 'error': '照片是空的，请重新拍照上传'})

        orders = request.env['repair.order'].browse(order_ids).exists()
        if len(orders) != len(set(order_ids)):
            return self._json({'ok': False, 'error': '有工单不存在，请刷新后重试'})
        try:
            shipment = request.env['machine.repair.shipment']._ship_orders(
                orders, tracking_no, carrier=carrier or None, note=note or None,
                photos=photos)
        except UserError as exc:
            return self._json({'ok': False, 'error': str(exc)[:200]})
        except Exception as exc:  # noqa: BLE001
            return self._json({'ok': False, 'error': str(exc)[:200]})
        return self._json({
            'ok': True,
            'shipment': shipment.name,
            'tracking_no': shipment.tracking_no,
            'count': len(orders),
            'photos': len(photos),
            'ship_date': shipment.ship_date.strftime('%Y-%m-%d %H:%M'),
            'orders': orders.mapped('name'),
        })

    @http.route('/machine_repair/mobile/ship_photo/<int:photo_id>', type='http',
                auth='user', sitemap=False)
    def ship_photo(self, photo_id, **kwargs):
        """包装照片（手机端缩略图/大图都走这里，避免 /web/image 在 WebView 里的问题）。"""
        photo = request.env['machine.shipment.photo'].browse(photo_id).exists()
        if not photo or not photo.image:
            return request.not_found()
        data = bytes(photo.image)
        if kwargs.get('size') != 'full' and 'image_256' in photo._fields and photo.image_256:
            data = bytes(photo.image_256)
        return request.make_response(data, [
            ('Content-Type', 'image/jpeg'),
            ('Content-Length', str(len(data))),
            ('Cache-Control', 'private, max-age=86400'),
        ])

    # ------------------------------------------------------------------ 现场拍照
    @http.route('/machine_repair/mobile/new_qr', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def new_qr(self, **kwargs):
        """给从来没录过的板子生成二维码（包月/外来维修都一样）。"""
        def as_int(value):
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0
        partner_id = as_int(kwargs.get('partner_id'))
        if not partner_id:
            return self._json({'ok': False, 'error': '请先选择客户'})
        oem_sn = (kwargs.get('sn') or '').strip()
        board_qr = request.env['machine.board.qr'].create({
            'partner_id': partner_id,
            'machine_id': as_int(kwargs.get('machine_id')) or False,
            'product_id': as_int(kwargs.get('product_id')) or False,
            'sn': oem_sn or False,
            'source': 'oem' if oem_sn else 'generated',
        })
        return self._json({'ok': True, 'qr': self._qr_payload(board_qr)})

    @http.route('/machine_repair/mobile/discard_qr', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def discard_qr(self, **kwargs):
        """放弃一张刚生成、还没用过的二维码（新板取消）。

        只有既没有照片、也没有工单的码才允许删除，避免删掉已在用的数据。
        """
        try:
            qr_id = int(kwargs.get('qr_id') or 0)
        except (TypeError, ValueError):
            qr_id = 0
        board_qr = request.env['machine.board.qr'].browse(qr_id).exists()
        if not board_qr:
            return self._json({'ok': False, 'error': '二维码不存在'})
        if board_qr.repair_order_ids:
            return self._json({'ok': False,
                               'error': '这张码已经有工单，不能删除（共 %s 张）'
                                        % len(board_qr.repair_order_ids)})
        if board_qr.photo_ids:
            return self._json({'ok': False, 'error': '这张码已经拍过照片，不能删除'})
        name = board_qr.name
        board_qr.unlink()
        return self._json({'ok': True, 'name': name})
    @http.route('/machine_repair/mobile/video/<int:video_id>', type='http', auth='user',
                sitemap=False)
    def video_stream(self, video_id, **kwargs):
        """按正确的 Content-Type 输出视频（否则浏览器拿到 octet-stream 无法播放）。"""
        video = request.env['machine.repair.video'].browse(video_id).exists()
        if not video or not video.video:
            return request.not_found()
        data = bytes(video.video)
        mimetype = video.mimetype or 'video/mp4'
        headers = [
            ('Content-Type', mimetype),
            ('Content-Length', str(len(data))),
            ('Accept-Ranges', 'none'),
            ('Content-Disposition', 'inline; filename="%s"' % (video.name or 'video.mp4')),
            ('Cache-Control', 'private, max-age=600'),
        ]
        return request.make_response(data, headers)

    # ------------------------------------------------------------------ 工单详情
    @http.route('/machine_repair/mobile/order_detail', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def order_detail(self, **kwargs):
        order = self._order(kwargs.get('order_id'))
        if not order:
            return self._json({'ok': False, 'error': '工单不存在'})
        Order = request.env['repair.order']
        board_qr = order.board_qr_id
        # 工单上缺机台/板件时退回二维码上的档案（老单据常见），并告诉前端“是缺的”，
        # 让详情页可以直接补填，而不是只显示一个 '-'。
        machine = order.machine_id or board_qr.machine_id
        product = order.product_id or board_qr.product_id
        return self._json({'ok': True, 'order': {
            'id': order.id,
            'name': order.name,
            'state': Order.state_label(order.state),
            'urgency': dict(order._fields['urgency_level'].selection).get(
                order.urgency_level, order.urgency_level or ''),
            'partner': order.partner_id.display_name or '',
            'partner_id': order.partner_id.id or False,
            'machine': machine.machine_no or '',
            'machine_id': machine.id or False,
            'machine_from_qr': bool(machine) and not order.machine_id,
            'machine_missing': not machine,
            'product': product.display_name or '',
            'product_id': product.id or False,
            'product_from_qr': bool(product) and not order.product_id,
            'product_missing': not product,
            'board_product': (board_qr.product_id.display_name or '') if board_qr else '',
            'qr': order.board_qr_id.name or '',
            'technician': order.technician_id.name or '',
            'deadline': order.deadline_datetime.strftime('%Y-%m-%d %H:%M')
                        if order.deadline_datetime else '',
            'error_code': order.error_code or '',
            'fault': order.fault_description or '',
            'analysis': order.analysis_note or '',
            'process': order.repair_process or '',
            'measure': {
                'resistance': order.measure_resistance,
                'voltage': order.measure_voltage,
                'inductance': order.measure_inductance,
                'capacitance': order.measure_capacitance,
                'note': order.measure_note or '',
            },
            'photos': [{
                'id': photo.id,
                'kind': photo.kind,
                'label': dict(photo._fields['kind'].selection).get(photo.kind, photo.kind),
                'url': '/machine_repair/mobile/photo/%s' % photo.id,
            } for photo in order.board_qr_id.photo_ids],
            'videos': [{
                'id': video.id,
                'name': video.name,
                'duration': video.duration_sec or 0,
                'size': video.file_size or 0,
                'url': '/machine_repair/mobile/video/%s' % video.id,
            } for video in order.video_ids],
            'can_complete': order.state in ('confirmed', 'timeout_alert'),
            'done': order.state in ('done', 'secondary_completed'),
            'customer_type': order.partner_id.customer_type or 'monthly',
            'warranty_months': int(order.warranty_months or 0),
            'warranty_pending': order.warranty_start_pending,
            'shipped': bool(order.shipment_id),
            'can_ship': order.state in ('done', 'secondary_completed') and not order.shipment_id,
            'shipment': {
                'id': order.shipment_id.id,
                'name': order.shipment_id.name,
                'tracking_no': order.shipment_id.tracking_no or '',
                'carrier': order.shipment_id.carrier or '',
                'ship_date': order.shipment_id.ship_date.strftime('%Y-%m-%d %H:%M')
                             if order.shipment_id.ship_date else '',
                'photos': [{
                    'id': photo.id,
                    'url': '/machine_repair/mobile/ship_photo/%s' % photo.id,
                } for photo in order.shipment_id.photo_ids],
            } if order.shipment_id else None,
            'requirements': {
                'photos_ok': order.board_qr_id.has_required_photos,
                'front': order.board_qr_id.front_photo_count,
                'back': order.board_qr_id.back_photo_count,
                'videos': len(order.video_ids),
                'videos_ok': len(order.video_ids) > 0,
                'measures_done': sum([
                    1 for flag in (order.measure_resistance, order.measure_voltage,
                                   order.measure_inductance, order.measure_capacitance) if flag]),
                'measures_total': 4,
                'state_label': Order.state_label(order.state),
            },
        }})

    @http.route('/machine_repair/mobile/measure', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def measure(self, **kwargs):
        order = self._order(kwargs.get('order_id'))
        if not order:
            return self._json({'ok': False, 'error': '工单不存在'})
        kind = kwargs.get('kind') or ''
        field = {
            'resistance': 'measure_resistance',
            'voltage': 'measure_voltage',
            'inductance': 'measure_inductance',
            'capacitance': 'measure_capacitance',
        }.get(kind)
        if not field:
            return self._json({'ok': False, 'error': '未知的测量项'})
        value = kwargs.get('value')
        order.write({field: True})
        if value:
            prefix = (order.measure_note + '\n') if order.measure_note else ''
            order.write({'measure_note': (prefix + '%s：%s' % (kind, value))[:2000]})
        order.message_post(body='电气测量完成：%s' % kind)
        return self._json({'ok': True, 'kind': kind})

    @http.route('/machine_repair/mobile/save_work', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def save_work(self, **kwargs):
        """保存详情页内容。**只写前端真正传上来的字段**。

        以前这里无条件写 analysis_note / repair_process / error_code / fault_description，
        建单后那次“补写错误代码”的小请求就会把已经填好的故障分析、维修过程清成空字符串。
        """
        order = self._order(kwargs.get('order_id'))
        if not order:
            return self._json({'ok': False, 'error': '工单不存在'})
        vals = {}
        for key, field, limit in (
            ('error_code', 'error_code', 120),
            ('fault', 'fault_description', 4000),
            ('analysis', 'analysis_note', 4000),
            ('process', 'repair_process', 4000),
        ):
            if key in kwargs:
                vals[field] = (kwargs.get(key) or '')[:limit]

        # 机台 / 板件型号：扫码时没带出来的，允许在详情页补填
        def _as_int(value):
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        machine = product = None
        if kwargs.get('machine_id'):
            machine = request.env['customer.machine'].browse(
                _as_int(kwargs.get('machine_id'))).exists()
            if machine:
                vals['machine_id'] = machine.id
        if kwargs.get('product_id'):
            product = request.env['product.product'].browse(
                _as_int(kwargs.get('product_id'))).exists()
            if product:
                vals['product_id'] = product.id
        if vals:
            order.write(vals)
        # 二维码上缺的机台/板件顺手补上，保证下次扫码就能看到
        fix = {}
        if machine and not order.board_qr_id.machine_id:
            fix['machine_id'] = machine.id
        if product and not order.board_qr_id.product_id:
            fix['product_id'] = product.id
        if fix and order.board_qr_id:
            order.board_qr_id.write(fix)
        return self._json({'ok': True})

    @http.route('/machine_repair/mobile/upload_video', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def upload_video(self, **kwargs):
        order = self._order(kwargs.get('order_id'))
        if not order:
            return self._json({'ok': False, 'error': '工单不存在'})
        if len(order.video_ids) >= 3:
            return self._json({'ok': False, 'error': '最多只能上传 3 段视频，请先删除'})
        upload = request.httprequest.files.get('video')
        if not upload:
            return self._json({'ok': False, 'error': '没有收到视频'})
        try:
            duration = int(float(kwargs.get('duration') or 0))
        except (TypeError, ValueError):
            duration = 0
        if duration and duration > 300:
            return self._json({'ok': False, 'error': '每段视频不能超过 5 分钟（当前 %s 秒）' % duration})
        data = upload.read()
        # 手机相册里的视频通常比现场录的更大，放宽到 200MB（时长仍限 5 分钟）
        if len(data) > 200 * 1024 * 1024:
            return self._json({'ok': False, 'error': '视频过大（超过 200MB），请压缩后重传'})
        video = request.env['machine.repair.video'].create({
            'order_id': order.id,
            'name': upload.filename or 'repair.mp4',
            'video': BinaryBytes(data),
            'file_size': len(data),
            'duration_sec': duration,
            'mimetype': (getattr(upload, 'mimetype', None) or 'video/mp4'),
        })
        order.message_post(body='上传了维修视频（%s 秒）' % duration)
        return self._json({'ok': True, 'video_id': video.id,
                           'count': len(order.video_ids)})

    @http.route('/machine_repair/mobile/delete_video', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def delete_video(self, **kwargs):
        try:
            video_id = int(kwargs.get('video_id') or 0)
        except (TypeError, ValueError):
            video_id = 0
        video = request.env['machine.repair.video'].browse(video_id).exists()
        if not video:
            return self._json({'ok': False, 'error': '视频不存在'})
        order = video.order_id
        video.unlink()
        return self._json({'ok': True, 'count': len(order.video_ids)})

    # ------------------------------------------------------------------ 消息 / 经验库
    @http.route('/machine_repair/mobile/messages', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def messages(self, **kwargs):
        """消息（对应 Odoo 讨论）：只取与我相关工单上的沟通记录。"""
        user = request.env.user
        Order = request.env['repair.order']
        mine = Order.search(['|', ('technician_id', '=', user.id),
                             ('create_uid', '=', user.id)], limit=200)
        msgs = request.env['mail.message'].search([
            ('model', '=', 'repair.order'),
            ('res_id', 'in', mine.ids),
        ], order='date desc', limit=25) if mine else []
        payload = []
        for msg in msgs:
            body = re.sub(r'<[^>]+>', ' ', msg.body or '')
            body = re.sub(r'\s+', ' ', body).strip()
            payload.append({
                'order': msg.res_id and Order.browse(msg.res_id).name or '',
                'date': msg.date.strftime('%m-%d %H:%M') if msg.date else '',
                'author': msg.author_id.name or '',
                'body': body[:120],
                'unread': not msg.is_read if hasattr(msg, 'is_read') else False,
            })
        return self._json({'ok': True, 'messages': payload,
                           'discuss_url': '/odoo/discuss'})

    @http.route('/machine_repair/mobile/machines', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def machines(self, **kwargs):
        """某客户名下的机台（生码建单时选机台用）。"""
        try:
            partner_id = int(kwargs.get('partner_id') or 0)
        except (TypeError, ValueError):
            partner_id = 0
        if not partner_id:
            return self._json({'ok': True, 'machines': []})
        machines = request.env['customer.machine'].search(
            [('partner_id', '=', partner_id)], order='machine_no')
        return self._json({'ok': True, 'machines': [{
            'id': machine.id,
            'no': machine.machine_no or '',
            'model': machine.machine_model or '',
            'template_id': machine.template_id.id or False,
        } for machine in machines]})

    @http.route('/machine_repair/mobile/boards', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def boards(self, **kwargs):
        """板件型号下拉：该机型的专属电路板排在最前，其余电路板排后面。"""
        def as_int(value):
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0
        machine_id = as_int(kwargs.get('machine_id'))
        Product = request.env['product.product']
        machine = request.env['customer.machine'].browse(machine_id).exists()

        # 机型专属的电路板（来自机型标准模板的器件行 + 该机台的器件基线）
        model_products = Product
        if machine:
            model_products |= machine.template_id.line_ids.mapped('product_id')
            model_products |= machine.line_ids.mapped('product_id')

        # 所有"电路板"分类的产品
        board_categs = request.env['product.category'].search([('is_board', '=', True)])
        others = Product.search([('categ_id', 'in', board_categs.ids)], limit=300) \
            if board_categs else Product

        seen, payload = set(), []
        for product in model_products:
            if product.id in seen:
                continue
            seen.add(product.id)
            payload.append({'id': product.id, 'name': product.display_name,
                            'is_model': True})
        for product in others:
            if product.id in seen:
                continue
            seen.add(product.id)
            payload.append({'id': product.id, 'name': product.display_name,
                            'is_model': False})
        return self._json({'ok': True, 'boards': payload,
                           'machine_model': machine.machine_model or ''})

    @http.route('/machine_repair/mobile/timeline', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def timeline(self, **kwargs):
        """工单动态：把「创建/发布/抢单/超时/完工/开票」等系统事件
        与工单里的聊天内容合并成一条时间线（系统信息在前，正文在后）。
        """
        user = request.env.user
        Order = request.env['repair.order']
        orders = Order.search(['|', ('technician_id', '=', user.id),
                               ('create_uid', '=', user.id)],
                              order='write_date desc', limit=40)
        state_labels = dict(Order._fields['state'].selection)
        events = []

        def add(order, kind, title, when, author, body):
            if not when:
                return
            events.append({
                'kind': kind,
                'title': title,
                'order': order.name,
                'order_id': order.id,
                'state': Order.state_label(order.state),
                'partner': order.partner_id.display_name or '',
                'machine': order.machine_id.machine_no or '',
                'date': when,
                'author': author or '',
                'body': body or '',
            })

        for order in orders:
            add(order, 'system', '创建工单', order.create_date,
                order.create_uid.name or '', order.fault_description or '')
            if order.claimed_at:
                add(order, 'system', '抢单接单', order.claimed_at,
                    order.technician_id.name or '', '')
            if order.state == 'timeout_alert':
                add(order, 'system', '超时未完成，等待接力',
                    order.deadline_datetime or order.write_date, '系统', '')
            if order.state in ('done', 'secondary_completed'):
                add(order, 'system', '完工', order.write_date,
                    order.technician_id.name or '', order.solution or '')
            if order.invoice_ids:
                add(order, 'system', '已开票 %s' % order.invoice_ids[0].name,
                    order.invoice_ids[0].create_date, '', '')
            for message in order.message_ids:
                body = re.sub(r'<[^>]+>', ' ', message.body or '')
                body = re.sub(r'\s+', ' ', body).strip()
                if not body:
                    continue
                is_system = message.message_type in ('notification', 'auto_comment')
                add(order, 'system' if is_system else 'chat',
                    '系统通知' if is_system else '留言',
                    message.date, message.author_id.name or '', body[:180])

        events.sort(key=lambda e: e['date'], reverse=True)
        payload = []
        for event in events[:60]:
            payload.append({
                'kind': event['kind'],
                'title': event['title'],
                'order': event['order'],
                'order_id': event['order_id'],
                'state': event['state'],
                'partner': event['partner'],
                'machine': event['machine'],
                'date': event['date'].strftime('%m-%d %H:%M') if event['date'] else '',
                'author': event['author'],
                'body': event['body'],
            })
        return self._json({'ok': True, 'events': payload,
                           'discuss_url': '/odoo/discuss/mail.box_inbox'})

    @http.route('/machine_repair/mobile/cases', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def cases(self, **kwargs):
        """故障经验库：按故障现象 / 机台 / 处理方式检索历史快照。"""
        q = (kwargs.get('q') or '').strip()
        domain = []
        if q:
            domain = ['|', '|',
                      ('fault_description', 'ilike', q),
                      ('solution', 'ilike', q),
                      ('machine_no', 'ilike', q)]
        records = request.env['repair.case'].search(
            domain, order='completion_date desc', limit=40)
        payload = []
        for case in records:
            payload.append({
                'name': case.name,
                'machine_no': case.machine_no or '',
                'template': case.machine_template_id.name or '',
                'fault': (case.fault_description or '').strip()[:120],
                'solution': (case.solution or '').strip()[:160],
                'parts': (case.part_summary or '').strip()[:80],
                'technician': case.technician_id.name or '',
                'date': case.completion_date.strftime('%Y-%m-%d') if case.completion_date else '',
            })
        return self._json({'ok': True, 'cases': payload})

    @http.route('/machine_repair/mobile/photo/<int:photo_id>', type='http', auth='user',
                sitemap=False)
    def photo_image(self, photo_id, **kwargs):
        """缩略图/原图统一走这里，避免 /web/image 在 WebView 里的加载问题。"""
        photo = request.env['machine.board.photo'].browse(photo_id).exists()
        if not photo or not photo.image:
            return request.not_found()
        field = photo._fields['image']
        data = bytes(photo.image)
        if kwargs.get('size') != 'full' and field.max_width:
            # 直接用 Odoo 生成好的缩略图变体
            thumb = photo.image_256 if 'image_256' in photo._fields else None
            if thumb:
                data = bytes(thumb)
        response = request.make_response(data, [
            ('Content-Type', 'image/jpeg'),
            ('Content-Length', str(len(data))),
            ('Cache-Control', 'private, max-age=86400'),
        ])
        return response

    @http.route('/machine_repair/mobile/photo', type='http', auth='user',
                methods=['POST'], sitemap=False)
    def photo(self, **kwargs):
        """拍电路板照片：front（正面）/ back（反面）/ detail（局部），存到二维码上。"""
        kind = kwargs.get('kind') or 'front'
        if kind not in ('front', 'back', 'detail'):
            kind = 'detail'
        try:
            qr_id = int(kwargs.get('qr_id') or 0)
        except (TypeError, ValueError):
            qr_id = 0
        order = self._order(kwargs.get('order_id'))
        board_qr = request.env['machine.board.qr'].browse(qr_id).exists() if qr_id else None
        if not board_qr and order:
            board_qr = order.board_qr_id
        if not board_qr:
            return self._json({'ok': False, 'error': '请先扫码或生成二维码，再拍照'})
        upload = request.httprequest.files.get('photo')
        if not upload:
            return self._json({'ok': False, 'error': '没有收到照片'})
        try:
            data = upload.read()
            photo = request.env['machine.board.photo'].create({
                'qr_id': board_qr.id,
                'order_id': order.id if order else False,
                'kind': kind,
                # Odoo 20：Binary/Image 字段用 BinaryBytes 包装**原始字节**
                # （不要再手工 base64，它存库时会自己编码）
                'image': BinaryBytes(data),
                'note': (kwargs.get('note') or '')[:60],
            })
            labels = dict(request.env['machine.board.photo']._fields['kind'].selection)
            if order:
                order.message_post(body='上传了电路板照片（%s）' % labels.get(kind, kind))
            return self._json({
                'ok': True, 'photo_id': photo.id, 'kind': kind,
                'front': board_qr.front_photo_count, 'back': board_qr.back_photo_count,
                'has_photos': board_qr.has_required_photos, 'size': len(data),
                # 直接把最新照片列表给前端，避免再发一次 /scan 才能看到缩略图
                'photo_list': [{
                    'id': item.id, 'kind': item.kind,
                    'url': '/machine_repair/mobile/photo/%s' % item.id,
                } for item in board_qr.photo_ids],
            })
        except Exception as exc:  # noqa: BLE001
            return self._json({'ok': False, 'error': str(exc)[:160]})
