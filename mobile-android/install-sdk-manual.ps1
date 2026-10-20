<#
  直接用国内镜像安装 Android SDK 组件（绕过 sdkmanager 连不上 Google 源列表的问题）
  ------------------------------------------------------------------------------
    .\install-sdk-manual.ps1
  需要：D:\Odoo\toolchain\jdk 已就位（get-toolchain.ps1 的 -SkipSdk 会装好 JDK）
  安装：platform-tools、platforms;android-34（或镜像里最新的稳定版）、build-tools
#>
param(
  [string]$Root = 'D:\Odoo\toolchain',
  [string]$Platform = 'platforms;android-34',
  [string]$BuildTools = 'build-tools;34.0.0',
  [string]$Mirror = 'https://mirrors.cloud.tencent.com/AndroidSDK/'
)
$ErrorActionPreference = 'Stop'
$sdkDir = Join-Path $Root 'sdk'
$downloads = Join-Path $Root 'downloads'
New-Item -ItemType Directory -Force -Path $sdkDir, $downloads | Out-Null
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

function Say($m) {
  $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $m
  Write-Host $line
  Add-Content -LiteralPath (Join-Path $Root 'toolchain.log') -Value $line -Encoding UTF8
}

Say '读取镜像的 SDK 清单 ...'
$index = Invoke-WebRequest ($Mirror + 'repository2-3.xml') -UseBasicParsing -TimeoutSec 60
[xml]$xml = $index.Content
$packages = @{}
foreach ($pkg in $xml.SelectNodes('//remotePackage')) { $packages[$pkg.path] = $pkg }

function Find-Package([string]$exact, [string]$pattern) {
  if ($packages.ContainsKey($exact)) { return $packages[$exact] }
  $candidates = @($xml.SelectNodes('//remotePackage') | Where-Object { $_.path -match $pattern })
  if (-not $candidates) { return $null }
  # 取 revision 最大的
  $best = $candidates | Sort-Object {
    $rev = $_.revision
    [version]('{0}.{1}.{2}' -f [int]($rev.major), [int]($rev.minor), [int]($rev.micro))
  } -Descending | Select-Object -First 1
  return $best
}

function Rewrite-Url([string]$url) {
  if ($url -match '^https://dl\.google\.com/android/repository/(.+)$') {
    return $Mirror + $Matches[1]
  }
  return $url
}

function Install-Package([string]$path) {
  $pkg = Find-Package $path $path
  if (-not $pkg) { Say ("  跳过（镜像里没有 {0}）" -f $path); return $false }
  Say ("  安装 {0}" -f $pkg.path)
  # host-os 是子元素；URL 是相对文件名，需要拼镜像前缀
  $archive = @($pkg.archives.archive | Where-Object { $_.'host-os' -eq 'windows' })[0]
  if (-not $archive) {
    $archive = @($pkg.archives.archive | Where-Object { $_.complete.url -match '-win|windows' })[0]
  }
  if (-not $archive) { $archive = @($pkg.archives.archive)[0] }
  $rawUrl = [string]$archive.complete.url
  $url = Rewrite-Url $rawUrl
  if ($url -notmatch '^https?://') { $url = $Mirror + $url }
  $dest = Join-Path $downloads ([IO.Path]::GetFileName($rawUrl))
  if (-not (Test-Path $dest)) {
    Say ("    下载 {0}" -f $url)
    Invoke-WebRequest -Uri $url -OutFile $dest -TimeoutSec 1800 -UseBasicParsing
    Say ("    OK {0:N1} MB" -f ((Get-Item $dest).Length / 1MB))
  }
  # 解压：zip 里通常是一个顶层目录，需要展开到目标
  $targetName = ($pkg.path -replace ';', [IO.Path]::DirectorySeparatorChar)
  $target = Join-Path $sdkDir $targetName
  if (Test-Path $target) { Remove-Item $target -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $target | Out-Null
  $tmp = Join-Path $downloads ('x_' + [IO.Path]::GetFileNameWithoutExtension($dest))
  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory($dest, $tmp)
  $entries = @(Get-ChildItem $tmp)
  if ($entries.Count -eq 1 -and $entries[0].PSIsContainer) {
    Copy-Item (Join-Path $entries[0].FullName '*') $target -Recurse -Force
  } else {
    Copy-Item (Join-Path $tmp '*') $target -Recurse -Force
  }
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Say ("    就位：{0}" -f $target)
  return $true
}

Install-Package 'platform-tools' | Out-Null
$platformOk = Install-Package $Platform
if (-not $platformOk) {
  $alt = ($xml.SelectNodes('//remotePackage') | Where-Object { $_.path -match '^platforms;android-3[0-9]$' } |
    Sort-Object path -Descending | Select-Object -First 1).path
  if ($alt) { Install-Package $alt | Out-Null }
}
$btOk = Install-Package $BuildTools
if (-not $btOk) {
  $altBt = ($xml.SelectNodes('//remotePackage') | Where-Object { $_.path -match '^build-tools;3[0-9]\.[0-9.]+$' } |
    Sort-Object path -Descending | Select-Object -First 1).path
  if ($altBt) { Install-Package $altBt | Out-Null }
}

Write-Host "`n=== SDK 现状 ==="
Get-ChildItem $sdkDir -Directory | ForEach-Object { "  $($_.Name)" }
foreach ($sub in @('platforms', 'build-tools')) {
  $dir = Join-Path $sdkDir $sub
  if (Test-Path $dir) {
    "  {0}: {1}" -f $sub, ((Get-ChildItem $dir -Directory | Select-Object -ExpandProperty Name) -join ', ')
  }
}
Say '完成'
