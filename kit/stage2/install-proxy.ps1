param(
  [Parameter(Mandatory = $true)][string]$ProxyHost,
  [int]$ProxyPort = 18080,
  [switch]$DryRun
)
# 非管理员路线：当前用户代理指向我们的 CONNECT 代理（游戏读系统代理时生效）
. "$PSScriptRoot\lib.ps1"
Write-Step "发现层接入（代理路线）：${ProxyHost}:${ProxyPort}"

# 0) 时间同步（TLS/证书校验对 >5 分钟偏差敏感）
$ts = Get-TimeSyncStatus
if ($ts.Unsynced -or $ts.LocalCmos) {
  Write-Warn2 '系统时间没有跟网络时间源同步（Leap 未同步 / Local CMOS）。联络主持方同步时间后再测'
} else {
  Write-Ok '系统时间已与网络源同步'
}

if (-not (Test-Tcp $ProxyHost $ProxyPort)) { Write-Warn2 "代理端口连不上：${ProxyHost}:${ProxyPort}" }
Set-UserProxy -proxyServer ("{0}:{1}" -f $ProxyHost, $ProxyPort) -DryRun:$DryRun
$ca = Join-Path $PSScriptRoot 'ca.cer'
if (Test-Path $ca) {
  Install-UserCert -certPath $ca -DryRun:$DryRun | Out-Null
} else {
  Write-Warn2 '包里没有 ca.cer：代理路线同样需要它（游戏会校验上游证书）'
}

# 代理路线同样需要本机 CRL 服务（测试证书 CDP=http://127.0.0.1:18081/ca.crl）
Start-CrlServer -DryRun:$DryRun

Write-Step '完成。下一步跑 selfcheck.ps1 -RouteProxy，全绿后按测试卡开游戏；结束用 rollback-all.ps1'
