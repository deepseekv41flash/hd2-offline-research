# HD2 阶段2 测试包：公共函数（hosts 备份/还原、证书、代理、TCP 探测）
$ErrorActionPreference = 'Stop'
$script:Root = $PSScriptRoot
$script:BackupDir = Join-Path $Root 'backup'
$script:Marker = '# HD2-STAGE2'

function Write-Step([string]$text) { Write-Host "[*] $text" -ForegroundColor Cyan }
function Write-Ok([string]$text)   { Write-Host "[OK] $text" -ForegroundColor Green }
function Write-Warn2([string]$text){ Write-Host "[!] $text" -ForegroundColor Yellow }
function Write-Fail2([string]$text){ Write-Host "[X] $text" -ForegroundColor Red }

function Ensure-BackupDir { if (-not (Test-Path $script:BackupDir)) { New-Item -ItemType Directory -Path $script:BackupDir | Out-Null } }
function Get-Stamp { return (Get-Date -Format 'yyyyMMdd-HHmmss') }
function Get-HostsPath { return (Join-Path $env:SystemRoot 'System32\drivers\etc\hosts') }
function Get-ProxyRegistry { return 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' }

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Backup-TextFile([string]$path, [string]$tag) {
  Ensure-BackupDir
  $dst = Join-Path $script:BackupDir ("{0}-{1}.bak" -f $tag, (Get-Stamp))
  Copy-Item -LiteralPath $path -Destination $dst -Force
  Write-Ok "已备份 $path -> $dst"
  return $dst
}

function Add-HostsEntry([string]$ip, [string]$domain, [switch]$DryRun) {
  if (-not (Test-Admin)) { throw 'hosts 需要管理员权限：请右键「以管理员身份运行」' }
  $hosts = Get-HostsPath
  $lines = Get-Content -LiteralPath $hosts -ErrorAction Stop
  $kept = @($lines | Where-Object { $_ -notmatch [regex]::Escape($script:Marker) })
  $entry = "{0} {1} {2} {3}" -f $ip, $domain, $script:Marker, (Get-Stamp)
  if ($DryRun) { Write-Ok "[DryRun] 将写入：$entry"; return }
  Backup-TextFile $hosts 'hosts' | Out-Null
  [IO.File]::WriteAllLines($hosts, ($kept + $entry), (New-Object System.Text.UTF8Encoding($false)))
  if (-not ((Get-Content -LiteralPath $hosts) -match [regex]::Escape($entry))) { throw 'hosts 写入后读回失败' }
  Write-Ok "hosts 已写入：$entry"
}

function Remove-HostsEntry([switch]$DryRun) {
  if (-not (Test-Admin)) { throw 'hosts 需要管理员权限：请右键「以管理员身份运行」' }
  $hosts = Get-HostsPath
  $lines = Get-Content -LiteralPath $hosts -ErrorAction Stop
  $kept = @($lines | Where-Object { $_ -notmatch [regex]::Escape($script:Marker) })
  if ($DryRun) { Write-Ok ("[DryRun] 将移除 " + ($lines.Count - $kept.Count) + " 行标记行"); return }
  Backup-TextFile $hosts 'hosts-rollback' | Out-Null
  [IO.File]::WriteAllLines($hosts, $kept, (New-Object System.Text.UTF8Encoding($false)))
  Write-Ok 'hosts 标记行已移除（其余内容未动）'
}

function Get-CertThumbprint([string]$certPath) {
  $c = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certPath)
  return $c.Thumbprint
}

function Install-UserCert([string]$certPath, [switch]$DryRun) {
  if (-not (Test-Path $certPath)) { throw "找不到证书：$certPath" }
  $thumb = Get-CertThumbprint $certPath
  if ($DryRun) { Write-Ok "[DryRun] 将把 $certPath（thumbprint=$thumb）装进 CurrentUser\Root"; return $thumb }
  & certutil.exe -user -addstore Root $certPath | Out-Null
  Ensure-BackupDir
  Set-Content -LiteralPath (Join-Path $script:BackupDir 'installed-cert-thumbprint.txt') -Value $thumb -Encoding ASCII
  Write-Ok "测试根证书已装进当前用户 Root（thumbprint=$thumb）"
  return $thumb
}

function Remove-UserCert([switch]$DryRun) {
  $tf = Join-Path $script:BackupDir 'installed-cert-thumbprint.txt'
  if (-not (Test-Path $tf)) { Write-Warn2 '没有安装记录（backup\installed-cert-thumbprint.txt），跳过'; return }
  $thumb = (Get-Content -LiteralPath $tf -Raw).Trim()
  if ($DryRun) { Write-Ok "[DryRun] 将从 CurrentUser\Root 删除 thumbprint=$thumb"; return }
  & certutil.exe -user -delstore Root $thumb | Out-Null
  Write-Ok "已从当前用户 Root 删除测试证书（$thumb）"
}

function Backup-Proxy {
  Ensure-BackupDir
  $k = Get-ProxyRegistry
  $p = Get-ItemProperty $k
  $info = [ordered]@{ ProxyEnable = $p.ProxyEnable; ProxyServer = $p.ProxyServer; ProxyOverride = $p.ProxyOverride }
  $file = Join-Path $script:BackupDir ("proxy-{0}.json" -f (Get-Stamp))
  $info | ConvertTo-Json | Set-Content -LiteralPath $file -Encoding UTF8
  Write-Ok "已备份当前用户代理设置 -> $file"
  return $file
}

function Set-UserProxy([string]$proxyServer, [switch]$DryRun) {
  if ($DryRun) { Write-Ok "[DryRun] 将设置当前用户代理：ProxyEnable=1 ProxyServer=$proxyServer"; return }
  Backup-Proxy | Out-Null
  $k = Get-ProxyRegistry
  Set-ItemProperty -Path $k -Name ProxyEnable -Value 1
  Set-ItemProperty -Path $k -Name ProxyServer -Value $proxyServer
  Write-Ok "当前用户代理已设为 $proxyServer"
}

function Clear-UserProxy([switch]$DryRun) {
  if ($DryRun) { Write-Ok '[DryRun] 将关闭当前用户代理（ProxyEnable=0）'; return }
  Set-ItemProperty -Path (Get-ProxyRegistry) -Name ProxyEnable -Value 0
  Write-Ok '当前用户代理已关闭（ProxyEnable=0；原配置保留在注册表里）'
}

function Test-Tcp([string]$hostName, [int]$port, [int]$timeoutMs = 3000) {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $iar = $client.BeginConnect($hostName, $port, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne($timeoutMs)) { $client.Close(); return $false }
    $client.EndConnect($iar); $client.Close(); return $true
  } catch { return $false }
}
