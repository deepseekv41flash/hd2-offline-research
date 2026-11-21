# system_proxy.ps1 — 切换/恢复当前用户 WinINET 系统代理（给 PlayFab 抓包 MITM 用）
#
# 只改 HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings，不需要管理员；
# 改前把原值备份到 JSON，-Restore 精确恢复。Clash Verge 的「系统代理守卫」当前是关闭的，
# 不会把我们改的值改回去（如开启请先关它）。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\system_proxy.ps1 -Action Get
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\system_proxy.ps1 -Action Set -Proxy 127.0.0.1:18080
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\system_proxy.ps1 -Action Restore
param(
    [ValidateSet('Get', 'Set', 'Restore')]
    [string]$Action = 'Get',
    [string]$Proxy = '127.0.0.1:18080',
    [string]$Backup = 'state/mitm/proxy-backup.json'
)

$ErrorActionPreference = 'Stop'
$regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$props = @('ProxyEnable', 'ProxyServer', 'ProxyOverride', 'AutoConfigURL')

function Get-Current {
    $item = Get-ItemProperty -Path $regPath -ErrorAction SilentlyContinue
    $out = [ordered]@{}
    foreach ($p in $props) {
        $v = $null
        if ($item -and ($item.PSObject.Properties.Name -contains $p)) { $v = $item.$p }
        $out[$p] = $v
    }
    return $out
}

function Refresh-WinInet {
    # 通知 WinINET 设置变化，正在运行的程序（含游戏）下一次请求即生效
    $sig = @'
using System;
using System.Runtime.InteropServices;
public static class WinInetRefresh {
    [DllImport("wininet.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern bool InternetSetOption(IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
    public static void Refresh() {
        InternetSetOption(IntPtr.Zero, 39, IntPtr.Zero, 0);   // INTERNET_OPTION_SETTINGS_CHANGED
        InternetSetOption(IntPtr.Zero, 37, IntPtr.Zero, 0);   // INTERNET_OPTION_REFRESH
    }
}
'@
    if (-not ('WinInetRefresh' -as [type])) {
        Add-Type -TypeDefinition $sig -ErrorAction SilentlyContinue | Out-Null
    }
    try { [WinInetRefresh]::Refresh() } catch { }
}

$backupPath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Backup))

switch ($Action) {
    'Get' {
        $cur = Get-Current
        Write-Output ("ProxyEnable   = " + $cur['ProxyEnable'])
        Write-Output ("ProxyServer   = " + $cur['ProxyServer'])
        Write-Output ("ProxyOverride = " + $cur['ProxyOverride'])
        Write-Output ("AutoConfigURL = " + $cur['AutoConfigURL'])
    }
    'Set' {
        if (-not (Test-Path (Split-Path -Parent $backupPath))) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $backupPath) -Force | Out-Null
        }
        if (-not (Test-Path $backupPath)) {
            $cur = Get-Current
            ($cur | ConvertTo-Json -Depth 4) | Set-Content -Path $backupPath -Encoding UTF8
            Write-Output "已备份原代理设置: $backupPath"
        } else {
            Write-Output "备份已存在，沿用: $backupPath"
        }
        Set-ItemProperty -Path $regPath -Name 'ProxyEnable' -Value 1 -Type DWord
        Set-ItemProperty -Path $regPath -Name 'ProxyServer' -Value $Proxy -Type String
        # 本地/Tailscale 中继 + CRL 必须绕过代理（CRL 走 WinHTTP，被代理会拿不到）
        $curOverride = [string](Get-ItemProperty -Path $regPath -Name 'ProxyOverride' -ErrorAction SilentlyContinue).ProxyOverride
        $extra = @('127.0.0.1', 'localhost', '127.*', '100.67.88.219')
        $parts = @()
        if ($curOverride) { $parts += $curOverride.Split(';') | Where-Object { $_ } }
        foreach ($e in $extra) { if ($parts -notcontains $e) { $parts += $e } }
        Set-ItemProperty -Path $regPath -Name 'ProxyOverride' -Value ($parts -join ';') -Type String
        try { Remove-ItemProperty -Path $regPath -Name 'AutoConfigURL' -ErrorAction SilentlyContinue } catch { }
        Refresh-WinInet
        Write-Output "系统代理已切到 $Proxy（-Action Restore 可恢复）"
    }
    'Restore' {
        if (-not (Test-Path $backupPath)) {
            Write-Output "没有备份可恢复: $backupPath"
            exit 1
        }
        $bak = Get-Content -Path $backupPath -Raw | ConvertFrom-Json
        foreach ($p in $props) {
            $v = $bak.$p
            if ($null -eq $v) {
                Remove-ItemProperty -Path $regPath -Name $p -ErrorAction SilentlyContinue
            } elseif ($p -eq 'ProxyEnable') {
                Set-ItemProperty -Path $regPath -Name $p -Value ([int]$v) -Type DWord
            } else {
                Set-ItemProperty -Path $regPath -Name $p -Value ([string]$v) -Type String
            }
        }
        Refresh-WinInet
        Write-Output "系统代理已恢复（备份: $backupPath）"
    }
}
