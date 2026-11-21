# snapshot_client_state.ps1 — 游戏客户端"状态"快照（B7：定位旧厅 id 的持久化落点）
#
# 记录：%APPDATA%\Arrowhead\Helldivers2 下文件（路径/大小/mtime/SHA256）+ 相关注册表值。
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\snapshot_client_state.ps1 -Tag before
#   powershell ... -Tag after-lobby
#   powershell -File tools\diff_client_state.ps1 -A ... -B ...   （或用 python 比较 JSON）
param(
    [string]$Tag = 'snap',
    [string]$OutDir = 'state/evidence/e2prep/client-state'
)

$ErrorActionPreference = 'Continue'
$root = Join-Path $env:APPDATA 'Arrowhead\Helldivers2'
$outDirAbs = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutDir))
if (-not (Test-Path $outDirAbs)) { New-Item -ItemType Directory -Path $outDirAbs -Force | Out-Null }
$outPath = Join-Path $outDirAbs ("snapshot-" + $Tag + ".json")

$files = @()
if (Test-Path $root) {
    Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -notin @('.dmp') } |
        ForEach-Object {
            $hash = ''
            try { $hash = (Get-FileHash -Path $_.FullName -Algorithm SHA256 -ErrorAction Stop).Hash } catch { }
            $files += [pscustomobject]@{
                path  = $_.FullName.Substring($root.Length).TrimStart('\')
                size  = $_.Length
                mtime = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
                sha256 = $hash
            }
        }
}

$reg = @()
foreach ($key in @('HKCU:\Software\Arrowhead', 'HKCU:\Software\Arrowhead Game Studios')) {
    if (Test-Path $key) {
        Get-ChildItem -Path $key -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
            $k = $_
            $props = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
            $vals = @{}
            foreach ($name in $props.PSObject.Properties.Name) {
                if ($name -like 'PS*') { continue }
                $vals[$name] = [string]$props.$name
            }
            $reg += [pscustomobject]@{ key = $k.Name; values = $vals }
        }
    }
}

$doc = [pscustomobject]@{
    tag = $Tag
    when = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    root = $root
    fileCount = $files.Count
    files = $files
    registry = $reg
}
($doc | ConvertTo-Json -Depth 6) | Set-Content -Path $outPath -Encoding UTF8
Write-Output ("snapshot " + $Tag + ": " + $files.Count + " files, " + $reg.Count + " registry keys -> " + $outPath)
