param([switch]$DryRun)
# 一键回滚：hosts 标记行 + 当前用户代理 + 测试证书
. "$PSScriptRoot\lib.ps1"
Write-Step '一键回滚（hosts / 用户代理 / 测试证书）'
try { Remove-HostsEntry -DryRun:$DryRun } catch { Write-Warn2 "hosts 回滚跳过：$($_.Exception.Message)" }
try { Clear-UserProxy    -DryRun:$DryRun } catch { Write-Warn2 "代理回滚跳过：$($_.Exception.Message)" }
try { Remove-UserCert    -DryRun:$DryRun } catch { Write-Warn2 "证书卸载跳过：$($_.Exception.Message)" }
Write-Ok '回滚完成。hosts 只删带 # HD2-STAGE2 标记的行；原文件备份在 backup\ 目录。'
