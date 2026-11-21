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
  $hosts = Get-HostsPath
  $lines = Get-Content -LiteralPath $hosts -ErrorAction Stop
  $kept = @($lines | Where-Object { $_ -notmatch [regex]::Escape($script:Marker) })
  $entry = "{0} {1} {2} {3}" -f $ip, $domain, $script:Marker, (Get-Stamp)
  if ($DryRun) { Write-Ok "[DryRun] 将写入：$entry"; return }
  if (-not (Test-Admin)) { throw 'hosts 需要管理员权限：请右键「以管理员身份运行」' }
  Backup-TextFile $hosts 'hosts' | Out-Null
  [IO.File]::WriteAllLines($hosts, ($kept + $entry), (New-Object System.Text.UTF8Encoding($false)))
  if (-not ((Get-Content -LiteralPath $hosts) -match [regex]::Escape($entry))) { throw 'hosts 写入后读回失败' }
  Write-Ok "hosts 已写入：$entry"
}

function Remove-HostsEntry([switch]$DryRun) {
  $hosts = Get-HostsPath
  $lines = Get-Content -LiteralPath $hosts -ErrorAction Stop
  $kept = @($lines | Where-Object { $_ -notmatch [regex]::Escape($script:Marker) })
  if ($DryRun) { Write-Ok ("[DryRun] 将移除 " + ($lines.Count - $kept.Count) + " 行标记行"); return }
  if (-not (Test-Admin)) { throw 'hosts 需要管理员权限：请右键「以管理员身份运行」' }
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

# ============================================================
# 阶段2 新增：Steam 屏蔽行临时摘除 / 时钟同步状态 / CRL 服务
# ============================================================
# 背景：志愿者机器上常有 `127.0.0.1 <steam 域名> #S302` 这类「Steam 离线屏蔽行」，
# 其中 login.steampowered.com 被指到本地会让 Steam 登录/联机直接失败。
# 测试期间必须临时注释掉，结束后原样恢复（本脚本只加/去自己的前缀，不动别的行）。

$script:SteamHostPattern = 'steampowered|steamcommunity|steamstatic|steamstore|steamcdn|steamvideo|steambroadcast|steamuser|steamusercontent|akamai.*steam|steamcontent'
$script:SteamDisablePrefix = '#HD2-STAGE2-DISABLED# '

function Get-HostsLines { return @(Get-Content -LiteralPath (Get-HostsPath) -ErrorAction Stop) }

function Get-ActiveHostsLines {
  return @(Get-HostsLines | Where-Object { $_.Trim() -ne '' -and -not $_.Trim().StartsWith('#') })
}

function Get-SteamBlockLines {
  return @(Get-ActiveHostsLines | Where-Object { $_ -match $script:SteamHostPattern })
}

function Test-HostsLoginBlocked {
  $hits = @(Get-ActiveHostsLines | Where-Object {
      $cols = @($_.Trim() -split '\s+')
      if ($cols.Count -lt 2) { return $false }
      ($cols[1] -replace ':.*$', '') -eq 'login.steampowered.com'
    })
  return ($hits.Count -gt 0)
}

function Disable-SteamBlockLines([switch]$DryRun) {
  $lines = Get-HostsLines
  $toBlock = @($lines | Where-Object {
      $_.Trim() -ne '' -and -not $_.Trim().StartsWith('#') -and $_ -match $script:SteamHostPattern
    })
  if ($toBlock.Count -eq 0) { Write-Ok 'hosts 里没有生效中的 Steam 屏蔽行（无需处理）'; return 0 }
  if ($DryRun) {
    Write-Ok ("[DryRun] 将临时注释 {0} 条 Steam 屏蔽行：" -f $toBlock.Count)
    $toBlock | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    return $toBlock.Count
  }
  if (-not (Test-Admin)) { throw 'hosts 需要管理员权限：请右键「以管理员身份运行」' }
  Backup-TextFile (Get-HostsPath) 'hosts-before-steam-unblock' | Out-Null
  $out = foreach ($line in $lines) {
    if ($toBlock -contains $line) { $script:SteamDisablePrefix + $line } else { $line }
  }
  [IO.File]::WriteAllLines((Get-HostsPath), $out, (New-Object System.Text.UTF8Encoding($false)))
  ($toBlock | ConvertTo-Json -Compress) | Set-Content -LiteralPath (Join-Path $script:BackupDir 'steam-block-disabled.json') -Encoding UTF8
  Write-Ok ("已临时注释 {0} 条 Steam 屏蔽行（rollback-all.ps1 会原样恢复）" -f $toBlock.Count)
  return $toBlock.Count
}

function Restore-SteamBlockLines([switch]$DryRun) {
  $lines = Get-HostsLines
  $blocked = @($lines | Where-Object { $_.StartsWith($script:SteamDisablePrefix) })
  if ($blocked.Count -eq 0) { Write-Ok 'hosts 里没有本脚本注释过的 Steam 屏蔽行（无需恢复）'; return 0 }
  if ($DryRun) { Write-Ok ("[DryRun] 将恢复 {0} 条 Steam 屏蔽行" -f $blocked.Count); return $blocked.Count }
  if (-not (Test-Admin)) { throw 'hosts 需要管理员权限：请右键「以管理员身份运行」' }
  $out = foreach ($line in $lines) {
    if ($line.StartsWith($script:SteamDisablePrefix)) { $line.Substring($script:SteamDisablePrefix.Length) } else { $line }
  }
  [IO.File]::WriteAllLines((Get-HostsPath), $out, (New-Object System.Text.UTF8Encoding($false)))
  Write-Ok ("已恢复 {0} 条 Steam 屏蔽行" -f $blocked.Count)
  return $blocked.Count
}

function Get-OemConsoleEncoding {
  $oem = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage' -ErrorAction SilentlyContinue).OEMCP
  if (-not $oem) { return $null }
  try { return [Text.Encoding]::GetEncoding([int]$oem) } catch { return $null }
}

function Get-TimeSyncStatus {
  # w32tm 是原生命令：在中文 Windows 上必须按 OEM 代码页解码，否则整段输出是乱码、正则永远不中
  $prev = [Console]::OutputEncoding
  $raw = ''
  try {
    $enc = Get-OemConsoleEncoding
    if ($enc) { [Console]::OutputEncoding = $enc }
    $raw = (& w32tm.exe /query /status 2>&1 | Out-String)
  } catch {
    $raw = ''
  } finally {
    try { [Console]::OutputEncoding = $prev } catch {}
  }
  return [pscustomobject]@{
    Raw      = $raw.Trim()
    Unsynced = [bool]($raw -match '未同步|Unsynchronized|unsynchronized')
    LocalCmos = [bool]($raw -match 'Local CMOS|CMOS')
  }
}

function Test-CrlEndpoint([int]$Port = 18081, [string]$CrlPath = '') {
  try {
    $req = [System.Net.WebRequest]::Create("http://127.0.0.1:$Port/ca.crl")
    $req.Proxy = $null
    $req.Timeout = 3000
    $resp = $req.GetResponse()
    $ms = New-Object System.IO.MemoryStream
    $resp.GetResponseStream().CopyTo($ms)
    $bytes = $ms.ToArray()
    $resp.Close()
    if (-not $CrlPath) { $CrlPath = Join-Path $script:Root 'ca.crl' }
    if (Test-Path $CrlPath) {
      $expect = (Get-FileHash -LiteralPath $CrlPath -Algorithm SHA256).Hash
      $got = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)).Replace('-', '')
      return ($got -eq $expect)
    }
    return ($bytes.Length -gt 0)
  } catch { return $false }
}

function Start-CrlServer([int]$Port = 18081, [switch]$DryRun) {
  $crl = Join-Path $script:Root 'ca.crl'
  if (-not (Test-Path $crl)) { Write-Warn2 "包里没有 ca.crl：$crl（证书链校验会失败，向对接人索取）"; return }
  if (Test-CrlEndpoint $Port) { Write-Ok "CRL 服务已在 127.0.0.1:$Port 运行（内容与 ca.crl 一致）"; return }
  if (Test-Tcp '127.0.0.1' $Port) { Write-Warn2 "127.0.0.1:$Port 被别的进程占用，CRL 服务起不来（先关掉占用者）"; return }
  if ($DryRun) { Write-Ok "[DryRun] 将在 127.0.0.1:$Port 起 CRL 服务（$crl）"; return }
  $scriptPath = Join-Path $script:Root 'crl-server.ps1'
  $proc = Start-Process -FilePath 'powershell.exe' -PassThru -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $scriptPath + '"'), '-Port', $Port
  )
  Start-Sleep -Milliseconds 900
  if (Test-CrlEndpoint $Port) {
    Write-Ok "CRL 服务已启动（PID $($proc.Id)）：http://127.0.0.1:$Port/ca.crl —— 测试期间别关它"
  } else {
    Write-Warn2 "CRL 服务可能没起来（PID $($proc.Id)）。手工跑：powershell -ExecutionPolicy Bypass -File `"$scriptPath`""
  }
}

function Stop-CrlServer([switch]$DryRun) {
  $ids = @()
  $pidFile = Join-Path $script:BackupDir 'crl-server.pid'
  if (Test-Path $pidFile) {
    $ids = @(Get-Content -LiteralPath $pidFile | Where-Object { $_ -match '^\d+$' } | ForEach-Object { [int]$_ })
  }
  try {
    $conn = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18081 -State Listen -ErrorAction Stop
    if ($conn) { $ids += @($conn.OwningProcess) }
  } catch {}
  $ids = @($ids | Sort-Object -Unique)
  if ($ids.Count -eq 0) { Write-Ok 'CRL 服务没在运行（无需停止）'; return }
  if ($DryRun) { Write-Ok ("[DryRun] 将停止 CRL 服务进程：{0}" -f ($ids -join ',')); return }
  foreach ($procId in $ids) {
    try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch {}
  }
  Write-Ok ("CRL 服务已停止（{0}）" -f ($ids -join ','))
}
