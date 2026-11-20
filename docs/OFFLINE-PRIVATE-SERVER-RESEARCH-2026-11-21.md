# HD2 离线单机 / 自建联机研究线 · 阶段一（单机离线）结项档（2026-11-21）

> 执行依据：`D:\mod\work\hd2-proxy-toolkit\docs\HANDOFF-OFFLINE-PRIVATE-SERVER-2026-11-20.md` **§9 runbook**。
> 本档所属仓库：`D:\mod\work\hd2-offline-research`（研究线独立仓，单独 git 管理）。
> 用户排序要求（2026-11-21 00:xx）：**先做单机离线（线 A），再做自建联机（线 B）** —— 本档按此排序。
> 三态：`✓✓ 实机` / `✓ 离线+数据`（本机可复跑、有输出）/ `⚠ 未证` / `✗ 否证`。

---

## 0. 一句话结论

**线 A 的单机闭环已实机跑通：登录 → 主界面 → 单人任务（进图/局内/撤离结算）→ 回主界面，官方 API 零外连，
全部由本地 mock 应答。** 三段实机（2026-11-21 01:14 首验、01:31 空大厅 profile 复验、01:35–01:39 任务链）
全部通过：登录 437 B → 200 `sessionId`；任务链 `Operation/Create → Mission/Start → Mission/end → Mission/Session → Abandon`
逐条 200/202，**客户端正常显示结算画面**（用户确认），EAC-on 启动也照常工作。
工程侧 73 条抓包响应 100% 命中、时间字段按规则平移、TLS 在 Windows Schannel 与 .NET SslStream 均握手成功。
**A1 ✓✓ / A3 ✓✓ 关闭**；A2 剩域名级清单（需管理员抓包）、A4 按边界不验。

---

## 1. 本轮执行了交接档 §9 的哪些步骤

| §9 步骤 | 状态 | 证据 / 产物 |
|---|---|---|
| §9.0 工作区边界 30 秒自检 | ✓ | `Get-Location=D:\mod` 且不在任何 git 仓内；产品仓库 `git -C D:\mod\work\hd2-proxy-toolkit status --short` **空**；443/8080/17897 全部无监听 |
| §9.1 读交接档 + 三份研究资料 | ✓ | API-ENDPOINTS / MISSION-CAPTURES / FINDING-零安装客机 已读并用于设计；另读 `D:\mod\out\gwe\` 的 18 组 Operation/Create、5 份 Mission/end、PUT lobby 报文 |
| §9.2 环境自检 | ✓ | 产品版本 **0.4.3-alpha.1**（交接档快照写 0.4.1-alpha.5，已过期）；hosts 第 16 行仍指 `100.67.169.17`；无 hd2proxy / helldivers2 进程；无 TUN/UU 网卡；默认路由走以太网 `100.67.0.1` |
| §9.3 「不需要用户操作」的部分 | ✓ | 见 §2–§4：mock 实现 + 脱敏样本 + 四道门禁 |
| §9.4 需要用户配合的部分 | ⚠ 进行中（T1/T2 已完成，T3 待做） | T1 证书信任、T2 启动游戏/主界面、T2b 出网审计已完成（§2.4）；剩 T3 单人任务、T4 正式抓包 |
| §9.5 产出结论文档 | ✓（位置有偏差） | 本档写在研究仓 `docs/`；**没有**写进产品仓（§9.5 说「写回本文档同目录」与开头 ⛔ 边界「禁止写入产品仓」冲突，见 §5.1） |

---

## 2. 本轮交付与门禁证据（本机可复跑）

研究仓内容：`mock/`（回放+状态机+TLS）、`tools/`（分析/脱敏/回放/控制台）、`tests/`（pytest **63** 项）、
`profiles/`、`docs/`。**没有改动产品仓库任何文件。**

### 2.1 四道门禁（全部 PASS）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 单测 | `python -m pytest tests -q` | **63 passed**（阶段一为 56；T3 准备新增证书信任命令 6 项 + 空大厅/结算中性化 2 项） |
| 离线门禁（证书→CRL→TLS→两种回放） | `python tools\mockctl.py selftest` | **SELFTEST PASS** |
| 真实抓包 handler 回放 | `python tools\replay_client.py --src samples\store-season --mode handler` | **11/11 PASS**（73/73 命中、fallback=0、未识别时间字段=0） |
| 脚本化启动链+任务链+大厅链 | 同上 `--mode scenario` | **46/46 PASS** |
| TLS 端到端（真实 socket） | 同上 `--mode server`（对 127.0.0.1:18443 的 mock） | **5/5 PASS**（73 条请求全成功、fallback=0） |

关键输出摘录：

```
[PASS] 抓包 73 条响应全部被 mock 接住（fallback=0） — 73/73 命中
[PASS] 未出现未识别时间字段（下次抓包可扩展规则） — []
[PASS] Status.time 按抓包时刻平移到当前战争时钟 — recorded=87057730 got=87252452 expected≈87252452
[PASS] timeSinceStart 独立计数器同样平移 — recorded=88957993 got=89152714
[PASS] Operation/Create 回显客户端提出的 id — client=1284 resp=1284 [state:operation_create]
[PASS] Mission/end 回填 operationId/missionId 且带 rewards — operationId=1284 missionId_match=True rewards=2
[PASS] GET /api/lobby/{id} 原样返回 connectionString（自建信令核心） — lobbyId_match=True connstring_forwarded=True
[PASS] TLS 回放 73 条请求全部成功 — []
SUMMARY mode=server: 5 passed, 0 failed, 5 total
```

### 2.2 变异测试（门禁真的会红）

按用户门禁偏好做的一次「故意改坏 → 必须 FAIL → 恢复 → 必须 PASS」：

| 步骤 | 结果 | 证据文件（本地，gitignore） |
|---|---|---|
| 把 Status 时间规则从 `war` 改成 `keep` | **3 failed, 53 passed**（`test_war_fields_shift_by_elapsed` / `test_status_time_shifted_one_day` / `test_time_advances_between_two_polls`） | `state/evidence/mutation-broken.log` |
| 恢复文件（逐字节一致） | **56 passed in 5.22s** | `state/evidence/mutation-restored.log` |

### 2.3 脱敏样本

```
[sample] samples\store-season: 73 条, leak_check=PASS,
         scrub={'uuid': 1, 'name': 42, 'zeros': 267, 'lobbyid': 20, 'connection': 10, 'fill': 4}
[sample] mission-end 模板 5 份 -> samples\mission-end
```

* 原始抓包只读引用：`D:\mod\tmp\capture-store-season-20261118\raw`（73 响应）、
  `D:\mod\tmp\capture-new-account-20261117\raw`（53 响应，作为第二份回放矩阵）；
* 脱敏对象：`connectionString` / `playerName` / `memberAccountId|platformAccountId|accountId|account` /
  `sessionId` / `lobbyId` / lobby `seed`；`sample_build.py` 内含**原值残留自检**（任一原值还能在样本里
  被搜到就 `SystemExit`）；
* `samples/`、`state/` 全部 gitignore —— 即便脱敏也不入库。

### 2.4 实机验证（2026-11-21 01:10–01:32，EAC-on 正常启动）

游戏环境：build `26568081`，Steam 在线，`EasyAntiCheat_EOS = Running`，hosts → `100.67.169.17`，
mock 绑 `100.67.169.17:443` + CRL `127.0.0.1:18081`。

#### 2.4.1 T1：证书信任 + 三条 Windows 原生链路（✓✓）

| 检查 | 证据 |
|---|---|
| CA 装入 `CurrentUser\Root` | `certutil -user -addstore` exit=0；`certutil -user -verifystore Root F8388638…` exit=0；`is_trusted.CurrentUser=true` |
| Windows **Schannel**（`curl.exe` 8.23.0 Schannel） | `GET WarId` → `{"id":801}`；`POST Login` → `{"sessionId":"…"}`；`GET Status` → 200 / 52,038 B |
| .NET **SslStream**（`Invoke-RestMethod -NoProxy`） | WarId 与 timeSinceStart 均 200 |
| 全量回放经 LAN IP:443 | `mode=server` **5/5 PASS**（73 条请求全成功、fallback=0） |

两个踩坑（已修/已记）：
1. `Import-Certificate` 在非交互 `powershell -NoProfile` 下报 `Cannot find drive 'Cert'` → 研究线改用系统原生
   **`certutil`**（`trust_argv()` 纯函数 + 6 项单测）；
2. 本 shell 有 `HTTPS_PROXY=127.0.0.1:7897`，curl 默认走代理会**直连真实服务器**（拿到官方
   `errorCode 10002026`）→ 证书门禁必须 `--noproxy '*'`；已确认该变量**非持久**、WinHTTP 为「Direct access」。

#### 2.4.2 T2：启动游戏 → 登录 → 主界面（✓✓，A1 关闭）

| 观测 | 值 |
|---|---|
| 游戏进程 | `helldivers2`（多轮 PID：16920 / 36844 / 10048），`Responding=True`，窗口 `HELLDIVERS™ 2` |
| 登录 | **`POST /api/Account/Login 437B -> 200 52B`** —— 437 B 与 B3 侦察的「定长 437 B」完全吻合；客户端接受 mock 的 `sessionId` |
| 启动请求 | 34–37 个端点、40+ 条请求全部 200；`4xx/5xx=0`、`fallback=0` |
| 主界面 | 截图确认飞船内可交互（订单/装备/社交/战备面板）；好友/Episode/Stats 周期轮询正常 |
| 旧大厅噪声 | 首轮出现 `POST /api/lobby/<录制旧id>/member`（mock 回 `{}`，游戏容忍）→ **T3 前已改成空大厅列表** |

#### 2.4.3 T2b：出网审计（180 秒 200 ms 全程序采样）

采样文件（本地、gitignore）：`state/evidence/t2b-connections.jsonl`。窗口覆盖 EAC 引导 → 登录 → 主界面。

**游戏/关联进程连过的全部 TCP 远端**：

| 远端 | 归属 | 判定 |
|---|---|---|
| `100.67.169.17:443` ×4 | 本机 mock | ✅ 官方 API 的唯一去处 |
| `127.0.0.1:7897` ×8 | 本机代理隧道（verge-mihomo） | ⚠ 本地隧道，出网由代理完成，去向不可见 |
| `2603:1061:14:164::1:443` ×2 | Microsoft Azure IPv6 | 旁路（PlayFab/EOS 类） |
| `32.200.4.29:443` ×2 / `44.245.124.232:443` / `100.63.145.185:443` | AWS（us-east-1 / us-west-2） | 旁路 |
| `2600:9000:…:443` / `104.18.124.108:443` | AWS CloudFront / Cloudflare | CDN/旁路 |
| `54.192.35.5`、`104.18.125.108`（`start_protected_game`） | CloudFront / Cloudflare | EAC 启动引导 |

**真实 API 的三个 IP（公共 DNS：`68.154.14.134` / `172.200.240.121` / `172.200.240.16`）在整个窗口出现
0 次**；mock 侧同窗口收到 41 条请求 / 34 端点、0 错误。

结论口径：
* ✅ **官方游戏服务器零交互**（登录/战况/养成/任务/大厅全由本机 mock 应答；mock 代码无上行转发、进程无出站连接）；
* ❌ **「没往任何服务器发数据」不成立**：PlayFab（大厅/Party）、EOS/EAC、Sony RECAP、本机代理隧道仍在与外部通信
  —— 这正是交接档 §1 划定的边界（Steam/EAC 不接管），不是泄漏，但确实有出网；
* 盲区：UDP 目的地址 netstat 不可见（Steam SDR / EOS 中继）；代理隧道去向不可见（Verge HTTP 控制口关闭）。

#### 2.4.4 T3 准备（已完成）

| 改动 | 原因 | 证据 |
|---|---|---|
| `profiles/offline-mock.json` → `lobby.mode="empty"` | 不再把录制里的「别人的旧大厅」抛给客户端 | `/api/lobby/accounts` 响应 11,624 B(录制) → **34 B(空列表)**；`member` 调用 **0 次** |
| `Mission/end` 结算中性化（`campaignWon/impact/othersImpact*` → 0/False） | 离线不能假装「星球已解放」 | 单测 + scenario 断言 |
| mock 重启 + 游戏重启复验 | 保证 T3 从干净会话开始 | 01:31 复验：28 条请求、0 错误、登录 437 B |

#### 2.4.5 T3：单人任务链（✓✓ 实机通过，2026-11-21 01:33–01:40）

用户正常操作：星图选任务 → 开局 → **约 4 分钟任务 → 正常撤离结算**（用户确认结算画面正常）。

| 时刻 | 端点 | 请求 | 响应 | 命中 |
|---|---|---|---|---|
| 01:33:56 | `POST /api/Operation/Create` | 298 B | 200 / 60 B | `state:operation_create`（回显客户端 id） |
| 01:35:08 | `POST /api/Operation/Mission/Start` | 166 B | 200 / 52 B | `state:mission_start`（分配 missionId）**← 进图** |
| 01:35:39 | `POST /api/Operation/Mission/SetMaxRewards` | 73 B | 200 / 2 B | `state:mission_max_rewards` |
| 01:39:21 | `POST /api/Progression/inventory/consume` | 272 B | 200 / 2 B | 新端点（见下） |
| 01:39:21 | `POST /api/Operation/Mission/end` | 863 B | **200 / 1,617 B** | `state:mission_end`（模板回填，**结算画面正常**） |
| 01:39:28 | `PUT /api/Progression/Achievements` | 141 B | 200 / 2 B | 新端点（见下） |
| 01:39:28 | `POST /api/Mission/Session` | 1,249 B | **202 / 0 B** | `state:mission_session`（客户端接受 202） |
| 01:39:39 | `POST /api/Pes/GetIngameSurvey` | 961 B | 200 / 2 B | `state:pes_post` |
| 01:40:10 | `POST /api/Operation/Abandon` | 27 B | 200 / 2 B | `state:operation_abandon`（结算后自动清作战） |
| 01:40:15+ | 主界面轮询恢复 | — | 200 | 回船后一切正常 |

任务窗口 45 条请求、0 个 4xx/5xx、0 个 error，仅 2 条 fallback（见下的新端点）。

**A3 结论（✓✓）**：单人离线任务中**没有出现任何阻断性服务端校验** —— 进图、局内、撤离、结算、
`Mission/Session`(202)、`Abandon` 都被客户端正常接受，全程只有 mock 在应答。
限制：本次是约 4 分钟的正常撤离局（未覆盖长任务/失败撤离/被杀等分支）。

**T3 暴露的两个新端点（启动抓包里没有录制）**：

| 端点 | 说明 | 处置 |
|---|---|---|
| `POST /api/Progression/inventory/consume` | 结算前后消耗（补充类） | 已登记为 `state:progression_consume` → 200 `{}`（客户端接受） |
| `PUT /api/Progression/Achievements` | 结算后成就进度上报 | 已登记为 `state:progression_achievements` → 200 `{}`（客户端接受） |

> 这两条是「真正跑一局」才会出现的端点 —— 也正是登记 fallback 清单的价值：下次抓包/回放范围要补它们。
> 因为客户端接受空体，暂不需要录制真实响应；若要还原成就数值，得补抓。

---

## 3. 关键设计（capture → replay 的本地云端）

### 3.1 匹配与降级

`exact（含 query）→ base（去 query）→ template（数字/uuid 段抽象）→ fallback 200 "{}"`。
fallback 一律 **200**（不是 404）：客户端有轮询循环，4xx 会打断流程；
未命中会累计在 `health.unmatched`，是「下次要补抓哪些端点」的清单。

### 3.2 时间平移（A5 的工程答案）

抓包里发现**两个独立计数器**，差约 190 万秒，**不能互相替代**：

| 计数器 | 抓包值（2026-11-18T10:32Z） | 用途 |
|---|---:|---|
| `Status.time` | 87,057,730 | 事件 `expireTime`、空间站 `currentElectionEndWarTime`、MajorOrder `startTime` 的基准 |
| `timeSinceStart.secondsSinceStart` | 88,957,993 | 主界面「战争已进行」显示 |

平移策略：**按每条响应自己的抓包时刻**算 `delta = now - captured_at`（同一响应内部相对关系不变）。
规则表（`mock/timeshift.py`）四类基准：`war`（+delta）/ `elapsed`（+delta）/ `unix_fresh`
（已过期则重置到 now+7d）/ `keep`（倒计时与历史 Unix 时间原样保留）。未识别的「像时间」字段
**不动但上报**，并作为门禁：两份真实抓包的未识别字段当前均为 **0**（规则显式覆盖了
`FriendsV2.blockedTime`、`lobby.startTime`、`Progression.expiresIn`、`Stats.*Time` 等）。

### 3.3 有状态端点（自建联机的信令层雏形，先服务于单机）

| 端点 | 行为 | 证据 |
|---|---|---|
| `POST /api/Account/Login` | 每次发新 `sessionId`（uuid4） | 抓包响应只有 `{"sessionId":...}`（52 B） |
| `PUT /api/lobby` + `GET /api/lobby/{id}` | body **原样存表并原样返回**（含 `connectionString`） | `D:\mod\out\gwe\04853d5d_api_lobby_*`（1,246 B）与 `req_PUT_*_api_lobby.json`（1,350 B）同构。**限定**：byte 级转发成立，但客机真正「入厅」由 PlayFab Lobby 完成（见 §8 / 新未知 B8），**不能**据此推断"客机能加入" |
| `POST /api/Operation/Create` | **回显客户端提出的 operation id** | 全量核对 `D:\mod\out\gwe`：27 个 Create 响应里 **25 个**的 id 能在抓到的请求体里找到同 id（如 `1210→1210`、`1243→1243`），另 2 个缺对应请求文件 |
| `POST /api/Operation/Mission/Start` | 分配 `missionId`（uuid4） | 抓包响应 `{"missionId":"..."}`（52 B；gwe 里 59 份唯一响应） |
| `POST /api/Operation/Mission/end` | 用抓包模板回填 `operationId/missionIndex/missionId`，并把模板里的 `platformAccountId` 换成 mock 账号；离线默认把 `othersImpact*` 归零（不假装社区战果） | gwe 里 12 份唯一响应；样本取前 5 份作模板（`d0bdbe5b|12b9a872|dd2350f7` 等） |
| `POST /api/Mission/Session` | 202 空响应 | 已知它有服务端校验（errorCode 8012016），**别承诺"重连可用"** |
| `POST /api/Account/ReportPosition` | 200 `{}` | 反作弊位置上报，28 次/会话量级 |
| `POST /api/Progression/inventory/consume` | 200 `{}`（T3 实测客户端接受） | T3 结算前出现（272 B 请求）；启动抓包未录制，登记为已知空响应 |
| `PUT /api/Progression/Achievements` | 200 `{}`（T3 实测客户端接受） | T3 结算后出现（141 B 请求）；同上 |

### 3.4 TLS 配方（为什么必须这么做）

产品侧实测结论（交接档以外的产品源码只读参考）：**只有 CA 模式能握手**；叶子必须带 CDP 且 CRL 可取；
SAN 只放目标域名；有效期 ≤2 天；签完删 CA 私钥。研究线与产品的差别：CA 名字/目录/端口独立，
CRL 走 **127.0.0.1:18081**，信任默认装 **`CurrentUser\Root`（非管理员、可逆）**。
本轮补上的一个坑：叶子还必须带 **AKI**（AuthorityKeyIdentifier），否则 OpenSSL 3 链校验直接
`Missing Authority Key Identifier`（Python 客户端验证时暴露；Schannel 也要求）。

---

## 4. 线 A 未知点现状（A1–A5）

| # | 未知 | 现状 | 还缺什么 |
|---|---|---|---|
| A1 | 客户端是否接受回放登录 | **✓✓ 实机（§2.4.2）**：437 B 登录体 → 200 `sessionId` → 主界面；客户端只认 `sessionId`，不校验其它字段 | —— |
| A2 | 游戏除 API 主机外还连哪些主机 | ✓ 静态侦察完成（见 [A2-HOST-DEPENDENCIES-2026-11-21.md](A2-HOST-DEPENDENCIES-2026-11-21.md)）；**运行时首份清单已抓到**（§2.4.3：Azure/AWS/CloudFront/Cloudflare + 本机代理隧道；真实 API IP 0 命中） | 若要域名级清单（SNI/UDP）仍需 T4 正式抓包（需管理员） |
| A3 | 任务中是否有服务端实时校验 | **✓✓ 实机（§2.4.5）**：整局 4 分钟，进图/局内/撤离/结算/Mission/Session(202)/Abandon 全部被接受，**无阻断性校验**；结算画面正常 | 未覆盖长任务/失败撤离/被踢分支，可再补样本 |
| A4 | 断网/离线 Steam 票据 | ⚠ 未证；**Steam 在线（非离线模式）即可规避**，用户已把 Steam 排除在接管范围外 | 不需要主动验，避免离线模式 |
| A5 | 时间字段平移策略 | ✓ 工程实现 + 门禁；**✓✓ 客户端接受平移值**（主界面连续轮询 8 分钟无过期/错误） | 极端场景（跨天/事件结束）仍可再验 |

**A2 的净结论（对线 A 很重要）**：真正需要 mock 的仍然**只有那一个 API 主机**；
其它旁路（PlayFab/EOS/Steam/Sony）只需「断网时不阻塞登录/进图」，这必须靠 §6 T4 的运行时观测定性，
**不能**在静态阶段臆断。

---

## 5. 与交接档的偏差 / 新发现（需要回填交接档）

### 5.1 §9.5 的文档位置与 ⛔ 边界冲突

交接档 §9.5 要求「结论写回本文档同目录（`hd2-proxy-toolkit\docs\`）」，但同文件开头
「⛔ 工作区与文件边界」明令**禁止写入产品仓、禁止 git 操作**，且注明该节是 2026-11-20 新增的硬性限制。
本轮按**更严格**的 ⛔ 执行：结论文档写进研究仓。若需要回填到产品仓 `docs/`，需要用户明确批准
（属于"写产品仓"动作）。

### 5.2 端口 18080 已被用户进程占用（§8.1 未覆盖）

实测 `127.0.0.1:18080` 属于用户另一个进程
`C:\Users\Admin\AppData\Local\Programs\Python\Python313\python.exe D:/mod/tmp/gh_connect_proxy.py 18080`
（PID 22672，非本线进程，**未动它**）。因此研究线 CRL 固定用 **18081**（CDP 也写 18081），
已改默认值并重新签发证书。→ 建议在交接档 §8.1 的端口表里补上这条。

### 5.3 产品版本快照过期

交接档 §2 写产品 `v0.4.1-alpha.5`，实际 `hd2proxy.exe --version` = **0.4.3-alpha.1**。
本线不依赖产品引擎（自带 mock/证书/状态目录），但若之后要复用产品 capture 功能，注意版本差。

### 5.4 `HD2PROXY_STATE` 隔离对本线不适用

§8 建议用 `set HD2PROXY_STATE=...` 隔离——那是**跑产品引擎**时的做法。本线跑的是自己的
mock（`state/` + `state/certs` + `logs` 全在研究仓内），隔离更强，不需要设该变量。

### 5.5 hosts 现状

hosts 第 16 行仍指向 `100.67.169.17`（产品 `install` 的残留），而产品引擎当前未运行
⇒ **现在开游戏会 `Error 56`**。研究线 mock 复用同一 LAN IP:443，所以**不需要改 hosts**；
但实验前后要遵守「443 只能有一个主人」并确认没有第二个监听者。

### 5.6 交接档需要更正/补强的地方（来自 A2 与 B2B3 侦察）

| # | 交接档原文 | 本轮结论 | 三态 |
|---|---|---|---|
| 1 | §3.1「`accountId` 就是 **SteamID64**」 | **错**：观测 445 个账号 ID 无一落在 SteamID64 区间，223/445 还超过 `2^63` ⇒ 是 HD2 自有 64 位账号 ID | ✓✓（更正） |
| 2 | §5.1 / §12.4 把传输层写成单一「Steam P2P + SDR」 | **不完整**：exe 里有 `steam_transport`（Steam 优先）+ `playfab_transport`（兜底，`Falling back to playfab`）；大厅**控制面**完全在 PlayFab Multiplayer Lobby。不影响「不自建/不替换传输」的结论，但改「私服必须假装什么」的边界 | ✓✓（补强） |
| 3 | §5.2「`connectionString` 原样转发 = 自建信令核心」 | byte 级转发是**必须且正确**的最小实现（且不能伪造），但**入厅**由 PlayFab（Azure）完成 ⇒ 新增未知 **B8**（PlayFab 标题 974A9 是否存活/仍接受 Steam 实体登录）；另 **B10**（运行时关 PlayFab 的开关，纯静态线索） | ✓✓ / ⚠ |
| 4 | §9.5 要求结论写回产品仓 `docs/` | 与开头 ⛔ 边界冲突；本档写研究仓（见 §5.1） | —— |
| 5 | §8.1 端口表 | 缺 18080/18081 这条（用户进程占用，见 §5.2） | ✓ |

详见 [A2 文档](A2-HOST-DEPENDENCIES-2026-11-21.md) 与 [B2B3 文档 §5](B2B3-LOBBY-LOGIN-RECON-2026-11-21.md)。

---

## 6. 下一步 runbook（线 A，需用户配合；每步先说明再做）

> 用户授权纪律：涉及系统改动（证书信任、启动游戏、抓包）的步骤**先说明动作与风险，得到确认后再执行**。
> 预计：T1 约 5 分钟；T2 约 10–15 分钟；T3 约 20–30 分钟；T4 可并入 T2/T3。
> **进度（2026-11-21 01:32）：T0/T1/T2/T2b ✅ 已完成；下一项 = T3；T4 正式抓包待定（需管理员）。**

### T0 预检（无风险）

```powershell
cd D:\mod\work\hd2-offline-research
python tools\mockctl.py ports                       # 443/8080/17897/18081 归属
& 'D:\mod\release-test\current\hd2proxy\hd2proxy.exe' status   # 期望：引擎未运行
Select-String -Path C:\Windows\System32\drivers\etc\hosts -Pattern thehelldiversgame
python tools\mockctl.py cert-status --check-trust   # 当前证书 + 是否已装信任
```
判据：`target:443` 与 `18081` free；产品引擎未运行；hosts 仍指向本机 LAN IP。

### T1 证书信任 + Schannel 验证（✅ 已完成 2026-11-21 01:13；非管理员、可逆）

动作：把研究 CA 装进 `CurrentUser\Root`（只影响当前用户，不改 LocalMachine；卸载用 `cert-uninstall`）。
风险：该用户下任何程序都会暂时信任这张 CA（CA 私钥已在签发后删除，无法再签别的域名；
签发受 NameConstraints 限制为单域名；有效期 48h）。

```powershell
python tools\mockctl.py cert-install
python tools\mockctl.py cert-status --check-trust    # 期望 CurrentUser: true

# 另开窗口起 mock（绑 LAN IP:443 + CRL 18081）
python tools\mockctl.py serve

# 用 Windows 自带的 Schannel（curl 默认走 Schannel）验一次真实信任链 + CRL：
curl.exe -v --resolve api.live.prod.thehelldiversgame.com:443:100.67.169.17 `
  https://api.live.prod.thehelldiversgame.com/api/WarSeason/current/WarId
```
判据：`HTTP/1.1 200`、body `{"id":801}`、无证书错误；mock 日志出现该请求。
失败处置：curl 报 `CRYPT_E_NO_REVOCATION_CHECK` ⇒ CRL 没取到（确认 18081 在听、CDP 是 18081）；
`SSL certificate problem` ⇒ 信任未装/装到了别的用户；mock 日志无请求 ⇒ 443 被别的进程占用。

### T2 启动游戏 → 进主界面（A1，✅ 已完成 2026-11-21 01:31，三次启动均通过）

前置：mock 在跑（T1 已起）；Steam **在线**（不要用离线模式）；hosts 不变。
动作：正常启动 Helldivers 2，观察 `state\logs\mock.log`。

判据（任一不满足即记录现场）：
1. mock 日志出现 `POST /api/Account/Login ... -> 200`；
2. 随后出现主界面轮询（`Configuration/GameClient`、`WarSeason/current/WarId`、
   `WarSeason/801/Status|warinfo|timeSinceStart`、`GalacticWarEffects`、`Progression/*`、`lobby/accounts` 等）；
3. 游戏进入主界面，无 `Error 56 / Error 60`；mock 日志无 4xx/5xx。

失败处置（按现象分支）：
* 日志**完全没有请求** ⇒ TLS/信任（回 T1）或 hosts/端口（T0）；
* 卡住且日志中出现 `fallback` 端点 ⇒ 用 `python tools\mockctl.py health ...` 看 `unmatched` 清单，
  该端点是我们没抓过的 ⇒ 记为「补抓项」，用产品引擎再录一轮（回到产品会话）；
* 报「数据过期/无法连接任务」⇒ 看 `health.unknown_time_fields`（应空）与 `last_shift`；
* 记录现场：`state\logs\mock.log` 片段 + `health` JSON。

### T3 单人任务链（A3，✅ 已完成 2026-11-21 01:35–01:39，见 §2.4.5）

前置：T2 已进主界面。动作：星图选星球 → 开始任务 → 打完/撤离。
判据：mock 日志依次出现 `Operation/Create → Mission/Start →（局内 ReportPosition 周期调用）→
Mission/end` 且都是 200；游戏能进图、局内不被踢、能出结算画面。
失败处置：记录第一个异常端点；特别关注 `Mission/Session`（已知有服务端校验）与
`Operation/Create` 的 id 是否被接受。

### T4 运行时主机依赖快照（A2/E1–E8）

按 [A2 文档 §6](A2-HOST-DEPENDENCIES-2026-11-21.md) 执行（dumpcap/tshark 或 pktmon），
**抓包要在启动游戏前开始**。判据：DNS/SNI 里是否出现第二个 `*.thehelldiversgame.com`；
`974A9.playfabapi.com` 何时连；Steam 域不接管。

### T5 收尾（必做）

```powershell
# 停 mock（Ctrl+C）→ 确认端口空
python tools\mockctl.py ports
python tools\mockctl.py cert-uninstall          # 撤信任（可逆）
# hosts：本轮无需改动；若将来改过，恢复为产品行（或产品侧 hd2proxy.exe install）
```

---

## 7. 风险与必须用户拍板的点

1. **CA 信任安装**：CurrentUser（推荐，非管理员、可逆、48h 有效）vs LocalMachine（管理员，影响全机）
   vs 暂不装（只能验到 Python 客户端，无法验 Schannel/游戏）。
2. **启动游戏**：stock（EAC-on）启动可能比 A2 的 dump 会话多出 EOS/EAC 通道（A2 文档 §7.4 已注明）；
   需要测哪一侧、是否接受。
3. **ToS / B6**：离线单机风险低于自建联机，但仍属「绕过官方后端」，需用户明确接受。
4. **443 独占**：实验期间产品引擎必须停；实验后如果要回产品线，先停 mock 再起产品引擎。
5. **文档回填**：是否允许把本档摘要写进产品仓 `docs/`（§5.1 的冲突；默认不写）。

---

## 8. 线 B（自建联机）状态：**按用户排序，暂缓**

已就绪的地基（先服务单机，之后可直接复用）：
* `mock/state.py` 的大厅状态机：`PUT /api/lobby` 原样存、`GET /api/lobby/{id}` 原样返回
  （`connectionString` 不解析、不生成 —— 与交接档 §5.2 的「原样转发」一致），成员加入/清空/列表都已过 scenario 断言；
* `mock/state.py` 的作战/任务链状态机（Create 回显 id、Start 分配 missionId、end 回填结算）。

尚未做（等线 A 有实机结论再开）：
* **B1**（同局域网双机能否只靠自建信令 + Steam 建连）—— 需要第二台机器；
* **B2/B3** 侦察**已完成**（只读，见 [B2B3-LOBBY-LOGIN-RECON-2026-11-21.md](B2B3-LOBBY-LOGIN-RECON-2026-11-21.md)），
  关键结论对本档有直接影响：

  | 结论 | 三态 | 对方案的意义 |
  |---|---|---|
  | `connectionString` **不是** Steam SDR 的 `SteamDatagramHostedAddress`，而是 **PlayFab Multiplayer「Lobby」签发的不可解释连接串**（`PFMultiplayerJoinLobby` / `PFLobbyGetConnectionString` 调用链 + 官方 REST「opaque to everyone except the Lobby service」） | ✓✓ | 交接档 §5.2「原样转发」仍是**必须且正确的最小实现**；但**伪造/自造无效**（串内含 title/大厅/密钥，服务端校验） |
  | 结构恒为 `cv2:<UUIDv4>.r-<8位>\|<6位>\|kv1:<44 字符 base64(32B)>`（107 字符，1397 条样本 / 290 个大厅 / 跨 3 个 build 无例外）；6 位数字最可能是 **PlayFab TitleId 974A9 的十进制**（619689） | ✓ / ✓✓ | 转发层不需要理解语义；工程上只要保证「同串原样回给客户端」 |
  | **入厅由 PlayFab（Azure 云）完成**：`/api/lobby` 只是带外发现通道，客机执行 `PFMultiplayerJoinLobby` 时连的是 PlayFab，不是我们的假后端 | ✓✓ | B 线新增依赖 **B8**：PlayFab 标题 974A9 是否存活/仍接受 Steam 实体登录；另有 **B10**（是否存在运行时关闭 playfab 的开关，纯静态线索） |
  | 登录 body 本地无落盘样本；62 次登录请求**全部 437 B 定长**；exe 同时有 `GetAuthSessionTicket` 与 `GetAuthTicketForWebApi`，**无** encrypted app ticket 证据 | ✓✓ / ⚠ | B3 建议：**不要解析票据**——按 IP/预配置表把会话映射到自造 `accountId`（与交接档 §5.1 一致）；真验票需要 AppID 553850 的 Steamworks partner key，不可得 |

  ⇒ **线 B 的方案基线要改**：自建后端能接管的是「发现层（登录/大厅列表/任务登记）」，
  **入厅层仍在 PlayFab**（利好：PlayFab 是公网云服务，官方后端全灭时这段可能自动保住；
  风险：若标题下线，则需要 B10 的运行时开关，未证）。这条等线 A 收口后再排期。

---

## 9. 复现清单（把本档结论从头跑一遍）

```powershell
cd D:\mod\work\hd2-offline-research

# 样本
python tools\sample_build.py --src D:\mod\tmp\capture-store-season-20261118\raw `
  --dst samples\store-season --mission-end-src D:\mod\out\gwe

# 门禁
python -m pytest tests -q                                          # 56 passed
python tools\mockctl.py selftest                                   # SELFTEST PASS
python tools\replay_client.py --src samples\store-season --mode handler   # 11/11
python tools\replay_client.py --src samples\store-season --mode scenario  # 46/46

# TLS 端到端（本机 dry-run，不抢 443）
python tools\mockctl.py serve --dry-run --insecure-ok              # 另开窗口
python tools\replay_client.py --src samples\store-season --mode server `
  --url 127.0.0.1:18443 --ca state\certs\ca.pem                    # 5/5
```

> 注：`samples/` 可随时删掉重建；`state/`（证书/日志/证据）同样可重建。
