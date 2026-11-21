# clash_watch.ps1 — 持续采样 Clash(mihomo) 连接表，记录「新出现的连接」与关停字节数
#
# 用途：E2 邀请/加入阶段，记录游戏（及其它进程）经系统代理走的**域名级**连接——
#       PlayFab/Party/Steam 的主机名会在这里出现（直连流量由 net_watch.ps1 覆盖）。
# 只读：仅 GET /connections。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\clash_watch.ps1 `
#       -Out state\evidence\e2prep\clash-watch.log -IntervalMs 500
param(
    [string]$PipeName = 'verge-mihomo-production-a7ab1a3352362c32c04114460d048bbe8b1b946c523f72ff0920fd51f9f949ea',
    [string]$Secret = 'set-your-secret',
    [string]$Out = 'state/evidence/e2prep/clash-watch.log',
    [int]$IntervalMs = 500,
    [int]$ConnectTimeoutMs = 2000
)
$ErrorActionPreference = 'Continue'
$outPath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Out))
$dir = Split-Path -Parent $outPath
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$log = $outPath

function Write-Line([string]$s) {
    $line = "$(Get-Date -Format 'HH:mm:ss.fff') $s"
    [System.IO.File]::AppendAllText($log, $line + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
}

function Get-ClashConnections {
    $pipe = New-Object System.IO.Pipes.NamedPipeClientStream('.', $PipeName, [System.IO.Pipes.PipeDirection]::InOut)
    $pipe.Connect($ConnectTimeoutMs)
    $enc = [System.Text.Encoding]::UTF8
    $req = "GET /connections HTTP/1.1`r`nHost: localhost`r`nAuthorization: Bearer $Secret`r`n`r`n"
    $bytes = $enc.GetBytes($req)
    $pipe.Write($bytes, 0, $bytes.Length)
    $pipe.Flush()
    $ms = New-Object System.IO.MemoryStream
    $buf = New-Object byte[] 65536
    $bodyStart = -1
    $chunked = $false
    $decoded = $null
    while ($true) {
        $n = $pipe.Read($buf, 0, $buf.Length)
        if ($n -le 0) { break }
        $ms.Write($buf, 0, $n)
        $all = $ms.ToArray()
        if ($bodyStart -lt 0) {
            $ascii = [System.Text.Encoding]::ASCII.GetString($all)
            $idx = $ascii.IndexOf("`r`n`r`n")
            if ($idx -ge 0) {
                $headerText = $ascii.Substring(0, $idx)
                $bodyStart = $idx + 4
                if ($headerText -match '(?im)^Transfer-Encoding:\s*chunked\s*$') { $chunked = $true }
            }
        }
        if ($bodyStart -ge 0) {
            $bb = $all[$bodyStart..($all.Length - 1)]
            if ($chunked) {
                $res = New-Object System.IO.MemoryStream
                $pos = 0
                $complete = $false
                while ($pos -lt $bb.Length) {
                    $crlf = -1
                    for ($i = $pos; $i -lt $bb.Length - 1; $i++) {
                        if ($bb[$i] -eq 13 -and $bb[$i + 1] -eq 10) { $crlf = $i; break }
                    }
                    if ($crlf -lt 0) { break }
                    $hex = [System.Text.Encoding]::ASCII.GetString($bb, $pos, $crlf - $pos).Trim()
                    $len = 0
                    if (-not [int]::TryParse($hex, [System.Globalization.NumberStyles]::HexNumber, $null, [ref]$len)) { break }
                    $dataStart = $crlf + 2
                    if ($len -eq 0) { $complete = $true; break }
                    if ($dataStart + $len -gt $bb.Length) { break }
                    $res.Write($bb, $dataStart, $len)
                    $pos = $dataStart + $len + 2
                }
                if ($complete) { $decoded = $res.ToArray(); break }
            } else {
                $decoded = $bb
                break
            }
        }
    }
    $pipe.Dispose()
    if ($null -eq $decoded) { return $null }
    $json = [System.Text.Encoding]::UTF8.GetString([byte[]]$decoded)
    return ($json | ConvertFrom-Json)
}

Write-Line "# clash_watch start interval=${IntervalMs}ms pipe=$PipeName"
$seen = @{}
$lastPoll = ''
$fails = 0
while ($true) {
    $lastPoll = Get-Date -Format 'HH:mm:ss.fff'
    try {
        $d = Get-ClashConnections
        if ($null -eq $d) { $fails++; Write-Line "POLL-FAIL empty (fails=$fails)"; Start-Sleep -Milliseconds $IntervalMs; continue }
        $fails = 0
        $now = @{}
        foreach ($c in @($d.connections)) {
            if ($null -eq $c) { continue }
            $id = [string]$c.id
            $now[$id] = $true
            if (-not $seen.ContainsKey($id)) {
                $seen[$id] = [pscustomobject]@{ up0 = [int64]$c.upload; down0 = [int64]$c.download; ts = $lastPoll }
                $m = $c.metadata
                $hostName = if ($m.host) { $m.host } else { '' }
                $dest = if ($hostName) { "$hostName($($m.destinationIP)):$($m.destinationPort)" } else { "$($m.destinationIP):$($m.destinationPort)" }
                Write-Line ("NEW id={0} {1}->{2} network={3} type={4} chains={5} start={6}" -f $id.Substring(0, [Math]::Min(8, $id.Length)), $m.sourceIP, $dest, $m.network, $m.type, (($c.chains) -join '>'), $c.start)
            }
        }
        foreach ($id in @($seen.Keys)) {
            if (-not $now.ContainsKey($id)) {
                $info = $seen[$id]
                Write-Line ("CLOSE id={0} open_at={1} up={2} down={3}" -f $id.Substring(0, [Math]::Min(8, $id.Length)), $info.ts, $info.up0, $info.down0)
                $seen.Remove($id)
            }
        }
    } catch {
        $fails++
        Write-Line "POLL-ERR $($_.Exception.Message)"
    }
    Start-Sleep -Milliseconds $IntervalMs
}
