# 阶段2：真实环境成功加入链路 + 加入门槛（2026-11-22 凌晨实测）

> 一次真实两机测试（用户 + 真实好友「烈烈龙」）抓到了**成功加入的完整链路**，并定位了
> E2 双机失败的**根因**：不是 ids 映射，而是**缺少 `GET /api/Account/Keys` 公钥交换**——
> 拿不到对端公钥，加入方永远不会被接纳。
>
> 证据：官方 API 抓包 `D:\mod\tmp\capture-full-passthrough-20261121`（88 份）、
> PlayFab 明文 `state/mitm/capture-20261121-232530.jsonl`（447 条 req/resp）、
> 参考实现 `state/evidence/hellforge/HellForge`、E2 现场日志 `state/evidence/e2prep/e2-mock-watch.log`。

## 0. 三条结论（先看这个）

| # | 结论 | 证据强度 |
|---|---|---|
| 1 | **加入门槛 = 公钥交换**：每台游戏在 `POST /Account/Login` 的 `publicKey` 自报公钥；`GET /Account/Keys?id=<对端 id>` 取对端公钥。**拿不到 → 永远不接纳 → 加载卡住 / lobby timeout**。E2 现场我们的 mock 5 次都回了 `{}`。 | ✓✓ 实机（官方抓包 + E2 日志 + 逆向记录） |
| 2 | **`/api/lobby/accounts` 就是「好友可加入」的唯一数据源**：客户端从 `lobbies[].connectionString` 取大厅串，再交给 PlayFab `JoinLobby`。**没有它，PlayFab 侧根本不会出现该厅**。 | ✓✓ 实机（时序：厅在 accounts 出现 1m50s 后才 JoinLobby） |
| 3 | `state` 是生命周期枚举（**1=在飞船**，2/5=已开始任务）。客户端自己上传的 `state=1` 是**合法**的，mock 不该改写成 2。 | ✓✓ 实机（28 份厅 body，state ∈ {1,2,5}） |

## 1. 真实成功链路（00:06:43–00:10，用户视角）

用户操作：进别人房间 → 退出 → 同一个人进用户房间 → 退出游戏。

```
00:04:53  accounts 响应里**首次**出现厅 f20de3e9（含 connectionString cv2:f20de3e9…）
00:06:43.914  JoinLobby  conn=cv2:f20de3e9-…  → 200 {"LobbyId":"f20de3e9-…"}
00:06:47      SubscribeToLobbyResource → 200
00:06:48      GetLobby → 200（拿到厅的真实 body）
00:06:50.931  LeaveLobby → 200                    （用户退出）
00:06:54      JoinLobby 同一个厅 → 200 … 00:07:14 LeaveLobby
00:07:30      JoinLobby → 200 … 00:07:35 LeaveLobby
00:07:55      JoinLobby → 200 … 00:08:11 GET /api/Account/Keys?id=<房主>  ← 公钥交换
00:08:12      LeaveLobby 200
00:08:13      player planet -> 268 (mission)        （进入任务）
00:08:38.162  LeaveLobby → 400
00:08:38.169  RequestParty → 200（分配 Azure VM 4.193.184.219:30127/UDP，DTLS）
00:08:39.987  CreateLobby → 200                     （用户自己建房）
00:08:59.507  InviteToLobby → 200                   （好友被邀请加入用户房间）
00:09:43.498  InviteToLobby → 200
00:09:40.282  RemoveMember → 404                    （尝试移除未在厅成员）
```

**关于 `PUT /api/lobby` / `GET /api/lobby/<id>` / `POST .../member` 的边界（2026-11-22 更正）**：
抓包目录里 lobby 相关只有 `accounts` 的响应体，**但这不能证明这三者没被调用** ——
`capture_response` 对**空响应体直接 return**（`hooks/capture.py`：`if ... or not body: return`），
所以空 body 的响应既不会落盘也不会留日志。而 `PUT /api/lobby` 的**响应本来就被游戏忽略**
（HellForge 逆向：`game.dll 0x12cb5a0`，reply ignored），它的响应很可能就是空的。
→ 准确表述：**没有抓到它们的非空响应体**；mock 仍需正常服务这三条（`PUT` 存 body、`member` 保活、
`clear` 撤销），只是**响应形状不必复刻**。E2 里客户端确实调用过这三条（mock 日志有），说明客户端会调。

### 1.1 时序证明 connectionString 来自 accounts

| 事实 | 数据 |
|---|---|
| `f20de3e9` 首次出现在 accounts 响应 | **00:04:53**（此后 9 份响应都含它，`players` 4 槽） |
| PlayFab 侧首次出现该 conn string | **00:06:43.914**（就是第一次 `JoinLobby` 的请求体） |
| 此前 PlayFab `FindLobbies` 是否返回过它 | **否**（447 条 PlayFab 记录里 00:06:43 之前从未出现该 lobbyId） |

⇒ 客户端能拿到 `connectionString` 的唯一来源就是 `/api/lobby/accounts` 的 `lobbies[]`。

## 2. 真实 `/api/lobby/accounts` 契约（含金样本）

请求：`POST /api/lobby/accounts`，体 `{"ids":[<accountId>…]}`（本次 112 个，全是好友，**不含自己**）。

响应（18,427 B 典型）：

```json
{
  "accountLobbies": [
    {"account":"14685664063319761590","lobbyId":"32eaf1fb-…r-20260323"},
    {"account":"14996573887956613835"},
    … 与请求 ids **同序等长**（112 → 112）
  ],
  "lobbies": [ {…厅 body…} × 8 ]
}
```

- **严格同序等长回显**：13 份抓包 13/13 验证（`got == req` 逐项相等），112/112。
- 在厅里的 id 带 `lobbyId`；不在厅里的**完全没有该 id 键**（不是空串）。
- 本次 112 个 id 中 9 个有厅 → 去重后 **8 个厅 body**；所有被引用的 lobbyId 都能在 `lobbies` 找到。
- 自己**不在** ids 里（好友列表）；若自己在厅里，会作为某个「好友厅」的成员出现在 `players[]`。

### 2.1 金样本：在飞船里的好友厅（含用户自己）

`f20de3e9-d48a-45e2-be47-241fb1690249.r-20260323`（00:08:21 抓，脱敏版已入库
`tests/fixtures/real_lobby_in_ship.json`）：

| 字段 | 值 | 说明 |
|---|---|---|
| `state` | **1** | 在飞船（未部署） |
| `numLobbyMembers` | 2 | **真实成员数**（不是槽位数） |
| `players` | 固定 **4 槽** | 2 个真实成员 + 2 个占位 `{"memberAccountId":"0","hasInfo":false,…}` |
| `planetId` / `warId` / `territoryId` | **-1** | 未选星球 |
| `difficulty` / `startTime` | 0 / 0 | 未开始任务 |
| `privacyMode` | 1 | |
| `seed` / `operationMissionStates` | **字段缺席** | 飞船内没有（不是空串/null） |
| `memberAccountIds` | **缺席** | 客户端上传有，服务端不下发 |
| 服务端补充的字段 | `completedObjectives`/`numSessionMembers`/`operationSeed`/`operationType`/`planetType`/`transitionTarget` = 0 | 客户端不上传 |

**`lobbies[]` 的真实内容 = 客户端 `PUT /api/lobby` 上传的字段 + 上述服务端补充**。
作为对照：公开匹配的厅（state 2/5）会带 `planetId`/`warId=801`/`seed`/`operationMissionStates`，
但那不是好友「在飞船上可加入」的场景。

### 2.2 大厅解析器必需字段（缺一个 → 好友全灰）

`game.dll 0x1053f60` 需要这 20 个数字字段齐全，缺任一则**整个 `/lobby/accounts` 响应解析失败**：

```
completedObjectives, crossplayMode, difficulty, gameModeProgress, hostability, onboardingState,
operationProgress, operationSeed, operationType, planetId, planetType, preferenceGroup,
privacyMode, race, regionIndex, sosBeacons, startTime, state, territoryId, transitionTarget
```

（`numSessionMembers` 不在必需列表里，但真实后端会补。）

### 2.3 `state` 枚举证据

28 份厅 body 里出现 `state ∈ {1,2,5}`，且同一 lobbyId 会变：

| state | `startTime` | `difficulty` | `seed` | 解读 |
|---|---|---|---|---|
| **1** | 0 | 0 | 缺席/空 | **在飞船**（好友显示 On Ship、可直接加入） |
| 2 | epoch | 10 | 160–184 hex | 已开始任务 |
| 5 | epoch | 7–10 | 160–184 hex | 任务中/转换（`operationMissionStates` 出现 state=3 完成） |

## 3. 加入门槛：`GET /api/Account/Keys`（**E2 根因**）

### 3.1 真实样本

```
GET /api/Account/Keys?id=10753400516677771412      （00:08:11，就在进入任务前）
200  115 B
{"accountKeys":[{"platformAccountId":"<房主 accountId 已脱敏>",
                 "key":"kVWP7ESPy71AU7yRxpsd3IZTouQJnVyjfEo4lGHKWQI="}]}
```

### 3.2 机制（逆向记录，逐条对上真实抓包）

> 每台游戏在 `POST /Account/Login` 里发自己的 32 字节公钥（字段 `publicKey`；引擎编码进 64 字节缓冲，
> 所以**不是** 64 位十六进制，按原样保存即可）。一台游戏**只有在**
> `GET /Account/Keys?id=<对端 platform account id>` 拿到对端公钥、
> 且与该游戏在游戏连接上看到的公钥一致时，才会接纳这名玩家（检查点 `0x108f92b`）；
> **拿不到公钥，加入方永远不会被接纳，表现为 "lobby timeout"**。

### 3.3 E2 现场：5 次全部回了 `{}`

```
state/evidence/e2prep/e2-mock-watch.log
  21:46:21.999 GET /api/Account/Keys?id=2231…6623（志愿者 accountId，已脱敏） 0B -> 200 2B [fallback]
  21:58:03.878 GET /api/Account/Keys?id=2231…6623（志愿者 accountId，已脱敏） 0B -> 200 2B [fallback]
  22:08:41.893 GET /api/Account/Keys?id=2231…6623（志愿者 accountId，已脱敏） 0B -> 200 2B [fallback]
  22:25:11.595 GET /api/Account/Keys?id=13887067702231565058 0B -> 200 2B [fallback]
  22:47:17.616 GET /api/Account/Keys?id=13887067702231565058 0B -> 200 2B [fallback]
```

`[fallback]` + `2B` = 我们回的是 `{}`。**两个方向**都在问（房主要加入方的 key，加入方要房主的 key），
两端都拿不到 → 双方都不接纳 → 卡在加载。

## 4. 参考实现：HellForge（同目标的完整方案）

`state/evidence/hellforge/HellForge`（C#，源码 + 文档齐全）做的事与线 B 同构，**但路线不同**：

| 维度 | HellForge | 我们的线 B（EAC 开、不改游戏文件） |
|---|---|---|
| 官方 API | 本地后端全量替代（含 `/Account/Keys`、`/lobby*`） | 本地 mock 接管官方 API host |
| PlayFab | **本地重定向**（heap patch 把 URL 换成 `127.0.0.1:<port>/playfab`） | **保留真实 PlayFab** |
| 大厅/Party | 换 `PlayFabMultiplayerWin.dll` / `PartyWin.dll` 为替身（SDK shim） | 保留真实 PlayFab Lobby/Party |
| 好友发现 | Steam presence + Steam lobby | 官方 `lobby/accounts` + Steam 好友列表 |

**结论**：HellForge 的「换 DLL + patch heap」路线与我们的 EAC/不注入约束冲突，不能用；
但它是**已验证的契约参考**——上面 §2/§3 的字段与门槛机制均以它的逆向记录为准，
并与我们真实抓包逐项对上（金样本 28 字段、`accountKeys` 形状、20 个必需数字字段）。

> 另注：HellForge 文档说明 PlayFab 侧账号 id 由 SteamID64 派生、好友列表走
> PlayFab `Client/GetFriendsList`。这解释了早期「好友列表一直加载中」是 PlayFab 调用问题，
> 与本地 mock 无关。

## 5. E2 失败归因（更新）

| 候选原因 | 判定 | 依据 |
|---|---|---|
| **缺 `Account/Keys` 公钥交换** | **主因**（✗ 我们当时没实现） | 5 次 fallback `{}`；机制 = 不接纳 ⇒ lobby timeout；用户现场「卡在加载」 |
| `/api/lobby/accounts` 忽略 `ids` | **次因**（也在本次修掉） | 真实契约逐 id 回显；不改则好友 id→厅映射错 |
| 厅 body 缺必需字段 | 潜在（本次补齐） | 缺任一 → 整个响应解析失败 → 好友全灰 |
| `state` 写成 1 | **否证**（不是问题） | 真实在飞船厅就是 `state=1` |
| member 身份写成合成 `7656119…` | 已修（2026-11-21） | 客户端看到厅里不是自己 |
| PlayFab/Party 数据面 | 仍存疑（未证） | E2 曾 Party 连上 P2P 27ms 后 10–20s 报 4104/4379 |

## 6. 本次改了什么（已部署 A 机，14/14 通过）

| 改动 | 文件 | 说明 |
|---|---|---|
| `/api/lobby/accounts` **按 ids 同序回显** | `mock/discovery.py` | `accounts(account, ids)`；无厅条目**不带** lobbyId 键；`lobbies` 去重 |
| 厅 body 真实形状 | `mock/discovery.py` | `_lobby_view()`：补 21 个默认数字字段、`players` 补齐 4 槽、丢 `memberAccountIds`、`numLobbyMembers` 用真实人数 |
| **公钥交换** | `mock/discovery.py` | `login()` 采集 `publicKey`；`account_keys()` 按真实 accountId/内部令牌回公钥；查不到的 id 不出现在数组 |
| 路由 | `mock/rules.py` | `GET /api/Account/Keys` → `account_keys` |
| 接线 | `mock/handler.py` | `_query_ids()` 解析 `?id=`（支持重复/逗号）；`lobby_accounts` 传请求体 |
| **修 bug**：登录忽略账号头 | `mock/handler.py` | `account_login` 没透传 `X-HD2-Account` → 探针登录建出另一个账号、公钥挂错人 |
| 探针断言更新 | `tools/discovery_probe.py` | 未知厅接受 `404`（B8 定论；原断言只认 200） |
| 一致性套件 +3 项 | `tools/discovery_conformance.py` | C12 ids 同序回显 / C13 厅 body 真实形状 / C14 公钥按 id 可取 |
| 契约测试 +7 条 | `tests/test_discovery_lobby.py`、`tests/fixtures/real_lobby_in_ship.json` | 含**逐字段对照真实 body** 的回归（金样本脱敏入库） |

验证：

```
pytest                    153 passed
tools/discovery_conformance.py（进程内）   14/14 PASS
A 机 healthcheck.sh                        PASS
A 机 conformance（--url https://api.live.prod… --connect-to 127.0.0.1:443）
                                          14/14 PASS
```

> 部署注记：在 A 机上跑一致性套件要用
> `--url https://api.live.prod.thehelldiversgame.com --connect-to 127.0.0.1:443 --insecure`。
> 只给 `--host`（SNI 与 Host 混用）会被 nginx 判 421/401。

## 7. 仍未验证 / 下一步

1. **双机复测（E3）**：带本次修复重跑两机——两端的 `publicKey` 都能自报、`Account/Keys` 能互查，
   看是否越过「卡在加载」。这是唯一还没被真实数据覆盖的一环。
2. `Account/Keys` 是否支持一次多 id：真实抓包只用单个 id（我们额外兼容了逗号分隔，无风险）。
3. 公钥到达时序：若对端在登录后、首次 PUT 前就被查询，会查不到（HellForge 用 3s 等待兜底）。
   我们当前不加等待，靠「PUT 先于加入」的时序；若复测暴露问题再加。
4. `warId`/`territoryId` 是否由服务端强制填（真实在飞船厅是 `-1`，说明不强制）。
5. E2 遗留的 PlayFab Party 4104/4379 是否仍会出现（需双机复测判定）。

## 8 环境回滚记录（2026-11-22 00:2x，已实测校验）

全通基线（hd2proxy passthrough + PlayFab MITM）已全部撤销，机器回到「官方直连」状态：

| 项 | 动作 | 校验结果 |
|---|---|---|
| hd2proxy 引擎 | 停进程（后台 job 取消） | 无 `hd2proxy.exe` 进程 |
| hosts | `hd2proxy.exe uninstall` + 清掉残留 `HD2-STAGE2-TUNNEL` 行（备份 `hosts.bak-hd2rollback`） | hosts 无任何 `helldivers` 行 |
| DNS | `ipconfig /flushdns` | `api.live.prod.thehelldiversgame.com` → `68.154.15.59`（真实官方） |
| 会话证书信任 | `uninstall` 删除 `D6B85787…` | LocalMachine\Root 无 HD2 证书 |
| 历史测试 CA | 清掉两个存储里所有 `HD2 *` 自签 CA（LocalMachine 4 个 + CurrentUser 10 个） | 4 个存储扫描均为 0 |
| PlayFab MITM | 停进程 | 无 python MITM 进程 |
| 系统代理 | `tools/system_proxy.ps1 -Action Restore` | 回到用户常态 `127.0.0.1:7897`（Clash） |
| 端口 | — | `:443` 无监听 |
| 服务/计划任务 | — | 无 `hd2proxy` 服务、无 HD2 计划任务 |
| **端到端** | 直连官方 API | `GET /api/WarSeason/current/WarId` → **HTTP 200**（0.86 s） |

> 注意：**研究 CA 也被清掉了**（`CN=HD2 Offline Research CA`，`state/certs/ca.pem` 本地文件仍在，
> 有效期到 2026-11-23 03:52 UTC）。E3 重建时用户级重新导入即可：
> `certutil -user -addstore Root state/certs/ca.cer`。

### E3 重建清单（按需，约 10 分钟）

1. `certutil -user -addstore Root state/certs/ca.cer`（用户级，无需 UAC）；
2. hosts 加一行 → `100.67.88.219 api.live.prod.thehelldiversgame.com # HD2-STAGE2-TUNNEL`（需一次 UAC 写 hosts）；
3. 起中继：`tools/e2_tunnel.py --via server2 --listen-host 100.67.88.219 --listen-port 443 --target 106.52.201.42:443`；
4. A 机 `hd2-discovery` 已在线且 14/14 通过，**无需改动**；
5. PlayFab 保持真实（不启 MITM）——E3 验的是官方 API 侧的公钥交换；
6. 志愿者机器：同样要装研究 CA + hosts 指向**本机中继**（或让中继对志愿者可达）。
