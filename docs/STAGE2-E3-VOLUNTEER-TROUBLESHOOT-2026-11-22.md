# E3 志愿者端排查单（2026-11-22）

> 你说"服务器连不上"。我方查了服务器日志：**你 14:53 用 curl 能拿到 200，网络/证书/CRL 全是通的**；
> 而且今天 **12:38–12:40 你的游戏真的连上来并跑通了整套请求**（登录、大厅、保活，全部 200）。
> 所以问题几乎肯定在**本机、且发生在游戏调用我方 API 之前**——最常见的是 Steam 被 hosts 屏蔽行挡住。
> 按下面 3 步走，1 分钟能定位。

## 0. 最可能的原因（先试这个，30 秒）

E2 结束后如果你跑过 `rollback-all.ps1`，它会把 hosts 里 11 条 **Steam 屏蔽行**（含 `login.steampowered.com`）
**原样恢复**。这些行会挡住 Steam 登录/联机，游戏就会报"连不上服务器"，
**但 curl 依然通**（curl 不需要 Steam）——正好符合现在的现象。

**修复：以管理员 PowerShell 重跑一次接入脚本即可**

```powershell
.\install-hosts.ps1 -ServerIp 106.52.201.42
```

看到这三句就对了：`已临时注释 N 条 Steam 屏蔽行` / `hosts 已写入` / `CRL 服务已启动`。
然后**重开 Steam → 重新启动游戏**再试。

> 注意：`install-hosts.ps1` 起的 CRL 窗口**测试期间不要关**。

## 1. 一键收集（把输出整段贴回来）

普通 PowerShell 里，在测试包目录（`C:\hd2-stage2`）粘贴运行：

```powershell
$ErrorActionPreference = 'Continue'
Write-Output "=== 1. 当前时间 ==="
Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'

Write-Output "=== 2. hosts 里的 HD2 / Steam 相关行 ==="
Select-String -Path C:\Windows\System32\drivers\etc\hosts -Pattern 'helldivers|#S302|steampowered' |
  ForEach-Object { $_.Line.Trim() }

Write-Output "=== 3. 研究根证书 ==="
$c = Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Subject -match 'HD2 Offline Research CA' }
if ($c) { $c | ForEach-Object { "thumb=$($_.Thumbprint) notAfter=$($_.NotAfter.ToString('yyyy-MM-dd HH:mm'))" } }
else { "缺：未安装研究根证书" }

Write-Output "=== 4. 本机 CRL 服务 18081 ==="
if (Get-NetTCPConnection -LocalPort 18081 -State Listen -ErrorAction SilentlyContinue) { "监听中" } else { "缺：18081 未监听" }
try { $r = Invoke-WebRequest 'http://127.0.0.1:18081/ca.crl' -UseBasicParsing -TimeoutSec 5; "CRL 可取 $($r.RawContentLength) B" }
catch { "CRL 取不到：$($_.Exception.Message)" }

Write-Output "=== 5. 到发现层 HTTPS（严格校验，绕过系统代理）==="
& curl.exe -sS --noproxy "*" -m 10 -o NUL -w "http=%{http_code} tls=%{time_appconnect}s`n" https://api.live.prod.thehelldiversgame.com/health

Write-Output "=== 6. 到发现层的实际响应 ==="
& curl.exe -sS --noproxy "*" -m 10 https://api.live.prod.thehelldiversgame.com/api/WarSeason/current/WarId
Write-Output ""

Write-Output "=== 7. 时钟同步 ==="
& w32tm /query /status | Select-String 'Leap|Source|Last Successful'

Write-Output "=== 8. Steam / 游戏进程 ==="
Get-Process steam, helldivers2 -ErrorAction SilentlyContinue |
  ForEach-Object { "$($_.ProcessName) pid=$($_.Id) start=$($_.StartTime.ToString('HH:mm:ss'))" }

Write-Output "=== 9. selfcheck 全量输出 ==="
if (Test-Path .\selfcheck.ps1) { .\selfcheck.ps1 -ServerIp 106.52.201.42 2>&1 | Out-String }
```

## 2. 每一项的正常/异常含义

| # | 项 | 正常 | 不正常时说明什么 |
|---|---|---|---|
| 2 | hosts | 有 `100.67.88.…`/`106.52.201.42 api.live.prod…` 一行；**`#S302`/`steampowered` 行都带 `#HD2-STAGE2-DISABLED#` 前缀** | Steam 屏蔽行还在生效 → 重跑 `install-hosts.ps1` |
| 3 | 根证书 | `thumb=23ED103B…` | 缺 → 重跑 `install-hosts.ps1` |
| 4 | CRL | `监听中` + `CRL 可取 366 B` | 没有 → CRL 窗口被关了，重跑 `install-hosts.ps1` |
| 5 | HTTPS | `http=200` | 非 200/超时 → 才轮到怀疑网络或我们服务器（把输出给我） |
| 6 | 响应体 | 形如 `{"warId":801,…}` 的 JSON | 空/异常 → 把原文给我 |
| 7 | 时钟 | Leap 指示正常 | 偏移大 → `w32tm /resync` |
| 8 | 进程 | 有 `steam`，尝试时也有 `helldivers2` | 没有 → 游戏没在跑 |

> 关键判据：**第 5/6 项是 200 但游戏仍报错 → 100% 是本机 Steam/EAC 层，不是网络层**。

## 3. 如果上面全绿、游戏还是报错，请给我三样

1. **报错原文/截图**：出现在哪个界面（启动器 / 主菜单 / 好友列表），文字尽量完整；
2. **你点击/启动的确切时间**（精确到秒，含时区）—— 我会拿这个时间到服务器日志里逐秒对照，
   只要请求到达服务器就一定有记录，这是最硬的判据；
3. 第 1 节那整段输出。

## 4. 顺带提醒（E2 的既有结论，仍然有效）

- 测试期间**关掉所有 VPN / 加速器 / Radmin**；
- 先登录 Steam（不要离线模式）再启动游戏；
- 不要用系统的"代理设置"指向任何加速器 —— 游戏不走系统代理，但 `curl` 会走，会造成假象
  （所以上面 curl 都加了 `--noproxy "*"`）；
- 结束回滚：`.\rollback-all.ps1`（停 CRL、恢复 Steam 行、删 hosts 标记行、卸证书）。
