# machine_repair — 纺织机台维修管理

自定义 Odoo 20 模块，实现《机台维修业务管理方案》的 6 个阶段，并扩展手机端定位。
放在 `D:\Odoo\custom-addons\machine_repair`，通过 `make-conf.ps1` 自动纳入 `addons_path`。

## 阶段进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 机型标准模板 `machine.base.template` + 专属电路板/通用元件分类 | ✅ 已实现并验证 |
| 2 | 客户机台台账 `customer.machine`、模板复制、批量复制 | ✅ 已实现并验证 |
| 3 | 工单大厅：三级紧急、抢单、退单、超时接力、看板高亮 | ✅ 已实现并验证（扩展原生 `repair.order`） |
| 4 | 完工库存联动、SN 校验、返修追踪、修改次数限制 | ✅ 已实现并验证 |
| 5 | 维修快照 `repair.case` + 经验库 `repair.experience` + 定时聚合 | ✅ 已实现并验证 |
| 6 | 包月账单（含放假天数折算）+ 6 大报表 | ✅ 已实现并验证 |
| 7 | 手机定位：工作位置围栏、在岗判定、管理员位置看板（仅管理员可见） | ✅ 已实现并验证 |
| 8 | 包月/活期客户类型、器件基线整列设置、发货提醒（快递单号 + 包装照片）、手机端开票/发货分栏 | ✅ 已实现并验证 |

## 阶段 1 已实现内容

- `machine.base.template`（机型标准模板）：`name` / `code` / `active` / `note` / `line_ids` / `line_count`
- `machine.base.template.line`（模板器件行）：`product_id`（**只能选「专属电路板类目」下的产品**）、`is_sn_required`、`qty`、`note`、`sequence`，并对 `(template_id, product_id)` 加唯一约束
- `product.category.is_board`：新增标志位，用于圈定"专属电路板类目"
- 数据：`专属电路板`（is_board=True）、`通用电子元件`（is_board=False）两个类别
- 菜单：机台维修 → 基础档案 → 机型标准模板
- 权限：`ir.access.csv`，`crud` 授予 `base.group_user`

## 阶段 2 已实现内容

- `customer.machine`（客户机台档案）：`partner_id` / `machine_no` / `template_id` /
  `installation_address` / `monthly_fee` / `monthly_start_date` / `monthly_active` /
  `monthly_end_date` / `line_ids` / `note`；继承 `mail.thread`（有 chatter 与字段追踪）；
  约束 `unique(partner_id, machine_no)`（同一客户下编号不重复）
- `customer.machine.line`（机台器件基线）：`product_id`（限专属电路板类目）、
  **`sn`（SN / 二维码）**、`is_sn_required`、`qty`、`template_line_id`、`note`
- **动作 A（等价于"从模板生成样板机"）**：机台表单按钮「从模板载入器件基线」，
  按模板 `line_ids` 批量生成器件行（仅当器件行为空时可执行）
- **动作 B（批量复制）**：机台表单按钮「批量复制机台」→ 向导 `machine.copy.wizard`
  - 编号自动递增且保留位数：`M-009 → M-010`、`LINE-A12 → LINE-A15`
  - 客户 / 机型 / 包月单价 / 器件基线全部沿用样板机
  - **SN 默认清空**（每台实物机台的板件序列号不同）；仅补录误删数据时才勾选「同时复制 SN」
- 菜单：机台维修 → 基础档案 → 客户机台台账

实测（ORM，末尾回滚，未落库）：

```
machine  M-001 lines=1 sn=SN-AAAA   fee=1200.0
  M-002  lines=1 sn=(cleared) fee=1200.0
  M-003  lines=1 sn=(cleared) fee=1200.0
```

## 阶段 3 已实现内容（扩展原生 `repair.order`，不重造工单）

字段：

- `urgency_level` 三级紧急度（普通 7 天 / 紧急 3 天 / 加急 1 天）
- `urgency_days`、`urgency_sequence`（加急=1 紧急=2 普通=3，供置顶排序）
- `deadline_datetime` 计划完成期限（自动推算，**管理员可手动覆盖**，`compute + store + readonly=False`）
- `technician_id` 维修师傅、`claimed_at` 抢单时间、`relay_count` 接力次数、
  `relay_from_id` 接力来源、`relay_reason` 退单/接力原因、`modify_count` 当天修改次数（阶段 4 用）
- `is_overdue` / `deadline_state`（看板徽标用，字段驱动，模板里不做条件判断）
- `claimable`

状态扩展（原生只有 `draft/confirmed/done/cancel`）：

`pending_claim`（待抢单）、`timeout_alert`（超时接力）、`secondary_completed`（接力完工）

动作：

| 方法 | 作用 |
|---|---|
| `action_publish` | 从「新建」或「超时接力」发布到大厅 |
| `action_claim` | **并发安全抢单**：`UPDATE ... WHERE technician_id IS NULL AND state IN (...)`，`rowcount != 1` 即判定被抢 |
| `action_release` | 主动退单，放回大厅并记录原因 |
| `action_relay` | 转超时接力（清空师傅、`relay_count + 1`） |
| `action_secondary_complete` | 接力完工 |
| `_cron_check_deadlines` | 每小时扫描超期工单，自动转 `timeout_alert`（Cron 见 `data/repair_cron.xml`） |

界面：

- 看板卡片：超时接力=金色边框、加急=红色、紧急=橙色；紧急度与期限状态用 `widget="badge"` 徽标；
  置顶排序 `urgency_sequence, deadline_datetime`
- 表单：`调度信息` 分组 + 「发布到大厅 / 抢单 / 退单 / 转接力 / 接力完工」按钮；
  statusbar 已包含新状态
- 菜单：机台维修 → **工单大厅**（仅 `stock.group_stock_user` 可见）
- 新组「机台维修 / 师傅」：`implied_ids` 自动带上 `stock.group_stock_user`

回归测试：`D:\Odoo\.tools\test-phase3.py`（最后 rollback，不留数据）
跑法见文件头注释，必须用 `cmd` 输入重定向 + `PYTHONUTF8=1`。

## 阶段 4 已实现内容（库存联动与防错）

> 前提认知：**Odoo 20 已经没有 `repair.line` 模型**了。配件就是 `stock.move` +
> `repair_line_type` 字段，完工时 `action_repair_done` 会统一 `_action_done()` 做出入库。
> 所以"库存联动"不需要另建出库单，**复用原生库存移动即可**；
> 拆下的旧件走原生的 `recycle_location_id`（Recycled Parts Destination）。

- `machine_id` 关联客户机台；`is_rework` / `repair_round` / `previous_repair_count` 自动识别返修
- `write()` 重写：工单处于 `done` / `secondary_completed` 时，非管理员每天最多改 **3 次**，
  超限抛 `UserError`；每次修改写 chatter 留痕；`action_reset_modify_count()` 供管理员一键解锁
- `_check_parts_serial_numbers()` + 重写 `action_repair_done()`：
  序列号管控的配件必须有 SN 明细行，且该 SN 在源库位有可用库存，否则**不许完工**
  （挡住"幽灵设备"出库）

实测：`round=1 → round=2 返修识别`、`#1/#2/#3 允许 → #4 拦截`、管理员绕过、重置后恢复、
无 SN 的板件完工被拒。

## 阶段 5 已实现内容（快照与经验库）

- `repair.fault.category`：管理员维护的故障分类下拉（权限：普通用户只读，管理员 crud）
- `repair.case` + `repair.case.line`：完工自动快照，复制机台/师傅/故障/配件清单/SN/单价/紧急度/轮次
  - **不可篡改**：ACL 只给 `cr`（创建+读），并在 `write()`/`unlink()` 里硬拦截非管理员
  - 编号由 `ir.sequence`（`CASE/2026/00001`）
- `repair.experience`：双轨制录入（标准下拉 + `custom_fault_input` 自由文本），
  `priority` 默认 50、管理员整理置顶 100，`occurrence_count` / `top_parts` / `last_seen_date`
- 每日 Cron `_cron_aggregate()`：把未归并的快照按【机型 + 故障分类】归档，自动累加次数、
  重算高频器件 TOP；未匹配标准分类的进"待整理"（`state=draft`）

实测：`CASE/2026/00001 parts=1`、`write blocked`、聚合 `count=1 → 2`、`top=TEST-BOARD ×1.0`。

## 阶段 6 已实现内容（包月折算与报表）

- `machine.billing.line` 计费明细 + `machine.monthly.billing.wizard` 批量生成向导
- 公式：**应收 = 包月单价 × (实际激活天数 − 放假天数) ÷ 当月实际天数**，
  分母用 `calendar.monthrange` 取 **28/30/31**，不是固定 30；中途加减机按
  `monthly_start_date` / `monthly_end_date` 折算
- 防重复：同月已有计费明细时拒绝再次生成
- 可选同时生成 `account.move` 应收发票（按客户合并，一台一行）
- 6 大报表用原生 `pivot / graph / list` 视图（**天然支持一键导出 Excel**），菜单在"报表"下

实测（2026 年 10 月，31 天，放假 2 天）：

| 机台 | 激活天数 | 计费天数 | 应收 | 预期 |
|---|---|---|---|---|
| M-56（全月） | 31 | 29 | **2806.45** | 2806.45 ✅ |
| M-56-B（16 号起） | 16 | 14 | **1354.84** | 1354.84 ✅ |

重复生成被拒；发票生成成功（`amount_total=6000.0`）；两个 Cron 均存在。

## 阶段 7 已实现内容（手机定位 / 工作位置监控，仅管理员可见）

### 1. 数据模型

| 模型 | 作用 | 关键字段 |
|---|---|---|
| `machine_repair.work_location` | 工作位置（电子围栏），可在自有厂区或客户现场 | `name` / `partner_id` / `latitude` / `longitude` / `coord_system`（WGS-84 / GCJ-02）/ `radius`（默认 150 米）/ `user_ids` / `active` |
| `machine_repair.location_ping` | 位置上报流水（只追加） | `user_id` / `latitude` / `longitude` / `accuracy` / `distance` / `place_id` / `state` / `source` / `ip` / `recorded_at` |
| `machine_repair.user_location` | 每人一条「最新位置」快照 | 与流水同结构 + `age_minutes` / `is_stale`，`user_id` 唯一 |
| `res.users`（继承） | 人员分配与开关 | `machine_repair_location_enabled` / `machine_repair_location_ids` |
| `res.config.settings`（继承） | 定位设置 | 上报间隔 / 移动阈值 / 失联分钟数 / 保留天数 / 总开关 |

判定逻辑在服务端 `work_location._evaluate_position()`：多个工作位置时，
落在**任意一个**围栏内即 `inside`（取围栏内最近的点展示），否则取整体最近点与 `outside`。
**没有分配工作位置时也照常记录**，状态记为 `unassigned`（未分配），不阻断上报。
距离用 Haversine 公式（`_distance_m`），纯 Python，无外部依赖。
工作位置按 `coord_system` 统一转成 WGS-84 后再和手机坐标算距离；
`models/geo.py` 提供 WGS-84 ↔ GCJ-02 转换和高德链接。

### 2. 谁能看 / 谁能上报（关键安全设计）

- **所有手机端登录用户（包括管理员）**打开页面/App 即自动上报，**不要求分配工作位置**。
- 新组 **`机台维修 / 位置监控管理员`**（`group_machine_repair_location_manager`），
  默认把 `admin` 加入；系统管理员 `base.group_system` 同样放行。
- 三个位置模型**只给这个组 / 系统管理员** ACL；师傅组没有任何 ACL。
- 手机端上报接口一律 `sudo()` 写库，普通账号不因上报而获得读权限。
- 页面/接口双重校验：`/machine_repair/locations` 非管理员返回 **403**；
  `/machine_repair/locations/data` 非管理员返回 **403 JSON**。
- `res.users` 上的位置字段带 `groups=`，普通账号即使调 RPC 读
  `machine_repair_location_ids` 也是 `AccessError`（实测）。
- 手机端 `/location/state` 只返回**本人**配置与工作位置名称/半径，**不下发坐标**。

### 3. 接口

| 路由 | 方法 | 说明 |
|---|---|---|
| `/machine_repair/mobile/location/state` | POST | 手机启动时问：要不要上报、间隔、本人在哪几个位置（可为空） |
| `/machine_repair/mobile/location` | POST | 网页上报一次坐标；服务端算好 `inside/outside/unassigned` 并同时写流水 + 快照 |
| `/machine_repair/mobile/location/native` | POST | Android 前台服务后台调用；`csrf=False`，用 session Cookie + `X-MR-Native` 头认证 |
| `/machine_repair/locations` | GET | 管理员位置看板（独立轻量页，手机可用） |
| `/machine_repair/locations/data` | GET | 看板数据（统计 / 人员 / 位置 / 最近上报），仅管理员 |

上报参数：`lat` / `lng` / `accuracy` / `src`（`pwa` 或 `android`）。8 秒内移动小于 5 米不刷流水，
只更新快照，避免同一位置反复写表。**没有工作位置时 `place_id` 为空、状态 `unassigned`，
位置仍完整保存**。

### 4. 手机端（PWA / Android App）—— 全自动、无界面

- **Android App 后台上报**：`LocationService` 是 `foregroundServiceType="location"` 的前台服务，
  带常驻通知。v1.13 起在**页面加载完成、App 仍在前台时**就启动服务（不再依赖 `onPause`，
  避开 Android 12+ 后台启动前台服务的限制）；网页 JS 通过 `AndroidScanner.nativeLocation()`
  自动跳过，避免 App 内双份上报。登录一次、位置权限选「始终允许」后，关闭 App/锁屏仍继续
  每 60 秒上报；开机后 `BootReceiver` 会尝试恢复（部分系统会拒绝，打开一次 App 也会恢复）。
- **判断服务有没有运行**：通知栏应常驻「位置上报中」；没有通知就是没启动，常见原因是
  未登录/Cookie 未保存/定位权限没给；App 会 Toast，通知里也会写「尚未登录」「需要重新登录」
  「手机定位未开启」「位置上报失败」等具体原因。v1.13 还会引导「忽略电池优化」。
- **认证**：服务复用 WebView 的 `session_id` Cookie，调 `/machine_repair/mobile/location/native`；
  该接口 `csrf=False`，但要求 `X-MR-Native: 1` 且拒绝带 `Origin` 的请求，跨站表单无法伪造。
  服务每分钟请求也会让 Odoo 会话保持活跃。
- **网页/PWA**：页面加载后自动请求 `/location/state`，然后 `watchPosition`；
  默认 60 秒一次、移动 30 米补报，切回前台/恢复网络补一次。网页仍是前台定位。
- **限制**：用户「强行停止」、厂商电池管理杀进程后，必须打开一次 App 才能恢复；
  后台权限必须选「始终允许」，否则关闭 App 后可能停止（App 会弹提示引导去设置）。
- **没有分配工作位置也照常上报**，管理端显示「未分配位置」。
- 手机上不显示位置卡片/手动按钮；「我」页只有我的工单和账号。

### 5. 管理员看板

- 入口：`机台维修 → 位置监控`（实时位置 / 位置看板 / 工作位置 / 位置记录）+
  `机台维修 → 配置 → 定位设置`。
- 看板内容：6 个统计卡（含「未分配」）、**高德真实街道地图**（可缩放，工作位置半径圆 + 人员点）、
  人员状态列表、工作位置列表、最近 30 条上报；每 30 秒自动刷新。
- 地图：直接用高德栅格瓦片（`webrd0{1-4}.is.autonavi.com`，免 key），
  手机 GPS 的 WGS-84 先转 GCJ-02 再投影，避免点位偏移 300~600 米；
  每个人员/历史记录/工作位置都带「高德地图」链接，瓦片加载失败也能点链接查看。
- 颜色：绿=在工作位置、红=离开、橙=失联、灰=未分配/未上报。

### 6. 定位设置（仅管理员）

| 设置 | 默认 | 说明 |
|---|---|---|
| 启用手机端位置上报 | 开 | 总开关，关闭后不记录新位置 |
| 上报间隔 | 60 秒 | 手机端节流下限 15 秒 |
| 移动多少米立即上报 | 30 米 | 0~5000 |
| 超过多少分钟视为失联 | 15 分钟 | 看板标「失联」 |
| 位置记录保留天数 | 90 天 | 每日 Cron `_cron_purge_old_pings` 清理，0=不清理 |

`定位设置` 的菜单与 `res.config.settings` 只开放给**系统管理员**；位置监控管理员能看位置、
维护工作位置，但不能改全局上报参数（避免普通管理账号关掉监控）。

### 7. 回归测试

前置建数据 / 收尾清理 / 测试本体（测试使用独立的 `mrloc-tech@test.local` 和
`mrloc_admin@test.local`，**不会碰真实业务账号**；清理会删这两个测试账号及其位置记录）：

```powershell
$env:PYTHONUTF8='1'
cmd /c "D:\Odoo\venv\Scripts\python.exe D:\Odoo\odoo\odoo-bin shell --config D:\Odoo\odoo.conf -d odoo --no-http --log-level=warn < D:\Odoo\.tools\test-location-setup.py"
D:\Odoo\venv\Scripts\python.exe D:\Odoo\.tools\test-location.py
cmd /c "D:\Odoo\venv\Scripts\python.exe D:\Odoo\odoo\odoo-bin shell --config D:\Odoo\odoo.conf -d odoo --no-http --log-level=warn < D:\Odoo\.tools\test-location-teardown.py"
```

实测结果（2026-10-21）：全部通过 —— 手机端无卡片/无按钮、未分配工作位置也能上报（`unassigned`）、
分配后室内 `0.0 米`、室外 `1111.9 米`、管理员也自动上报、**原生后台接口 200/无头 403/跨站 Origin 403/匿名 401**、
师傅 403/`AccessError`、临时管理员看板与 RPC、6 个菜单、4 个动作、Cron、设置开关与间隔生效、Service Worker v2。

### 8. Odoo 20 上踩到的两个新坑（已修）

1. **`ir.actions.act_window.target` 不再接受 `'inline'`**（v20 只剩 `current/new/fullscreen/main`）。
   定位设置动作去掉 `target` 即可，否则模块升级直接 `ValueError: Wrong value... 'inline'`。
2. **给 `base.user_admin` 写 `group_ids` 不会生效**（该记录在 base 的 noupdate 数据里）。
   改成在新建组时写 `res.groups.user_ids`：
   `<field name="user_ids" eval="[(4, ref('base.user_admin'))]"/>`，升级后实测组内 1 人。

## 附加：配件创建入口 / 报表首页 / 站内提醒

**配件入口**（都在「机台维修 → 基础档案」下，不用去库存产品里大海捞针）：

| 菜单 | 打开 | 已预置的默认值 |
|---|---|---|
| 通用电子元件 | `product.template`，domain 锁定「通用电子元件」类目 | 类别=通用电子元件、追溯=**无**、可库存=是 |
| 机型专属电路板 | `product.template`，domain 锁定「专属电路板」类目 | 类别=专属电路板、追溯=**按唯一序列号**、可库存=是 |

列表里「追溯」用徽标显示（`serial` 红色），避免建了板件却忘了开 SN。
建好板件后再到「机型标准模板 → 专属电路板清单」把它挂到具体机型下（One2many 子表）。

**报表首页**：用原生 `board` 模块（不是 `spreadsheet_dashboard`）。
`spreadsheet.dashboard` 的数据是 `.osheet` JSON，手写极易出错且无法验证；
`board` 的 arch 是简单 XML（`<action name="动作 id" view_mode="pivot"/>`），
6 张报表左右两栏一页显示，实测 arch 里的 `%(...)d` 已解析为真实动作 id（552–557）。

**站内提醒**（无需 SMTP）：

- 工单超时被巡检自动转「超时接力」时 → 通知原师傅
- 工单被手动转「超时接力」且操作者不是原师傅 → 通知原师傅

实现是 `message_post(body=..., partner_ids=[师傅的 partner])` + `mail.mt_comment`：
对方会在界面收到 bus 推送。**若要同时发邮件**，需先在
「设置 → 技术 → 出站邮件服务器」配好 SMTP（当前未配置，所以只发站内）。

## ⚠️ Odoo 20 与旧版教程的关键差异（本次实施实测踩到）
方案里引用的写法大多来自 17/18 时代，在 Odoo 20 上会直接失败：

1. **权限文件改名 + 换格式**
   旧：`security/ir.model.access.csv`，表头 `id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink`
   新：**`security/ir.access.csv`**，表头 `id,name,model_id,group_id/id,operation,domain`
   - `model_id` 直接写**模型名**（如 `machine.base.template`），不再用 XML ID `model_machine_base_template`
   - 四个 `perm_*` 列合并为单列 **`operation`**，取值是 `crud` 的子集（如 `r`、`crud`）
   - 原因：模型 `ir.model.access` 已改名为 **`ir.access`**（源码里的升级脚本 `odoo/upgrade_code/19.4-00-ir-access.py` 可佐证）。
     CSV 加载器按**文件名**取模型名（`convert.py: model = filename.split('-')[0]`），
     继续用旧文件名会抛 `KeyError: 'ir.model.access'` 并导致整个 registry 加载失败。
2. **列表视图用 `<list>`**，`<tree>` 已不再使用。
3. **搜索视图的分组过滤器不能再包 `<group expand="0">`**
   直接写 `<filter name="..." string="..." context="{'group_by': '字段'}"/>` 即可；
   否则视图 RNG 校验失败（`Invalid attribute expand for element group`）→ `ParseError`，安装中断。
4. **唯一约束用 `models.Constraint`**
   `_sql_constraints` 已废弃。写法：`_uniq_x = models.Constraint('unique(a, b)', '提示消息')`，
   落到数据库是真实约束（本模块实测生成 `machine_base_template_line_uniq_template_product`）。
5. **可翻译字段在库里是 `jsonb`**
   例如 `product_category.name` 列类型为 `jsonb`。写 SQL 时必须 `name::text` 或 `name->>'en_US'`，
   否则报 `invalid input syntax for type json`。
6. 图标/菜单：`<menuitem>` 不带 action 即为分组容器，Odoo 20 仍支持。
7. **manifest 的 data 顺序有意义**：按钮里用 `%(action_xxx)d` 引用另一个 XML ID 时，
   被引用的文件必须**先**加载，否则升级报 `ValueError: External ID not found in the system: ...`，
   整个升级事务回滚。
8. **产品要能用序列号，必须 `is_storable=True`**：Odoo 17+ 起 `tracking`
   （序列号/批次）只对"可库存"产品生效；只设 `tracking='serial'` 而产品不可库存时序列号不起作用。
9. 日志时间戳是 **UTC**，而 `Get-Date` 是本地时间；按时间过滤日志要用
   `(Get-Date).ToUniversalTime()`，否则过滤结果为空。
10. **`res.users.groups_id` 已改名 `group_ids`**（另有 `all_group_ids` 含隐含组）。
    而 `res.users.role` 是**字符串**（Selection），不是 many2one，写 `user.role.name` 会报
    `AttributeError: 'str' object has no attribute 'name'`。
11. **`mail.message` 的创建权限带域限制**：`[('res_access_create', '=', True)]`，
    意思是"你对这条业务单据本身有没有创建权"。
    所以 `repair.order` 上的 `message_post` 要求用户有 `stock.group_stock_user`
    （原生 `repair.order` 的 CRUD 只授予这个组）。否则报
    `AccessError: (Document type: Message, Operation: create)`。
12. **原生 SQL 之前必须先 `flush_all()`**：Odoo 的 ORM 写入是延迟刷盘的，
    直接 `cr.execute` 会看到旧状态，导致条件更新匹配不到行。
    正确做法是 `self.env.flush_all()`，**而不是 `cr.commit()`** —— 整个请求本就在一个事务里，
    中途 commit 会破坏原子性。
13. **`ir_cron` 表没有 `name` 列**：Odoo 20 把任务名挂到了 `ir_actions_server_id` 上，
    实际列名是 **`cron_name`**。SQL 里查任务名要用 `cron_name`（ORM 侧 `cron.name` 仍可读，是 related）。
    另外可翻译字段（`res_groups.name`、`product_category.name`）在库里是 **jsonb**，SQL 要 `::text`。
14. **Odoo 20 没有 `repair.line` 模型**：维修配件就是 `stock.move`（带 `repair_id` +
    `repair_line_type`），完工时由 `action_repair_done` 统一做出入库。照旧版教程去建
    `repair.line` 或手工造出库单都是错的。
15. **`stock.move` 没有 `name` 字段**（旧版有），创建移动时传 `name` 会报
    `ValueError: Invalid field 'name' in 'stock.move'`。
16. **`%(xmlid)d` 只适用于视图 arch，不适用于 domain/context 字段**。
    视图 arch 里写 `<button name="%(action_x)d"/>`、`<action name="%(action_x)d"/>` 都能解析；
    但 `ir.actions.act_window` 的 `domain` / `context` 是 Char 字段，要用
    `<field name="domain" eval="[('categ_id', '=', ref('module.xmlid'))]"/>`（列表会被转成合法域字符串）。
17. **`board` 仍是社区版可用的原生看板**：`board.board` 是 `_auto = False` 的抽象模型，
    一个"仪表盘"本质就是一条 `model="board.board"` 的 `ir.ui.view`
    （`<board style="1-1">` + `<column>` + `<action/>`），再配一个
    `res_model="board.board"`、`view_mode="form"`、`context="{'disable_toolbar': True}"` 的窗口动作。
18. **`fields.Binary` 在 Odoo 20 用 `BinaryValue` / `BinaryBytes` 代理对象**：
    直接赋 `bytes` 会报 `TypeError: use BinaryValue instead of bytes`；
    写值要用 `BinaryBytes(原始字节)`（**不是 base64**），读出来是代理对象，
    取原始内容用 `bytes(value)` 或 `.content`，要 base64 用 `.to_base64()`。
19. **`ir_model_data.noupdate` 一旦为 true 不会被自动改回**：把 XML 里的 `noupdate="1"` 去掉，
    已存在的记录仍然不会更新。要么一开始就别加，要么显式写一次
    `ir.model.data.write({'noupdate': False})`（本模块修序列前缀时就是这么做的）。

## 附加：电路板二维码（板件身份主线）

### 设计原则（按实际业务修正过）

**前期越简单越好**：机型模板与客户机台台账**只做结构，不录二维码、不需要序列号**。
二维码只在**真正产生维修业务时**（发布工单）才扫码录入或现场生成 —— 这样建模板、建台账都很快，
也不会出现"台账里一堆提前造好的码、实际板子还没装"的混乱。

### 业务流

| 场景 | 操作 | 系统行为 |
|---|---|---|
| 建模板 / 建台账 | 机型标准模板、客户机台台账 | **不建码**（实测 `machine_line_qr=False total_qr=0`） |
| 包月客户报修 | 工单 → 扫板件码 → 「识别二维码」 | 自动带出客户 / 机台 / 板件型号；该码自动回填到台账对应行 |
| 外来维修 | 工单 → 选客户 → 扫原厂码 | 库里没有就**自动建档**（来源=原厂码，SN 保留原厂值） |
| 原厂码模糊 / 没有 | 工单 → 「生成二维码」 | 按客户+机台+板件现场生成 `QR-000001` 式系统码 |
| 客户 / 机台变了 | 再次扫码 | **在该二维码上留痕**：字段 tracking + 额外一条"扫码信息变更"记录（含来源工单） |
| 一码多档（重复） | 扫码命中多条 | **直接拦住**，提示去「电路板二维码」合并或报废，避免修错板 |

### 二维码内容（实测样例）

```
MR1;QR=QR-000023;H=EAB115;CID=47;CNAME=客户甲;MODEL=QR-BOARD;
    PDT=2026-10-18;MS=2026-10-01;ME=2027-09-30;WAR=2027-10-18;QID=23;PID=29;S=d8e97120
```

| 段 | 含义 |
|---|---|
| `QR=` | 二维码编号 |
| `H=` | **身份摘要**：客户信息 + 创建时间 + 板件型号 的 sha1 前 6 位 |
| `CID=` / `CNAME=` | 客户 id / 客户名 |
| `MODEL=` | 电路板型号 |
| `PDT=` | 板件创建时间 |
| `MS=` / `ME=` | **包月开始 / 结束**（从机台自动带出） |
| `WAR=` | **保修到期日**（默认创建时间 + 保修月数，可改） |
| `QID=` / `PID=` | 二维码记录 id / 板件产品 id |
| `S=` | 整段内容的 sha1 前 8 位，**改过的码会被拒绝** |

扫码即可反查：客户是谁、包月到什么时候、这块板哪年建的、什么型号、**保修是否到期**。

### 模型与界面

- `machine.board.qr`：编号 / 来源（原厂·系统生成）/ 身份摘要 / 客户 / 机台 / 板件型号 / 原厂 SN /
  创建时间 / 包月起止 / 保修月数与到期日 / **保修状态**（保修中·已过保）/ 内容 / 二维码图片 /
  相关工单 / 维修次数 / 有效·报废，**继承 `mail.thread`（chatter 记录全部变更）**
- 菜单：机台维修 → 基础档案 → **电路板二维码**（列表带保修徽标；搜索可按保修中/已过保过滤）
- 工单表单：`扫码录入` + 「识别二维码」（扫整段内容也能解析）；`board_qr_id` 显示绑定结果

### 电路板从哪里建

- **推荐（按你的说法）**：机型标准模板 → 专属电路板清单 → 直接在下拉里**新建产品**。
  视图通过 `context="{'machine_repair_board_default': True}"` 传意图，
  `product.template.create` 据此自动补齐：类别=专属电路板、**追溯=按唯一序列号**、可库存=是。
- 也保留独立入口「机型专属电路板」，用于集中维护 / 批量改名，两处等价。

## 界面验证方式（无需人工截图）

`.tools\verify-ui.py` 用真实 Web 会话登录（`/web/session/authenticate` +
`ir.ui.menu.load_menus`），输出该用户**实际可见的菜单树**，等价于人工登录查看，
但比查数据库可信 —— 菜单会按用户权限过滤。

注意：看板卡片是**客户端 JS 渲染**，服务端不产卡片 HTML，所以"高亮是否好看"只能人眼确认；
但 arch 是否可加载、字段是否声明正确，可以用 `get_views` 验证。

## 安装与升级

```powershell
# 首次安装（会短暂停掉 Odoo）
D:\Odoo\start-odoo.ps1 -Stop
D:\Odoo\venv\Scripts\python.exe D:\Odoo\odoo\odoo-bin --config D:\Odoo\odoo.conf -d odoo -i machine_repair --stop-after-init
D:\Odoo\start-odoo.ps1 -Detach

# 改代码后升级（-u 重新加载数据与视图）
D:\Odoo\start-odoo.ps1 -Stop
D:\Odoo\venv\Scripts\python.exe D:\Odoo\odoo\odoo-bin --config D:\Odoo\odoo.conf -d odoo -u machine_repair --stop-after-init
D:\Odoo\start-odoo.ps1 -Detach
```

注意：Odoo 自己写 `D:\Odoo\logs\odoo.log`，所以**不要**用 PowerShell 的 `*>` 去抓安装输出（会得到空文件），直接看日志。

## 另计配件计费闭环（包月费 / 换板费分开算）

链路：

```
客户机台台账：每行「计费方式」= 含在包月内 / 另计费 +「另计单价」
        ↓  工单选了机台（或扫码带出机台）
工单自动生成配件行（stock.move, repair_line_type='add'）：只带「另计费」的板件
        数量默认 1 块（现场换几块由师傅改）、price_unit = 台账另计单价
        ↓  点「开票」（原生 action_create_invoice）
发票行由配件行自动生成，价格取 move.price_unit  ← 原生机制，不用改核心
        ↓
保修内（二维码 warranty_state=active）→ 工单自动勾 under_warranty → 发票价 0
```

实测（`test-extra-billing.py`）：

```
LEDGER    [('sb880','monthly',0), ('usd3335','extra',88.5)]
PARTS     [('usd3335', 1.0, 88.5)]
INVOICE   LINE usd3335 qty=1 price=88.5      ← 不是产品售价 300
INSURED   warranty=active -> LINE price=0.0  ← 保修期内免费
```

要点：

- 自动带出的时机：**新建工单时**、**改了 `machine_id` 时**（扫码识别也会触发）
- 手动补拉：表单顶部「带出另计配件」按钮（台账后来改价/加板件时用）
- 包月费不在这里收：仍走「批量生成包月账单」按月开票；两笔钱、两张票，互不干扰
- 发票行会带产品默认税（实测 88.5 → 101.78，即 15%），要免税或不含税价请在产品的客户税上设置
- 配件行数量默认 1：台账里的 `qty` 是"这台机器装了几块"，不等于"这次换了几块"

## 修正：过保 = 重新维修 + 重新计时 + 重新收费（**不是换板**）

上面的"配件行"做法已废弃。按实际业务口径，过保是**修好它**，不是卖新板，所以：

- 收费走 **维修服务行**（`repair.service.line`，产品=「电路板维修费」），
  **不生成配件行、不扣库存**
- 单价取台账「另计单价」，通过本模块给 `repair.service.line` 补的 `price_unit` 字段
  注入到发票行（原生服务行没有价格字段，价格取自产品售价）
- **完工时该板件保修重新计时**：`warranty_start_date = 完工日`，月数取本单「本次保修月数」
- 保修内（`warranty_state = active`）→ 自动 `under_warranty` → 发票 0 且**保修期不动**
- 外来维修（没有机台台账）同样处理：在工单上手填「另计维修单价」+「本次保修月数」，
  完工后一样重新计时

实测（`test-repair-charge.py`）：

```
CHARGE    under_warranty=False price=126.0 months=6
SERVICE   [('电路板维修费', 'usd3335', 1.0, 126.0)]
STOCK     part_moves=0                       ← 库存零变动
INVOICE   LINE 电路板维修费 — usd3335  price=126.0
RENEW     start=2026-10-18 months=6 end=2027-04-18   ← 重新计时
INSURED   under_warranty=True → total=0.0，保修期不变
EXTERNAL  months=3 end=2027-01-18             ← 外来维修同样重新计时
```

**数据前提**：需要产品「电路板维修费」（`data/repair_service_product.xml` 已自动建，类型=服务）。
注意 Odoo 20 **没有** `product.product_category_all` 根类目，服务类产品请用
`product.product_category_services`。

**界面**：工单「调度信息」里有「另计维修单价」「本次保修月数」（选机台时自动带出）；
「Services」页签里能看到「电路板维修费 — 板件名 / 单价」；顶部按钮「带出另计维修」可手动重拉。

## 阶段 3 架构已定案：A（扩展原生 `repair.order`）

Odoo 20 已装 **`repair`（维修）应用**，原生具备：`repair.order` + 配件行 + 序列号/批次出入库 + 完工入库/出库。
方案里的 `work.order` + 阶段 4 的 SN 联动与它高度重叠，两条路里已选定 **A**：

- **A. 继承扩展 `repair.order`**（已采用）：只补"调度"一层（紧急度 / 期限 / 抢单 / 接力 / 修改计数），
  直接复用原生库存与 SN 能力、报价单、开票与报表。
- **B. 新建独立 `work.order`**（未采用）：完全自主可控，但要自己实现出入库与 SN 校验，工作量与出错面大得多。

**部署前提**：师傅账号必须拥有 `stock.group_stock_user`（原生 `repair.order` 的 CRUD 只授予该组，
否则连 chatter 消息都发不出去）。可直接把用户加进本模块新建的「机台维修 / 师傅」组，
它已经 `implied_ids` 了 `stock.group_stock_user`。

## 手机端修复记录（2026-10-19）：详情信息“丢失” + 视频分享没反应

两个现场反馈都在这台机器上复现并定位到了**具体代码**，不是“字段命名不一致 / Pinia 状态没同步”那类问题
（本项目没有前端状态库，数据直连 PostgreSQL，工单表里数据其实一直都在）。

### 1. 「保存后详情里机台 / 板件 / 错误代码 / 故障描述都不见了」

实测（订单 WH/RO/00063，`id=62`）：详情页里这几项显示 `-`，但数据库里也确实为空，
不是渲染丢的，而是**根本没录进去**：

| 原因 | 说明 | 修复 |
|---|---|---|
| 扫码建单时没有机台/板件可填 | 机台/板件下拉只存在于「生码建单」页；扫老码（`QR-000049` 这类没建档的板）时无处可选，建出来的单就是空的 | 详情页缺机台/板件时出现「补填机台 / 板件」下拉，直接写回工单并回写二维码；扫码信息卡也会提前提示 |
| 错误代码在详情页只读 | 建单时没带上的错误代码，之后在手机端**没有入口**再填 | 详情页新增「错误代码」输入框，跟着「保存以上内容」一起提交 |
| 后台表单根本没有这些字段 | `repair.order` 表单里只有调度信息，没有错误代码 / 故障描述 / 分析 / 过程，在 Odoo 里打开工单就“什么都没” | 表单新增「故障信息（手机端录入）」分组 |
| 一次点击开了两张单 | 日志里 `11:51:10` 与 `11:51:11` 两条 `create_order`，同一张码建出 WH/RO/00062、00063 两张单（连点） | 建单加飞行中锁 + 按钮禁用，实测连点两次只发 1 条请求 |

### 2. 「点视频分享没反应」

根因有两个，叠在一起就是“完全没反应”：

1. **提示被详情页挡住了。** `#toast` 的 `z-index: 50`，而详情浮层 `.detail-sheet` 是 `60`（遮罩 80）。
   详情页里的任何提示（分享失败、已保存、测量完成…）都渲染在浮层**下面**，用户看不到。
   实测 `document.elementFromPoint()` 命中的是浮层而不是 toast。→ toast 提到 `z-index: 300`，
   详情页顶部另加一行 `#d-hint` 状态文字。
2. **`navigator.share()` 是在 `fetch()` 之后调用的。** 视频要先下载成 File 再分享，等异步回来时
   用户手势已过期，浏览器按规范直接拒绝（`NotAllowedError`），分享面板根本不会弹；安卓 WebView /
   部分国产浏览器压根没有 `navigator.share`，老代码只 `toast` 一句“这台手机不支持分享” —— 而这句话
   又被第 1 条挡住，所以看起来就是“点了没反应”。

现在的实现（`views/mobile_templates.xml`）：

- 详情页渲染时后台预取视频（≤30MB）成 `File` 缓存；
- 点击「分享」时**在第一层同步**调用 `navigator.share()`：文件就绪就分享文件，没就绪立刻分享链接；
- 不支持分享的浏览器弹出可见的「复制链接 / 打开视频 / 关闭」面板（剪贴板 API 不可用时退回
  `textarea + execCommand`）；
- 失败提示写进 toast + 详情页状态行，并且区分“用户自己取消（AbortError）”与真失败。

### 3. 顺带修掉的

- `save_work` 以前**无条件**写四个字段，建单后那次“补写错误代码”会把已填的故障分析/维修过程清空；
  现在只写前端真正传上来的字段，并支持补填 `machine_id / product_id`。
- `esc()` 只转义 `& < >`，视频文件名带引号会把 `data-sharevideo` 属性截断；现在同时转义引号，
  属性里只放 video id（原来拼 `id|url|文件名`，`|` 也会把解析搞乱）。
- CSS 里有一处坏的转义（`` .btn.danger { ... }`n `` 之后跟着 `.btn.sel`），会让这条规则解析失败。
- 详情页重渲染会把滚动位置带走 / 底部导航出现双份确认框（同一个点击被详情页和全局委托各处理一次）。

### 验证方式（都是实测，不是“应该能行”）

```powershell
# 1) 升级模块（会短暂停 Odoo，约 5 秒）
D:\Odoo\venv\Scripts\python.exe D:\Odoo\odoo\odoo-bin --config D:\Odoo\odoo.conf -d odoo -u machine_repair --stop-after-init
# 2) 用计划任务把 Odoo 拉起来（不在当前会话的进程树里，会话结束也不会被带走）
Start-ScheduledTask -TaskName 'Odoo 20.0 - 服务'
```

用无头 Chrome + CDP 真机脚本跑过（`D:\Odoo\tmp\probe4.py`、`probe5.py`、`probe6.py`）：
toast 在浮层之上、分享在点击第一层同步调用且带 `files:1`、没有 share API 时降级面板可见可复制、
补填只提交 `machine_id/product_id`、连点建单只发 1 条请求、`save_work` 只写传上来的字段。

## 唯一性修复（2026-10-19）：能建出同名的电路板 / 机型 / 故障分类

现场反馈：在「机型专属电路板」里点新建，可以反复建出同名电路板（库里实际出现两份 DDMP / KBD / PB）。
根因很简单：那个菜单只是把 `product.template` 的列表按类目过滤了一下，新建走的是**标准产品表单**，
而 Odoo 的产品名称本身不做唯一性校验，模块之前也没加任何约束。

### 补上的唯一性规则

| 对象 | 规则 | 实现 |
|---|---|---|
| 电路板 / 通用电子元件（`product.template`） | 同一类目内不允许重名（忽略大小写与首尾空格）；跨类目允许 | `models/product_template.py` 的 `@api.constrains('name', 'categ_id')` |
| 机型标准模板（`machine.base.template`） | 机型名称唯一 | `models/machine_base_template.py` `@api.constrains('name')` |
| 故障分类（`repair.fault.category`） | 分类名称唯一 | `models/repair_case.py` `@api.constrains('name')` |
| 电路板二维码（`machine.board.qr`） | 原厂 SN / 条码唯一（空值允许重复） | `models/board_qr.py` `@api.constrains('sn')` |
| 包月计费明细（`machine.billing.line`） | 同一机台同一个月只能一行 | `unique(machine_id, period_month)` + `create()` 里的人话校验 |

另外补了 `product.category.is_component`（跟 `is_board` 对称）—— 以前「通用电子元件」类目只能靠 xmlid 认，
同名校验这类按类目生效的规则就只能覆盖电路板、漏掉元件。两个标志位现在也放到产品类别表单/列表里了
（`views/product_category_views.xml`），以前 `is_board` 在界面上根本看不见、只能改数据库。

### 已经清理掉的重复数据

| 数据 | 处理 | 备份 |
|---|---|---|
| 重复电路板 DDMP(#56)、KBD(#60)、PB(#61) | 确认无任何引用（模板行/二维码/工单/库存/序列号）后删除，保留先建的那条 | 改动前整库备份 `D:\Odoo\backups\` |
| 重复工单 WH/RO/00073(#72)（QR-000063 两张待抢单） | ORM 正常删除 | — |
| 重复工单 WH/RO/00063(#62)（QR-000049 两张已完工，连点建单产生的） | Odoo 核心**禁止删除已完工维修单**（`repair.order._unlink_except_confirmed`），按测试数据在备份后强制清理；保留早建的 WH/RO/00062 | `dup-orders-sql-*.json`、视频文件 `dup-video-1-*.3gp` |

### 验证（`odoo-bin shell` 里 13 个用例，全部通过）

```
PASS 电路板重名 DDMP                -> ValidationError: 同名的电路板「DDMP」已经存在（#48）
PASS 电路板重名 ddmp（大小写/空格）    -> ValidationError
PASS 电路板新名 ZZ-NEW              -> 创建成功
PASS 元件重名 ZZ-COMP               -> ValidationError
PASS 跨类目同名（电路板 vs 元件）      -> 允许
PASS 机型模板重名 GAMMA / gamma      -> ValidationError
PASS 故障分类重名（含空格）            -> ValidationError
PASS 二维码原厂 SN 重复              -> ValidationError
PASS 二维码 SN 为空                  -> 允许
PASS 同机台同月第二条计费明细          -> ValidationError: 机台「01」在 2026-10-01 已经有一条…
```

## 工单大厅分栏 + 手机端分栏 + 管理员「作废并删除测试单」（2026-10-19）

### 1. 网页端「工单大厅」分栏菜单

大厅原来只显示未完成的工单（domain 写死 draft/pending_claim/confirmed/timeout_alert），
已完工的单子根本看不到。现在拆成 6 个分栏，每个分栏是独立 action + 独立 domain：

| 分栏 | domain |
|---|---|
| 全部 | `[]` |
| 待抢单 | `[('state','=','pending_claim')]` |
| 维修中 | `[('state','=','confirmed')]` |
| 超时接力 | `[('state','=','timeout_alert')]` |
| 已完工 | `[('state','in',['done','secondary_completed'])]` |
| 已开票 | `[('invoice_ids','!=',False)]` |

实现要点（都是实测踩出来的）：

* 分栏按钮挂在**大厅专用列表视图** `<header>` 里（`view_repair_hall_list_menu`，extension 视图）。
  列表视图的 `<header>` 按钮只有带 `display="always"` 才会出现在控制面板上，
  否则会被塞进右上角那个齿轮/省略号菜单里（第一次做漏了这个属性，界面上一个按钮都没有）。
* `%(action_xxx)d` 在 arch 里是**加载时解析**的，所以顺序必须是：
  列表视图 → 6 个 action（action 里引用 view_id）→ 再给列表加 header 按钮（引用 action xmlid），
  否则 xmlid 还不存在会直接报错。
* 核心 `repair.order.state` 的中文翻译在这个库里缺失（列表显示 `Done` / `Cancel`），
  新增计算字段 `state_display`（中文状态）用于列表显示，badge 配色仍按 `state` 判断。

### 2. 手机端分栏菜单

* **任务工单**：待抢单 / 维修中 / 超时接力 / 已完工（看整个车间，带数量角标）
* **我的工单**：维修中 / 超时接力 / 已完工（只看自己的，带数量角标）

接口 `/machine_repair/mobile/my_orders` 改成按组返回：

```json
{"groups": {"pending": [...], "repairing": [...], "timeout": [...], "done": [...],
            "mine_repairing": [...], "mine_timeout": [...], "mine_done": [...]},
 "counts": {"pending": 9, "done": 2, "mine_done": 0, ...}}
```

`_order_payload` 增加了 `state_key` / `claimable` / `mine`：
列表里「抢单」只对没人接的单子出现，「完工 / 开票」只对自己抢到的单子出现
（以前别人的单也会显示「完工」，点了必然失败）。

### 3. 管理员「作废并删除测试单」

Odoo 核心**禁止删除已完工维修单**（`repair.order._unlink_except_confirmed`），
测试期造出来的单子以前只能手工动 SQL。现在封成一个向导：

* 入口：工单表单顶部、工单大厅列表顶部（都是 `groups="base.group_system"`，只有管理员可见）；
* 二次确认：先弹出向导（列出要删的工单 + 备份目录），勾选「我已确认这些是测试数据」后才能点「确认作废并删除」；
  没勾选直接点会报错、弹窗不关；
* 列表里没选任何行就点按钮会提示「请先选择要删除的工单」（不会误删全部）；
* **自动备份**：删除前把工单全部字段、维修服务行、完工快照、聊天记录导出成 JSON，
  视频二进制单独存成文件，目录默认 `D:\Odoo\data\test-order-backups`
  （可用系统参数 `machine_repair.test_order_backup_dir` 覆盖）；
* 删除顺序：快照/视频/消息/附件/关注者 → 未完工的先走原生「取消」→ 最后删工单，全程一个事务，失败整单回滚。

### 4. 本次清理

* 3 张空单（WH/RO/00003、00042、00056）已用上面的向导删除，备份在 `test-order-backups`；
* 剩余 11 张工单：待抢单 9、已完工 2。

### 5. 验证

* 网页端（无头 Chrome 实测）：控制面板出现 全部/待抢单/维修中/超时接力/已完工/已开票/作废并删除测试单 七个按钮，
  点「已完工」切到对应 action、只显示 2 条，状态列显示中文；
* 向导实测：不选行 → 拒绝；选中 1 行 → 弹窗列出该单；不勾选确认 → 报错且弹窗不关；点取消 → 未删除任何数据；
* 手机端（无头 Chrome 实测）：任务工单 4 个分栏（待抢单 9 / 已完工 2）、我的工单 3 个分栏，切换正常，页面版本 v33。

## 客户类型 / 整列设置 / 发货提醒 + 手机端开票·发货分栏（2026-10-21）

### 1. 客户类型：包月用户 / 活期用户

* `res.partner.customer_type`：`monthly`（包月用户，默认）/ `adhoc`（活期用户）。
  客户表单「销售和采购 → 销售」里可选；客户搜索也加了「包月用户 / 活期用户」过滤。
* `customer.machine.customer_type`：`related + store`，机台表单上**可直接修改**。
  * 关键点：Odoo 里 related 字段默认就是 `readonly=True`（`fields.py`：
    `attrs['readonly'] = attrs.get('readonly', True)`），必须在字段定义上显式写
    `readonly=False`，`setup_related` 才会给它装上 inverse（判断条件是
    `not (self.readonly or 目标字段只读)`）；有了 inverse，在机台表单改类型才会反写到
    `res.partner.customer_type`。第一版漏了这个参数，界面上就是个灰掉、选不了的下拉。
  * 语义：客户类型属于**客户档案**，同一客户名下机台共用；在机台上改会同步到客户
    （并触发活期归一化），在客户表单改则机台这边自动跟着重算（related 依赖）。
  * 新建机台：先选客户，类型自动带出；也可以直接在下拉里换（保存时写回客户）。
* **活期用户的效果**：
  * 机台上的「包月单价 / 包月生效日期 / 包月中」变灰（`readonly`），不参与计费；
  * 器件基线**全部强制「另计费」**——不管是从模板载入、手工加行、批量复制机台，
    还是客户类型从包月改成活期（`res.partner.write` 里同步名下机台，见
    `customer.machine._normalize_adhoc`），都会被拉回 `billing_type='extra'`；
  * 整列设置里选「含在包月内」也会被忽略（活期没有包月）；
  * `machine.monthly.billing.wizard` 的包月账单会跳过活期机台。
* 改回「包月用户」不会自动填回包月单价（金额只能人工确认），器件行保持原值。

### 2. 器件基线「整列设置」

* 向导 `machine.billing.column.wizard`，入口：客户机台表单按钮 **「整列设置计费」**。
* 可勾选要设置的项：计费方式 / 另计单价 / 保修月数；点「应用到全部行」一次刷完所有器件行。
* **刷完仍可逐行微调**（个别板件价格/保修不同），这正是这一版的诉求：
  先整列铺一遍默认值，再单独改例外。
* 默认值取第一行现有值；动作完成后在机台 chatter 留痕（写了几行、改了什么）。

### 3. 发货：一个快递单号可合并多张工单

新模型：

| 模型 | 说明 |
|---|---|

* `machine.repair.shipment`（维修发货单）：`name`（`FH2026-0001` 序列）、`tracking_no` 快递单号、
  `carrier` 快递公司、`ship_date`、`user_id`、`order_ids`（可多张工单）、`photo_ids`、`note`；
  继承 `mail.thread`（有 chatter）。有工单的发货单禁止删除（删了就查不到单号与照片）。
* `machine.shipment.photo`（包装照片，可多张）：`image`（1600px 内）、`name`、`sequence`。

工单侧新增字段：`shipment_id` / `shipped` / `ship_status`（未发货·已发货，store 计算，可搜索）/
`tracking_no` / `ship_date` / `ship_photo_count` / `warranty_start_pending`。

发货规则（`machine.repair.shipment._ship_orders`）：

* 只有**已完工**（done / 接力完工）且**没发过货**的工单能发货；
* 必须扫/填快递单号，且**至少 1 张包装照片**；
* 一个快递单号可以寄多块板 → 多张工单进同一张发货单；
* 发货后工单写回发货单、显示「已发货 + 快递单号」，chatter 留痕。

入口：

* 后台顶部菜单 **发货提醒**（= 已完工未发货工单）/ **发货记录**（发货单列表）；
* 工单列表顶部 **「合并发货」**（勾选多行后点）、工单表单顶部 **「发货（快递单号 + 包装照片）」**；
  两者都走向导 `machine.repair.ship.wizard`（多选工单 + 单号 + 多张包装照片，一次提交）；
* 工单搜索加了「未发货 / 已发货」过滤，大厅列表加了发货状态徽标。

### 4. 活期客户 + 首次维修：保修从**发货时间**起算

* 完工时（`action_repair_done`）先判断：活期客户 + 本单是该板件的**第一次**维修（`repair_round == 1`）
  + 收费维修 → 只打 `warranty_start_pending` 标记，**不**按完工日写保修；
* 确认发货时（`_mark_shipped` → `_start_warranty_from_shipment`）才把
  `machine.board.qr.warranty_start_date` 写成**快递发货日期**，保修月数取本单「本次保修月数」；
* 手机端建单页（活期客户）可以改本次保修月数（1–12），随建单一起提交；包月客户维持原口径（完工日重新起算）。

### 5. 发货提醒（每天 9 点 / 15 点）

* 定时任务 `ir_cron_repair_ship_reminder` 每 15 分钟跑一次，只在**本地时间 9 点 / 15 点那一小时**里
  给「已完工未发货」的工单写一条动态：
  `【发货提醒 · 上午 9 点】本单已完工，还没有发货。请管理员安排快递…`
* 去重：工单上的 `ship_reminder_date` + `ship_reminder_slot`（am/pm），同一时段只提醒一次；
* 时区：公司时区 → 系统参数 `machine_repair.ship_reminder_tz` → 默认 `Asia/Shanghai`
  （定时任务跑在 OdooBot 下，它没有个人时区，直接用 `context_timestamp` 会退化成 UTC，
  提醒会跑到北京时间 17 点 / 23 点）；
* 工单动态（手机端「消息 → 工单动态」）里能看到这条提醒，列表上也有「未发货」徽标。

### 6. 手机端：消息分栏 工单动态 / 讨论 / **开票** / **发货**

* **开票**：所有已完工、没有开票的工单；会计/管理员可直接点「开票」，师傅只看得到列表；
* **发货**：所有已完工、没有发货的工单；勾选（支持全选/清空）后点「合并发货（已选 N）」：
  * 快递单号：手填 / 粘贴 / **扫快递单**（App 用原生扫码；浏览器借扫码页摄像头，扫到自动回到发货页）；
  * 包装照片：拍照（单张）或从相册多选，页内压缩后预览，可删除；
  * 点「确定发货」→ 一次 multipart 提交（多单 + 单号 + 多图），服务端建发货单并回写工单。
* 工单详情新增「发货」卡片：未发货给「去发货」按钮；已发货显示发货单、快递单号、快递公司、
  发货时间和包装照片（可放大/下载）；活期首次维修显示「保修从发货快递时间起算」。
* 工单列表行显示「未发货 / 已发货 + 快递单号」。
* 页面版本 v37。

### 7. 接口

| 路由 | 说明 |
|---|---|
| `POST /machine_repair/mobile/ship_confirm` | 多选工单 + `tracking_no` + `carrier` + 多个 `photo` 文件 → 建发货单并确认发货 |
| `GET /machine_repair/mobile/ship_photo/<id>` | 包装照片（缩略图/原图） |
| `POST /machine_repair/mobile/my_orders` | 新增 `groups.invoice` / `groups.ship` 与对应 counts |
| `POST /machine_repair/mobile/order_detail` | 新增 `shipment`（单号/时间/照片）、`warranty_pending`、`shipped` |
| `POST /machine_repair/mobile/create_order` | 活期客户可带 `warranty_months`（1–12） |

### 8. 验证（ORM，独立进程，末尾回滚）

```
PASS mobile template renders len=157693（含 开票/发货 分栏、发货页、保修月数下拉）
PASS bulk billing_type=extra / extra_price=7 / warranty=6   → 整列设置生效
PASS per-line override works                                → 整列之后仍可单行改
PASS machine customer_type follows partner                  → 客户类型同步
PASS customer_type is writable related (readonly=False + inverse) → 机台表单可选/可改
PASS partner follows machine form edit                      → 机台上改类型反写客户档案
PASS create with customer_type=adhoc writes partner         → 新建时选类型也生效
PASS monthly fields cleared fee=0.0 active=False            → 活期包月字段清零
PASS all lines forced extra / 新行也变另计 / 整列改不回月付
PASS shipment created（FH…）+ 2 张包装照片 + 多工单挂同一发货单
PASS orders marked shipped / ship_status=shipped
PASS double-ship blocked / 未完工不能发货 / 没照片不能发货
PASS first adhoc repair -> warranty pending
PASS warranty start = ship date（=发货日期）/ warranty months from order（3）
PASS ship reminder cron runs sent=6 / 提醒标记写入
PASS 13 个视图（工单/机台/发货单/发货向导/整列设置/客户）全部能编译
PASS 菜单·动作·定时任务·序列 全部创建
```

实测发货提醒：北京时间 09:5x 的定时任务为 8 张已完工未发货工单写入了提醒，
文案为 `【发货提醒 · 上午 9 点】本单已完工，还没有发货。…`；同一时段内重复运行不再重复提醒。

> 注意：`message_post` 在这个版本里会对 body 做转义，**不要**在提醒文案里写 `<b>` 之类的 HTML
> （会显示成字面量），需要强调就用「」或纯文本。

## 维修视频：本地相册上传 + 转发微信/QQ（2026-10-23）

### 1. 上传：录制 / 相册两条入口

以前只有一个 `<input type=file accept="video/*" capture="environment">`，手机上只能录制；
Android App 的 `onShowFileChooser` 更彻底 —— 只要 accept 是 video 就强行换成 `ACTION_VIDEO_CAPTURE`，
所以「从相册选视频」在任何情况下都打不开。现在：

* 网页「维修视频」卡片：**录制视频**（带 capture，走相机）+ **从相册 / 文件上传**（不带 capture，`multiple`）；
* 上传逻辑抽成 `uploadVideoFile(file)` / `uploadVideoFiles(files)`：逐段读时长（≤5 分钟）、逐段提交
  `/machine_repair/mobile/upload_video`，一次最多 3 段；服务端单段上限 80MB → **200MB**（时长仍限 5 分钟）；
* App 侧 `onShowFileChooser` 改为按 `FileChooserParams.isCaptureEnabled()` 判断：
  带 capture 才走录制，不带 capture 交给 `params.createIntent()`（系统文件选择器，支持多选）。

### 2. 转发：三条路径，转发的是视频文件

| 场景 | 行为 |
|---|---|
| App 内 | `AndroidScanner.shareVideo(url, name)`：带 Cookie 下载 → `content://`（自写 `ShareFileProvider`）→ `ACTION_SEND` 系统转发面板（微信 / QQ / 企业微信都在列表里） |
| Chrome / Safari | `navigator.share` Level 2 带 **File** 分享；文件太大或没预取时退回链接分享 |
| 微信 / QQ 内置浏览器 | 不支持 Web Share，弹面板：**下载视频** + 复制链接 + 步骤说明（下载后去聊天里选相册发送） |

实现细节：

* Android 7+ 不允许 `Uri.fromFile` 共享文件（`FileUriExposedException`），项目又不依赖 androidx，
  所以自己写了最小 `ShareFileProvider`（`content://com.odoo.machinerepair.files/share/<文件名>`，
  `exported=false`、只读、靠 `FLAG_GRANT_READ_URI_PERMISSION` 临时授权）；
* 下载走 `HttpURLConnection` 并带上 `CookieManager.getCookie(url)`，否则视频接口 302 到登录页；
* 原生转发结果用 `window.onNativeShare(ok, msg)` 回传，网页 toast 提示；
* App 版本 v1.13 → **v1.14**（versionCode 14 → 15），网页版本 v37 → **v38**。

### 3. 验证

* 模板渲染通过，页面含 `btn-video-record` / `btn-video-upload` / `video-record-input` /
  `multiple` 输入框 / `onNativeShare` / `NATIVE.shareVideo` / 降级下载面板；
* 抽出的整段内联 JS 用 `node --check` 校验通过（131 KB）；
* Android 侧用 `aapt2 + javac + d8 + apksigner` 重新打包 v1.14 成功，
  `aapt2 dump` 确认 manifest 里有 `ShareFileProvider`，dex 里能查到 `ShareFileProvider` /
  `shareVideo` / `onNativeShare`；产物已覆盖 `D:\Odoo\dist\机台维修App-v1.15.apk`。

### 9. 视频上传 / 转发：圆环进度 + “选了没反应”修复（2026-10-24，页面 v39 / App v1.15）

**症状**：从相册选好视频点确定后，既不提示成功也不提示失败，列表里也不出现。

**根因**：`change` 处理里先 `ev.target.value = ''` 再调用上传函数，传进去的是**同一个 FileList**；
部分 WebView 清空 value 时会把 FileList 一并清空，上传函数拿到空列表后直接 `return` ——
所以完全没有反馈。修复：先 `Array.prototype.slice.call(ev.target.files || [])` 复制成数组，再清空 input。

**进度显示**：

* 新增 `#progress-mask`（SVG 圆环，`stroke-dashoffset` 按百分比变化）+ `showProgress / setProgress / hideProgress`；
* 上传改用 `XMLHttpRequest` + `xhr.upload.onprogress`，显示「第 1/2 段 · 1.2 MB / 8.4 MB」和百分比；
* App 内转发：Java 下载视频时每 300ms（且百分比变化）回调 `window.onNativeShareProgress(pct)`，
  拿不到 Content-Length 时传 -1（只显示“正在下载视频到手机…”）；下载完打开系统转发面板并 `hideProgress()`；
* 上传结束一定会给反馈（成功 / 具体错误），并且无论成败都刷新一次详情。

**版本**：网页 v38 → v39；Android `versionCode 15 → 16`、`versionName 1.14 → 1.15`、
`AndroidScanner.version()` → `native-1.15`。APK 已覆盖 `D:\Odoo\dist\机台维修App-v1.15.apk`。

### 10. APK 发布命名（2026-10-24）

以前每次构建都覆盖 `D:\Odoo\dist\机台维修App-最新版.apk`，看不出版本、也没法回滚。
现在 `build-apk.ps1`：

* **文件名带版本号**：`D:\Odoo\dist\机台维修App-v1.15.apk`；版本号**自动取自**
  `AndroidManifest.xml` 的 `versionName`（也可 `-Version 1.16` 手动指定）；
* 出新版时旧包自动移进 `D:\Odoo\dist\archive\`，dist 根目录永远只有当前这一版；
* 历史固定名 `机台维修App-最新版.apk` 也自动归档，不再生成；
* 内部分发目录 `mobile-android\dist\jiTaiWeiXiu-<版本>.apk` 保持不变。
