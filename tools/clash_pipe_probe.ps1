# clash_pipe_probe.ps1 — 通过 mihomo 的命名管道读连接表（只读，不改 Clash 配置）
# 用法： powershell -NoProfile -ExecutionPolicy Bypass -File tools\clash_pipe_probe.ps1 [-Path /connections]
param(
    [string]$PipeName = 'verge-mihomo-sidecar-release-a7ab1a3352362c32c04114460d048bbe8b1b946c523f72ff0920fd51f9f949ea',
    [string]$Path = '/connections',
    [string]$Secret = 'set-your-secret',
    [int]$TimeoutMs = 3000
)
$ErrorActionPreference = 'Stop'
try {
    $pipe = New-Object System.IO.Pipes.NamedPipeClientStream('.', $PipeName, [System.IO.Pipes.PipeDirection]::InOut)
    $pipe.Connect($TimeoutMs)
    $enc = [System.Text.Encoding]::UTF8
    $req = "GET $Path HTTP/1.1`r`nHost: localhost`r`nAuthorization: Bearer $Secret`r`nConnection: close`r`n`r`n"
    $bytes = $enc.GetBytes($req)
    $pipe.Write($bytes, 0, $bytes.Length)
    $pipe.Flush()
    $ms = New-Object System.IO.MemoryStream
    $buf = New-Object byte[] 65536
    while ($true) {
        $n = $pipe.Read($buf, 0, $buf.Length)
        if ($n -le 0) { break }
        $ms.Write($buf, 0, $n)
    }
    $pipe.Dispose()
    $text = $enc.GetString($ms.ToArray())
    $idx = $text.IndexOf("`r`n`r`n")
    if ($idx -ge 0) {
        Write-Output $text.Substring(0, $idx)      # 响应头
        Write-Output $text.Substring($idx + 4)     # 响应体
    } else {
        Write-Output $text
    }
} catch {
    Write-Output "PIPE_ERROR: $($_.Exception.Message)"
    exit 1
}
