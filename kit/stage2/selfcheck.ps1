param(
  [string]$ServerIp = '',
  [switch]$RouteProxy,
  [switch]$DryRun
)
. "$PSScriptRoot\lib.ps1"
$script:fails = 0
function Check([string]$name, [bool]$ok, [string]$detail = '') {
  if ($ok) { Write-Ok "$name $detail" } else { Write-Fail2 "$name $detail"; $script:fails++ }
}
Write-Step '自检开始'

# 1) 游戏版本必须与基线逐字节一致
$EXPECT = @{
  BuildId = '26568081'
  ExeSha  = 'B434F6F326B7759978265F5863EF72E3C5EDD4AA5315CC1EF4B640D204EAE0BB'
  DllSha  = '08B756F535CDB9BDD1B4953FBA53C3DFACD25730A155EA6593E5DE63211ACF5F'
}
$acf = $null
$steam = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue).SteamPath
if ($steam) {
  $steam = $steam -replace '/', '\'
  $libs = @($steam)
  $lf = Join-Path $steam 'steamapps\libraryfolders.vdf'
  if (Test-Path $lf) {
    foreach ($m in [regex]::Matches((Get-Content $lf -Raw), '"path"\s+"([^"]+)"')) {
      $libs += ($m.Groups[1].Value -replace '\\\\', '\')
    }
  }
  foreach ($lib in $libs) {
    $cand = Join-Path $lib 'steamapps\appmanifest_553850.acf'
    if (Test-Path $cand) { $acf = $cand; break }
  }
}
if ($acf) {
  $build = ([regex]::Match((Get-Content $acf -Raw), '"buildid"\s+"(\d+)"')).Groups[1].Value
  Check 'buildid' ($build -eq $EXPECT.BuildId) "(=$build)"
  $game = Join-Path (Split-Path $acf -Parent) 'common\Helldivers 2'
  $exe = Join-Path $game 'bin\helldivers2.exe'
  $dll = Join-Path $game 'data\game\game.dll'
  if (Test-Path $exe) { Check 'helldivers2.exe SHA256' ((Get-FileHash $exe -Algorithm SHA256).Hash -eq $EXPECT.ExeSha) }
  if (Test-Path $dll) { Check 'game.dll SHA256' ((Get-FileHash $dll -Algorithm SHA256).Hash -eq $EXPECT.DllSha) }
} else {
  Write-Warn2 '未定位到游戏目录（跳过版本检查）；请确认 Steam 已安装 HD2'
}

# 2) 接入状态
if ($RouteProxy) {
  $ri = Get-ItemProperty (Get-ProxyRegistry) -ErrorAction SilentlyContinue
  Check '当前用户代理已启用' ($ri.ProxyEnable -eq 1) "ProxyServer=$($ri.ProxyServer)"
} else {
  $hit = @(Get-Content (Get-HostsPath) | Where-Object { $_ -match [regex]::Escape($script:Marker) })
  Check 'hosts 标记行' ($hit.Count -ge 1) "(count=$($hit.Count))"
}
$thumbFile = Join-Path $PSScriptRoot 'backup\installed-cert-thumbprint.txt'
Check '测试证书安装记录' (Test-Path $thumbFile)
if (Test-Path $thumbFile) {
  $thumb = (Get-Content $thumbFile -Raw).Trim()
  $found = @(Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Thumbprint -eq $thumb })
  Check '证书在 CurrentUser\Root 中' ($found.Count -ge 1) "(thumbprint=$thumb)"
}

# 2b) Steam 屏蔽行 / CRL / 时钟 / Steam 在线（阶段2 新增的必查项）
if (-not $RouteProxy) {
  $steamBlocks = @(Get-SteamBlockLines)
  Check 'hosts 无生效中的 Steam 屏蔽行' ($steamBlocks.Count -eq 0) "count=$($steamBlocks.Count)（用 install-hosts.ps1 会自动临时注释并保留备份）"
  Check 'login.steampowered.com 未被指到本地' (-not (Test-HostsLoginBlocked)) '被屏蔽会让 Steam 登录/联机失败'
}
Check 'CRL 服务可取（127.0.0.1:18081，内容与 ca.crl 一致）' (Test-CrlEndpoint 18081) '取不到 CRL 时 Schannel 会拒绝握手（CRYPT_E_NO_REVOCATION_CHECK）'
$steamProcs = @(Get-Process -Name steam -ErrorAction SilentlyContinue)
Check 'Steam 客户端在运行' ($steamProcs.Count -ge 1) $(if ($steamProcs.Count) { '' } else { '先启动并登录 Steam，再从 Steam 启动游戏' })
$ts = Get-TimeSyncStatus
Check '系统时间已与网络源同步' (-not ($ts.Unsynced -or $ts.LocalCmos)) $(if ($ts.Unsynced -or $ts.LocalCmos) { '管理员 PowerShell 运行 w32tm /resync 后重跑' } else { '' })

# 3) 环境
$vpn = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {
    $_.Status -eq 'Up' -and $_.InterfaceDescription -match 'VPN|Radmin|WireGuard|Tailscale|ZeroTier|Hamachi'
  })
Check '无 VPN/虚拟网卡在 Up' ($vpn.Count -eq 0) $(if ($vpn.Count) { ($vpn.InterfaceDescription -join ',') } else { '' })
if ($ServerIp) { Check "TCP 443 可达 $ServerIp" (Test-Tcp $ServerIp 443) }
Check '时区为中国标准时间' ((Get-TimeZone).Id -eq 'China Standard Time') "(=$((Get-TimeZone).Id))"

Write-Step ''
if ($script:fails -eq 0) { Write-Ok '自检通过：可以开始测试'; exit 0 }
Write-Fail2 "自检未通过（$($script:fails) 项）：先处理再测"; exit 1
