# df2026 · 别快纺织机维修系统

Odoo 20 自定义模块（纺织机电路板维修管理）+ Android 客户端（WebView 壳 + 原生扫码/拍照）。

## 目录

| 路径 | 说明 |
|---|---|
| `custom-addons/machine_repair/` | Odoo 20 模块：机型模板、客户机台台账、工单抢单/接力、二维码身份、照片与视频、电气测量、月度包月账单、报表看板、手机端 PWA |
| `mobile-android/` | Android 客户端源码（`aapt2 + javac + d8 + apksigner` 直接打包，无需 Gradle）|
| `MODULE-README.md` | 模块详细说明（含 19 条 Odoo 20 踩坑记录） |

## 不含什么

按"干净开源"原则，本仓库**不包含**：

- Odoo 源码（运行时环境）、Python 虚拟环境
- PostgreSQL 程序与数据目录、数据库备份
- 日志、构建产物（`build/`、`dist/`、APK）、签名证书 `keystore/`
- 本地配置 `odoo.conf`、数据库凭据、任何真实业务数据

## 快速开始

```bash
# 1) 放进 Odoo 的 addons 路径
git clone https://github.com/biekuaicom/df2026.git
# 2) 依赖模块：base product mail stock repair account board
# 3) 安装模块 machine_repair
```

Android 打包见 `mobile-android/README.md`（需要 JDK 17 + Android SDK build-tools 34）。

## 许可

内部业务系统，按需自行取用。
