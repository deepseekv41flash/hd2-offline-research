param([switch]$DryRun)
# 一键回滚：CRL 服务 + Steam 屏蔽行 + hosts 标记行 + 当前用户代理 + 测试证书
. "$PSScriptRoot\lib.ps1"
Write-Step '一键回滚（CRL 服务 / Steam 屏蔽行 / hosts / 用户代理 / 测试证书）'
try { Stop-CrlServer -DryRun:$DryRun | Out-Null } catch { Write-Warn2 "CRL 服务停止跳过：$($_.Exception.Message)" }
try { Restore-SteamBlockLines -DryRun:$DryRun | Out-Null } catch { Write-Warn2 "Steam 屏蔽行恢复跳过：$($_.Exception.Message)" }
try { Remove-HostsEntry -DryRun:$DryRun } catch { Write-Warn2 "hosts 回滚跳过：$($_.Exception.Message)" }
try { Clear-UserProxy -DryRun:$DryRun } catch { Write-Warn2 "代理回滚跳过：$($_.Exception.Message)" }
try { Remove-UserCert -DryRun:$DryRun } catch { Write-Warn2 "证书卸载跳过：$($_.Exception.Message)" }
Write-Ok '回滚完成：hosts 标记行已删、Steam 屏蔽行已恢复原样；原文件备份在 backup\ 目录。'
