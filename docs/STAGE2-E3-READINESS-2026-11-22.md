# E3 就绪记录（2026-11-22 14:46）—— 带「公钥交换」修复的双机复测环境

> 目的：E2 根因（缺 `GET /api/Account/Keys` 公钥交换）已修并部署 A 机后，
> 把两侧环境搭到「开游戏即可测」的状态，并给出**这次要看什么**的判据。
> 三态：✓✓ 实机；✓ 离线/数据；⚠ 未证。
> 前序：`STAGE2-REAL-JOIN-FLOW-2026-11-22.md`（契约与根因）、`STAGE2-E2-READINESS-2026-11-21.md`（网络拦截与中继方案）。

## 1. 双方就绪状态

| 侧 | 状态 |
|---|---|
| 发现层（服务器 A，nginx 反代） | ✓✓ `hd2-discovery` active；`healthcheck.sh` **PASS**；发现层状态**干净**（0 个活跃厅、5 个账号、4 个 tombstone、generation 38）；`--log-headers` 已开 |
| 主持方（本机） | ✓✓ 研究 CA 已装（`23ED103B…`，到 2026-11-23 20:32）；本地 CRL `127.0.0.1:18081` 在跑；hosts → 本机中继；中继已通 A |
| 志愿者（DESKTOP-QJ0SO7U） | ✓ 包 `C:\hd2-stage2` 15 个文件齐全，`ca.cer`/`ca.crl` 与 A 机**字节一致** → **无需更新**，照 E2 同一套脚本即可 |
| 时间窗 | 证书 11-23 12:32 UTC / CA 11-23 20:32 本地到期；建议 **11-23 之前**完成 |

## 2. 本机环境（E3 专用，已实测）

| 项 | 值 |
|---|---|
| 研究 CA（A 机签发用，DER） | `state/certs-a/ca.cer`，sha256 `284c18b8…bee8`，`CN=HD2 Offline Research CA` |
| 同一 CA 的 PEM（给 Python 用） | `state/certs-a/ca.pem` |
| CRL | `state/certs-a/ca.crl`，366 B，sha256 `664f4d17…cb7`，`nextUpdate=Nov 24 12:32 UTC` |
| CA 装入用户根 | 指纹 `23ED103BC72332C6DF1CD9DD627C479591E9037C`（`certutil -user -addstore Root`） |
| 本地 CRL 服务 | `127.0.0.1:18081`，脚本 `tools/crl_serve.ps1`（本轮新增入库），后台 job `shell/gitbash-37f9bc4da5d54790` |
| 中继 | `100.67.88.219:443` → 经 server2(8.163.50.174) → `106.52.201.42:443`；PID **17932**，日志 `state/evidence/e3prep/tunnel.log` |
| hosts | `100.67.88.219 api.live.prod.thehelldiversgame.com # HD2-STAGE2-TUNNEL 20261122`；备份 `hosts.bak-e3-20261122-144423` |

A 机证书：`CN=api.live.prod.thehelldiversgame.com`，`notAfter=Nov 23 12:32:03 UTC`，AKI `5F:1E:9D:6B:…`（与本 CA 的 SKI 一致）。

## 3. 端到端验证（✓✓ 本轮实机）

| 检查 | 命令 | 结果 |
|---|---|---|
| 严格 TLS（Windows/Schannel，不用 `-k`） | `curl --noproxy '*' https://api.live.prod.thehelldiversgame.com/health` | **200**，`Server: nginx`，`X-Mock-Source: health`（说明 CA 信任 + CRL 获取 + 中继 + nginx + mock 全链通） |
| 全量协议一致性 | `python tools/discovery_conformance.py --url https://api.live.prod.thehelldiversgame.com --cafile state/certs-a/ca.pem --unknown-policy error404` | **14/14 PASS**（含 C14 公钥按 accountId 可取） |
| A 机本地 | `healthcheck.sh` | PASS |

> **踩坑记录**：本机 `https_proxy=http://127.0.0.1:7897`，`curl`/`Invoke-WebRequest` 会走系统代理从而**绕过 hosts**（表现为 404 或 `HTTP/1.1 200 Connection established` 后超时）。
> 命令行验证必须 `curl --noproxy '*'`；**游戏本身不走系统代理**，不受影响。
> Python 的 ssl 不读 Windows 证书存储，且 `ca.cer` 是 DER → 用 `state/certs-a/ca.pem`（本轮给一致性套件加了 `--cafile`）。

## 4. E3 这次要看什么（判据）

| # | 观察点 | 通过的样子 | 失败时抓什么 |
|---|---|---|---|
| 1 | 好友状态 | 对方显示 **On Ship**（不是 Online），且可点击加入 | 好友列表截图 + 我方 `lobbies[]` 是否含对方真实 id |
| 2 | **公钥交换（本次修复的判据）** | mock 日志出现 `GET /api/Account/Keys?id=…` 且**返回体 > 20 B**（`{"accountKeys":[]}` 是 18 B；带一把 key ≈ 100 B），source=`discovery:account_keys` | 日志里该行的字节数、请求的两个 id 各自是谁 |
| 3 | 加入是否越过加载 | 进舱 / 看到对方角色，不再卡"加载中" | PlayFab `JoinLobby`/`RequestParty` 结果、Party telemetry 4104/4379 |
| 4 | 双向都测 | 我进对方房（对方当房主也过门槛） | 两端都要看，公钥是**双方**互相查 |

日志跟随（开测前起，脱离会话）：

```bash
python tools/e2_watch.py --servers-json C:/Users/Admin/.dsh/mcp-servers/ssh-mcp-server/servers.json \
  --via default --remote /opt/hd2-discovery/state/logs/mock.log \
  --local state/evidence/e3prep/e3-mock-watch.log --interval 2
```

## 5. 志愿者侧（与 E2 完全同一套，无需更新）

1. 管理员 PowerShell 跑 `.\install-hosts.ps1 -ServerIp 106.52.201.42`（自动：临时注释 Steam 屏蔽行、写 hosts、装根证书、起本机 CRL 18081）；
2. 普通 PowerShell 跑 `.\selfcheck.ps1 -ServerIp 106.52.201.42`，看到全绿；
3. 开游戏，好友列表里找到我方，看是否 **On Ship** 并可加入；
4. 结束跑 `.\rollback-all.ps1`（停 CRL、恢复 Steam 行、删 hosts 标记行、卸证书）。

> E2 已知的志愿者侧注意点不变：`hosts` 里原有 11 条 Steam 屏蔽行会被临时加 `#HD2-STAGE2-DISABLED#` 前缀，
> `rollback-all.ps1` 原样恢复；CRL 服务必须保持运行，否则 Schannel 拒绝握手。

## 6. 回滚（E3 结束）

```bash
# 1) 停中继 + CRL 服务（后台 job）
job_kill shell/gitbash-b9323f92e0484cf2   # 中继
job_kill shell/gitbash-37f9bc4da5d54790   # CRL
# 2) 还原 hosts（用本轮备份），flushdns —— 需一次 UAC
# 3) 卸研究 CA（用户级）
powershell -Command "Get-ChildItem Cert:\CurrentUser\Root | ? { \$_.Thumbprint -eq '23ED103BC72332C6DF1CD9DD627C479591E9037C' } | Remove-Item"
# 4) 复核：hosts 无 helldivers 行；18081 无监听；CA 不在根存储；:443 无监听
```

## 7. 已知限制 / 风险

1. **证书寿命**：A 机叶子 11-23 12:32 UTC、CA 11-23 20:32 本地到期。过期需在 A 机跑 `deploy/discovery/cert-issue.sh` 并**重新分发 ca.cer 给志愿者**。
2. **账号归属靠来源 IP**：我方经中继 → A 看到的来源是**服务器 B 的公网 IP**；志愿者是其宽带 IP。两者可区分，但若志愿者也用中继则需显式 `X-HD2-Account`。
3. **公钥到达时序**：对端登录后、首次 `PUT /api/lobby` 之前若被查 `Account/Keys` 会查不到（HellForge 用 3 s 等待兜底，我们暂未加）。E3 若在这里失败，再加等待。
4. PlayFab 保持**真实**（不启 MITM）；Party 4104/4379 是否仍出现需 E3 判定。
