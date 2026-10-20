<#
  一键下载 Android 打包工具链（**不需要管理员权限**，全部解压到 D:\Odoo\toolchain）
  ------------------------------------------------------------------------------
    .\get-toolchain.ps1                 下载 JDK 17 + Android SDK 并装好 platform/build-tools
    .\get-toolchain.ps1 -SkipSdk        只装 JDK
    .\get-toolchain.ps1 -Status         只看现状

  下载源（国内可用优先，失败自动回退）：
    JDK  : 清华 Adoptium 镜像 -> 华为云 OpenJDK -> Adoptium 官方
    SDK  : 腾讯云镜像 -> Google 官方
  装机位置：D:\Odoo\toolchain\{jdk,sdk,downloads}
  装完后执行：.\build-apk.ps1
#>
param(
  [string]$Root = 'D:\Odoo\toolchain',
  [switch]$SkipSdk,
  [switch]$Status
)
$ErrorActionPreference = 'Stop'
$jdkDir = Join-Path $Root 'jdk'
$sdkDir = Join-Path $Root 'sdk'
$downloads = Join-Path $Root 'downloads'

function Say($m) {
  $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $m
  Write-Host $line
  Add-Content -LiteralPath (Join-Path $Root 'toolchain.log') -Value $line -Encoding UTF8
}

function Report {
  Write-Host "`n=== 现状 ==="
  $java = Join-Path $jdkDir 'bin\java.exe'
  if (Test-Path $java) {
    $ver = & $java -version 2>&1 | Select-Object -First 1
    Write-Host "  JDK : $jdkDir"
    Write-Host "        $ver"
  } else { Write-Host "  JDK : 未安装" }
  $bt = Get-ChildItem (Join-Path $sdkDir 'build-tools') -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
  $plat = Get-ChildItem (Join-Path $sdkDir 'platforms') -Directory -ErrorAction SilentlyContinue
  Write-Host "  SDK : $sdkDir"
  Write-Host "        build-tools: $(if ($bt) { $bt.Name } else { '无' })"
  Write-Host "        platforms  : $(if ($plat) { ($plat.Name -join ', ') } else { '无' })"
  $apk = Get-ChildItem (Join-Path (Split-Path $PSScriptRoot) 'dist') -Filter '*.apk' -ErrorAction SilentlyContinue
  Write-Host "  已产出 APK: $(if ($apk) { ($apk.Name -join ', ') } else { '无' })"
}

if ($Status) { Report; exit 0 }

New-Item -ItemType Directory -Force -Path $Root, $downloads | Out-Null
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

function Try-Download([string[]]$urls, [string]$dest, [string]$label) {
  foreach ($url in $urls) {
    try {
      Say "下载 $label <- $url"
      $sw = [Diagnostics.Stopwatch]::StartNew()
      Invoke-WebRequest -Uri $url -OutFile $dest -TimeoutSec 1800 -UseBasicParsing
      $sw.Stop()
      $size = (Get-Item $dest).Length
      Say ("  OK {0:N1} MB / {1:N0}s / {2:N0} KB/s" -f ($size / 1MB), $sw.Elapsed.TotalSeconds,
           ($size / 1KB / [Math]::Max(1, $sw.Elapsed.TotalSeconds)))
      return $true
    } catch {
      $msg = $_.Exception.Message
      if ($msg.Length -gt 80) { $msg = $msg.Substring(0, 80) }
      Say ("  FAIL: {0}" -f $msg)
      Remove-Item $dest -Force -ErrorAction SilentlyContinue
    }
  }
  return $false
}

function Expand-Zip([string]$zip, [string]$dest) {
  if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest)
}

# ---------------------------------------------------------------- 1) JDK 17
if (-not (Test-Path (Join-Path $jdkDir 'bin\java.exe'))) {
  Say '=== 准备 JDK 17 ==='
  $jdkZip = Join-Path $downloads 'jdk17.zip'
  $jdkUrls = @()
  try {
    $index = Invoke-WebRequest 'https://mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jdk/x64/windows/' `
      -UseBasicParsing -TimeoutSec 30
    $names = [regex]::Matches($index.Content, 'href="([^"]*jdk_x64_windows[^"]*\.zip)"') |
      ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique
    foreach ($name in $names) {
      $jdkUrls += 'https://mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jdk/x64/windows/' + $name
    }
    Say ("  清华镜像候选 {0} 个" -f @($names).Count)
  } catch { Say '  清华镜像目录读取失败，改试其它源' }
  $jdkUrls += 'https://mirrors.huaweicloud.com/openjdk/17.0.2/openjdk-17.0.2_windows-x64_bin.zip'
  $jdkUrls += 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse'

  if (-not (Try-Download $jdkUrls $jdkZip 'JDK 17')) { Say 'JDK 下载全部失败'; exit 1 }
  Say '  解压 JDK ...'
  $tmp = Join-Path $downloads 'jdk_extract'
  Expand-Zip $jdkZip $tmp
  $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
  if (Test-Path $jdkDir) { Remove-Item $jdkDir -Recurse -Force }
  Move-Item $inner.FullName $jdkDir
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Say "  JDK 就位：$jdkDir"
} else {
  Say '=== JDK 已存在，跳过 ==='
}

# ---------------------------------------------------------------- 2) Android SDK
if (-not $SkipSdk) {
  Say '=== 准备 Android SDK ==='
  $sdkManager = Join-Path $sdkDir 'cmdline-tools\latest\bin\sdkmanager.bat'
  if (-not (Test-Path $sdkManager)) {
    $sdkZip = Join-Path $downloads 'cmdline-tools.zip'
    $sdkUrls = @(
      'https://mirrors.cloud.tencent.com/AndroidSDK/commandlinetools-win-11076708_latest.zip',
      'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip'
    )
    if (-not (Try-Download $sdkUrls $sdkZip 'Android 命令行工具')) { Say '命令行工具下载失败'; exit 1 }
    Say '  解压命令行工具 ...'
    $tmp = Join-Path $downloads 'cmd_extract'
    Expand-Zip $sdkZip $tmp
    $target = Join-Path $sdkDir 'cmdline-tools\latest'
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item (Join-Path $tmp 'cmdline-tools\*') $target -Recurse -Force
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Say '  sdkmanager 就位'
  }

  $sdkManager = Join-Path $sdkDir 'cmdline-tools\latest\bin\sdkmanager.bat'
  $env:JAVA_HOME = $jdkDir
  $env:PATH = (Join-Path $jdkDir 'bin') + ';' + $env:PATH
  Say '接受许可协议 ...'
  cmd /c "echo y| `"$sdkManager`" --sdk_root=`"$sdkDir`" --licenses" 2>&1 |
    Select-Object -Last 3 | ForEach-Object { Say "  $_" }
  Say '安装 platform-tools / platforms;android-34 / build-tools;34.0.0（约 900 MB）...'
  & $sdkManager --sdk_root="$sdkDir" 'platform-tools' 'platforms;android-34' 'build-tools;34.0.0' 2>&1 |
    Where-Object { $_ -notmatch '^\s*$' } | Select-Object -Last 25 | ForEach-Object { Say "  $_" }
  if ($LASTEXITCODE -ne 0) { Say 'sdkmanager 安装失败'; exit 1 }
  Say '  SDK 组件安装完成'
}

Report
Say "下一步: `"$PSScriptRoot\build-apk.ps1`" -Jdk `"$jdkDir`" -Sdk `"$sdkDir`""
