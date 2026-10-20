# 机台维修 Android App（原生壳）

## 为什么做成这样

手机网页端能扫码，但**摄像头授权在多种环境里会被挡**（微信/QQ 内置浏览器、部分国产浏览器）。
装成 App 后由 Android 系统直接授予 `CAMERA` 权限，不再依赖浏览器的授权链路，稳定得多。

这个 App 是**轻量原生壳**：

- 用 `WebView` 加载服务器上的手机端页面 → **业务只维护一份**，改完手机下次打开即生效
- `WebChromeClient.onPermissionRequest` 直接授予摄像头/麦克风 → 网页里的 `getUserMedia` 可用
- `onShowFileChooser` 支持「拍照上传」（走系统相机）
- 页面里的**离线队列**继续有效（提交失败先存本地，网络恢复自动补传）
- **零第三方依赖**（不用 androidx），所以可以用 `aapt2 + javac + d8 + apksigner` 直接打包，
  不需要 Gradle、不需要 Android Studio、不需要连 maven 仓库

## 目录

```
mobile-android\
  app\src\main\AndroidManifest.xml         清单（INTERNET / CAMERA）
  app\src\main\java\...\MainActivity.java  壳：WebView + 摄像头授权 + 拍照回调
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
.\build-apk.ps1

# 3) 安装：把 dist\jiTaiWeiXiu-1.0.apk 传到手机点开安装（需允许"未知来源"）
#    或手机用 USB 连电脑后：
.\build-apk.ps1 -Install
```

## 说明与限制

- **首次打开需要联网登录一次**（和网页端同一套账号）；登录态由 WebView 的 Cookie 保持。
- 摄像头：App 启动时会申请 `CAMERA` 权限；拒绝后可在系统设置里补开。
- 离线：页面里的队列仍在（开单/完工/开票失败先存手机、恢复自动补传），
  但**离线查询历史数据**需要本地数据库，属于下一步（需要原生 Room/SQLite + 同步逻辑）。
- 签名：脚本自动生成的证书只能用于内部侧载；若要上应用商店需换成正式签名并保留好 keystore
  （丢了就无法覆盖升级）。
- 若以后服务器域名变化，改 `MainActivity.java` 里的 `START_URL` 后重新打包。
