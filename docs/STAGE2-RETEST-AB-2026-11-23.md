# 阶段2 复测作业单：A+B（`Account/Keys` 时序缝隙）— 2026-11-23

> **一句话**：A+B 已实现、已部署 A 机、已线上验证（2026-11-23 13:57），**实机一轮已于当晚 19:50 验完**：
> 兜底路径 **✓✓ 受控复现通过**（自然复现结构性不可行）。结果与四轮实录见
> `STAGE2-RETEST-AB-RESULT-2026-11-23.md`；本作业单保留为可复跑流程。
> 本作业单把它压成「准备 3 分钟 + 实机 10 分钟 + 判据自动算」，并写清**不要测错方向**。
>
> 配套设计档：`STAGE2-DESIGN-KEY-TIMING-GAP-2026-11-22.md`（§1.1 现场时序、§5 验收用例、§7 实施状态）。
> 配套工具：`tools/stage2_retest.py`（check / snapshot / judge，**全部只读**，不做任何写操作）。

## 0. 一页速览

| 环节 | 谁 | 内容 | 时长 | 需要的授权 |
|---|---|---|---|---|
| 准备 | 我方 | 装 CA → hosts → 本机 CRL → 中继 → 自检 | ~3 分钟 | **一次 UAC**（只有 hosts） |
| 志愿者 | 志愿者 | **不用换包**（CA 未变、20:32 前有效），按老流程装 hosts | ~2 分钟 | 管理员（已有流程） |
| 实机 | 双方 | 我先进船公开 → **志愿者重启后立刻点加入** | ~5 分钟 | — |
| 判定 | 我方 | `stage2_retest.py judge` 出 PASS / FAIL / INCOMPLETE | 1 分钟 | 无 |
| 收尾 | 我方 | 停中继/CRL → 还原 hosts → 卸 CA | ~2 分钟 | **一次 UAC**（hosts） |

**证书硬截止**：A 机叶子与 CA 均 **2026-11-23 20:32（本地）** 到期（14:00 实测剩 6.47 h）。
过了这个点重签会生成**新 CA**，志愿者必须换新 `ca.cer`/`ca.crl`（见 §6）。

## 1. 这一轮到底在验什么（测错方向等于白测）

**缝隙的方向**：**我方（房主）的客户端**去查**志愿者**的公钥。

```
志愿者重启 → 登录（新会话新公钥，A 机只知来源 IP、不知 accountId）
   … A 机学到 accountId 的唯一来源是志愿者自己的 PUT /api/lobby（实测晚 ~63 s）…
我方客户端 GET /api/Account/Keys?id=<志愿者>  ← 落在这段空窗里就是 miss（18 B 空数组）
   → 客户端静默放弃：好友显示 On Ship，点「加入」没反应
```

所以复现动作**必须是**「**志愿者重启后立刻点加入我的船**」。

| 动作 | 会不会触发缝隙 | 说明 |
|---|---|---|
| 志愿者重启 → 立刻点加入我的船 | **会**（本轮要测的） | 我方查志愿者的 key，此刻 A 机可能还没学到志愿者的 id |
| 我重启 → 立刻点加入志愿者的船 | 会，但方向相反 | 镜像用例，可加测（A+B 服务端对称，判据相同） |
| 志愿者先进厅、我再加入 | **不会** | 志愿者的 id 早已被学到，测的是命中路径（`keys_hit`），证明不了 A+B |

判据工具里 C01「兜底被触发」的 fail 含义是**「本轮没复现缝隙」**，不是「功能坏了」——
看到 C01 fail 先回头看志愿者是不是**没有立刻点**、或者点之前等了列表刷新。

## 2. 我方准备（4 步 + 自检）

### 2.0 现状基线（2026-11-23 14:00 实测，勿重复做）

- 本机**已回滚**：hosts 无 `thehelldivers` 行、无 18081 监听、无本地 443 监听、CA 未在根存储。
- A 机发现层**已重置为全 0**（`accounts=0 identities=0 keys_known=0 bindings=0`，
  `stats.*` 全 0，`generation=0`）——这是 B 兜底能触发的前提，已在本轮做完并留证：
  `state/evidence/retest-20261123/`（重置前状态文件 + 重置前 `/health`）。
  > 重置操作：`systemctl stop hd2-discovery` → `rm state/discovery-state.json` → `start`；
  > 重启后状态文件自动重建（106 B 空档），`/health` 计数归零。
- 直连 A 机 `106.52.201.42:443` 会 **RST**（2026-11-23 复测，与 E2 结论一致）⇒ 必须走中继。

### 2.1 装 CA（用户级，**不需要管理员**）

```powershell
certutil -user -addstore Root D:\mod\work\hd2-offline-research\state\certs-a\ca.cer
```

指纹应为 `23ED103BC72332C6DF1CD9DD627C479591E9037C`（工具自检会打出来核对）。

### 2.2 hosts → 本机中继（**唯一需要 UAC 的步骤**）

先备份，再追加一行（管理员 PowerShell）：

```powershell
Copy-Item $env:SystemRoot\System32\drivers\etc\hosts "$env:SystemRoot\System32\drivers\etc\hosts.bak-retest-$(Get-Date -Format yyyyMMdd-HHmmss)"
Add-Content $env:SystemRoot\System32\drivers\etc\hosts -Value '100.67.88.219 api.live.prod.thehelldiversgame.com # HD2-STAGE2-TUNNEL' -Encoding ascii
ipconfig /flushdns
```

> 本仓库既有提权模板（一次 UAC）：`MSYS_NO_PATHCONV=1 sudo --disable-input pwsh -NoProfile -NonInteractive -File <脚本> </dev/null`。

### 2.3 本机 CRL（前台常驻；Schannel 取不到 CRL 会直接断握手）

```powershell
pwsh -NoProfile -File tools\crl_serve.ps1 -CrlPath state\certs-a\ca.crl   # 127.0.0.1:18081
```

### 2.4 中继（前台常驻；游戏 → 本机 443 → B → A）

```bash
python tools/e2_tunnel.py --servers-json C:/Users/Admin/.dsh/mcp-servers/ssh-mcp-server/servers.json \
  --via server2 --listen-host 100.67.88.219 --listen-port 443 --target 106.52.201.42:443
```

> **可选单跳（2026-11-23 已实测，但不用于本轮验收）**：去掉服务器 B，改用 A 自己的 SSH，
> 目标写 A 的回环 443 —— 少一跳、少一台服务器依赖，游戏 TLS 仍端到端：
>
> ```bash
> python tools/e2_tunnel.py --servers-json .../servers.json --via default \
>   --listen-host 100.67.88.219 --listen-port 443 --target 127.0.0.1:443
> ```
>
> 为什么可行：实测确认被拦的是 **TLS SNI（游戏域名）**，不是 IP:443（`A:443 + 自有域名 SNI` → 200；`A:80 + Host=游戏域名` 明文 → 404 不拦）。
> 延迟收益约 30 ms/请求（~12%），详见 `STAGE2-RELAY-OPTIMIZATION-2026-11-23.md`。
> **本轮仍用上面经 B 的两跳命令**（E3 已验证两轮），单跳留作复测后的优化。

### 2.5 自检（8 项，应全 ✓）—— **必须在开游戏之前跑**

```bash
python tools/stage2_retest.py check
```

> **开测前手工确认一次**（30 秒）：没有 TUN 虚拟网卡、没有开加速器/代理的 TUN 模式：
> `Get-NetAdapter | ? { $_.InterfaceDescription -match "TUN|Wintun|TAP" }` 应为空。
> 原因：实测商业加速/VPN 节点会**按域名**把 HD2 流量导到官方后端（见 RELAY-OPTIMIZATION §10），
> 游戏会悄悄连官方服，排查成本极高。Clash 系统代理模式不受影响（游戏不读系统代理）。

> `A01` 这一项只在**开游戏前**成立（要求 A 机发现层全 0）。我方进了船之后 `accounts≥1`
> 是**我自己的账号**，不是污染——那时**不要再跑 check、更不要重置状态**，直接进 §2.6 拍基线。

| 项 | 检查什么 | 典型的失败原因 |
|---|---|---|
| CA01 | 证书材料在、CA 未过期 | 没取回 `state/certs-a/*` |
| CA02 | CA 在 `CurrentUser\Root` | 忘了 §2.1（游戏会直接握手失败） |
| HOST01 | hosts 指向 `100.67.88.219` | 漏写 §2.2 / 指向旧实验 IP `100.67.169.17` |
| CRL01 | 18081 提供的 CRL 与本地**字节一致** | CRL 服务没起 / 起的是旧 CA 的 CRL |
| TRL01 | `100.67.88.219:443` 可连 | 中继没起 |
| E2E01 | 经中继完整 TLS + `GET /health` | 前面几项的连带结果 |
| CERT01 | 服务端叶子剩余时间 | 已过期 → 必须重签（§6） |
| A01 | A 机发现层净空 | 状态被污染 → 会让 B 兜底不触发（见 §7） |
| （手工） | **无 TUN / 加速器接管** | 有 TUN 网卡或加速器在跑时，游戏流量可能被"按域名"导到官方后端（2026-11-23 实测商业节点行为），现象像"连不上" |

### 2.6 基线快照（必须在志愿者重启**之前**拍）

```bash
python tools/stage2_retest.py snapshot --out state/evidence/retest-20261123/health-before.json
```

> 此刻我方账号应已登录并被 A 机学到（`accounts≥1`、`login≥1`）；**候选集只应剩志愿者一个**，
> 这是 B 生效的理想条件。
>
> **为什么基线要拍在「我进船之后」而不是「双方都还没登录」**：判据比的是增量，基线越晚越干净
> （我方的登录/上报会被算进 Δ，但这几项本来就是"应该有增量"的）。反过来若基线拍在开游戏前，
> 我方自己那一次登录+PUT 也要算增量，判据会平白多要求一次。

## 3. 实机动作（顺序不能换）

| # | 我方 | 志愿者 | 目的 |
|---|---|---|---|
| 1 | 重启游戏 → 进飞船 → 隐私设 **Public** → 等好友列表显示我在船上 | 保持在线（**先别重启**） | 让志愿者能看到我的船 |
| 2 | 拍 **before** 快照（§2.6） | — | 增量基准 |
| 3 | — | **重启游戏** → 进飞船 → 列表里一出现我的船就**立刻点「加入」** | **缝隙就发生在这一下** |
| 4 | 不要动游戏，等 30–60 s | 进去后正常待着 | 让 `member` 保活跑满一轮（拿到双人厅体） |
| 5 | 拍 **after** 快照（同 §2.6 换文件名） | — | 判定输入 |

**三条纪律**：

1. **只点一次**。「加入没反应再点一次就成功」正是本轮的缺陷现象——点第二次会把成功伪装成正常，
   让 `keys_bind_hit`/`keys_waited` 无法归因。
2. 志愿者**不要先等列表刷新再等几秒**：重启 → 进飞船 → **立刻**点。
3. 我方在步骤 3 期间**不要重启**（我方重启会让我方 id 进入未知态，A 机候选变 2，B 兜底失效）。

> ⚠ **重启会清零统计**：`/health` 的 discovery 统计是内存态，`systemctl restart hd2-discovery` 后全部归零。
> 因此**先拍 after 快照，再关测试闸门/重启服务**，否则增量证据会丢（2026-11-23 踩过）。

## 4. 判定

```bash
python tools/stage2_retest.py snapshot --out state/evidence/retest-20261123/health-after.json
# 有 mock 日志跟随（tools/e2_watch.py）时一并给它，才能验「双人厅体 ≥1000 B」
python tools/stage2_retest.py judge \
  --before state/evidence/retest-20261123/health-before.json \
  --after  state/evidence/retest-20261123/health-after.json \
  --log-file state/evidence/retest-20261123/mock-watch.log
```

判定规则（工具内固定，逐条可复算）：

| 判据 | 通过条件 | 含义 |
|---|---|---|
| V01 | accounts / login 不减少 | 增量可信（中途没被重置） |
| **C01** | `keys_bind_hit` 或 `keys_waited` **有增量** | **缝隙被复现且兜底生效**（核心） |
| C02 | `keys_miss` **不增** | 等待没有退化成未命中 |
| C03 | `join_rejected` 不增 | 成员帧没被拒 |
| C04 | `login` Δ≥2 | 双方都重新登录过 |
| C05 | `put` Δ≥2 且（`keys_hit`+`keys_bind_hit`）Δ≥1 | 双方都上报身份、Keys 有成功解析（**2026-11-23 修正**：原来用 `lobby_get` 冒充"查过 Keys"是错的，镜像方向实测 `get` 恒 0）|
| C06 | `join` Δ≥1 | 加入被接受 |
| C07 | `bindings` ≥1 | B 的产物留痕 |
| L01 | 日志里出现 **≥1200 B** 的 member 响应 | 双人厅体（实测 **1 人上限 1184 B**、2 人 **1236/1237 B**、任务开始 1340+；1000–1199 B 只判 WARN＝疑似只有自己） |
| L02 | 日志里 `[discovery:lobby_member:rejected]` 为 0 | 无拒绝 |

> **阈值是实测校准过的，不要"顺手改小"**：E3 两轮真实日志的 `member` 回包直方图为
> 占位/空壳 344/349/369 B、**1 人真实厅体 1158–1184 B**、2 人 **1236/1237 B**、
> 任务已开始 1340–1457 B。若把双人分界设成 1000 B，单人厅体会被当成双人证据 → **假 PASS**。
> 校准后复核：E3 第二轮共 35 次 `member 200`，其中真实厅体 22 次、双人档 3 次（1236×2 + 1237×1），
> 与结项档记录的「入厅成功 = 1236/1237 B 双人厅体」一致；`tests/test_stage2_retest.py`
> 有回归用例把这个分界钉住。

三类结论与处置：

- **PASS** → 收口：把结果写进 `STAGE2-DESIGN-KEY-TIMING-GAP` §7.4 / `STAGE2-E3-RESULT` 增补节，
  并把「实机复测」从 ⏳ 改成 ✓✓。
- **FAIL 且只有 C01** → 本轮**没复现缝隙**：检查志愿者是否「重启后立刻点」、
  我方是否在步骤 3 期间重启过、快照是否拍在动作之前。按 §3 重来一次。
- **FAIL 且含 C02/C03/L02** → 兜底确实出了偏差：把 after 快照 + mock 日志存档，
  按 `mock/discovery.py` 的 `account_keys()` 分支逐步回查（等待窗口 / 单未知候选 / 拒绝路径）。
- **INCOMPLETE** → 只有 L01/L02 缺证据：补 `--log-file` 重判，不必重测。

## 5. 收尾回滚（与 E3 同一套，可逆）

```bash
# 1) 停中继 + CRL（各 Ctrl+C 或 job_kill）
# 2) 还原 hosts（用 §2.2 的备份）+ ipconfig /flushdns —— 一次 UAC
# 3) 卸研究 CA（用户级）
powershell -Command "Get-ChildItem Cert:\CurrentUser\Root | ? { \$_.Thumbprint -eq '23ED103BC72332C6DF1CD9DD627C479591E9037C' } | Remove-Item"
# 4) 复核：无 443 监听、无 18081 监听、hosts 无 thehelldivers 行、CA 查不到
python tools/stage2_retest.py check   # 应回到「CA02/HOST01/CRL01/TRL01/E2E01 fail」的回滚态
```

> 注意：`check` 的回滚态**本来就是 fail**（5 项），这是判据不是故障——回滚完成的标志正是它
> 回到「ok 1 / fail 5 / skip 2」这个形状。

## 6. 若 20:32 前开不了测：重签 + 再分发

**重签会生成新 CA**（`mock/certs.py:generate()` 每次 `_new_key()`），因此志愿者必须换包。步骤：

1. A 机重签：`bash /opt/hd2-discovery/deploy/discovery/cert-issue.sh`（新叶子 + 新 CA + 新 CRL）。
2. 取回新材料覆盖本地：`ca.cer`、`ca.crl`（还有 `ca.pem`，Python 用）→ `state/certs-a/`。
3. 本机：先卸旧 CA（§5 第 3 步，旧指纹），再按 §2.1 装新 CA。
4. 志愿者包：12 个文件里**只替换 `ca.cer`、`ca.crl`**（其余不变），用
   `python tools/package_stage2_kit.py --ca <新 ca.cer> --crl <新 ca.crl> --server-ip 106.52.201.42 ...`
   重打 zip 并核对 SHA256。
5. 重新跑一遍 §2.5 自检：`CA01` 会打**新指纹**，`CERT01` 会显示新的到期时间。

> **这条换包路已排练过（2026-11-23 14:20）**：用当前证书重跑 `package_stage2_kit.py` 产出
> 12 条目的 zip（9 个 kit 脚本 + `ca.cer` + `ca.crl` + `server.txt`），包内 kit 文件与仓库
> `kit/stage2` **逐字节一致**，`server.txt` 正确打印到期时间；同时核对志愿者机器上已交付的
> 9 个 kit 文件与仓库**逐字节一致**（无漂移）⇒ 重签后「只换 `ca.cer`/`ca.crl` 两个文件」
> 这条指令是**可验证**的，不需要让志愿者重装整套。

## 7. 已知风险 / 不要做的事

1. **A 机状态被污染 ⇒ B 不触发**：单未知绑定要求「有且仅有一个刚登录未上报的账号」。
   任何探针/healthcheck（`discovery_probe.py` 会登录）都会留下这种账号。
   → **开游戏前**若 `A01` 报 fail，按 §2.0 的步骤重置一次；**重置后不要再跑 healthcheck.sh**。
   ⚠ 反过来：我方已经进船时 `accounts≥1` 是预期的（那是我自己），此时**不要**按 A01 的提示重置
   —— 重置会把基线增量打乱（`judge` 的 V01 会直接判 fail）。
2. **不要把 `keys_hit` 当通过**：那是「没遇上缝隙」的命中路径，证明不了 A+B。
3. **不要缓存公钥**：公钥是会话级（同一账号 5 次登录 5 把 key），缓存旧值等于制造新的 miss。
4. **CRL/中继必须前台常驻**：后台 job 被杀后游戏侧表现为握手失败，排查成本高。
5. **不要直连 A 机**：2026-11-23 实测被网关 RST，必须走中继。
6. **命令行验 HTTPS 要 `--noproxy '*'`**：系统代理 Clash 会绕过 hosts 造成假象。
7. **3 人以上场景本轮不覆盖**：候选 ≥2 时按设计不猜，属于已知限制（见设计档 §6.3）。
