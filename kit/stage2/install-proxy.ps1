param(
  [Parameter(Mandatory = $true)][string]$ProxyHost,
  [int]$ProxyPort = 18080,
  [switch]$DryRun
)
# 非管理员路线：当前用户代理指向我们的 CONNECT 代理（游戏读系统代理时生效）
. "$PSScriptRoot\lib.ps1"
Write-Step "发现层接入（代理路线）：${ProxyHost}:${ProxyPort}"
if (-not (Test-Tcp $ProxyHost $ProxyPort)) { Write-Warn2 "代理端口连不上：${ProxyHost}:${ProxyPort}" }
Set-UserProxy -proxyServer ("{0}:{1}" -f $ProxyHost, $ProxyPort) -DryRun:$DryRun
$ca = Join-Path $PSScriptRoot 'ca.cer'
if (Test-Path $ca) {
  Install-UserCert -certPath $ca -DryRun:$DryRun | Out-Null
} else {
  Write-Warn2 '包里没有 ca.cer：代理路线同样需要它（游戏会校验上游证书）'
}
Write-Step '完成。下一步跑 selfcheck.ps1 -RouteProxy，全绿后按测试卡开游戏；结束用 rollback-all.ps1'
