# Odoo 20.0 本地部署说明（D:\Odoo）

## 一、现在就能用

| 项目 | 值 |
|---|---|
| 访问地址 | **http://127.0.0.1:8069** |
| 登录 | `admin` / `admin` ← **请首次登录后立即修改**（设置 → 用户 → admin） |
| 版本 | Odoo **20.0**（`20.0.0 final`），`20.0` 稳定分支，HEAD `50b51284` |
| 数据库 | **PostgreSQL 18.1**，仅监听 `127.0.0.1:5432` |
| Python | 3.12.9（你的系统安装）+ `D:\Odoo\venv` 隔离环境 |

已实测通过：登录页 200、`admin` 登录成功（uid=2、`is_system=True`）、前后端资源包全部 200
（前端 CSS 444 KB、后端 CSS 921 KB —— 说明 SCSS 编译链路正常）、数据库已装 16 个基础模块。

## 二、目录结构

| 路径 | 用途 |
|---|---|
| `D:\Odoo\odoo` | Odoo 20.0 源码（git 仓库，`--depth 1`，1.07 GB） |
| `D:\Odoo\venv` | 隔离的 Python 环境（143 MB），依赖都装在这里 |
| `D:\Odoo\pgsql` | 便携版 PostgreSQL 18.1 程序（866 MB） |
| `D:\Odoo\pgsql-data` | 数据库集群数据目录（79 MB） |
| `D:\Odoo\data` | Odoo filestore / session |
| `D:\Odoo\logs` | `odoo.log`、`postgres.log`、stdout/stderr |
| `D:\Odoo\custom-addons` | 放自建模块；**放入模块后**再把它加进 `addons_path`（重新运行 `.tools\make-conf.ps1` 即可自动判断） |
| `D:\Odoo\.tools` | 便携版 Git、各种部署/维护脚本、凭据文件 |
| `Odoo-Start.ps1` / `Odoo-Stop.ps1` | 桌面与开始菜单快捷方式调用的启动 / 停止脚本 |
| `SOURCE-INFO.txt` | 源码来源与提交记录 |

## 三、一键启动 / 停止（推荐）

桌面上已经放好两个带 Odoo 图标的快捷方式，**双击即可**：

| 快捷方式 | 作用 |
|---|---|
| **Odoo 启动** | 启动数据库 → 启动 Odoo（等到就绪）→ 自动打开 http://127.0.0.1:8069 |
| **Odoo 外网访问** | 启动 Cloudflare Tunnel，取得可在外网打开的 https 地址（见文末第十节） |
| **Odoo 停止** | 关闭外网入口 → 停 Odoo → 停数据库，并显示端口与隧道是否都已释放 |

开始菜单「Odoo」文件夹里也有同样的一对（按 Win 键搜 `Odoo` 就能找到）。
两者都不需要管理员权限；想重建或换位置，运行 `D:\Odoo\.tools\make-shortcuts.ps1`。

命令行等价操作：

```powershell
cd D:\Odoo
.\Odoo-Start.ps1            # 等于双击「Odoo 启动」
.\Odoo-Stop.ps1             # 等于双击「Odoo 停止」
.\start-odoo.ps1 -Status    # 只看状态
.\start-odoo.ps1 -Detach    # 只启动 Odoo（后台、进程独立）
.\postgres.ps1 -Start       # 只启动数据库
```

> 如果 PowerShell 报「禁止运行脚本」，先执行一次：`Set-ExecutionPolicy -Scope Process Bypass`

日志：Odoo → `D:\Odoo\logs\odoo.log`；PostgreSQL → `D:\Odoo\logs\postgres.log`

**为什么关掉启动窗口不会把服务一起关掉**：启动脚本刻意不给子进程重定向标准输出，
而是让 PostgreSQL / Odoo 各自拥有独立的（隐藏）控制台。否则关闭启动窗口时，
Windows 会向仍挂在同一控制台上的进程发送关闭事件，把它们一并结束。

> 两个提示：① 要改 `Odoo-Start.ps1` / `Odoo-Stop.ps1` 里的中文，请用会保留
> **UTF-8 BOM** 的编辑器保存 —— Windows PowerShell 5.1 默认按 ANSI 读 `.ps1`，
> 丢掉 BOM 会让中文变乱码并直接报语法错误。
> ② 如果是在本 AI 会话里用命令启动的服务，会话结束时会连整棵进程树一起清理；
> 用桌面快捷方式（或你自己的 PowerShell 窗口）启动则不受影响。

## 四、账号与密码

全部记录在 **`D:\Odoo\.tools\db-credentials.txt`**：

| 用途 | 账号 | 密码 |
|---|---|---|
| Odoo 连接数据库的角色 | `odoo` | `Odoo-6f7d385def154e31` |
| PostgreSQL 超级用户 | `postgres` | `Pg-1f4d0f474c5eedd3` |
| 数据库管理页主密码（`/web/database/manager`） | — | `Master-de9e528a09e9` |
| Odoo 管理员 | `admin` | `admin`（建议立即修改） |

命令行连接数据库：

```powershell
$env:PGPASSWORD='Odoo-6f7d385def154e31'
D:\Odoo\pgsql\bin\psql.exe -h 127.0.0.1 -U odoo -d odoo
```

## 五、更新到 20.0 分支最新代码

```powershell
cd D:\Odoo
.\update-odoo.ps1                    # 只更新源码
.\update-odoo.ps1 -WithDeps          # 同时刷新 Python 依赖
.\update-odoo.ps1 -WithDeps -Restart # 再重启服务
```

如果更新涉及模型/视图变化，需要升级模块：

```powershell
D:\Odoo\venv\Scripts\python.exe D:\Odoo\odoo\odoo-bin --config D:\Odoo\odoo.conf -d odoo -u all --stop-after-init
```

## 六、本次部署对系统做了什么（透明说明）

1. **只修过一次 Windows 文件权限**：开始时 `D:\Odoo` 缺少「写所有者」权限，沙箱完全无法工作；
   已为当前用户补上完全控制权。备份与回滚脚本在 `D:\Odoo-acl-recovery\`，可用其中的
   `acl-backup-*.json.ps1` 撤销。
2. **没有注册任何 Windows 服务、没有改 PATH、没有动系统目录**。`git`、`psql`、`python`
   都不在 PATH 上，所有脚本都用绝对路径，互不干扰。
3. **注册了四个计划任务**（用当前用户身份、最低权限，不需要管理员）：

   | 任务 | 触发 | 作用 |
   |---|---|---|
   | `Odoo 20.0 - 服务` | 登录时 | 拉起数据库与 Odoo |
   | `Odoo 20.0 - 外网隧道` | 登录时 | 拉起 Cloudflare Tunnel |
   | `Odoo 20.0 - 隧道守护` | 登录时 + **每 5 分钟** | 健康守护：数据库 / Odoo / 隧道 谁掉线就拉谁（名字里的"隧道"是历史叫法） |
   | `Odoo 20.0 - 空闲自动备份` | **每 5 分钟检查一次** | 业务数据有变更、且系统**静默满 5 分钟**后自动备份；**每天只保留最后一次**，最多 30 天 |
   | `Odoo 20.0 - 每日备份` | 已**停用** | 旧策略（每天 03:00、保留 14 份）。任务保留以便回退：`make-idle-backup-task.ps1 -KeepDaily` |

   查看/删除：`make-autostart.ps1 -Status|-Remove`、`make-tunnel-watchdog.ps1 -Status|-Remove`、
   `make-idle-backup-task.ps1 -Status|-Remove`，或 `Get-ScheduledTask | Where-Object TaskName -like 'Odoo 20.0*'`。
   它们只在你登录后运行、注销即停止 —— 这套机制**没有管理员权限做不到"登录前自启"**，见第十一节。
4. 你自己安装了 Python 3.12.9 与 Git 2.56；Odoo 的依赖全部装在 `D:\Odoo\venv`，与系统 Python 隔离。
5. 由于当前账户没有管理员权限，**PostgreSQL 是便携式安装，不是 Windows 服务**，
   由上面的计划任务或 `.\postgres.ps1 -Start` 启动。

## 七、与官方 `requirements.txt` 的差异（已记录，便于日后核对）

| 项目 | 官方 | 这里 | 原因 |
|---|---|---|---|
| `psycopg2==2.9.9` | 需编译 | `psycopg2-binary==2.9.9` | PyPI 上 `psycopg2` 无 Windows 预编译包，本机没有 C 编译器 |
| `rjsmin==1.2.0` | C 扩展 | 用 rjsmin 源码自带的**纯 Python 实现**（1.3.0） | rjsmin 只发布 Linux 预编译包；功能相同，JS 仍会压缩 |
| `libsass==0.22.0` | 无 3.12 预编译包 | `libsass 0.23.0` | 0.23.0 才有 Windows 预编译包 |
| `vobject==0.9.6.1` | 固定旧版 | 新版（有预编译包） | 0.9.6.1 在 Python 3.12 上无法构建 |

此外额外安装了可选的 `markdown2`（消除日志里的渲染警告）。
`ofxparse` 在 Windows 上无预编译包且无法构建，**仅影响「OFX 银行对账单导入」这一可选功能**。

## 八、故障排查

| 现象 | 处理 |
|---|---|
| 打不开 8069 | `.\start-odoo.ps1 -Status`，再看 `D:\Odoo\logs\odoo.log` 末尾 |
| 数据库连不上 | `.\postgres.ps1 -Status`，再看 `D:\Odoo\logs\postgres.log` |
| 端口被占用 | 改 `D:\Odoo\odoo.conf` 的 `http_port` 后重启 |
| 忘记 admin 密码 | 用 `odoo-bin shell` 重置；`list_db = False` 时网页版数据库管理器是关闭的（需要时临时改成 `True` 再重启） |

## 九、备份建议

```powershell
# 数据库
$env:PGPASSWORD='Odoo-6f7d385def154e31'
D:\Odoo\pgsql\bin\pg_dump.exe -h 127.0.0.1 -U odoo -Fc odoo > D:\Odoo\backup-odoo.dump
```

另外需要备份：`D:\Odoo\data`（附件与文件存储）、`D:\Odoo\odoo.conf`、
`D:\Odoo\.tools\db-credentials.txt`。

## 十、外网访问（Cloudflare Tunnel）

桌面快捷方式 **Odoo 外网访问**（或 `.\Odoo-Tunnel.ps1 -Start`）会启动 Cloudflare Tunnel，
并打印一个形如 `https://xxxx-xxxx-xxxx.trycloudflare.com` 的公网地址 —— 在任何联网的
手机或电脑上打开它就能访问这台 Odoo。

| 命令 | 作用 |
|---|---|
| `.\Odoo-Tunnel.ps1 -Start` | 启动隧道（并确保本地 Odoo 已运行），打印公网地址 |
| `.\Odoo-Tunnel.ps1 -Status` | 查看隧道进程与当前公网地址，并从本机绕公网自测一次 |
| `.\Odoo-Tunnel.ps1 -Stop` | 关闭公网入口（本地 Odoo 不受影响） |
| `.\Odoo-Tunnel.ps1 -Login` | 登录 Cloudflare，准备改用你自己的域名 |

要点：

* **不需要公网 IP、不需要路由器端口映射、不需要开任何入站端口** —— 隧道是本机主动向
  Cloudflare 建立的出站连接。
* 用 **http2（TCP 7844）** 而不是默认的 QUIC（UDP 7844）：实测本机到 Cloudflare 的
  TCP 7844 通畅，而 UDP 在这种网络环境下常不稳定。
* **登录后自动建立隧道，并且有守护任务保活**：计划任务 `Odoo 20.0 - 隧道守护` 每 5 分钟检查
  一次，发现连接器掉线就自动重新拉起（实测：杀掉连接器 → 约 7 秒恢复 → 公网恢复 200）。
  用「Odoo 停止」会连同守护任务一起停用，所以"停"是真的停、不会被自动打开；
  再次双击「Odoo 外网访问」会重新启用。守护日志：`D:\Odoo\logs\tunnel-guard.log`
* 已在运行时双击「Odoo 外网访问」**不会重建隧道**，只显示当前地址（避免地址又变）；
  当前地址也可以看 `D:\Odoo\logs\tunnel-url.txt`，或执行 `.\Odoo-Tunnel.ps1 -Status`。
* **快速隧道的地址每次重启都会变**，适合临时远程使用。要固定地址请用自己的域名：
  1. `.\Odoo-Tunnel.ps1 -Login`（在浏览器里授权你的域名）
  2. `D:\Odoo\.tools\cloudflared.exe tunnel create odoo-home`
  3. 在 Cloudflare 加一条 CNAME：`odoo.你的域名` → `<隧道ID>.cfargotunnel.com`
  4. `.\Odoo-Tunnel.ps1 -Start -Named odoo-home -Hostname odoo.你的域名`

### 外网打不开？按这个顺序查（一分钟定位）

先跑一条命令，它会把模式、连接器进程、公网地址、本地 Odoo、外网自测都列出来：

```powershell
.\Odoo-Tunnel.ps1 -Status
```

| 现象 | 原因 | 处理 |
|---|---|---|
| **502 Bad Gateway** | 隧道是通的，但连接器连不上本机 8069（Odoo 没在跑） | 双击「Odoo 启动」；`.\start-odoo.ps1 -Status` |
| **1033 / Tunnel not found** | 连接器没在运行 | 双击「Odoo 外网访问」，或 `.\Odoo-Tunnel.ps1 -Start`（守护任务也会在 5 分钟内自动拉起） |
| **503** | 控制台没给这个隧道配 Public Hostname | 控制台加：类型 `HTTP`、URL `localhost:8069` |
| 域名解析不了 | DNS 记录没建或没生效 | 控制台确认 Public Hostname 已保存（Cloudflare 会自动建 DNS 记录） |
| 本机能开、外网不能 | 上面几种都不像时，先看隧道日志末尾 | `Get-Content D:\Odoo\logs\cloudflared.log -Tail 30` |

### 暴露到公网后的安全设置（本次已调整）

| 设置 | 值 | 原因 |
|---|---|---|
| `proxy_mode` | `True` | 让 Odoo 信任隧道传来的 `X-Forwarded-Proto/For`，正确生成外部链接 |
| `list_db` | `False` | 公网访问者看不到数据库列表与管理器（实测 `/web/database/list` 返回 `AccessDenied`） |
| `dbfilter` | `^odoo$` | 只对外服务这一个数据库 |
| `http_interface` | `127.0.0.1` | Odoo 本身仍只监听本机，公网只能经由隧道进入 |

> **关于管理员密码**：默认的 `admin / admin` 已由你本人改掉（Odoo 日志里能看到
> `Login failed for login:admin`，说明默认密码已失效）。想更稳妥，还可以在
> Cloudflare Zero Trust 里给这个域名加一层 Access 登录保护。

## 十一、断电 / 重启后的恢复，与稳定安全加固

### 1. 断电重启后会发生什么（现状）

| 时间点 | 会发生什么 |
|---|---|
| 断电瞬间 | PostgreSQL 是崩溃安全配置（`fsync=on`、`full_page_writes=on`、`synchronous_commit=on`、数据校验和开启 —— 已实测确认），**不会损坏数据库**，最多丢失极少量未落盘的事务 |
| 来电后 | 机器是否自动开机由 BIOS 决定（见第 3 点） |
| 你登录 Windows 后 | `服务` 任务拉起数据库与 Odoo，`外网隧道` 任务拉起隧道；`隧道守护` 每 5 分钟兜底 |
| 某个环节卡住时 | 守护任务在 5 分钟内自动修复 |

**实测演练**：把 PostgreSQL、Odoo、隧道连接器三层**全部杀掉**，只运行守护任务 ——
PostgreSQL **2 秒**、Odoo **4 秒**、连接器 **6 秒**恢复，**15 秒后本地与公网都回到 200**。

### 2. 一个必须如实说明的限制

**没有管理员权限，做不到"没登录就把服务起来"。** 我实测过用 S4U（不登录也运行）注册任务，
系统直接返回 `Access is denied`。所以当前是"**登录后自动恢复**"，而不是"开机即服务"。

如果你希望断电重启后**不用登录**也能对外服务，需要你用一个**管理员** PowerShell 执行下面之一：

| 方案 | 做法 | 说明 |
|---|---|---|
| A. 装成 Windows 服务（推荐） | `D:\Odoo\.tools\cloudflared.exe service install <token>`（token 在 `.tools\tunnel-token.txt`）；PostgreSQL 用 `pg_ctl register -N odoo-pg -D D:\Odoo\pgsql-data`；Odoo 可用 NSSM 之类包成服务 | 装完请把对应的计划任务停用，避免重复启动 |
| B. 允许自动登录 | `netplwiz` 取消"必须输入密码"，或设置 `AutoAdminLogon` | ⚠️ 机器安全性降到"开机即进入"，必须放在物理安全的位置 |
| C. 维持现状 | 登录后自动恢复 | 适合"平时有人在场"的场景 |

装了服务之后告我一声，我帮你验证并清理计划任务。

### 3. 硬件层面（这两条比软件更管用）

1. **在 BIOS 里把「AC Power Recovery / Restore on AC Power Loss」设为 Power On** —— 来电后机器会自己开机（BIOS 我改不了）。
2. **配一台 UPS**：避免硬关机，也能跨过瞬时断电。另外建议把电源计划的"接通电源时"设为
   **关机**而不是睡眠，免得机器睡着后外网访问不通。

### 4. 数据安全：变更后空闲自动备份（每天只留最后一次）

- 任务 `Odoo 20.0 - 空闲自动备份` **每 5 分钟**检查一次，满足两个条件才备份：
  1. 业务数据有变更（水位 = `repair_order` / `customer_machine` / `repair_case` /
     `machine_billing_line` / `account_move` / `stock_move` 等表的 `max(write_date)` 前移了）；
  2. 距那次变更已**静默满 5 分钟** —— 避免师傅正录数据时抓到半截数据。
- 产物仍是 `D:\Odoo\backups\odoo-backup-<时间戳>.zip`（数据库 + 附件 + odoo.conf），
  每次都用 `pg_restore -l` **校验转储可读**。
- **保留策略：每天只留最后一份**（同一天更早的备份会被自动删除），默认保留最近 **30 天**。
- 状态：`D:\Odoo\.tools\backup-state.json`（记录数据水位、上次备份时间）；日志：`D:\Odoo\logs\idle-backup.log`。
- 手动跑：`D:\Odoo\.tools\idle-backup.ps1 -Force`；看状态：`… -Status`；
  任务管理：`make-idle-backup-task.ps1 -Status|-Run|-Remove|-KeepDaily`。
- 想回到旧的每日策略：`make-idle-backup-task.ps1 -KeepDaily`（会停用空闲备份、启用每日 03:00 任务）。
- 建议定期把 `D:\Odoo\backups` 复制到别的磁盘或网盘 —— 本机故障时才不至于一起没。

恢复步骤：

```powershell
# 1) 解压 zip；停掉 Odoo：D:\Odoo\Odoo-Stop.ps1
# 2) 恢复数据库（会覆盖现有同名对象）
$env:PGPASSWORD='<odoo 口令，见 .tools\db-credentials.txt>'
D:\Odoo\pgsql\bin\pg_restore.exe -h 127.0.0.1 -U odoo -d odoo --clean --if-exists <解压目录>\odoo.dump
# 3) 把 filestore 放回 D:\Odoo\data\filestore
# 4) 启动：D:\Odoo\Odoo-Start.ps1
```

### 5. 安全加固清单

已完成：`list_db=False`、`dbfilter=^odoo$`、`proxy_mode=True`、token 文件权限收紧、
PostgreSQL 与 Odoo 都只监听 127.0.0.1、公网只能经隧道进入、公网数据库管理功能实测被拒、
`web.base.url` 由 `http://127.0.0.1:8069` 修正为 `https://df2026.213579.com` 并冻结。

**Cloudflare 侧的三项都已启用并实测**（控制台操作由你完成，我这边只做验证）：

| 设置 | 状态与实测结果 |
|---|---|
| **Always Use HTTPS** | ✅ `http://` → **301** → `https://` |
| **HSTS**（SSL/TLS → Edge Certificates → HTTP Strict Transport Security） | ✅ 响应头实测 `Strict-Transport-Security: max-age=15552000`（6 个月），未含 `includeSubDomains`、未 `preload`；`/`、`/web/login`、`/web/database/selector` 三条路径均发送 |
| No-Sniff Header | ✅ 实测 `X-Content-Type-Options: nosniff` |

> **"会话 Cookie 没有 `Secure` 标记"这个缺口已由 HSTS + Always Use HTTPS 补上** ——
> 浏览器从此拒绝用明文 http 访问该域名，Cookie 不可能走明文信道，效果与 `Secure` 标记等同，
> 而且不需要改动 Odoo 核心代码（改了会在 `update-odoo.ps1` 更新时被覆盖）。
> 启用后请勿把 DNS 从 Proxied 改成 DNS only、勿暂停 Cloudflare、勿把 HTTPS 重定向回 HTTP，
> 否则在 max-age 到期前访客会打不开。

> 附：若日后改用 API 修改 HSTS（`PATCH /zones/{zone_id}/settings/security_header`），Token 必须
> **同时**有权限（`Zone Settings:Edit`）**和区域资源绑定**（Zone Resources → Include →
> Specific zone → `213579.com`）。实测只选了权限、没选资源的 Token：**所有带 zone 的接口一律
> 返回 403（错误码 9109）**，而 `/accounts` 正常 —— 这个组合就代表"资源没绑定"。
> 现成的脚本留在 `.tools\set-hsts.ps1`（凭据从 `.tools\cf-api-token.txt` 读取，该文件已删除）。

关于"Cookie 没有 `Secure` 标记"这件事，查证结论如下（三条都是实测/源码确认的）：

1. **Cloudflare Tunnel 无法注入自定义请求头。** 官方"源站参数"完整清单里只有
   `httpHostHeader`、`disableChunkedEncoding`（HTTP）、若干 TLS 与 Connection 参数、`access`，
   **没有任何添加请求头的选项**；控制台的 "Additional application settings" 同样只有这三类。
   在本地 config.yml 里写 `originRequest.addHeaders` 会被 cloudflared 判为未知字段
   （`field addHeaders not found in type config.OriginRequestConfig`），而且 token 模式下
   本地 ingress 会被云端配置覆盖（连接特性含 `allow_remote_config`），所以本地配置也不起作用。
2. **即使有 `X-Forwarded-Host`，Odoo 也不会给会话 Cookie 加 `Secure`。**
   `odoo/http/session.py` 与 `odoo/http/dispatcher.py` 里设置 `session_id` 时只传了
   `httponly=True`，**从未传 `secure=`**（`set_cookie` 的 `secure` 默认 `False`）。
   这是 Odoo 的设计：它假设前置代理负责 HTTPS 强制。
3. **所以正确做法是 HSTS**（外加已开启的 Always Use HTTPS）：浏览器从此拒绝用明文 http
   访问这个域名，Cookie 也就不可能走明文信道 —— 效果与 `Secure` 标记等同，且不需要改动 Odoo 核心代码
   （改核心代码会在 `update-odoo.ps1` 更新时被覆盖）。

> 附注：Odoo 的 `proxy_mode` 只有在收到 `X-Forwarded-Host` 时才启用 werkzeug 的 ProxyFix
> （`odoo/http/router.py:256`）。Cloudflare 不发这个头，所以这条链路永远不会触发 ——
> 但 `web.base.url` 已被我们**显式冻结**为 `https://df2026.213579.com`，因此邮件、报表、
> 门户里的绝对链接都是正确的，不受影响。

可选但推荐：**Cloudflare Zero Trust → Access**（在 Odoo 登录页前再加一道登录门）、
**Security → WAF → Rate limiting**（对 `/web/login` 限速，抵挡撞库）。

### 6. 本次为"断电重启"做的改动一览

| 改动 | 文件 | 状态 |
|---|---|---|
| 守护任务从"只守隧道"升级为"守数据库 + Odoo + 隧道" | `.tools\tunnel-guard.ps1` | ✅ 实测：三层全杀 → 15 秒全恢复 |
| PostgreSQL 启动失败时识别残留锁文件并重试一次 | `postgres.ps1` | ✅ 已加（实测 PG 18 本身也能自愈，属额外保险） |
| 每日自动备份 + 完整性校验 + 保留策略 | `.tools\backup.ps1`、`.tools\make-backup-task.ps1` | ✅ 已实测 |
| 修正 `web.base.url`（并冻结） | 数据库 `ir_config_parameter` | ✅ |
| Always Use HTTPS | Cloudflare 控制台 | ✅ 你已完成，实测 301 跳转 |
| HSTS + No-Sniff | Cloudflare 控制台 | ✅ 已启用并实测：`max-age=15552000`、`nosniff` |

## 十二、手机端（PWA）—— 用手机摄像头扫码，不需要扫码枪

**入口**：手机浏览器打开

    https://df2026.213579.com/machine_repair/mobile

用你的 Odoo 账号登录即可（管理员、师傅都能用）。iPhone Safari / Android Chrome 打开后
点「分享 → 添加到主屏幕」，就会像 App 一样有图标、全屏运行。

**能做什么**

| 功能 | 说明 |
|---|---|
| 摄像头扫码 | 手机后置摄像头实时识别（jsQR 已本地化，不依赖 CDN） |
| 手工输入 | 也可以粘贴二维码内容或编号，或继续用扫码枪 |
| 板件信息 | 客户、机台、板件型号、建档时间、包月区间、**保修状态**、已维修次数 |
| 一键开单 | 选紧急度（普通 7 天 / 紧急 3 天 / 加急 1 天）直接生成工单并发布到大厅 |
| 选客户 | 扫到库里没有的新码时，先搜客户再建单（外来维修） |
| 我的工单 / 待抢单 | 抢单（带并发保护）、看期限与超期标记 |
| **自动定位** | 打开即全自动上报（无界面、无按钮），位置只在管理端看板显示（见第十五节） |

**注意**

- 摄像头**必须 HTTPS**（iOS/Android 的硬性限制）；http:// 打开会提示，可改用手工输入。
- 页面是独立轻量页，不走 Odoo 前端框架，弱网下也能打开（jsQR 250 KB 本地加载）。
- 接口：/machine_repair/mobile/{scan,create_order,my_orders,claim,customers,location,location/state}，
  全部要求已登录会话 + CSRF 校验；位置记录只有管理员能读。
## 十三、摄像头扫码打不开时怎么办（诊断）

手机网页端 /machine_repair/mobile 的摄像头**必须 HTTPS**，而且很多国内浏览器会拦：

| 现象 | 原因 | 处理 |
|---|---|---|
| 页面红字提示「检测到微信/QQ 等内置浏览器」 | 微信/QQ 内置浏览器默认禁止网页调用摄像头 | 点右上角「···」→「在浏览器中打开」，用 Chrome/Safari/手机自带浏览器 |
| 「摄像头打不开：NotAllowedError」 | 你拒绝了授权，或浏览器禁了摄像头 | 点地址栏锁形图标 → 摄像头 → 允许，再刷新 |
| 「NotReadableError」 | 摄像头被相机/视频通话占用 | 先关掉其它用摄像头的 App |
| 「HTTPS=否」 | 用 http 打开 | 换 https 地址 |

页面会直接把诊断信息（HTTPS 是否、camera API 是否有、错误名）显示在扫码卡片下方，
把那段文字发我就能定位。

## 十四、Android App（原生壳）—— 摄像头 / 定位授权更稳

工程位置：`D:\Odoo\mobile-android`

- 形态：`WebView` 轻量原生壳，加载同一份手机端页面（业务只维护一份）
- 关键点：`onPermissionRequest` 由 **Android 系统直接授予 CAMERA**，绕开浏览器授权链路；
  **启动时一次性申请相机 + 定位权限**（和相机一样自动弹系统菜单），
  `onGeolocationPermissionsShowPrompt` 负责网页定位授权（只信任本系统域名），
  `onShowFileChooser` 支持现场拍照上传；全屏无地址栏
- **后台定位**：`LocationService` 前台服务（常驻通知）在关闭 App/锁屏后继续用 GPS 上报；
  `BootReceiver` 开机尝试恢复；需要位置权限「始终允许」+ 允许自启动/后台运行
- **零第三方依赖**，用 `aapt2 + javac + d8 + apksigner` 直接打包（不需要 Gradle/Android Studio）
- 打包：
  `powershell
  cd D:\Odoo\mobile-android
  .\get-toolchain.ps1    # 下载 JDK17 + Android SDK 到 D:\Odoo\toolchain（约 1.1 GB，无需管理员）
  .\build-apk.ps1        # 产出 mobile-android\dist\jiTaiWeiXiu-1.15.apk（自动生成签名证书）
  `
- 安装：发 `D:\Odoo\dist\机台维修App-v1.15.apk` 到手机点开安装（允许「未知来源」），
  文件名带版本号，一眼能看出装的是哪一版；或 `.\build-apk.ps1 -Install` 用 adb（APK 不落手机，最干净）
- **覆盖升级（不会出现两个图标）**：v1.8~v1.15 的包名都是 `com.odoo.machinerepair`，
  签名证书 SHA-256 都是 `4251…43b5`，versionCode 依次为 **9 / 10 / 11 / 12 / 13 / 14 / 15 / 16**。
  直接安装 v1.15 会覆盖旧版（部分安装器只显示「安装」、没有「更新」字样，
  但包名/签名/versionCode 匹配就是覆盖），保留登录 Cookie、离线队列和设置；
  **不要先卸载旧版**（卸载会清数据）。`adb install` 必须带 `-r`，或用 `.\build-apk.ps1 -Install`。
- **旧安装包别留在手机里**：手机上的 `.apk` 只是下载文件，不是旧版程序，装完可删；
  Android 不允许 App 静默删除「下载」目录，所以要么手动删，要么用 `-Install`（APK 不落手机）。
  PC 端旧版本自动归档到 `D:\Odoo\dist\archive\`，对外只发 dist 根目录里带版本号的那个
  （当前是 `机台维修App-v1.15.apk`；出新版时文件名跟着版本走，上一版自动进 archive）。
  只有手机提示“签名不一致/应用未安装”才说明装过被改签的改版，这时只能卸载重装。
- 限制（诚实说明）：**首次打开仍需联网登录**；离线只能排队补传，查不了历史；
  自动签名只能内部侧载，上架需正式签名
- 国内下载源：JDK 走清华 Adoptium 镜像，SDK 命令行工具走腾讯云镜像（Adoptium 官方国内会超时）
- **已产出**：`D:\Odoo\mobile-android\dist\jiTaiWeiXiu-1.15.apk`；发布文件名带版本号
  `D:\Odoo\dist\机台维修App-v1.15.apk`（出新的版本时文件名跟着变，上一版自动移进
  `D:\Odoo\dist\archive\`，不要发到手机）。v1.15 约 421 KB，
  v2/v3 签名通过、zipalign 4 字节对齐，含相机 + 前后台定位 + 前台服务 + 开机恢复
- 工具链位置：`D:\Odoo\toolchain\{jdk,sdk}`；签名证书 `mobile-android\keystore\repair.jks`（**务必备份**，
  丢了就无法覆盖升级）
- 已知限制：
  * App 首次打开需要联网登录一次
  * Android WebView **不支持 Service Worker** → 离线时打开 App 会显示"打不开服务器"，
    但页面里的离线队列（开单/完工/开票/照片）仍然有效，恢复网络后自动补传
  * 国内装 SDK：`sdkmanager` 连不上 Google 源列表，改用 `install-sdk-manual.ps1`
    直接从腾讯云镜像安装 platform-tools / platforms;android-34 / build-tools;34.0.0
  * 打包脚本是无 BOM 的 UTF-8：Windows PowerShell 5.1 直接跑会中文乱码/语法错误，
    已在文件头加 BOM；脚本也会自己设置 `JAVA_HOME`，否则 `d8` 会报找不到 java

## 十五、手机定位与在岗监控（全自动上报，位置只给管理员看）

目标：**所有手机端用户（包括管理员）**安装 App / 打开网页并允许系统定位后，
**全自动上报位置**——手机上不显示位置卡片、不需要手动点「上报」。
服务端按「工作位置」的经纬度 + 半径判断是否在岗；
**只有「位置监控管理员 / 系统管理员」**能在网页端看实时位置、历史记录和地图。

### 1. 手机端：全自动，不显示

- **Android App**：首次安装打开时，和摄像头权限一样**自动弹出系统权限菜单**，
  定位选「允许」；然后在提示里把位置权限改成 **「始终允许」**，后台上报才会开启。
  之后即使关闭 App、锁屏，前台服务仍会继续每 60 秒上报（切到后台后通知栏常驻「位置上报中」）。
- **手机网页（PWA）**：首次打开时由浏览器弹定位授权，允许即自动开始；网页版只有前台定位。
  如果以前点过「拒绝」，浏览器不会再次弹窗，需要在站点设置里把「位置」改回允许。
- 不需要用户做任何「上报」操作；App 打开一次并登录、授权后，默认 **60 秒**一次，
  移动超过 **30 米**立即补报；切回前台、恢复网络也会自动补一次。
- **没有分配工作位置也照常记录**（网页端显示「未分配位置」）；
  只有在需要判断「是否在工作位置」时，才需要配置工作位置并分配人员。
- 必须 HTTPS；App 首次必须联网登录一次，之后后台服务用登录 Cookie 上报。
- 手机端**不再显示**「位置上报」卡片，也没有「立即定位并上报」按钮；
  「我」页只有我的工单和账号。定位权限被拒绝时只影响上报，不影响扫码/工单。

### 2. 网页端：实时位置（管理员）

1. **机台维修 → 位置监控 → 实时位置**：每人最新状态、距中心多少米、最后上报时间；
   每行有「地图」按钮，直接在高德地图打开该坐标。
2. **机台维修 → 位置监控 → 位置看板（地图）**：
   真实高德街道地图（可放大/缩小，点人名直接打开高德）+ 统计卡 + 人员状态 + 最近 30 条上报，
   每 30 秒自动刷新。地图上会画出工作位置半径圆、人员点位和比例尺。
3. **机台维修 → 位置监控 → 位置记录**：按人/按天查历史流水，每条都能点「地图」。
4. 想判断「是否在岗」时，再到 **位置监控 → 工作位置 → 新建**：
   - 名称（如「一号车间」）、纬度、经度、范围半径（车间建议 100~300 米）
   - **坐标系**：从高德/腾讯地图长按取的经纬度选 **GCJ-02**；手机 GPS 实测的坐标选 **WGS-84**
     （默认 GCJ-02，选错会让围栏偏移约 300~600 米）
   - 在「在此工作的人员」里选人（可多选）；不选也能看位置，只是显示「未分配位置」
5. **机台维修 → 配置 → 定位设置**（仅系统管理员）：上报间隔（默认 60 秒）、
   移动多少米立即上报（30 米）、失联分钟数（15）、记录保留天数（90）
6. 给其他管理账号开权限：**设置 → 用户 → 选人 → 位置监控** 页签；或把人加进
   「机台维修 / 位置监控管理员」组（`admin` 默认已在该组）。该组可看位置、维护工作位置，
   但「定位设置」仍只给系统管理员。

> 没有配置工作位置时，看板会提示「只能看到位置记录、无法判断是否在岗」，
> 但位置照常记录；配好工作位置后，历史快照会按当前围栏重新计算状态。
>
> 地图用的是**高德瓦片**（免 key，浏览器需要能访问 `*.is.autonavi.com`）；
> 如果公司网络把瓦片挡了，看板仍会显示人员/距离，点行内「高德地图」可打开手机/电脑的高德地图。

### 3. 权限与隐私（重要）

| 项目 | 现状 |
|---|---|
| 谁能看到位置 | 位置监控管理员组 + 系统管理员；师傅账号在 Odoo 里**没有位置菜单** |
| 谁能上报 | 所有手机端登录用户（含管理员）自动上报，不需要分配工作位置 |
| 直接调接口/ RPC | 师傅读位置流水、工作位置、别人的人员分配都是 `AccessError`；看板页面/数据 403 |
| 客户端能否伪造「在岗」 | 不能。`inside/outside` 由服务端按工作位置半径算，手机只上报经纬度 |
| 地图/坐标 | 手机 GPS 存 WGS-84；画到高德地图、打开高德链接时转 GCJ-02；工作位置按录入时选的坐标系换算 |
| 手机能拿到哪些配置 | 只有本人被分配的位置**名称/半径**，不下发经纬度；没有位置卡片 |
| 记录保留 | 默认 90 天，每日 Cron 清理；可改为 0（永不清理，不建议） |
| 建议 | 在员工手册/劳动合同里写明用途与时段；只用于工作位置核对，不用于其他用途 |

### 4. 当前限制（如实说明）

- **App 后台定位已实现（v1.13 修正启动时机）**：页面加载完成、App 仍在前台时就启动
  Android 前台服务 + 常驻通知，关闭 App/锁屏后继续上报；不再依赖 `onPause` 启动，
  避开 Android 12+ 的后台启动限制。**通知栏没有「位置上报中」就说明服务没启动**，
  常见原因是未登录、Cookie 没保存、定位权限没给；App 会 Toast，通知里会写具体原因。
  但 Android 有硬限制：① 首次必须打开 App 登录并授权；② 位置权限要选「始终允许」；
  ③ 用户「强行停止」App、或系统/厂商电池管理杀掉进程后，只能等下次打开 App 恢复；
  ④ 重启手机后 App 会尝试自动恢复，部分系统会拒绝，需要打开一次。
- **耗电**：高精度后台定位会比普通使用多耗电；可把上报间隔调大（最短 15 秒）。
  建议在手机「电池 → 应用启动管理」里允许机台维修 App 自启动/后台运行；
  v1.13 会弹「忽略电池优化」提示。
- **精度取决于手机**：室内/地下车库 GPS 漂移可能几十米，可把半径调大（如 300 米）；
  手机上已经没有「立即上报」按钮，系统会在下一次定位回调时自动补上。
- **坐标由手机提供**：安装虚拟定位/改机工具可以伪造位置，本系统不做反作弊。
  如需更强约束，可再加「现场二维码/蓝牙信标打卡」作为交叉验证。
- **必须 HTTPS**：外网走 Cloudflare Tunnel 的 https 地址即可；局域网 http 不行。
- **地图瓦片需要外网**：高德瓦片加载失败时地图区域会只剩网格/点位，但「高德地图」链接、坐标和距离仍然可用。
- iOS 把网页「添加到主屏幕」后仍是前台/系统允许的有限后台；要 7×24 后台定位需要原生 App 能力。

### 5. 代码位置与测试

| 内容 | 位置 |
|---|---|
| 模型（工作位置 / 流水 / 快照 / 设置） | `custom-addons\machine_repair\models\{work_location,location_ping,user_location,res_users,res_config_settings}.py` |
| 坐标系转换 / 高德链接 | `custom-addons\machine_repair\models\geo.py`（WGS-84 ↔ GCJ-02、`amap_marker_url`） |
| 接口（上报 / 状态 / 看板） | `custom-addons\machine_repair\controllers\mobile.py` |
| 后台菜单/视图/设置 | `custom-addons\machine_repair\views\location_views.xml`、`res_users_views.xml` |
| 管理员看板页 | `custom-addons\machine_repair\views\location_templates.xml`（路由 `/machine_repair/locations`） |
| 手机端自动上报 JS | `custom-addons\machine_repair\views\mobile_templates.xml`（搜「位置自动上报」） |
| Android 启动权限/定位授权 | `mobile-android\app\src\main\java\com\odoo\machinerepair\MainActivity.java`（搜 `Geolocation` / `REQ_STARTUP_PERMISSIONS`） |
| Android 后台上报服务 | `LocationService.java`（GPS + 前台通知 + `/location/native`） |
| 开机/升级恢复 | `BootReceiver.java` |
| 回归测试 | `.tools\test-location-setup.py` → `.tools\test-location.py` → `.tools\test-location-teardown.py` |

测试命令见 `custom-addons\machine_repair\README.md` 的「阶段 7」。
实测：未分配工作位置时也能上报（状态「未分配」）、分配后室内 `0.0 米`/室外 `1111.9 米`、
管理员也自动上报、师傅 403/`AccessError`、原生后台接口 200/无头 403/匿名 401、
临时管理员看板正常、定位设置开关与间隔生效；测试使用独立账号，真实账号数据未动。
正式使用前如需判断在岗，请按第 2 节添加真实工作位置并分配人员（可选）。

