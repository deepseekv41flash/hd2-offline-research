# 本机 CRL 服务（127.0.0.1:18081）——阶段2 客户端必需
# 来源：E2 志愿者包 C:\hd2-stage2\crl-server.ps1（2026-11-21），收进仓库以便 E3/E4 复现。
# 为什么必需：发现层叶子证书的 CDP=http://127.0.0.1:18081/ca.crl，
# Windows Schannel 取不到 CRL 会直接拒绝握手（CRYPT_E_NO_REVOCATION_CHECK）。
# 用法（普通用户即可，无需管理员）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools/crl_serve.ps1 -CrlPath state/certs-a/ca.crl
# 停止：Ctrl+C 或结束该后台任务。
param(
  [int]$Port = 18081,
  [string]$CrlPath = '',
  [switch]$Quiet
)
# 最小 CRL 服务：阶段2 的测试证书叶子带 CDP=http://127.0.0.1:18081/ca.crl，
# Windows Schannel 取不到 CRL 会直接拒绝握手（CRYPT_E_NO_REVOCATION_CHECK），
# 所以每台客户端都要在本机 18081 上提供 ca.crl。用 TcpListener 实现：普通用户即可监听，不需要管理员。
#
# 用法（由 install-hosts.ps1 / lib.ps1 自动拉起；也可手工）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\crl-server.ps1
# 停止：在窗口里 Ctrl+C，或跑 rollback-all.ps1。
$ErrorActionPreference = 'Stop'

if (-not $CrlPath) { $CrlPath = Join-Path $PSScriptRoot 'ca.crl' }
if (-not (Test-Path -LiteralPath $CrlPath)) { throw "找不到 CRL 文件：$CrlPath" }
$bytes = [IO.File]::ReadAllBytes($CrlPath)
$expectedSha = (Get-FileHash -LiteralPath $CrlPath -Algorithm SHA256).Hash

$backupDir = Join-Path $PSScriptRoot 'backup'
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$pidFile = Join-Path $backupDir 'crl-server.pid'

$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
try {
  $listener.Start()
} catch {
  throw "无法监听 127.0.0.1:$Port —— $($_.Exception.Message)（端口被占用？先跑一次 rollback-all.ps1）"
}
Set-Content -LiteralPath $pidFile -Value $PID -Encoding ASCII
if (-not $Quiet) {
  Write-Host "[CRL] http://127.0.0.1:$Port/ca.crl 已启动（$($bytes.Length) B，SHA256 ${expectedSha}）" -ForegroundColor Green
  Write-Host '[CRL] 测试期间保持运行；结束时 Ctrl+C 或跑 rollback-all.ps1' -ForegroundColor DarkGray
}

$httpHead = "HTTP/1.1 200 OK`r`nContent-Type: application/pkix-crl`r`nContent-Length: $($bytes.Length)`r`nConnection: close`r`n`r`n"
$notFound = "HTTP/1.1 404 Not Found`r`nContent-Length: 0`r`nConnection: close`r`n`r`n"

try {
  while ($true) {
    $client = $listener.AcceptTcpClient()
    try {
      $client.ReceiveTimeout = 3000
      $stream = $client.GetStream()
      $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::ASCII, $false, 1024, $true)
      $requestLine = $reader.ReadLine()
      while ($true) {
        $line = $reader.ReadLine()
        if ([string]::IsNullOrEmpty($line)) { break }
      }
      $path = ''
      if ($requestLine -and ($requestLine -match '^GET\s+(\S+)\s+HTTP/')) { $path = $Matches[1] -replace '\?.*$', '' }
      if ($path -in @('/ca.crl', '/ca.crl.der', '/')) {
        $head = [Text.Encoding]::ASCII.GetBytes($httpHead)
        $stream.Write($head, 0, $head.Length)
        $stream.Write($bytes, 0, $bytes.Length)
        if (-not $Quiet) { Write-Host "[CRL] $(Get-Date -Format 'HH:mm:ss') GET $path -> 200" -ForegroundColor DarkGray }
      } else {
        $head = [Text.Encoding]::ASCII.GetBytes($notFound)
        $stream.Write($head, 0, $head.Length)
      }
      $stream.Flush()
    } catch {
      # 客户端提前断开等：忽略，继续服务
    } finally {
      try { $client.Close() } catch {}
    }
  }
} finally {
  try { $listener.Stop() } catch {}
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
