<#
  打包「机台维修」Android APK（不依赖 Gradle / Android Studio）
  ------------------------------------------------------------
    .\build-apk.ps1                                  用默认工具链路径（版本号自动取自 AndroidManifest.xml）
    .\build-apk.ps1 -Version 1.16                    手动指定版本号（一般不需要）
    .\build-apk.ps1 -Jdk D:\toolchain\jdk -Sdk D:\toolchain\sdk
    .\build-apk.ps1 -Install                         打包成功后用 adb 安装到连着的手机

  产物：mobile-android\dist\jiTaiWeiXiu-<版本>.apk
        发布：D:\Odoo\dist\机台维修App-v<版本>.apk（文件名带版本号；上一版自动移入 dist\archive\）
  签名：首次运行会用 keytool 自动生成 mobile-android\keystore\repair.jks
        （口令在脚本里，仅用于内部侧载安装；正式上架请换成自己的签名）

  需要事先装好（都不需要管理员权限，解压即用）：
    1) JDK 17        https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse
    2) Android SDK   https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip
       解压到 <Sdk>\cmdline-tools\latest，然后
       & "<Sdk>\cmdline-tools\latest\bin\sdkmanager.bat" --sdk_root="<Sdk>" "platform-tools" "platforms;android-34" "build-tools;34.0.0"
#>
param(
  [string]$Root = 'D:\Odoo\mobile-android',
  [string]$Jdk = 'D:\Odoo\toolchain\jdk',
  [string]$Sdk = 'D:\Odoo\toolchain\sdk',
  # 不传 -Version 时自动从 AndroidManifest.xml 的 versionName 取，
  # 避免“清单里是 1.16、文件名还是 1.15”这种对不上号的情况。
  [string]$Version = '',
  [switch]$Install
)
$ErrorActionPreference = 'Stop'
function Fail($msg) { Write-Host "❌ $msg" -ForegroundColor Red; exit 1 }

$app = Join-Path $Root 'app\src\main'
$build = Join-Path $Root 'build'
$dist = Join-Path $Root 'dist'
$gen = Join-Path $build 'gen'
$classes = Join-Path $build 'classes'
$dex = Join-Path $build 'dex'
$keystoreDir = Join-Path $Root 'keystore'
$keystore = Join-Path $keystoreDir 'repair.jks'
$ksPass = 'RepairApp2026'
$alias = 'repair'

$manifest = Join-Path $app 'AndroidManifest.xml'
if (-not $Version -and (Test-Path $manifest)) {
  $hit = Select-String -Path $manifest -Pattern 'android:versionName="([^"]+)"' -Encoding UTF8 |
    Select-Object -First 1
  if ($hit) { $Version = $hit.Matches[0].Groups[1].Value }
}
if (-not $Version) { $Version = '1.0' }
Write-Host ("版本号：{0}" -f $Version)

foreach ($p in @($build, $gen, $classes, $dex, $dist, $keystoreDir)) {
  New-Item -ItemType Directory -Force -Path $p | Out-Null
}

# ---------------------------------------------------------------- 工具链检查
$java = Join-Path $Jdk 'bin\java.exe'
$javac = Join-Path $Jdk 'bin\javac.exe'
$keytool = Join-Path $Jdk 'bin\keytool.exe'
$jar = Join-Path $Jdk 'bin\jar.exe'
if (-not (Test-Path $java)) { Fail "找不到 JDK：$Jdk（先装 JDK 17）" }

# d8.bat 用 JAVA_HOME/PATH 找 java；不设置会在 [4/6] 报
# "JAVA_HOME is not set and no 'java' command could be found"
$env:JAVA_HOME = $Jdk
$env:Path = (Join-Path $Jdk 'bin') + ';' + $env:Path

$buildTools = Get-ChildItem (Join-Path $Sdk 'build-tools') -Directory -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | Select-Object -First 1
if (-not $buildTools) { Fail "找不到 Android build-tools：$Sdk\build-tools" }
$aapt2 = Join-Path $buildTools.FullName 'aapt2.exe'
$zipalign = Join-Path $buildTools.FullName 'zipalign.exe'
$apksigner = Join-Path $buildTools.FullName 'apksigner.bat'
$d8 = Join-Path $buildTools.FullName 'd8.bat'
$androidJar = Get-ChildItem (Join-Path $Sdk 'platforms') -Directory -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'android.jar' } |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $androidJar) { Fail "找不到 android.jar（sdkmanager 装 platforms;android-34）" }
foreach ($tool in @($aapt2, $zipalign, $apksigner, $d8)) {
  if (-not (Test-Path $tool)) { Fail "缺少工具：$tool" }
}
Write-Host "JDK       : $Jdk"
Write-Host "buildTools: $($buildTools.Name)"
Write-Host "androidJar: $androidJar"

# ---------------------------------------------------------------- 1) 编译资源
Write-Host "`n[1/6] aapt2 compile"
& $aapt2 compile --dir (Join-Path $app 'res') -o (Join-Path $build 'res.zip')
if ($LASTEXITCODE -ne 0) { Fail 'aapt2 compile 失败' }

# ---------------------------------------------------------------- 2) 链接资源
Write-Host "[2/6] aapt2 link"
$unsigned = Join-Path $build 'unsigned.apk'
& $aapt2 link -o $unsigned -I $androidJar --manifest (Join-Path $app 'AndroidManifest.xml') `
  --java $gen --min-sdk-version 24 --target-sdk-version 34 `
  --auto-add-overlay (Join-Path $build 'res.zip')
if ($LASTEXITCODE -ne 0) { Fail 'aapt2 link 失败' }

# ---------------------------------------------------------------- 3) 编译 Java
Write-Host "[3/6] javac"
$sources = @(Get-ChildItem (Join-Path $app 'java') -Recurse -Filter '*.java' |
  Select-Object -ExpandProperty FullName)
$sources += @(Get-ChildItem $gen -Recurse -Filter '*.java' | Select-Object -ExpandProperty FullName)
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'   # 编译器的"过时 API"提示会走 stderr，不能当致命错误
# 依赖的 jar（ZXing core 用于原生扫码解码）
$jars = @(Get-ChildItem (Join-Path $Root 'app\libs') -Filter '*.jar' -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty FullName)
$cp = $androidJar
if ($jars.Count) { $cp = ($androidJar + ';' + ($jars -join ';')); Write-Host "  依赖: $($jars.Count) 个 jar" }
& $javac -encoding UTF-8 -source 17 -target 17 -classpath $cp -d $classes @sources 2>&1 |
  Where-Object { $_ -and $_ -notmatch 'bootstrap class path' } | ForEach-Object { "  $_" }
$javacCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($javacCode -ne 0) { Fail "javac 编译失败 (exit $javacCode)" }

# ---------------------------------------------------------------- 4) 转 dex
Write-Host "[4/6] d8"
$classFiles = @(Get-ChildItem $classes -Recurse -Filter '*.class' | Select-Object -ExpandProperty FullName)
$dextInputs = @($classFiles) + @($jars)
$ErrorActionPreference = 'Continue'
& $d8 --lib $androidJar --min-api 24 --output $dex @dextInputs 2>&1 | ForEach-Object { "  $_" }
if ($LASTEXITCODE -ne 0) { Fail 'd8 生成 dex 失败' }

# ---------------------------------------------------------------- 5) 把 dex 塞进 APK
Write-Host "[5/6] 打包 dex"
$dexFile = Join-Path $dex 'classes.dex'
if (-not (Test-Path $dexFile)) { Fail "找不到 dex：$dexFile" }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zipStream = [System.IO.Compression.ZipFile]::Open($unsigned, 'Update')
try {
  $existing = $zipStream.GetEntry('classes.dex')
  if ($existing) { $existing.Delete() }
  [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
    $zipStream, $dexFile, 'classes.dex', [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
} finally { $zipStream.Dispose() }
Write-Host ("  已写入 classes.dex（{0} B）" -f (Get-Item $dexFile).Length)

$aligned = Join-Path $build 'aligned.apk'
& $zipalign -f 4 $unsigned $aligned
if ($LASTEXITCODE -ne 0) { Fail 'zipalign 失败' }

# ---------------------------------------------------------------- 6) 签名
Write-Host "[6/6] 签名"
if (-not (Test-Path $keystore)) {
  & $keytool -genkeypair -v -keystore $keystore -alias $alias -keyalg RSA -keysize 2048 `
    -validity 10000 -storepass $ksPass -keypass $ksPass `
    -dname "CN=Machine Repair, OU=Factory, O=Repair, L=CN, S=CN, C=CN"
  if ($LASTEXITCODE -ne 0) { Fail '生成签名证书失败' }
}
$apk = Join-Path $dist ("jiTaiWeiXiu-$Version.apk")
& $apksigner sign --ks $keystore --ks-key-alias $alias `
  --ks-pass "pass:$ksPass" --key-pass "pass:$ksPass" --out $apk $aligned
if ($LASTEXITCODE -ne 0) { Fail 'apksigner 签名失败' }
& $apksigner verify --print-certs $apk | Select-Object -First 3 | ForEach-Object { "  $_" }

# 发布到 D:\Odoo\dist：文件名带版本号（机台维修App-v1.15.apk），
# 旧版本自动挪到 dist\archive，dist 根目录永远只有“当前这一版”，不会堆一堆同名文件。
# 这样发人/拷手机时一眼能看出装的是哪一版，回滚也有旧包可用。
$publishDir = 'D:\Odoo\dist'
$archiveDir = Join-Path $publishDir 'archive'
New-Item -ItemType Directory -Force -Path $publishDir, $archiveDir | Out-Null
$publishedName = "机台维修App-v$Version.apk"
$published = Join-Path $publishDir $publishedName

Get-ChildItem $publishDir -File -Filter '机台维修App-v*.apk' -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -ne $publishedName } |
  ForEach-Object {
    $target = Join-Path $archiveDir $_.Name
    if (Test-Path $target) { Remove-Item $target -Force }
    Move-Item -LiteralPath $_.FullName -Destination $target -Force
    "  旧版本已归档：$($_.Name)"
  }

# 历史固定名（机台维修App-最新版.apk）也归档，避免和新命名混淆
$legacy = Join-Path $publishDir '机台维修App-最新版.apk'
if (Test-Path $legacy) {
  Move-Item -LiteralPath $legacy -Destination (Join-Path $archiveDir '机台维修App-最新版.apk') -Force
  "  旧命名的安装包已归档：机台维修App-最新版.apk"
}

Copy-Item -LiteralPath $apk -Destination $published -Force

"`n✅ 打包完成：$apk"
"   大小：" + [math]::Round((Get-Item $apk).Length / 1KB, 1) + " KB"
"   发布（带版本号）：$published"
"   安装：把 apk 传到手机点开安装（需允许「未知来源」），或执行："
"         .\build-apk.ps1 -Install"

if ($Install) {
  $adb = Join-Path $Sdk 'platform-tools\adb.exe'
  if (Test-Path $adb) {
    & $adb install -r $apk
  } else { Write-Host "  找不到 adb：$adb" }
}
