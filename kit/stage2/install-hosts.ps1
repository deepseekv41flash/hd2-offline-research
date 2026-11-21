param(
  [Parameter(Mandatory = $true)][string]$ServerIp,
  [string]$Domain = 'api.live.prod.thehelldiversgame.com',
  [switch]$DryRun
)
# 管理员路线：hosts 指向发现层 + 用户级测试根证书
. "$PSScriptRoot\lib.ps1"
Write-Step "发现层接入（hosts 路线）：$Domain -> $ServerIp"
if (-not (Test-Tcp $ServerIp 443)) { Write-Warn2 "TCP 443 连不上 $ServerIp（确认云安全组已放行且服务已启动）" }
Add-HostsEntry -ip $ServerIp -domain $Domain -DryRun:$DryRun
$ca = Join-Path $PSScriptRoot 'ca.cer'
if (Test-Path $ca) {
  Install-UserCert -certPath $ca -DryRun:$DryRun | Out-Null
} else {
  Write-Warn2 '包里没有 ca.cer：hosts 写入了，但游戏会因证书不受信任而失败，请向对接人索取'
}
Write-Step '完成。下一步跑 selfcheck.ps1 -ServerIp <IP>，全绿后按测试卡开游戏；结束用 rollback-all.ps1'
