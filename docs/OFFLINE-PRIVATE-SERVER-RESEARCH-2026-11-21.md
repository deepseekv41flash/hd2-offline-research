# HD2 离线单机 / 自建联机研究线 · 阶段一（单机离线）结项档（2026-11-21）

> 执行依据：`D:\mod\work\hd2-proxy-toolkit\docs\HANDOFF-OFFLINE-PRIVATE-SERVER-2026-11-20.md` **§9 runbook**。
> 本档所属仓库：`D:\mod\work\hd2-offline-research`（研究线独立仓，单独 git 管理）。
> 用户排序要求（2026-11-21 00:xx）：**先做单机离线（线 A），再做自建联机（线 B）** —— 本档按此排序。
> 三态：`✓✓ 实机` / `✓ 离线+数据`（本机可复跑、有输出）/ `⚠ 未证` / `✗ 否证`。

---

## 0. 一句话结论

**线 A 的工程底座已经就位并可离线复跑：73 条真实抓包响应 100% 被本地 mock 接住、时间字段按规则平移、
任务链/大厅链状态机跑通、TLS（自签 CA + CDP/CRL + AKI）端到端可用；四道门禁全绿（见 §2）。
但「游戏客户端是否接受这套 mock」这一步（A1/A3）仍未验证 —— 它只能在实机上验，下一节 §6 给出分步 runbook。**

---

## 1. 本轮执行了交接档 §9 的哪些步骤

| §9 步骤 | 状态 | 证据 / 产物 |
|---|---|---|
| §9.0 工作区边界 30 秒自检 | ✓ | `Get-Location=D:\mod` 且不在任何 git 仓内；产品仓库 `git -C D:\mod\work\hd2-proxy-toolkit status --short` **空**；443/8080/17897 全部无监听 |
| §9.1 读交接档 + 三份研究资料 | ✓ | API-ENDPOINTS / MISSION-CAPTURES / FINDING-零安装客机 已读并用于设计；另读 `D:\mod\out\gwe\` 的 18 组 Operation/Create、5 份 Mission/end、PUT lobby 报文 |
| §9.2 环境自检 | ✓ | 产品版本 **0.4.3-alpha.1**（交接档快照写 0.4.1-alpha.5，已过期）；hosts 第 16 行仍指 `100.67.169.17`；无 hd2proxy / helldivers2 进程；无 TUN/UU 网卡；默认路由走以太网 `100.67.0.1` |
| §9.3 「不需要用户操作」的部分 | ✓ | 见 §2–§4：mock 实现 + 脱敏样本 + 四道门禁 |
| §9.4 需要用户配合的部分 | ⚠ 待办 | §6 runbook（证书信任 → 启动游戏 → 单人任务 → 主机依赖抓取） |
| §9.5 产出结论文档 | ✓（位置有偏差） | 本档写在研究仓 `docs/`；**没有**写进产品仓（§9.5 说「写回本文档同目录」与开头 ⛔ 边界「禁止写入产品仓」冲突，见 §5.1） |

---

## 2. 本轮交付与门禁证据（本机可复跑）

研究仓内容：`mock/`（回放+状态机+TLS）、`tools/`（分析/脱敏/回放/控制台）、`tests/`（pytest 56 项）、
`profiles/`、`docs/`。**没有改动产品仓库任何文件。**

### 2.1 四道门禁（全部 PASS）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 单测 | `python -m pytest tests -q` | **56 passed in 4.82s** |
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
| A1 | 客户端是否接受回放登录 | ⚠ 未证（mock 已能返回合法 sessionId；**游戏是否买账要实机**） | §6 T2 |
| A2 | 游戏除 API 主机外还连哪些主机 | ✓ 静态侦察完成，见 [A2-HOST-DEPENDENCIES-2026-11-21.md](A2-HOST-DEPENDENCIES-2026-11-21.md)：明文里**没有第二条游戏自有 HTTP API**（`thehelldiversgame` 在 exe/runtime.dll/game.dll/全部 dll/bundle 明文 0 命中；bundle 已压缩，静态不能证否）；确认活跃旁路 = Steam 网络（边界外）、PlayFab Party（`974A9.playfabapi.com` 推导）、EOS、Sony RECAP（`upload.recap.tools` ✓✓ 实机 200） | §6 T4（运行时 DNS/SNI/连接快照） |
| A3 | 任务中是否有服务端实时校验 | ⚠ 未证（mock 侧 `Mission/Session`→202、`ReportPosition`→200、`Mission/end`→模板结算均已备） | §6 T3 |
| A4 | 断网/离线 Steam 票据 | ⚠ 未证；**Steam 在线（非离线模式）即可规避**，用户已把 Steam 排除在接管范围外 | 不需要主动验，避免离线模式 |
| A5 | 时间字段平移策略 | ✓ 工程实现 + 门禁（两份真实抓包未识别字段=0；变异测试证明门禁会红） | 客户端是否认可平移后的值（含 A1） |

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

### T0 预检（无风险）

```powershell
cd D:\mod\work\hd2-offline-research
python tools\mockctl.py ports                       # 443/8080/17897/18081 归属
& 'D:\mod\release-test\current\hd2proxy\hd2proxy.exe' status   # 期望：引擎未运行
Select-String -Path C:\Windows\System32\drivers\etc\hosts -Pattern thehelldiversgame
python tools\mockctl.py cert-status --check-trust   # 当前证书 + 是否已装信任
```
判据：`target:443` 与 `18081` free；产品引擎未运行；hosts 仍指向本机 LAN IP。

### T1 证书信任 + Schannel 验证（**需批准**；非管理员、可逆）

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

### T2 启动游戏 → 进主界面（A1，**需批准**）

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

### T3 单人任务链（A3）

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
