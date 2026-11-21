# net_watch.ps1 — 被动观测 Helldivers 2 的网络端点（0 风险，不注入/不改配置）
#
# 目的：E2 双击/邀请阶段，记录游戏进程的 TCP 对端（含新出现的 AWS/PlayFab 端点）与
#      系统 DNS 缓存新增项，用于判断「卡在客户端状态机」还是「卡在网络/云侧」。
#
# 用法： powershell -NoProfile -ExecutionPolicy Bypass -File tools\net_watch.ps1
#        -Name helldivers2 -Out state\evidence\e2prep\game-net-watch.log -IntervalMs 250
#
# 输出行：
#   <ts> TCP <state> <localport> -> <remote>:<port> pid=<pid>        （状态变化/新对端）
#   <ts> DNS <entry> -> <data>                                       （DNS 缓存新增）
#   <ts> PROC pid=<pid> name=<name> start=<time>                     （进程出现/消失）

param(
    [string]$Name = 'helldivers2',
    [string]$Out = 'state/evidence/e2prep/game-net-watch.log',
    [int]$IntervalMs = 250
)

$ErrorActionPreference = 'Continue'
$outPath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Out))
$dir = Split-Path -Parent $outPath
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
"# net_watch start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') name=$Name interval=${IntervalMs}ms" |
    Out-File -FilePath $outPath -Encoding utf8

$seen = @{}       # key -> 1，避免同一对端重复刷屏
$seenDns = @{}    # dns entry|data -> 1
$knownPids = @{}
$tick = 0

while ($true) {
    $tick++
    $ts = Get-Date -Format 'HH:mm:ss.fff'
    $procs = @(Get-Process -Name $Name -ErrorAction SilentlyContinue)
    foreach ($p in $procs) {
        if (-not $knownPids.ContainsKey($p.Id)) {
            $knownPids[$p.Id] = $true
            $startTxt = ''
            try { $startTxt = $p.StartTime.ToString('yyyy-MM-dd HH:mm:ss') } catch {}
            "$ts PROC pid=$($p.Id) name=$($p.ProcessName) start=$startTxt" |
                Out-File -FilePath $outPath -Encoding utf8 -Append
        }
        $conns = @(Get-NetTCPConnection -OwningProcess $p.Id -ErrorAction SilentlyContinue)
        foreach ($c in $conns) {
            if ($c.State -eq 'Bound') { continue }
            $key = "$($c.LocalPort)>$($c.RemoteAddress):$($c.RemotePort)"
            $line = "$ts TCP $($c.State) $($c.LocalPort) -> $($c.RemoteAddress):$($c.RemotePort) pid=$($p.Id)"
            # 状态变化也记录（同 key 不同 state）
            $skey = "$key/$($c.State)"
            if (-not $seen.ContainsKey($skey)) {
                $seen[$skey] = $true
                $line | Out-File -FilePath $outPath -Encoding utf8 -Append
            }
        }
    }
    foreach ($pidNow in @($knownPids.Keys)) {
        if (-not ($procs | Where-Object { $_.Id -eq $pidNow })) {
            "$ts PROC-GONE pid=$pidNow" | Out-File -FilePath $outPath -Encoding utf8 -Append
            $knownPids.Remove($pidNow)
        }
    }
    # DNS 缓存新增（PlayFab/Party 主机名若能落到系统 DNS 就会出现在这里）；每 8 拍查一次
    if ($tick % 8 -eq 0) {
        try {
            foreach ($e in @(Get-DnsClientCache -ErrorAction SilentlyContinue)) {
                $dk = "$($e.Entry)|$($e.Data)"
                if (-not $seenDns.ContainsKey($dk)) {
                    $seenDns[$dk] = $true
                    "$ts DNS $($e.Entry) -> $($e.Data)" | Out-File -FilePath $outPath -Encoding utf8 -Append
                }
            }
        } catch {}
    }
    Start-Sleep -Milliseconds $IntervalMs
}
