# machine_repair — 纺织机台维修管理

自定义 Odoo 20 模块，实现《机台维修业务管理方案》的 6 个阶段。
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
