# PlayFab 侧真机流程（阶段2 E3 核心发现）— 2026-11-21 23:24

> 条件：我方客户端（Steam 在线、经中继到发现层）在 23:23 启动、23:25 退出；
> `tools/playfab_mitm.py` 抓到它发往 `974a9.playfabapi.com` 的**明文**请求/响应。
> 原始证据：`state/evidence/e2prep/playfab-flow-20261121-2324.jsonl`（含实体令牌，勿外发）。

## 1 抓到的时间线（全部 200，除注明）

| # | 调用 | 关键结果 |
|---|---|---|
| 1 | `POST /MultiplayerServer/ListPartyQosServers` | 返回各 Azure 区域 QoS 地址列表 |
| 2 | `POST /pubsub/negotiate` | **返回实体令牌 JWT**：`title_player_account A50DA53B1AB77C2F`、title `974A9`、provider **Steam**、exp 2026-11-22 |
| 3 | `POST /Event/WriteTelemetryEvents` ×N | party/multiplayer/pubsub 初始化与 QoS 遥测 |
| 4 | `POST /Party/RequestParty` | **分配 Party 专用服务器**：`IPV4Address 52.230.35.175`、`Ports [{Name:"XRNM", Num:30481, Protocol:"UDP"}]`、`FQDN dns81e5-…southeastasia.cloudapp.azure.com`、`Region SoutheastAsia`、`DTLSCertificateSHA2Thumbprint 61B459B0…` |
| 5 | `POST /Lobby/CreateLobby` | `AccessPolicy:"Public"`，LobbyData 里带 build hash / accountId 等 20+ 个 kv；**响应返回 `LobbyId` 与 `ConnectionString`** |
| 6 | `POST /Lobby/SubscribeToLobbyResource` ×2 | 订阅 `LobbyInvite` 与 `LobbyChange`（pubsub topic） |
| 7 | `POST /Lobby/GetLobby` | 返回完整 Lobby（MaxPlayers 8、Owner、AccessPolicy Public、ConnectionString、SearchData…） |
| 8 | `POST /Lobby/UpdateLobby` ×2 | 写 MemberData（`platform_lobby`、`crossplay_mode`） |
| — | 遥测 `client_network_model_state_transition` / `client_connected_to_network` | **`ConnectDurationInMs: 500` → 房主 Party 网络已连上** |

`Client/GetTitleData` 有两次 **401 NotAuthenticated**（无实体令牌）——不在联机关键路径上，忽略。

## 2 三个结论（直接改变方案判断）

### 2.1 `connectionString` 是 PlayFab Lobby 签发的，不是本地生成

`/Lobby/CreateLobby` 的响应原文：
```json
{"LobbyId":"a75760fe-3173-4dad-a46b-2b0dcb4d7a20.r-20260323",
 "ConnectionString":"cv2:a75760fe-3173-4dad-a46b-2b0dcb4d7a20.r-20260323|619689|kv1:kk1SItHDnipWJzZewB9d0PWpdfClajn+mz22+Q6MI0E="}
```
→ 客户端把这个串原样写进 `PUT /api/lobby` 的 body（我们抓到的 1350 B body 与之一致）。
**含义**：自建发现层可以把"真房主"的串转给客人；但**伪造的串不会被 `JoinLobby` 接受**，
所以 B6 的假大厅只能用来观察"客户端在失败路径上做什么"，不能造出可加入的厅。

### 2.2 数据面是「PlayFab Party 专用 Azure VM + UDP(DTLS)」

`RequestParty` 返回的是**一台为此 party 分配的 VM**：IPv4 `52.230.35.175`、UDP 端口 `30481`（协议名 `XRNM`）、
DTLS 证书指纹。房主侧 `client_connected_to_network` 500 ms 成功。

**含义**：E2 之前只检查了 443/TLS 的连通性，**UDP 出站到 Azure（区域由 RequestParty 决定，实测 SoutheastAsia）
是新的必备前置条件**。若任一侧 UDP 被拦/被代理，加入方会停在 `Receiving Data`。
（此前"整段没有任何 Party/UDP 流量"的观测，是因为加入方根本没走到 RequestParty/网络连接这一步。）

### 2.3 房间成员与邀请走 PlayFab Lobby + pubsub，`/api/lobby*` 只是"公告板"

`Lobby/SubscribeToLobbyResource` 订阅 `LobbyChange`/`LobbyInvite` 说明：真实入厅事件由 PlayFab 推给房主。
我们自建发现层之前"房主没有收到成员变更"的现象，与"加入方 `JoinLobby` 没成功"一致。

## 3 下一步实验（游戏在场即可，不需要志愿者）

| 实验 | 做法 | 观察点 |
|---|---|---|
| B6-A 真串 | 用探针造一个假大厅，把**房主自己刚创建的真实 connectionString 复制进去**，在游戏里点加入 | 客户端是否发出 `POST /Lobby/JoinLobby`；PlayFab 的错误码（如"already a member"也能证明链路走通） |
| B6-B 空串 | 同厅但 `connectionString=""` | 客户端是否**根本不发** JoinLobby（说明响应字段被本地校验拦下） |
| B6-C 乱串 | `connectionString="garbage"` | 是否发 JoinLobby 并拿到参数错误 |
| B8 | 客户端待在大厅时 `systemctl restart hd2-discovery`（A 机已开 `unknown_lobby=closed`） | 客户端是否因终态响应自动退出旧厅（不再需要重开游戏） |

## 4 网络前置检查（新增，写进 E2 复测清单）

1. `443/TLS` 到 `api.live…`（经中继）与 `974a9.playfabapi.com`（走系统代理/直连）——已验证可行；
2. **UDP 出站**到 PlayFab Party VM（端口由 `RequestParty` 返回）与 `mpsqosprod.*.cloudapp.azure.com` QoS；
3. 两端的 Party 区域要能互通（实测房主被分配到 SoutheastAsia）。
