# T6a：旁路连接到底传了什么——用研究 CA 做 MITM 的内容取证（2026-11-21）

> 前置：[T4 运行时出网清单](T4-RUNTIME-EGRESS-2026-11-21.md) 只拿到「域名/端口/字节数」，
> 不知道**传的内容**。本文用研究线自己的 CA（装进 `CurrentUser\Root`）对旁路域名做 TLS 终止，
> 把请求/响应抽成 **schema（字段名/长度/字符类/hash）**——不落原值，与 B9 同一纪律。
> 三态：`✓✓ 实机` / `✓ 多源一致` / `⚠ 未证` / `✗ 否证`。

## 0. 一句话结论

**没被接管的连接分四类用途：PlayFab = 联机控制面（大厅 + Party 中继服务器分配 + QoS 区列表）；
EOS = 身份/配置/反作弊（EAC）/遥测；Sony CRS = 崩溃诊断上报；Steam 侧 = P2P 发现/静态资源。
全部与"游戏进度"无关——进度只走被 mock 接管的官方 API（T3/T3b 已证）。**
其中 **PlayFab 的三个接口直接决定"能不能联机"**（§2.1），这是线 B 的关键发现。

## 1. 方法与证据

| 项 | 值 |
|---|---|
| MITM 代理 | `tools/mitm_proxy.py`：HTTP CONNECT 代理；命中 `--intercept` 的域名做 TLS 终止（研究 CA 多 SAN 叶证书），其余域名链到用户代理 `127.0.0.1:7897` 纯转发不记录 |
| 客户端如何走代理 | 临时把 HKCU IE 代理（WinHTTP/WinINET 会读）指到 `127.0.0.1:18092`，并给游戏进程设 `HTTP(S)_PROXY`（libcurl 系 SDK 读）；**跑完立即恢复**（备份值 `ProxyEnable=1 ProxyServer=127.0.0.1:7897`） |
| 官方 API | 一并纳入拦截，但上游用 `--mock-ca` 校验 → 仍由本机 mock 应答（保证不碰真服务器） |
| 证书 | `mockctl cert-gen --hosts <13 个域名>`：多 SAN 叶证书 + 匹配的 CA NameConstraints（12 个旁路域名 + 官方 API），2 天有效 |
| 采集 | 2 次游戏启动窗口：**45 + 34 条记录**；日志 `state/evidence/t6/mitm-run1.jsonl` / `mitm.jsonl`，分析 `tools/analyze_mitm.py` |
| 脱敏 | 只记 schema：字符串=`len/字符类/sha256[:8]`；数字/布尔记值（非敏感）；不落任何 token/票据 |

**MITM 生效的判据**：客户端看到的是 `CN=HD2 Offline Research CA` 签发的叶证书（已实测），
而不是真服务器证书；且拿到的是真服务的正常 JSON 响应。

## 2. 实测内容（按域名）

### 2.1 `974a9.playfabapi.com` —— **联机控制面**（16 条请求，✓✓）

| 接口 | 请求 schema（摘要） | 响应 schema（摘要） | 作用 |
|---|---|---|---|
| `POST /MultiplayerServer/ListPartyQosServers` | `{CustomTags:{Version}}` | `{code, status, data:{QosServers:[17 × {ServerUrl, Region}], PageSize:17}}` | **拿到 17 个 Azure 区 QoS 服务器列表** → 正是 T4 里 UDP 3075 探测的目标来源 |
| `POST /Party/RequestParty` | `{BuildAliasParams:{AliasId(uuid)}, PartyId(uuid), PreferredRegions:[17], SessionCookie(80 base64), Version}` | `{data:{PartyId, ServerId, VmId(len 97), IPV4Address, FQDN, Ports[1×{Name,Num,Protocol}], Region, State, ConnectedPlayers[0], LastStateTransitionTime, **DTLSCertificateSHA2Thumbprint(64 hex)**, BuildId}}` | **PlayFab 给这一局分配了一台 Party 中继服务器**（含 FQDN/IP/端口/DTLS 证书指纹）→ 联机数据面就是连它 |
| `POST /Lobby/CreateLobby` | `{AccessPolicy, LobbyData{数字键 + descriptor(208 base64) + invite_id(uuid)}, MaxPlayers:8, Owner:{Id(16 hex),Type}, OwnerMigrationPolicy, SearchData{number_key*, string_key*}, UseConnections, members[1]}` | `{data:{LobbyId(len 47), **ConnectionString(len 107)**}}` | **大厅创建 + `connectionString` 就是这里签发的**（与 B2/B3 抓包 107 字符完全一致 ✓） |
| `POST /Lobby/GetLobby` / `UpdateLobby` | `{LobbyId}` / 更新字段 | `{data:{Lobby:{LobbyId, ChangeNumber, MaxPlayers, Owner, UseConnections, OwnerMigrationPolicy, AccessPolicy, MembershipLock, ConnectionString, SearchData, LobbyData, Members, RestrictInvitesToLobbyOwner}}}` | 大厅查询/更新（大厅本身就是联机房间） |
| `POST /Lobby/SubscribeToLobbyResource` | `{EntityKey:{Id(16 hex),Type}, ResourceId, SubscriptionVersion, Type, pubSubConnectionHandle(len 102)}` | `{data:{Topic}}` | 订阅大厅资源变更 |
| `POST /pubsub/negotiate?negotiateVersion=1` | — | `{accessToken(len 779), url(len 81)}` | **SignalR pub/sub 长连接凭证**（大厅实时推送通道） |
| `POST /Event/WriteTelemetryEvents` | `{CustomTags:{}, Events:[N × {CustomTags, Entity, EventNamespace, Name, OriginalId, OriginalTimestamp, Payload, PayloadJSON}]}` | `{data:{AssignedEventIds:[N × 32 hex]}}` | PlayFab 遥测 |

**UA 字符串**（直接暴露 SDK 身份）：`PlayFabParty/1.0`、`libHttpClient/1.0.0.0`、`XPlatCppSdk-3.60.211025`（query 参数）。

### 2.2 `api.epicgames.dev` —— **EOS：身份/配置/反作弊/遥测**（27 + 17 条请求，✓✓）

| 接口 | 内容要点 |
|---|---|
| `GET /sdk/v1/default?platformId=WIN`、`GET /sdk/v1/product/<ProductId>?deploymentId=…` | **EOS 客户端配置（gzip，7.9 KB）**：`services` 下 ~39 个服务的 `BaseUrl`；`P2PClient:{IceServers, P2PMinPort, P2PMaxPort}`；`AntiCheat.GameplayData:{Url}`；`Messaging.{EpicConnect,Stomp,XMPP}` 的 URL/心跳；`Core.Log` 各类日志开关；`GameClient.UseGzipEncoding` |
| `POST /auth/v1/oauth/token` | EOS 令牌颁发（请求 76/761 B，响应 1851/2946 B） |
| `GET /anticheat/v6/service/status/` | 反作弊服务状态（响应 682 B JSON） |
| `POST /anticheat/v8/games/client/message/` | **EAC 反作弊消息通道**：`application/octet-stream`，请求 388 B–**33 KB**、响应 1289–9634 B，整局持续心跳/上报——这是 EOS 侧**最大的一块流量** |
| `POST /telemetry/data/api/v1/public/data/clients?AppID=…`、`POST /telemetry/data/datarouter/api/v1/public/data?…` | EOS 遥测：`{Events:[N × {EventName, DateOffset, UserAgent, ProductName, ProductVersion, CallingLanguage, ProductId, SandboxId, DeploymentId, OSName, OSVer, SDKVer, IntegratedPlatform, BinaryArchitecture, NumDroppedEvents, SecondsSincePlatformInit, Platform}]}` |
| `GET /notifications/v1/<id>/connect` | **101 Switching Protocols** → EOS 通知 WebSocket 长连接 |

**UA**：`EOS-SDK/1.19.1.2-53289219 (Windows…`、`EOSSDK/++EOSSDK+Release-1.19.1.2`。

### 2.2.1 EAC 反作弊通道到底在传什么（从 `state/evidence/t6/mitm*.jsonl` 复算，✓✓）

用户问「EAC 服务的连接是只检查更新，还是传别的数据」。用两次 T6a 记录的原始 jsonl 复算（run1 窗口 156 s，45 条记录；
run2 窗口 78 s，34 条）。EOS/EAC 侧实际是**三条完全不同的通道**：

| 通道 | 端点 | 频次 | 体量 | 性质 |
|---|---|---|---|---|
| **① 会话主通道** | `POST /anticheat/v8/games/client/message/`（`application/octet-stream`） | 启动后 **每 ~12 s 一条**，节奏 5–30 s 不等，**持续整局** | 请求 **388 B–33,472 B**；响应 **1,289–9,634 B**；run1 共 13 条 = 请求 114.7 KB + 响应 49.8 KB | **双向会话**：上行客户端证明/完整性/事件上报，下行服务端下发指令/配置/状态（内容加密不可读） |
| **② 服务状态** | `GET /anticheat/v6/service/status/` | **每 ~60 s** 一次 | 响应固定 **682 B JSON**，请求 0 B | "反作弊服务此刻是否可用/维护"查询 |
| **③ 模块下发（唯一的"更新/下载"）** | `modules-cdn.eac-prod.on.epicgames.com`（CloudFront） | **启动时按需**，已缓存则不发生 | 模块本体（本次窗口未触发） | EAC 模块下载/校验，见 [T4](T4-RUNTIME-EGRESS-2026-11-21.md) |

同一窗口里 **EOS 侧其它流量**（同 host `api.epicgames.dev`）对比：

| 类别 | 条数 | 请求 B | 响应 B |
|---|---|---|---|
| anticheat（①+②） | 16 | **114,684** | **49,760** |
| telemetry（`/telemetry/data/...`，批量事件） | 6 | 24,206 | 4,033 |
| sdk 配置（`/sdk/v1/...`，gzip 7.9 KB，含 `AntiCheat.GameplayData.Url`） | 2 | 0 | 15,802 |
| auth（`/auth/v1/oauth/token`） | 2 | 837 | 4,797 |
| notifications（WebSocket 101） | 1 | 0 | 700 |

⇒ **反作弊通道占该窗口 EOS 侧字节的 ~77%**（164.4 KB / 214.8 KB），换算约 **1.05 KB/s（≈8.4 kbps）**，
即整局 ~6 MB/小时量级。而 PlayFab 侧同期只有 ~40 KB；Sony CRS 1.1 KB。

**结论**：

1. **不是"只检查更新"**。更新/下载只有 ③（且已缓存时启动根本不下载）；② 是服务状态轮询；
   真正占大头的是 ① 的**持续双向会话**——与游戏内 `EOS_AntiCheatClient_*` API 面（BeginSession/EndSession、
   消息保护、peer 注册、`OnIntegrityViolated` 回调、`AddNotify*` 通知）一一对应，属于"会话 + 完整性 + 上报"。
2. **内容不可解**：`application/octet-stream` 是 TLS 之内再套 EAC 自己的加密/签名，TLS MITM 只能拿到长度与节奏。
   所以上表的"用途"是**协议形状 + API 面 + 公开资料**的推断，不是解密结果；能确定的是频次/大小/双向性/启动必需性。
3. **启动必需**：[T7](T7-STAGE1-NECESSITY-2026-11-21.md) R2/R6 证明断开或伪 `200 {}` → EAC 弹
   「无法连接反作弊网络，游戏将退出」⇒ ① 的会话建立不是可选的遥测，而是进游戏的硬前提。
4. **未证边界**：EAC 独立服务进程 `EasyAntiCheat_EOS.exe` 不读 IE 代理设置，本工具 MITM 覆盖不到它的连接；
   T4 整机抓包只到主机级（`modules-cdn`），无进程级归属 ⇒「服务进程自己还传了什么」= `⚠ 未证`。


### 2.3 `crs.api.wwsga.me` —— **Sony 崩溃/诊断上报**（1 条请求，✓✓）

`POST /games/<uuid>/telemetry` → **400**（缺有效 game 上下文），但请求 schema 完整暴露了它上报什么：

```
header: {client_timestamp, client_timezone, build_version(16), session_id(uuid), device_type(2),
         arch(5), user_id, title_id(uuid), ga_app_id(uuid), crs_client_id,
         crs_url(148), crs_process_tag(28), app_version}
data:   {event_type(7), option_capture_image:true, option_capture_video:false,
         option_no_rate_limit:false, option_extended_dump:false, option_full_dump:false,
         option_no_uploads:false, option_anti_cheat:true}
```

⇒ 它是**崩溃转储/诊断上报客户端**（`crs` = crash reporting service），带"是否抓图/录像/扩展 dump/反作弊"开关。

### 2.4 本次窗口没出现的旁路

| 域名 | 说明 |
|---|---|
| `datarouter.ol.epicgames.com` | 本窗口 EOS 遥测实际打到 `api.epicgames.dev/telemetry/data/datarouter/...`（同域路径），未单独连该主机 |
| `modules-cdn.eac-prod.on.epicgames.com` | EAC 模块已缓存，本窗口未下载 |
| `testament/clientconfig.api.wwsga.me`、`pss-cloud.net` | 本窗口未出现（可能属其它进程/时机；T4 曾见到，需单独进程级验证） |
| Steam 侧（`steamcommunity.com` / `shared.steamstatic.com` / `p2p-*.discovery`） | 由 `steam.exe`/`steamwebhelper.exe` 发起，不读 IE 代理设置，MITM 覆盖不到（属 Steam 边界） |
| UDP DTLS（`45368` 等） | Party/EOS 数据面，DTLS 加密；本工具只做 TCP，未覆盖 |

## 3. 对线 A / 线 B 的意义

1. **线 A（离线单机）不受影响** `✓✓`：这些旁路全断也不影响单机（T3/T3b 已证）；它们承载的是联机控制面/身份/反作弊/遥测。
2. **线 B（自建联机）关键结论** `✓✓`：
   * 自建后端**可以接管** `/api/lobby`（游戏自有 API 层，已在 mock 里原样转发 `connectionString`）；
   * 但 **`connectionString` 的签发者、以及真正的中继服务器分配**都在 **PlayFab**：`Lobby/CreateLobby` 发 `connectionString`，
     `Party/RequestParty` 返回**中继服务器 FQDN/IP/端口/DTLS 证书指纹**。客机拿到的 `connectionString` 里就指向这台服务器；
   * ⇒ 「自建信令 + 原样转发」只能解决"互相看见"，**入厅/数据面仍要经过 PlayFab 分配的服务器**（B8 的实测答案）。
     要完全自建联机，需要额外解决 PlayFab Party 中继这一层（替换或自建中继，属于后续议题）。
3. **QoS 区列表来源** `✓✓`：T4 里 16 区 UDP 3075 探测，目标列表就是 `ListPartyQosServers` 返回的 17 个 `{ServerUrl, Region}`。

## 4. 三态判定

| 断言 | 状态 | 依据 |
|---|---|---|
| PlayFab / EOS / Sony CRS 的 HTTPS 内容可被研究 CA MITM 解密 | **✓✓ 实机** | 2 次窗口共 79 条记录，0 次 TLS 握手失败 |
| PlayFab = 大厅控制面 + Party 中继分配 + QoS 列表 | **✓✓ 实机** | §2.1 的接口与响应 schema |
| `connectionString` 由 PlayFab 签发（107 字符） | **✓✓ 实机** | `Lobby/CreateLobby` 响应 `ConnectionString(len 107)`，与 B2/B3 一致 |
| EOS 的主力流量是 EAC 反作弊消息（octet-stream） | **✓✓ 实机** | `/anticheat/v8/games/client/message/` 27+17 条中占多数，最大 33 KB |
| Sony CRS = 崩溃/诊断上报 | **✓✓ 实机** | §2.3 的 `crs_url`/`crs_process_tag`/dump/抓图开关 |
| EOS 的 HTTP **自带信任链、不可 MITM** | **✗ 否证** | 实测 44 条 EOS 请求全部解密（其 OpenSSL 用于 DTLS/P2P，HTTP 走系统信任的 libcurl.dll-Schannel） |
| Steam SDR / UDP DTLS 数据面内容 | **⚠ 未证** | 加密且由其它进程/协议栈承载；只能靠元数据 + 差分阻断 |

## 5. 复现

```powershell
cd D:\mod\work\hd2-offline-research
# 1) 多 SAN 证书 + 装信任
python tools\mockctl.py cert-gen --hosts "api.live.prod.thehelldiversgame.com,974a9.playfabapi.com,api.epicgames.dev,crs.api.wwsga.me"
python tools\mockctl.py cert-install
# 2) mock（官方 API 离线）+ MITM 代理
python tools\mockctl.py serve                                  # 另一个窗口
python tools\mitm_proxy.py --port 18092 --cert-dir state\certs `
  --intercept "974a9.playfabapi.com,api.epicgames.dev,crs.api.wwsga.me" `
  --mock-hosts api.live.prod.thehelldiversgame.com --mock-ca state\certs\ca.pem `
  --log state\evidence\t6\mitm.jsonl
# 3) 临时把 IE 代理指到 18092（先备份！），启动游戏 90-150 s
# 4) 恢复代理，分析
python tools\analyze_mitm.py --log state\evidence\t6\mitm.jsonl
```

单测：`python -m pytest tests/test_mitm_proxy.py tests/test_certs.py -q`
（MITM：明文穿回 + 只落 schema + gzip 解压 + 非命中域名不记录；证书：多 SAN + NameConstraints 自检。）

## 6. 局限与边界

* **只覆盖 TCP/HTTPS**：UDP（PlayFab QoS、Steam SDR、EOS 中继 DTLS）不在本工具范围；
* **只覆盖读系统代理/环境变量的进程**：`steam.exe`/`steamwebhelper.exe`、EAC 独立进程等不读这些设置，
  需要进程级注入或 WFP 级重定向（未做）；
* 抓到的内容里确实含第三方令牌（PlayFab `accessToken`、EOS `oauth token`、pubsub handle）——
  **一律只记长度/字符类/hash**，原值不落盘不入库（`state/` 且 gitignore）；
* MITM 会短暂接管本机代理（约 2–3 分钟），跑完必须恢复；本轮的备份/恢复已实测（`state/evidence/t6/ie-proxy-backup.json`）。
