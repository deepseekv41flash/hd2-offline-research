# clash_api.ps1 — 只读访问 Clash Verge 内核 (mihomo) 的 REST API（命名管道方式）
#
# 为什么走管道：本机 Verge 配置里 external-controller 的 TCP 端口未监听，只有
# external-controller-pipe（\\.\pipe\verge-mihomo-production-<hash>）可用。
#
# 用法（Git Bash 必须加 MSYS_NO_PATHCONV=1，否则 /version 会被转成 C:/Program Files/Git/version）：
#   MSYS_NO_PATHCONV=1 powershell -NoProfile -ExecutionPolicy Bypass -File tools\clash_api.ps1 \
#       -Path /connections -Out state\evidence\e2prep\clash-connections.json
#   MSYS_NO_PATHCONV=1 powershell ... -Path /version        （测试连通）
#
# 只读：仅发 GET，不改任何配置/规则/连接。
param(
    [string]$PipeName = 'verge-mihomo-production-a7ab1a3352362c32c04114460d048bbe8b1b946c523f72ff0920fd51f9f949ea',
    [string]$Path = '/version',
    [string]$Secret = 'set-your-secret',
    [string]$Out = '',
    [int]$ConnectTimeoutMs = 3000
)
$ErrorActionPreference = 'Stop'
$pipe = New-Object System.IO.Pipes.NamedPipeClientStream('.', $PipeName, [System.IO.Pipes.PipeDirection]::InOut)
$pipe.Connect($ConnectTimeoutMs)
$enc = [System.Text.Encoding]::UTF8
$req = "GET $Path HTTP/1.1`r`nHost: localhost`r`nAuthorization: Bearer $Secret`r`n`r`n"
$bytes = $enc.GetBytes($req)
$pipe.Write($bytes, 0, $bytes.Length)
$pipe.Flush()

$ms = New-Object System.IO.MemoryStream
$buf = New-Object byte[] 65536
$headerText = ''
$bodyStart = -1
$chunked = $false
$contentLength = -1
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
            $m = [regex]::Match($headerText, '(?im)^Content-Length:\s*(\d+)\s*$')
            if ($m.Success) { $contentLength = [int]$m.Groups[1].Value }
        }
    }
    if ($bodyStart -ge 0) {
        $bodyBytes = $all[$bodyStart..($all.Length - 1)]
        if ($chunked) {
            # 逐块解析：<hex len>\r\n<data>\r\n ... 0\r\n\r\n
            $res = [System.IO.MemoryStream]::new()
            $pos = 0
            $complete = $false
            while ($pos -lt $bodyBytes.Length) {
                $crlf = -1
                for ($i = $pos; $i -lt $bodyBytes.Length - 1; $i++) {
                    if ($bodyBytes[$i] -eq 13 -and $bodyBytes[$i + 1] -eq 10) { $crlf = $i; break }
                }
                if ($crlf -lt 0) { break }
                $hex = [System.Text.Encoding]::ASCII.GetString($bodyBytes, $pos, $crlf - $pos).Trim()
                $len = 0
                if (-not [int]::TryParse($hex, [System.Globalization.NumberStyles]::HexNumber, $null, [ref]$len)) { break }
                $dataStart = $crlf + 2
                if ($len -eq 0) { $complete = $true; break }
                if ($dataStart + $len -gt $bodyBytes.Length) { break }
                $res.Write($bodyBytes, $dataStart, $len)
                $pos = $dataStart + $len + 2
            }
            if ($complete) { $decoded = $res.ToArray(); break }
        } else {
            if ($contentLength -ge 0 -and $bodyBytes.Length -ge $contentLength) {
                $decoded = $bodyBytes[0..($contentLength - 1)]
                break
            }
            if ($contentLength -lt 0) { $decoded = $bodyBytes; break }
        }
    }
}
$pipe.Dispose()
$statusLine = ''
if ($headerText) { $statusLine = $headerText.Split("`r`n")[0] }
$body = ''
if ($null -ne $decoded -and $decoded.Length -gt 0) {
    $body = [System.Text.Encoding]::UTF8.GetString([byte[]]$decoded)
}
if ($Out) {
    $full = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Out))
    $dir = Split-Path -Parent $full
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [System.IO.File]::WriteAllText($full, $body, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output "STATUS: $statusLine"
    Write-Output "WRITTEN: $full ($($body.Length) chars)"
} else {
    Write-Output "STATUS: $statusLine"
    Write-Output $body
}
