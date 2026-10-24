# 机台维修 Android App（原生壳）

## 为什么做成这样

手机网页端能扫码，但**摄像头授权在多种环境里会被挡**（微信/QQ 内置浏览器、部分国产浏览器）。
装成 App 后由 Android 系统直接授予 `CAMERA` 权限，不再依赖浏览器的授权链路，稳定得多。

这个 App 是**轻量原生壳**：

- 用 `WebView` 加载服务器上的手机端页面 → **业务只维护一份**，改完手机下次打开即生效
- `WebChromeClient.onPermissionRequest` 直接授予摄像头/麦克风 → 网页里的 `getUserMedia` 可用
- **启动时一次性申请相机 + 定位 + 通知权限**（和相机一样自动弹系统菜单），
  之后 App 会引导把位置权限改成「始终允许」以启用后台上报；
  `onGeolocationPermissionsShowPrompt` 处理网页 `navigator.geolocation` 的授权：
  只信任 `https://df2026.213579.com`（和本机 127.0.0.1），权限已给就回传网页
- **原生后台上报服务** `LocationService`：`foregroundServiceType="location"` + 常驻通知，
  关闭 App/锁屏后继续用 `LocationManager` 拿 GPS 并 POST 到 `/location/native`；
  `BootReceiver` 在开机/升级后尝试恢复（部分系统会拒绝）
- `onShowFileChooser` 支持「拍照上传」（走系统相机）
- 页面里的**离线队列**继续有效（提交失败先存本地，网络恢复自动补传）
- **零第三方依赖**（不用 androidx），所以可以用 `aapt2 + javac + d8 + apksigner` 直接打包，
  不需要 Gradle、不需要 Android Studio、不需要连 maven 仓库

## 目录

```
mobile-android\
  app\src\main\AndroidManifest.xml         清单（相机/前后台定位/前台服务/开机）
  app\src\main\java\...\MainActivity.java  壳：WebView + 权限 + Cookie 保存 + 启停后台服务
  app\src\main\java\...\LocationService.java  前台服务：GPS + 常驻通知 + 后台上传
  app\src\main\java\...\BootReceiver.java  开机/升级后尝试恢复服务
  app\src\main\res\                        图标与字符串（已生成 5 档图标）
  get-toolchain.ps1                        一键下载 JDK 17 + Android SDK（无需管理员）
  build-apk.ps1                            一键出 APK（自动生成签名证书）
  dist\                                    产物目录
  build\                                   中间产物
```

## 打包步骤

```powershell
cd D:\Odoo\mobile-android

# 1) 装工具链（约 1.2 GB 下载，解压到 D:\Odoo\toolchain，不需要管理员权限）
.\get-toolchain.ps1

# 2) 打包（会生成签名证书 keystore\repair.jks，口令在脚本里，仅供内部侧载）
#    版本号自动取 app\src\main\AndroidManifest.xml 的 versionName（当前 1.15）
#    内部分发名：mobile-android\dist\jiTaiWeiXiu-<版本>.apk
.\build-apk.ps1

# 3) 发布文件名带版本号：D:\Odoo\dist\机台维修App-v1.15.apk
#    每次构建产物名 = 机台维修App-v<版本>.apk；旧版本自动挪到 D:\Odoo\dist\archive\
#    手机装完后把「文件管理/下载」里的旧 .apk 删掉，旧安装包不是程序的一部分。
#    最干净：手机用 USB 连电脑，APK 不落到手机存储，只留已安装的 App：
.\build-apk.ps1 -Install
```

> 打包脚本现在是 **UTF-8 with BOM**（Windows PowerShell 5.1 需要它，否则中文会乱码/语法错误）；
> 脚本内部会自动把 `D:\Odoo\toolchain\jdk\bin` 加进 `JAVA_HOME` / `PATH`，
> 否则 `d8` 会报 “JAVA_HOME is not set”。

## 覆盖升级（不会重复安装）

| 版本 | package | versionCode | 签名 SHA-256（前 8 位） |
|---|---|---|---|
| v1.8 | `com.odoo.machinerepair` | 9 | `42513552` |
| v1.9 | 同上 | 10 | 同上 |
| v1.10 | 同上 | 11 | 同上 |
| v1.11 | 同上 | 12 | 同上 |
| v1.12 | 同上 | 13 | 同上 |
| v1.13 | 同上 | 14 | 同上 |
| v1.14 | 同上 | 15 | 同上 |
| **v1.15** | 同上 | **16** | 同上 |

- 直接点 v1.15 的 APK：部分国产安装器只显示「安装」而不显示「更新」，
  但包名/签名/versionCode 匹配时会直接覆盖旧版，**桌面只有一个图标**；
  WebView 登录 Cookie、离线队列、App 设置都会保留。
- **不要先卸载旧版**再装（卸载会清数据）。`adb install` 要带 `-r`，或用 `.\build-apk.ps1 -Install`。
- 如果手机提示「签名不一致 / 应用未安装」，说明之前装的是被改签过的改版，
  只能卸载重装；本目录/archive 里 v1.8~v1.15 的签名是一致的。
- 升级到 v1.15 后**打开一次 App 并登录**，才会保存 Cookie 并启动原生后台上报。
- **手机上的旧 .apk 只是安装包文件，不是旧版程序**；装完可以随时删掉，不影响使用。
  Android 不允许 App 静默删除「下载」目录里的文件，所以要么手动删，要么用
  `.\build-apk.ps1 -Install`（adb 安装，APK 根本不落到手机）。
- PC 端发布文件名**带版本号**：`D:\Odoo\dist\机台维修App-v1.15.apk`；
  出新版本时脚本会自动改名为对应版本，并把上一版移进 `dist\archive\`，
  dist 根目录永远只有当前这一版，不会再出现「最新版」这种看不出版本的文件名。

## 说明与限制

- **首次打开需要联网登录一次**（和网页端同一套账号）；登录态由 WebView 的 Cookie 保持。
  之后原生服务会复用这个 Cookie 上报，每分钟请求也会让 Odoo 会话保持活跃。
- **权限**：App 启动时申请 `CAMERA` + `ACCESS_FINE_LOCATION` + `ACCESS_COARSE_LOCATION`
  （Android 13+ 还有通知权限）；位置权限之后要改成 **「始终允许」**，
  否则关闭 App 后可能被系统停止。App 会弹对话框说明并跳转设置；
  拒绝后可在 `系统设置 → 应用 → 机台维修 → 权限` 里补开。
- **定位是全自动的**：App 页面加载后**在前台就启动 `LocationService`**（不依赖切后台的
  `onPause`，避开 Android 12+ 后台启动前台服务的限制）；网页 JS 在 App 内通过
  `AndroidScanner.nativeLocation()` 自动跳过，避免双份上报。手机上**没有位置卡片、
  没有手动上报按钮**；是否分配工作位置都不影响上报（没分配时管理端显示「未分配位置」）。
- **后台运行**：`LocationService` 是前台服务，启动后通知栏常驻「位置上报中」，
  关闭 App/锁屏后仍继续上报。注意：
  * **先看通知栏有没有「位置上报中」**：没有就说明服务没启动，通常是没登录/Cookie 没保存/
    定位权限没给；App 会 Toast 提示，通知里也会写具体原因。
  * 用户「强行停止」App、或系统/厂商电池管理杀掉服务后，只能打开一次 App 恢复；
  * 部分手机需要在「电池 → 应用启动管理」里允许自启动/后台运行（v1.13 会弹「忽略电池优化」提示）；
  * 重启手机后 `BootReceiver` 会尝试恢复，但 Android 12+ 可能拒绝，打开 App 也会恢复。
- 离线：页面里的队列仍在（开单/完工/开票失败先存手机、恢复自动补传），
  但**离线查询历史数据**需要本地数据库，属于下一步（需要原生 Room/SQLite + 同步逻辑）。
- 签名：脚本自动生成的证书只能用于内部侧载；若要上应用商店需换成正式签名并保留好 keystore
  （丢了就无法覆盖升级）。
- 若以后服务器域名变化，改 `MainActivity.java` 的 `START_URL`、`LocationService.API_URL` 与
  `onGeolocationPermissionsShowPrompt` 里的域名白名单后重新打包。

## v1.14（2026-10-23）：视频支持「本地相册上传」+「转发到微信 / QQ」

### 1. 本地视频上传（不只是录制）

* 网页「维修视频」卡片拆成两个入口：
  * **录制视频** → `<input type=file accept="video/*" capture="environment">`，直接开系统相机（限 5 分钟）；
  * **从相册 / 文件上传** → 普通 `<input type=file accept="video/*" multiple>`，打开系统文件选择器，可多选。
* App 侧修的关键 bug：`onShowFileChooser` 以前**只要 accept 是 video 就强行改成 `ACTION_VIDEO_CAPTURE`**，
  所以网页里怎么点都只能录制。现在按 `FileChooserParams.isCaptureEnabled()` 判断：
  带 `capture` 才走录制，不带 `capture` 就交给 `params.createIntent()`（系统文件选择器，支持多选）。
* 上传按段逐个提交到 `/machine_repair/mobile/upload_video`，服务端单段上限从 80MB 放宽到 200MB（时长仍 ≤5 分钟），
  一次最多 3 段（超出会提示）。

### 2. 转发：交给系统分享面板（微信 / QQ 都在列表里）

* 新增原生桥 `AndroidScanner.shareVideo(url, filename)`：
  1. 用 WebView 的 Cookie 把 `/machine_repair/mobile/video/<id>` 下载到 App 缓存；
  2. 通过自写的 `ShareFileProvider`（`content://com.odoo.machinerepair.files/share/<文件名>`，
     `exported=false` + 临时读授权，**不依赖 androidx 的 FileProvider**）暴露给接收方；
  3. 发 `ACTION_SEND`（`type=video/mp4`，`EXTRA_STREAM`），系统弹出转发面板 —— 微信、QQ、企业微信等都在里面，
     **转发出去的是视频文件本身**，不是一条链接；
  4. 结果通过 `window.onNativeShare(ok, msg)` 回传给网页提示。
* 网页端三条路径依次降级：
  1. App 内 → `NATIVE.shareVideo`（上面的系统转发面板）；
  2. 支持 Web Share Level 2 的浏览器（Chrome / Safari）→ `navigator.share({files})`，
     文件没预取或太大时退回分享链接；
  3. 都不支持（微信 / QQ 内置浏览器）→ 弹面板：**下载视频** + 复制链接 + 操作说明
     （下载后去微信聊天里选「相册」发送）。
* 修了 `FileUriExposedException` 的坑：Android 7+ 不能用 `Uri.fromFile` 共享文件，
  所以走 `content://` provider。

## v1.15（2026-10-24）：视频上传 / 转发下载都有圆环进度

- **转发前下载进度**：`shareVideo` 下载视频时，Java 每 300ms（且百分比变化）回调一次
  `window.onNativeShareProgress(percent)`，网页用 SVG 圆环显示 0–100%；服务器没给
  `Content-Length` 时回调 `-1`，页面显示「正在下载视频到手机…」不显示百分比。
- **上传进度**：视频上传从 `fetch` 改成 `XMLHttpRequest`，监听 `xhr.upload.onprogress`，
  圆环显示「第 1/2 段 · 1.2 MB / 8.4 MB」并更新百分比。
- **修掉“选了相册视频没反应”的 bug**：`change` 事件里原来先 `input.value = ''` 再把 `FileList`
  传给上传函数，部分 WebView 清空 value 会同时清空那个 FileList，导致上传函数拿到空列表直接
  return，既不成功也不失败、也不弹提示。现在先把 FileList 复制成数组再清空。
- 上传结果改成一定会给反馈：成功提示「已上传 N 段视频」，失败提示具体原因并刷新详情；
  无论成败都会重新拉一次详情，避免“传了看不到”。
- 页面版本 v38 → v39；`AndroidScanner.version()` → `native-1.15`。
