# B2/B3 只读侦察：`lobby.connectionString` 语义 + 登录票据处理（2026-11-21）

> 性质：**只读侦察**。本轮没有启动游戏 / 没有起监听 / 没有动 hd2proxy / 没有改游戏目录、产品仓、
> `%LOCALAPPDATA%\HD2ProxyStudio`、hosts、证书。唯一写入 = 本文件。
> 三态：`✓✓ 实机` / `✓ 离线+数据` / `⚠ 未证` / `✗ 否证`。
> **脱敏纪律**：本文不出现任何 `connectionString` / `sessionId` / `lobbyId` / `accountId` / `SteamID64` /
> 玩家名 的完整原值。只给骨架、长度、字符集与稳定性结论。结构常量（如 `cv2`、`r-20260323`、`619689`）
> 不是按大厅/按玩家变化的敏感值，保留以便工程使用。

> ⚠ **2026-11-21 02:09 回填（B9 实测）**：本文 §3.3 关于登录体的推断已被实测推翻——
> 登录 body 是 **6/9 字段 JSON**（`buildIdentifier`/`publicKey`/`platform`/`instanceId`/`token`/`clientVersion`
> [+ 绑定账号多 3 个可选字段]），`token` 恒 116 字符，**"437 B 定长"只是因为样本全来自同一个绑定账号**，
> 不是"票据定长"。详见 `B9-LOGIN-BODY-SCHEMA-2026-11-21.md`，本文 §3.3/§6 的具体更正见文末「B9 回填」。

---

## 0. 一句话结论

1. **B2**：`connectionString` **不是 Steam SDR 的 `SteamDatagramHostedAddress`**，而是
   **PlayFab Multiplayer「Lobby」服务签发的不可解释（opaque）连接串**（`Lobby/CreateLobby` 返回
   `ConnectionString + LobbyId`，`Lobby/JoinLobby` 只把它原样回传）。自建信令只能**原样转发**，
   不能伪造；但「转发」只覆盖发现/分发环节，**真正的入厅由 PlayFab（Azure 云）完成** —— 这是 B 线
   一个此前没登记的新依赖（见 §5 B8）。
2. **6 位数字**：全样本恒定 `619689`，最可能是 **PlayFab TitleId 的十进制写法 = `0x974A9` = `974A9`**；
   外部证据：HD2（Steam AppID 553850）玩家机曾对 **`974a9.playfabapi.com`** 报 DNS 解析超时。
3. **B3**：登录 body 本地**没有落盘样本**（抓包白名单/代理设计都不存 login body）；62 次带长度日志的
   登录请求**全部 437 B**。~~（定长 → 票据字段定长）~~ —— **该推断已由 B9 推翻**：437 B 是「绑定账号」
   的定长形态，未绑定账号是 346 B，差异全在 3 个可选字段，`token` 两账号都是 116 字符。
   `helldivers2.exe` 同时存在
   `GetAuthSessionTicket` 与 `GetAuthTicketForWebApi` 两条取票路径，`EncryptedAppTicket` 无使用证据。
   票据类型判断（更像会话票据）**保留**，但"按尺寸反推票长"这条论证作废。
4. **B3 建议**：自建私服**不要解析票据**——登录响应本来就只有一个 `sessionId`，按 IP/预配置表把会话
   映射到自造 `accountId` 即可；要真验票必须拿到 AppID 553850 的 Steamworks **partner publisher key**
   （`ISteamUserAuth/AuthenticateUserTicket`），当前不可得。

---

## 1. 方法与证据来源

| 类别 | 来源（全部只读） |
|---|---|
| 抓包（响应） | `D:\mod\out\gwe\**\*.json`（988 条 index 记录，2026-10-27 … 11-07）、`D:\mod\tmp\capture-store-season-20261118\raw\`（73 条）、`D:\mod\tmp\capture-new-account-20261117\raw\`（53 条） |
| 抓包（请求体） | `D:\mod\out\gwe\req_*.json`（403 个；白名单只有 `/api/Operation` `/api/lobby` `/api/Progression/Items/Customize`） |
| 代理日志（只记长度） | `D:\Downloads\variant_mod\logs\proxy.log` / `proxy.log.1`（小写 `REQ/REP` 行；**不落 body**） |
| 二进制 | `…\Helldivers 2\bin\helldivers2.exe`（28,743,256 B）、`steam_api64.dll`（317,080 B）、`PartyWin.dll`（4,031,520 B）、`PlayFabMultiplayerWin.dll`（1,878,048 B）、`EOSSDK-Win64-Shipping.dll`（19,531,704 B）、`data\game\game.dll` |
| 官方文档 | PlayFab Lobby REST/C++ 参考、Steamworks `ISteamUser`、Steam SDK SDHA 头文件（URL 见 §7） |

只读检索手法（可复跑，不落盘）：

```python
# 结构统计：直接从所有抓包 JSON 抽 connectionString 做分段/长度/字符集统计
# 二进制取证：open(path,'rb').read() + re.finditer(b'...') 取偏移，不做反汇编/不写文件
```

---

## 2. B2 — `connectionString` 格式与语义

### 2.1 样本母体（比交接档多得多）

| 指标 | 值 |
|---|---|
| 带 `connectionString` 的大厅对象采样 | **1397** 条 |
| 去重后不同大厅 | **290** 个 |
| 覆盖游戏 build（`buildGameHash`） | **3 个**：`5870bf48340066e3`、`0a8ff2794f7f72a3`、`7256716030c211b1` |
| 覆盖时间 | 2026-10-27 … 2026-11-18（跨 3 周、跨 3 个 build） |
| 观察到的值 | **全部同一结构**，无一条例外 |

结论：这不是「偶发样本」，而是可以拿来做稳定性判定的母体 —— 后面所有「恒定」判断都来自这 1397 条。

### 2.2 结构拆解（脱敏骨架 + 度量）

骨架（总长 **107 字符**，全样本一致）：

```
cv2:<UUIDv4>.r-<8位数字>|<6位数字>|kv1:<44字符base64>
```

| 段 | 长度 | 字符集 | 稳定性（1397 样本 / 290 大厅 / 3 build） |
|---|---|---|---|
| `cv2:` 前缀 | 4 | 固定 ASCII | **恒定**（= version 2 类标记） |
| `<UUIDv4>` | 36 | `0-9a-f-`，v4（version nibble=`4` 1397/1397，variant=`8/9/a/b`，RFC4122） | **每大厅随机生成**（290 个不同值） |
| `.r-<8 位数字>` | 11 | `.r-` + 8 位十进制 | 数值恒定 **`r-20260323`**（跨 3 个 build 不变） |
| `|` 分隔 | 1 | 固定 | 恒定 |
| `<6 位数字>` | 6 | 十进制 | 恒定 **`619689`** |
| `|kv1:` | 5 | 固定 ASCII | 恒定（= key version 1 类标记） |
| `<44 字符 base64>` | 44 | 标准 base64（含 `+ / =`） | 解码恒为 **32 字节**；**每大厅唯一**（290 个不同值），同一大厅多次轮询**稳定不变**（= 每厅密钥） |

补充：**首段 UUID 与同对象的 `lobbyId` 字段 100% 相同**（1397/1397；`connectionString` 去掉 `cv2:` 和
`|…` 之后就是 `lobbyId`）。

### 2.3 关键否定：它不是 Steam SDR 的 SDHA —— `✗ 否证`

| 判据 | 证据 |
|---|---|
| **形态不符**：Steam `SteamDatagramHostedAddress` 是「`m_cbSize` + 不透明二进制 blob」的结构体（按需序列化成 ASCII 串，但由 Valve 私有格式定义），并不是 `cv2:…\|…\|kv1:…` 这种带 32 B 密钥的文本串 | Steamworks 头文件（`steamdatagram_tickets.h`）：`struct SteamDatagramHostedAddress { int m_cbSize; char m_data[k_cchSteamDatagramHostedAddressMax]; SteamNetworkingPOPID GetPopID() const; }` — <https://git.epicm.org/GitHub/Facepunch.Steamworks/commit/0bf379a41be6c6afabfa50a13167880ef945015a?files=Generator/steam_sdk/steamdatagram_tickets.h>；官方描述「intentionally opaque byte blob」— <https://swiftlys2.net/api-docs/beta/steamapi/steamdatagramhostedaddress> |
| **产权不符**：HD2 的大厅/队伍控制面在 PlayFab，不在 Steam（见 2.4）。`connectionString` 与 `lobbyId` 是 PlayFab Lobby 的**返回字段对** | 抓包字段集：大厅对象里同时有 `lobbyId` + `connectionString`，无任何 Steam 地址/SteamID 字段 |
| **字符串来源不符**：全 exe/dll 里没有 SDHA 文本格式的构造/解析代码；而 PlayFab 的「connection string」有完整调用链 | 见 2.4 偏移表 |
| **语义不符**：SDHA 是「怎么找到这台主机」的传输路由；本串是「怎么加进这个大厅」的**会员凭据**（PlayFab REST：`JoinLobby` 的 `ConnectionString` = "A field which indicates which lobby the user will be joining. This field is **opaque to everyone except the Lobby service**."） | <https://learn.microsoft.com/en-us/rest/api/playfab/multiplayer/lobby/join-lobby?view=playfab-rest> |

> 注：交接档 §12.4 的 Steam 证据（`ISteamNetworkingSockets012`、`POP remote/POP relay/FindingRoute`）
> **依然成立**，但它证明的是「**传输层**有 Steam 通道」，不能推出 `connectionString` 是 Steam 的。
> 本轮在 exe 里同时找到 `steam_transport`（Stingray，`…\runtime\steam\network\steam_transport.cpp`）
> 与 `playfab_transport`（`network.playfab_transport.*` 指标），还有明确的 fallback 逻辑，见 2.8。

### 2.4 关键正证：它是 PlayFab Multiplayer「Lobby」的连接串 —— `✓✓`

`helldivers2.exe` 中的调用链字符串（全部 ASCII 字面量，偏移为文件内 hex）：

| 偏移 | 字符串 | 含义 |
|---|---|---|
| `0x1704930` / `0x1704940` | `PartyWin.dll` / `PlayFabMultiplayerWin.dll` | 启动即加载 PlayFab 大厅库 + PlayFab Party 网络库 |
| `0x1744d80` | `Lobby joined (lobby_id=%s, connection_string=%s)` | 入厅后取 `lobbyId` + **`connection_string`** |
| `0x1744de8` | `Error calling PFLobbyGetConnectionString(): %x %s` | 显式调用 PlayFab「取大厅连接串」API |
| `0x1744f40` | `PFMultiplayerJoinLobby! (connection_string=%s)` | 用连接串**入厅**（`PFMultiplayerJoinLobby(handle, member, connectionString, …)`） |
| `0x1744ea0` | `PFMultiplayerCreateAndJoinLobby!` | 房主建厅 |
| `0x1744b60` / `0x1744b20` | `Error calling PFMultiplayerJoinLobby()` / `title_player_account` | PlayFab 实体类型（入厅主体是 `title_player_account`） |
| `0x1744aa0` / `0x1745000` / `0x17452f0` | `…\runtime\playfab\playfab_profile_manager.cpp` / `playfab_lobby.cpp` / `playfab_session_manager.cpp` | 引擎自研 PlayFab 封装（源码路径留在 exe 里） |

`PlayFabMultiplayerWin.dll` 里同一套 API 的导出名：`PFLobbyGetConnectionString`(`0x156d30`)、
`PFMultiplayerJoinLobby`(`0x156c38`)、`PFMultiplayerCreateAndJoinLobby`(`0x156c18`)、
`PFMultiplayerJoinArrangedLobby`(`0x156c50`)，以及 REST 字段名 `connectionString`(`0x15c138`)、
`lobbyId`(`0x15c110`)、`TitleId`(`0x15c3d0`)。

官方文档同构：

* `Lobby/CreateLobby` 的响应 = `CreateLobbyResult { ConnectionString: "A field which indicates which lobby
  the user will be joining", LobbyId }` —— **服务端签发，客户端只是保存/转发**；
* `Lobby/JoinLobby` 的请求 = `{ ConnectionString: "…opaque to everyone except the Lobby service…" }` ——
  **客户端无法解析，更无法自己造一个有效值**；
* 官方还明确写了「通过任意带外机制分发大厅连接串」正是推荐用法（HD2 的 `/api/lobby` 就是那个带外机制）。

URL：<https://learn.microsoft.com/en-us/rest/api/playfab/multiplayer/lobby/create-lobby?view=playfab-rest>、
<https://learn.microsoft.com/en-us/rest/api/playfab/multiplayer/lobby/join-lobby?view=playfab-rest>、
<https://learn.microsoft.com/en-us/gaming/playfab/multiplayer/lobby/join-lobbies>、
C++ 参考：<https://learn.microsoft.com/en-us/gaming/playfab/multiplayer/lobby/playfabmultiplayerreference-cpp/pflobby/functions/pflobbygetconnectionstring>、
<https://learn.microsoft.com/en-us/gaming/playfab/multiplayer/lobby/playfabmultiplayerreference-cpp/pflobby/functions/pfmultiplayerjoinlobby>

### 2.5 6 位数字 `619689` 的候选与判定 —— `✓`（强，但缺一条官方直证）

| 候选 | 排除/支持依据 | 结论 |
|---|---|---|
| Steam AppID（553850） | 数值不符（553850 ≠ 619689），且 AppID 不会进 PlayFab 串 | **✗** |
| 虚拟端口（virtual port） | 虚拟端口是 `uint16`（≤65535），619689 超界 | **✗** |
| 中继 POP ID / 区域码 | 1397 条样本里包含**数十个不同房主**的大厅，POP 会随房主地区变化；实测恒定 | **✗** |
| 会话/大厅随机数 | 290 个大厅全部相同 → 是部署级常量 | **✗** |
| 游戏 build/版本号 | 跨 3 个 `buildGameHash` 不变 → 不是 build 号 | **✗** |
| **PlayFab TitleId（十进制）** | `619689 = 0x974A9 = 974A9`（PlayFab TitleId 是 5–6 位十六进制）；连接串必须携带「它属于哪个 title」（官方：连接串定义「the title and member the string is valid for」类语义）；外部证据见下 | **✓ 主候选** |

外部证据：2026 年 HD2 玩家机上出现过 DNS 报错，直指 **`974a9.playfabapi.com`**（Steam 社区
「HELLDIVERS™ 2 Support」讨论串，App 553850）——
<https://steamcommunity.com/app/553850/discussions/1/6554498588629956250/> 。
即 HD2 的 PlayFab 标题域 = `974A9.playfabapi.com`，与 `619689` 的十六进制一致。
（`⚠`：PlayFab 官方不公开连接串内部字段布局，「十进制编码 TitleId」这一点没有文档直证；
连虚拟标题 `9F2A1.playfabapi.com` 也会被 PlayFab Front Door 先返回 `401`，所以**不能用 HTTP 探测反证
标题是否存在**，详见 §7 备注。）

### 2.6 `.r-20260323` 与 `kv1:` 段

| 段 | 判断 |
|---|---|
| `.r-<8 位>` = `r-20260323` | **跨 3 个 build、跨 3 周恒定**，且它在 `lobbyId` 里也出现 → 属于 PlayFab 大厅身份的一部分（realm/部署版本类标记）。`⚠` 精确语义未证；**但对我们无影响**：只要原样转发，客户端拿到的是同一个自洽串。 |
| `kv1:` + 32 B base64 | `kv1` = key version 1；32 B = 256-bit 密钥材料，**每大厅唯一、同厅多次抓取稳定**（290 个大厅 / 290 个不同值），应为大厅连接凭据的一部分。`⚠` 用途未证；**不需要解密**。 |

### 2.7 三态判定（B2）

| 结论 | 三态 |
|---|---|
| `connectionString` 是 PlayFab Multiplayer Lobby 服务签发的不透明连接串，不是 Steam SDHA | **✓✓**（二进制调用链 + 官方 REST 字段语义 + 1397 条抓包字段集） |
| 结构为 `cv2:<UUIDv4>.r-<8位>\|<6位>\|kv1:<44字符base64(32B)>`，总长 107 | **✓✓** |
| 串首段（去 `cv2:`）= 同对象 `lobbyId` 字段 | **✓✓**（1397/1397） |
| 6 位数字 = PlayFab TitleId 974A9（十进制 619689） | **✓**（强，无官方直证） |
| `.r-20260323` / `kv1: 32B` 的精确语义 | **⚠** |
| 自建信令「原样转发」= 必须且正确的最小实现 | **✓✓** |
| 自建信令「伪造/自造 connectionString」可让客机入厅 | **✗ 否证**（PlayFab 服务端校验，且串内含 title/大厅/密钥；`JoinLobby` 明确 "opaque to everyone except the Lobby service"） |
| 「原样转发」是否**足够**让客机真的进厅 | **⚠ 未证 → 依赖新未知 B8**（见 2.8/§5） |

### 2.8 对 B 线（自建联机）的净影响 —— 交接档 §5 需要补一条

本轮在 exe 里发现了**双轨传输 + PlayFab 控制面**的直接证据（交接档 §5.1 只写了 Steam 传输）：

* `0x1725af0`：`You must login to playfab before making a game session! (ignore this if you have disabled
  playfab in runtime)` —— 「创建游戏会话前必须先登录 PlayFab」；
* `0x1729570` / `0x17295d0` / `0x17296b0`：`Platform pong timeout … Falling back to playfab` /
  `Platform transport cannot talk to … Falling back to playfab` /
  `Platform authenticator denied the connection … Falling back to playfab`；
* `0x1729738`：`Creating playfab endpoint …`；`0x1744620+`：`network.playfab_transport.*` 指标；
* 传输实现源码路径：`…\runtime\steam\network\steam_transport.cpp`（`0x173a1c0`）与
  `…\runtime\playfab\playfab_lobby.cpp`（`0x1745000`）、`PartyWin.dll`（PlayFab Party 中继网络）。
* exe 里仍有 Steam 侧 SDR 状态字符串（`0x1738898 FindingRoute`、`0x1738989 POP remote`、
  `0x173899b POP relay`）—— 即 **Steam 传输仍然存在且优先**，PlayFab 是跨平台/兜底通道。

工程含义（对 `hd2-offline-research` 的 mock 也直接相关）：

1. HD2 的 **大厅成员关系**由 PlayFab Lobby 维护（`lobbyId`+`connectionString` 都是它签发的）；
   `/api/lobby` 只是**带外发现通道**。我们转发串 = 合法实现，但**客机执行
   `PFMultiplayerJoinLobby(connectionString)` 时连的是 PlayFab（Azure），不是我们的假后端**。
2. 因此「自建后端 + hosts 改向」能接管的是「发现层」，**不能接管入厅层**。入厅是否成功取决于
   PlayFab 是否可达 + HD2 的 PlayFab 标题（974A9）是否存活/仍接受 Steam 实体登录 → 新未知 **B8**。
3. 好消息：PlayFab 是**公网云服务**，其域名不在被 hosts 接管的范围；只要该标题还活着，即使官方游戏
   后端全灭，入厅这一段理论上仍能工作 —— 这是 B 线此前没考虑到的「可能自动被保住的半边」。
4. 若 PlayFab 标题也被下线，则需要客户端侧绕过（`runtime` 里有 "disabled playfab in runtime" 的说法 →
   新未知 **B10**：是否存在关闭 playfab 的运行时开关，让会话走平台层/离线层）。
   `⚠` 该开关名称/位置未知，属于纯静态线索，未验证。

---

## 3. B3 — 登录请求体里的 Steam 票据

### 3.1 本地 body 样本检索结果：没有 —— `✓✓`（查过，确无）

| 位置 | 结果 |
|---|---|
| `D:\mod\out\gwe\`（403 个 `req_*.json`） | **无** `Account/Login`；抓包 hook 的请求白名单写死为 `/api/Operation,/api/lobby,/api/Progression/Items/Customize`，见 `hd2-proxy-toolkit\src\hd2proxy\engine\hooks\capture.py`（注释明说「绝不写 header/token」） |
| `D:\mod\out\gwe\*_api_Account_Login.json`（23 个） | 全是**响应**：`{"sessionId":"<uuid>"}`（52 B） |
| `D:\Downloads\variant_mod\logs\proxy.log*` | 只有 `REQ POST /api/Account/Login 437B` 这类**长度**行；变种 mod 代理的安全设计就是「默认不落盘任何请求/响应体」 |
| `D:\mod\work\variant-net\`、`D:\mod\tmp\capture-*\`、`D:\mod\work\hd2-proxy-toolkit\` | 无 login body；`/api/Account/Login` 只出现在文档与单测里 |
| 结论 | **62 次带长度日志的登录全部 437 B（100% 恒定）**；总登录调用日志 131 行（含 gwe-dump）。定长 ⇒ 票据字段定长。 |

请求体本身是否可能被 gzip（从而 437 是压缩长度）？**否**：抓到的 `req_PUT_*_api_lobby.json` 文件
大小（1350 B）与代理日志 `REQ PUT /api/lobby 1350B` 完全一致 ⇒ 这些请求不压缩、日志长度=明文体长。

### 3.2 二进制证据（exe / dll 偏移）

**A. 取票路径（引擎自研 Steam 封装 `…\runtime\steam\steam_core.cpp`，源码路径在 `0x1739910`）**

| 偏移 | 字符串 | 含义 |
|---|---|---|
| `0x17396f0` | `[Auth] GetAuthSessionTicket failed, %i` | 调 `ISteamUser::GetAuthSessionTicket`（会话票据） |
| `0x1739950` | `[Auth] GetAuthSessionTicket failed with error %i, %i` | 同上，带 Steam 错误码 |
| `0x1739740` | `[Auth] request_auth_ticket for consumer_id %u` | 取票请求带 **consumer id**（引擎内部消费方枚举） |
| `0x17398f0` | `Calling GetAuthTicketForWebApi` | 调 `ISteamUser::GetAuthTicketForWebApi`（**给后端验票**用的 Web API 票据） |
| `0x17398a8` / `0x17398c8` | `GetAuthTicketForWebApi failed` / `… call failed` | 失败日志 |
| `0x1739830` / `0x1739860` | `on_api_auth_ticket_response …` | 等 `GetAuthTicketForWebApiResponse_t` 回调 |
| `0x1739988` | `[Auth] request_auth_ticket success, %i` | 取票成功 |

**B. 票据的两种「用票」场景**

| 偏移 | 字符串 | 含义 |
|---|---|---|
| `0x173b358`-`0x173b488` | `Steam validation for %llx with response %u` / `Sent ticket to %llx`(0x173b388) / `CancelAuthTicket %llx`(0x173b3d8) / `GetAuthSessionTicket %llx`(0x173b3f0) / `BeginAuthSession %llx`(0x173b488) / `EndAuthSession %llx`(0x173b3c0) | **P2P 对端互验**：会话票据 + `BeginAuthSession`（Steam 网络层鉴权） |
| `0x173b61d` | `Failed to request steam auth ticket, retrying, eos setup might time out`（旁边还有 `Setup eos` / `epiconlineservices`） | **EOS（Epic Online Services）初始化**也要 Steam 票据（跨平台身份） |
| `0x19e04f0` | RTTI `?AV?$CCallback@VSteamPlayer@PSNAccountLinking@@UGetAuthSessionTicketResponse_t@@` | **PSN 账号绑定**模块等 `GetAuthSessionTicketResponse_t` |
| `0x19e0521` / `0x19e0524` | `GetAuthSessionTicketResponse` / `AuthSessionTicketResponse` | 回调类型名 |

**C. 不用的路径（否证）**

| 证据 | 结论 |
|---|---|
| exe 中**没有** `RequestEncryptedAppTicket` / `GetEncryptedAppTicket` / `GetEncryptedAppTicket` 使用痕迹；这两个名字只出现在 `steam_api64.dll` 的导出名表（`0x43b87` / `0x439fe`，SDK 自带导出） | **✗** 登录票据不是 Encrypted App Ticket |
| `0x1739504` `SteamNetworkingSockets012`、`0x17394e9` `SteamNetworkingMessages002`、`0x173951e` `SteamNetworking006`、`0x1739621` `SteamUser023` | Steam 接口在位（与交接档 §12.4 一致），但**不等于** connectionString/登录票据来自 Steam |

**D. 登录体构造的候选模板（`helldivers2.exe` 字符串表）**

| 偏移 | 字符串 |
|---|---|
| `0x1498cc8` | `{ "platform": "STEAM", "code" : "{user_ticket}"}` ← **候选登录/认证 body 模板** |
| `0x1498f28` | `{ "accountId": "{account_id}"}` |
| `0x1498f60`-`0x1498fa0` | `{base_url}` / `{anon_token}` / `{config_id}` / `{link_platform}` / `{namespace}` |
| `0x1498fec`-`0x1499060` | `token` / `registration` / `origin` / `attributes` / `is_banned` / `is_suspended` / `url` |
| `0x14990c0` | `SteamUser021`（该模块直接引用 SteamUser 接口版本串） |
| `0x1499120`-`0x1499150` | `{token}` / `{account_id}` / `0123456789ABCDEF`（**十六进制编码字符集**）/ `{user_ticket}` |
| `0x1499340`-`0x14993a8` | 请求类型枚举：`RequestTypeNone` / `AuthenticationRequest` / `GetConfigData` / `GetRegisteredAccount` / `NewRegistrationURL` / `Deregister` |
| `0x14993b8`-`0x1499430` | WinHTTP 导入名（`WinHttpSendRequest` / `WinHttpReceiveResponse` / `WinHttpGetProxyForUrlEx` …）+ JsonCpp 报错串 |

**风格吻合度**：HD2 自己的请求 JSON 是「单行 + 冒号前带空格」的 JsonCpp 风格（例：
`{ "startTime" : "0", "lobbyId" : "…" }`，见抓到的 `req_PUT_*_api_lobby.json`），与 `0x1498cc8` 模板风格
一致；而 `EOSSDK-Win64-Shipping.dll` 中**没有**这套 `platform/code` 模板 —— 所以它更像 **HD2 自己
后端/账号服务** 的请求体，而不是 EOS 内部报文。`⚠` 但「它到底是不是 `/api/Account/Login` 的 body」
没有落盘样本可对，仍属推断。

**E. 尺寸自洽性检验（重要，决定了 body schema 的置信度）**

* 模板 `{ "platform": "STEAM", "code" : "{user_ticket}"}` 共 48 字符，其中占位符 `{user_ticket}` 13 字符
  ⇒ 固定部分 **35 字符**；若 body = 模板 + 单个票据串，则票据串 = `437 - 35 = 402` 字符。
* 402 若是**十六进制** ⇒ 201 字节（奇数 → **不可能**）；若是**标准 base64（带 `=`）** ⇒ 长度须为 4 的
  倍数（402 不是）；若是**无填充 base64** ⇒ 301 字节（可能，但 Steam 票据一般用 hex 传输）。
* ⇒ **单字段假设不完全闭合**：登录体很可能还有额外字段（例如 `accountId`/`token` 一类），或模板与实际
  拼装方式有差异。故 body schema 只能给「候选」，不能定案。

### 3.3 票据类型判定（session vs webapi vs encrypted app ticket）

| 类型 | 是否用于「登录 HD2 后端」 | 依据 |
|---|---|---|
| **Session ticket**（`GetAuthSessionTicket`） | **最可能** | ① 定长 437 B ⇒ 票据串 ~400 字符 / 200–300 B 量级，符合会话票据；② 引擎里确实等 `GetAuthSessionTicketResponse_t`（PSN 绑定）且 EOS 初始化也用它；③ Web API 票据会被 Steam 填充到 ~2.5 KB 级，hex 化后 body 会是 ~5 KB，与 437 B 严重不符 |
| Web API ticket（`GetAuthTicketForWebApi`） | 存在但更像别的消费方（EOS/PSN/后续服务） | 引擎有完整调用 + `consumer_id` 参数，但尺寸与登录体不符；且它设计给「服务端用 Web API 验票」 |
| Encrypted App Ticket | **不用** | exe 无该 API 使用痕迹（只有 DLL 导出） |

`⚠` 上表是「尺寸 + 调用链」的联合推断，**不是** body 直证。

### 3.4 三态判定（B3）

| 结论 | 三态 |
|---|---|
| 本地没有 login 请求体样本；62 次带长度日志全部 437 B | **✓✓** |
| 请求体不压缩（长度=明文字节数） | **✓✓**（`req_PUT` 文件大小 == 日志长度） |
| 登录响应只有 `{"sessionId":<uuid>}` | **✓✓**（23 个响应样本） |
| 引擎存在 `GetAuthSessionTicket`（会话票据）+ `GetAuthTicketForWebApi`（Web API 票据）两条取票路径 | **✓✓** |
| Encrypted App Ticket 不参与登录 | **✗ 否证**（无使用证据） |
| 登录票据**更可能是 session ticket**（尺寸 + 用途） | **⚠**（推断，缺 body） |
| 登录 body schema = `{platform:"STEAM",code:"<ticket>"}`（+ 可能还有字段） | **⚠**（模板在 exe、风格吻合，但 437 B 长度算式不闭合） |
| 自建私服**不解析票据**、只发 sessionId 的方案可行 | **✓✓**（响应本就只有 sessionId；后续接口靠 sessionId，不靠票据） |
| 若要真验票：需要 AppID 553850 的 Steamworks 合作方 publisher key（`ISteamUserAuth/AuthenticateUserTicket`，票据以 **hex 字符串**提交） | **✓**（Steamworks 文档 + SDK 常识），我们拿不到 ⇒ 实际**✗ 不可行** |

补充更正（影响身份映射方案）：**`accountId` 不是 SteamID64**。抓包里 445 个不同玩家 `memberAccountId`
中，**0 个**落在 SteamID64 区间（`76561197960265728`–`76561202255233023`），且有 **223/445 大于 `2^63`**
（是无符号 64 位随机样式）。⇒ 交接档 §3.1「`accountId` = SteamID64」需要更正为
「**HD2 自有的 64 位账号 ID**（与 SteamID 的映射在官方后端）」。私服自造 ID 即可，但不要假设能直接
塞 SteamID64 过校验。`sourcePlatform` 观测分布：`1`(3873)/`5`(1449，空位)/`0`(176)/`4`(90)，
`1` 应为 Steam 平台枚举值。

### 3.5 B3 建议方案（按代价从低到高）

1. **（推荐）完全不解析票据**：`POST /api/Account/Login` 收下任意 body → 生成 `sessionId` →
   内存表 `sessionId → 自造 accountId`（或按客户端 IP/预配置表映射）。后续所有接口只认 `sessionId`。
   这与 mock 现状一致，也避免持有敏感票据。
2. 若要「看起来更像官方」：把 body 只做长度/结构统计后丢弃，**不落盘、不入库**（沿用本轮纪律）。
3. 若要真身份：唯一正路是 Steamworks 合作方 key + `ISteamUserAuth/AuthenticateUserTicket`
   （需 hex 票据 + AppID 553850）—— 非合作方不可得，**不作为方案**。
4. 需要实测确认的点见 §6（B9）：抓一次 login body（脱敏后只记录 **模板/字段名/长度/编码形态**）。

---

## 4. 三态总表（速查）

| # | 结论 | 三态 |
|---|---|---|
| B2-1 | `connectionString` = PlayFab Multiplayer Lobby 的不透明连接串（非 Steam SDHA） | ✓✓ |
| B2-2 | 结构 `cv2:<UUIDv4>.r-20260323\|619689\|kv1:<44B64>`，107 字符，全样本一致 | ✓✓ |
| B2-3 | 首段 UUID == `lobbyId`（1397/1397） | ✓✓ |
| B2-4 | 6 位数字 = PlayFab TitleId `974A9`（十进制 619689） | ✓（强，无官方直证） |
| B2-5 | `.r-20260323`/`kv1:32B` 精确语义 | ⚠ |
| B2-6 | 自建信令必须原样转发、不能伪造 | ✓✓ |
| B2-7 | 原样转发是否足够端到端入厅（PlayFab 存活依赖） | ⚠（新未知 B8） |
| B3-1 | 本地无 login body；62 次登录全 437 B；body 未压缩 | ✓✓ |
| B3-2 | 引擎有 session/webapi 两条取票路径；无 encrypted app ticket 路径 | ✓✓ / ✗ |
| B3-3 | 登录票据更可能是 session ticket | ⚠（尺寸+调用链推断） |
| B3-4 | 登录 body schema 候选 `{platform, code}`（可能还有字段） | ⚠ |
| B3-5 | 私服不解析票据、只发 sessionId 的方案可行 | ✓✓ |
| B3-6 | 真验票需合作方 publisher key（不可得） | ✗（对非合作方） |
| B3-7 | `accountId` 不是 SteamID64（445/445 反证） | ✓✓（更正交接档 §3.1） |

---

## 5. 对既有文档的更正 / 新增未知

**更正**

1. 交接档 §3.1「`accountId` = SteamID64」→ **错**：观测 445 个账号 ID 无一在 SteamID64 区间，且大量
   超过 `2^63`。应为 HD2 自有 64 位账号 ID（§3.4 补注）。
2. 交接档 §5.1 把传输层写成单一「Steam P2P + SDR」→ **不完整**：exe 里有 `steam_transport`
   （Steam 优先）+ `playfab_transport`（PlayFab Party 兜底，`Falling back to playfab`），且**大厅控制面
   完全在 PlayFab Multiplayer Lobby**。不影响「不自建/不替换传输」的结论，但影响「私服必须假装什么」
   的边界（见 B8）。
3. 兄弟文档 `docs/OFFLINE-PRIVATE-SERVER-RESEARCH-2026-11-21.md` 的门禁项
   「GET /api/lobby/{id} 原样返回 connectionString（自建信令核心）」—— byte 级转发本身没问题，但
   **不能据此推断「客机能加入」**；入厅是 PlayFab 的事（B8）。建议在该门禁描述里加一句限定。

**新增未知（供交接档 §10 追加）**

| # | 未知 | 怎么验 | 影响 |
|---|---|---|---|
| **B8** | HD2 的 PlayFab 标题（974A9）在「官方后端不可达」时是否还活着、还接受 Steam 实体登录/入厅 | 不用启动游戏也能先做半静态验证：抓一次游戏的 PlayFab 请求域名/DNS（或看玩家侧 DNS 报错）；实机阶段：自建信令 + 官方 API 被 hosts 掐断，看入厅是否仍成功 | **高**：决定「原样转发」是够用还是空转 |
| **B9** | login body 的真实 schema / 票据类型（session vs webapi） | 抓一次 `/api/Account/Login` 的 body，只记录模板/字段名/长度/编码形态后丢弃（不落原值） | 中：决定是否需要伪装字段 |
| **B10** | 「disabled playfab in runtime」开关存在性与位置（exe `0x1725af0` 提示可有） | 静态：搜 data/Lua 与命令行参数处理；动态：带参数启动观察日志（需用户配合） | 中：PlayFab 若死，这是唯一软绕路 |

---

## 6. 仍需实测的点（按优先级）

| 优先级 | 实测项 | 判据（通过条件） |
|---|---|---|
| P0 | B8：自建信令接管 `/api/lobby*` 后，客机执行入厅是否仍走向 PlayFab 并成功 | 客机侧出现 `Lobby joined (lobby_id=…, connection_string=…)` 且 `numLobbyMembers` 增加；失败则记录 PlayFab 错误码/超时 |
| P0 | B1（交接档原有）：同 Steam 平台上双机 + 自建信令能否互相看见大厅并加入 | 两台客户端 `PUT/GET /api/lobby` 状态互通 + 实机进同一局 |
| P1 | ~~B9：抓 1 次 login body（脱敏）~~ **已做（2026-11-21 02:09）** | ✅ 字段名清单 / 长度 / 字符类已拿到（6 字段 + 绑定账号多 3 个），见 `B9-LOGIN-BODY-SCHEMA-2026-11-21.md` |
| P1 | B8 的前置：确认 HD2 PlayFab 标题是否仍响应（无需游戏） | 用与游戏一致的 host 名做 DNS/HTTPS 探测，记录状态码/错误体（注意：不可用「随机 TitleId」做反证，Front Door 会先 401） |
| P2 | B10：找 `playfab` 运行时开关 | 若有：关掉后 `network.cpp` 不再要求 PlayFab login，且会话改用平台层 |
| P2 | B2 的字段语义补全（`.r-` / `kv1` / `619689` 的官方定义） | 能拿到 PlayFab 内部格式说明或反推证据（非必需） |

---

## 7. 证据索引

### 7.1 二进制偏移（文件内绝对偏移，hex）

* `helldivers2.exe`
  * 加载模块清单：`0x1704900 game.dll`、`0x1704930 PartyWin.dll`、`0x1704940 PlayFabMultiplayerWin.dll`、
    `0x17049b0 steam_api64.dll`
  * PlayFab 会话/网络：`0x1725af0`（login playfab 才能建 game session）、`0x1725b60`
    （`…\runtime\engine\network\network.cpp`）、`0x1725ba8`（`game_session_config`）
  * 双轨传输：`0x1729570` / `0x17295d0` / `0x17296b0`（`Falling back to playfab`）、`0x1729738`
    （`Creating playfab endpoint`）、`0x173a1c0`（`…\runtime\steam\network\steam_transport.cpp`）、
    `0x173a210`（`steam_transport`）、`0x1744620`-`0x17446d8`（`network.playfab_transport.*`）、
    `0x1744788`（`playfab_transport`）
  * PlayFab Lobby：`0x1744d80`（lobby joined + connection_string）、`0x1744de8`
    （`PFLobbyGetConnectionString`）、`0x1744f40`（`PFMultiplayerJoinLobby! (connection_string=%s)`）、
    `0x1744ea0`（`PFMultiplayerCreateAndJoinLobby!`）、`0x1744b20`（`title_player_account`）、
    `0x1744aa0`/`0x1745000`/`0x17452f0`（playfab_* 源码路径）
  * Steam 网络/鉴权：`0x1738820`（`c_api_steam.cpp`）、`0x1738898`（`FindingRoute`）、
    `0x1738989`（`POP remote`）、`0x173899b`（`POP relay`）、`0x1739504`
    （`SteamNetworkingSockets012`）、`0x1739621`（`SteamUser023`）、`0x173b3c0`-`0x173b488`
    （`CancelAuthTicket`/`GetAuthSessionTicket`/`BeginAuthSession`/`EndAuthSession`）、
    `0x173b61d`（EOS 需要 steam auth ticket）
  * 取票：`0x17396f0`、`0x1739740`、`0x1739830`、`0x1739860`、`0x17398a8`、`0x17398c8`、`0x17398f0`、
    `0x1739910`（`steam_core.cpp`）、`0x1739950`、`0x1739988`
  * 账号/登录模板区：`0x1498cc8`、`0x1498f28`、`0x1498f60`-`0x1498fa0`、`0x14990c0`（`SteamUser021`）、
    `0x1499120`-`0x1499150`、`0x1499340`-`0x14993a8`、`0x14993b8`+（WinHTTP/JsonCpp）
* `steam_api64.dll`：`0x4395d`/`0x43986`（`SteamAPI_ISteamUser_GetAuthSessionTicket` /
  `…GetAuthTicketForWebApi` 导出名）、`0x439fe`/`0x43b87`（`GetEncryptedAppTicket` /
  `RequestEncryptedAppTicket` 导出名，仅导出）、`0x445c3`（`SteamAPI_SteamDatagramHostedAddress_*`）
* `PlayFabMultiplayerWin.dll`：`0x156c18`/`0x156c38`/`0x156c50`/`0x156d30`（CreateAndJoin / Join /
  JoinArranged / GetConnectionString）、`0x15c110`/`0x15c138`/`0x15c3d0`（REST 字段 lobbyId /
  connectionString / TitleId）、`0x1536b8`（PubSub connection string 错误）
* `PartyWin.dll`：`0x31bb40`/`0x31bb60`（`PartySerializeNetworkDescriptor` /
  `PartyDeserializeNetworkDescriptor`）、`0x31e4e0`/`0x31e580`（描述符/连接信息非法）、`0x31f4d0`
  （描述符 checksum 不匹配）— 用于排除「connectionString 是 Party network descriptor」的假设
  （Party 描述符有 checksum 且长度大得多）
* `EOSSDK-Win64-Shipping.dll`：无 `platform/code` 模板、无 `user_ticket` 串（⇒ `0x1498cc8` 模板不属于 EOS SDK）

### 7.2 抓包/日志文件

| 路径 | 用途 |
|---|---|
| `D:\mod\out\gwe\**\*.json` | 1397 条大厅对象（290 个不同大厅，3 个 build hash）；`req_*.json` 403 个请求体（无 login） |
| `D:\mod\out\gwe\index.txt` | 988 条记录，2026-10-27…11-07；含 `REQ PUT /api/lobby 1350` 与文件大小对照 |
| `D:\mod\tmp\capture-store-season-20261118\raw\18a5ad58_api_lobby_accounts.json` | 5 个大厅（含 `connectionString`+`lobbyId` 同对象对照） |
| `D:\mod\tmp\capture-store-season-20261118\raw\a5335597_api_Account_Login.json` | 登录响应仅 `sessionId`（52 B） |
| `D:\mod\tmp\capture-store-season-20261118\raw\d3347dcd_api_Configuration_GameClient.json` | 命名空间/轮询配置（无 PlayFab titleId/URL，说明 titleId 不在配置下发里） |
| `D:\Downloads\variant_mod\logs\proxy.log(.1)` | 62 次 `REQ POST /api/Account/Login 437B`（全部 437） |
| `D:\mod\work\hd2-proxy-toolkit\src\hd2proxy\engine\hooks\capture.py` | 请求体白名单来源（解释为何没有 login body） |
| `C:\Users\Admin\AppData\Roaming\Arrowhead\Helldivers2\user_settings.config` | 无 playfab/网络开关字段（8397 B，已查） |

### 7.3 外部 URL

* PlayFab Lobby Create（响应返回 `ConnectionString`+`LobbyId`）：
  <https://learn.microsoft.com/en-us/rest/api/playfab/multiplayer/lobby/create-lobby?view=playfab-rest>
* PlayFab Lobby Join（`ConnectionString` 对除 Lobby 服务外所有人不透明）：
  <https://learn.microsoft.com/en-us/rest/api/playfab/multiplayer/lobby/join-lobby?view=playfab-rest>
* PlayFab「Joining lobbies and connection strings」（带外分发=官方推荐用法）：
  <https://learn.microsoft.com/en-us/gaming/playfab/multiplayer/lobby/join-lobbies>
* PlayFab C++ `PFLobbyGetConnectionString` / `PFMultiplayerJoinLobby`：
  <https://learn.microsoft.com/en-us/gaming/playfab/multiplayer/lobby/playfabmultiplayerreference-cpp/pflobby/functions/pflobbygetconnectionstring>、
  <https://learn.microsoft.com/en-us/gaming/playfab/multiplayer/lobby/playfabmultiplayerreference-cpp/pflobby/functions/pfmultiplayerjoinlobby>
* PlayFab Party `SerializeNetworkDescriptor`（用于排除 Party descriptor 假设）：
  <https://learn.microsoft.com/en-us/gaming/playfab/multiplayer/networking/reference/classes/partymanager/methods/partymanager_serializenetworkdescriptor>
* Steam `SteamDatagramHostedAddress` 结构体（`m_cbSize` + blob）：
  <https://git.epicm.org/GitHub/Facepunch.Steamworks/commit/0bf379a41be6c6afabfa50a13167880ef945015a?files=Generator/steam_sdk/steamdatagram_tickets.h>
* Steam SDHA「intentionally opaque byte blob」：
  <https://swiftlys2.net/api-docs/beta/steamapi/steamdatagramhostedaddress>
* Steamworks `ISteamUser`（`GetAuthTicketForWebApi` 给 `ISteamUserAuth/AuthenticateUserTicket` 用；
  票据以 hex 字符串提交）：
  <https://partner.steamgames.com/doc/api/ISteamUser>、
  <https://docs.rs/steam-api-web/0.5.0/steam_api_web/user_auth/struct.UserAuth.html>
* HD2 玩家机报 `974a9.playfabapi.com` DNS 解析失败（证明 HD2 的 PlayFab 标题域）：
  <https://steamcommunity.com/app/553850/discussions/1/6554498588629956250/>

---

## 8. 本轮没做的事（边界重申）

* 没有启动游戏、没有起网络监听、没有启停 hd2proxy、没有改 hosts/证书/游戏目录/产品仓/运行态目录；
* 没有写除本文件以外的任何文件；抓包/样本只读引用；
* 没有对 `connectionString` 做解密/逆向算法（只做结构统计与调用链归属）；
* 没有抓新的 login body（需要用户配合实机，见 §6 P1 / B9）。

---

## 9. B9 回填（2026-11-21 02:09，实机）

B9 已完成，登录体 schema 实测拿到。本文受影响的表述与更正：

| 本文原表述 | 更正后 | 依据 |
|---|---|---|
| §0-3 / §3.3「62 次登录全部 437 B（定长 → 票据字段定长）」 | 437 B 只是**同一绑定账号**的定长；未绑定账号是 **346 B**。字段长度：`token` 116、`publicKey` 44、`buildIdentifier` 61、`instanceId` 6，两账号完全相同 | B9 §2 |
| §3.3「若 body = 模板 + 单个票据串，则票据串 = 437-35 = 402 字符」 | **作废**：body 是 6/9 字段 JSON，`token` 恒 116 字符 | B9 §3 |
| §3.4「按尺寸反推更像会话票据」中的**尺寸论证** | 作废；票据类型结论本身未被推翻（`token` 116 字符 ascii，既非 hex 也非纯 base64） | B9 §3 |
| §6 P1「是否含 accountId/token 字段」（未知） | **含**：`token`（票据）+ 绑定账号额外的 `betaToolkitAccountId`（19 位数字） | B9 §2 |
| 「中：决定是否需要伪装字段」 | **不需要**：mock 不校验 body，两种 body 都 200，两账号均进主界面 | B9 §4.3 |

**新增的未证项**（原 B9 未覆盖）：三个可选字段（`isSuspended`/`isBanned`/`betaToolkitAccountId`）的最终来源
（PSN 绑定态 vs 历史真服务器下发的账号态缓存）仍是 `⚠ 未证`，判别实验见 `B9-LOGIN-BODY-SCHEMA-2026-11-21.md` §4.2。

