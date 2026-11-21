param(
  [string]$OutDir = "$env:USERPROFILE\Desktop\hd2-stage2-logs",
  [switch]$DryRun
)
# 收集测试日志：hosts、代理备份、游戏日志目录、自检输出，打包成 zip
. "$PSScriptRoot\lib.ps1"
Write-Step "收集测试日志 -> $OutDir"
if ($DryRun) { Write-Ok "[DryRun] 将收集 hosts / 游戏日志 / 接入备份 到 $OutDir 并打包"; return }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

Copy-Item -LiteralPath (Get-HostsPath) -Destination (Join-Path $OutDir 'hosts.txt') -Force -ErrorAction SilentlyContinue
$arrow = Join-Path $env:APPDATA 'Arrowhead\Helldivers2'
if (Test-Path $arrow) {
  Copy-Item -Path (Join-Path $arrow '*') -Destination $OutDir -Recurse -Force -ErrorAction SilentlyContinue
} else {
  Write-Warn2 "未找到游戏日志目录：$arrow"
}
$backup = Join-Path $PSScriptRoot 'backup'
if (Test-Path $backup) {
  $dst = Join-Path $OutDir 'backup'
  New-Item -ItemType Directory -Path $dst -Force | Out-Null
  Copy-Item -Path (Join-Path $backup '*') -Destination $dst -Recurse -Force -ErrorAction SilentlyContinue
}
& (Join-Path $PSScriptRoot 'selfcheck.ps1') -DryRun *> (Join-Path $OutDir 'selfcheck.txt')
Set-Content -LiteralPath (Join-Path $OutDir 'collected-at.txt') -Value (Get-Date -Format 'o') -Encoding UTF8

$zip = "$OutDir.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $OutDir '*') -DestinationPath $zip -Force
Write-Ok "已打包：$zip"
Write-Warn2 '发回前请检查压缩包里不要有账号名/令牌等个人信息（游戏日志一般不含令牌，但仍请过一眼）'
