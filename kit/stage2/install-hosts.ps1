param(
  [Parameter(Mandatory = $true)][string]$ServerIp,
  [string]$Domain = 'api.live.prod.thehelldiversgame.com',
  [switch]$KeepSteamBlocks,
  [switch]$DryRun
)
# 管理员路线：hosts 指向发现层 + 用户级测试根证书 + 本机 CRL 服务（Schannel 要求）
. "$PSScriptRoot\lib.ps1"
Write-Step "发现层接入（hosts 路线）：$Domain -> $ServerIp"

# 0) 时间同步（TLS/证书校验对 >5 分钟偏差敏感）
$ts = Get-TimeSyncStatus
if ($ts.Unsynced -or $ts.LocalCmos) {
  Write-Warn2 '系统时间没有跟网络时间源同步（Leap 未同步 / Local CMOS）。先以管理员运行：w32tm /resync'
} else {
  Write-Ok '系统时间已与网络源同步'
}

# 1) 临时注释 Steam 屏蔽行（login.steampowered.com 被指到本地会让登录/联机直接失败）
if ($KeepSteamBlocks) {
  Write-Warn2 '按参数保留 Steam 屏蔽行（不推荐：可能导致 Steam 登录或联机失败）'
} else {
  Disable-SteamBlockLines -DryRun:$DryRun | Out-Null
}

# 2) hosts 指向发现层
if (-not (Test-Tcp $ServerIp 443)) { Write-Warn2 "TCP 443 连不上 $ServerIp（确认云安全组已放行且服务已启动）" }
Add-HostsEntry -ip $ServerIp -domain $Domain -DryRun:$DryRun

# 3) 用户级根证书
$ca = Join-Path $PSScriptRoot 'ca.cer'
if (Test-Path $ca) {
  Install-UserCert -certPath $ca -DryRun:$DryRun | Out-Null
} else {
  Write-Warn2 '包里没有 ca.cer：hosts 写入了，但游戏会因证书不受信任而失败，请向对接人索取'
}

# 4) 本机 CRL 服务（叶子证书 CDP=http://127.0.0.1:18081/ca.crl，取不到 Schannel 拒绝握手）
Start-CrlServer -DryRun:$DryRun

Write-Step '完成。下一步跑 selfcheck.ps1 -ServerIp <IP>，全绿后按测试卡开游戏；结束用 rollback-all.ps1'
