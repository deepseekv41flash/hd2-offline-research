# T8 计划：数据面取证 + EAC-off 直启（2026-11-21，**未执行**）

> 本文只是计划，不含实测结论。回答三个问题：
> ①UDP/DTLS 与 Steam 进程内容如何获取？②拿到后对研究路线有什么帮助？③EAC-off 直启能否实现、怎么做？
> 现状证据来源：T4（出网清单）/T6a（MITM 内容）/T7（必要性差分）/T7b（整局验收）与交接档 §12。
> 纪律沿用：每步先报动作与风险再执行；明文与令牌只留本地 `state/`，入库只放结构统计。

## 0. 摘要（三句话）

1. **内容获取**：优先做「**进程内 API 边界 hook**」（Steam `steam_api64` / EOS `EOSSDK` 的导出符号），
   它拿到的是**明文**且不需要破解任何加密；`SSLKEYLOGFILE` 作为 10 分钟试探；伪造 PlayFab Party 中继属于长期研究、不建议现在做。
2. **对研究路线**：**阶段1 基本无用**（这些通道不带游戏进度，T3/T3b/T7b 已证）；**阶段2 是关键证据**——
   它能回答"游戏数据面走 Steam SDR 还是 PlayFab Party"，直接决定 B1/B8 的实验设计与自建联机的可行性边界。
3. **EAC-off 直启可行**：EAC 只是"加载 EAC 后启动 `bin/helldivers2.exe`"的启动器（`EasyAntiCheat/Settings.json`
   写明 `"executable": "bin/helldivers2.exe"`），因此**直接运行该 exe 即绕过 EAC**；历史（2026-11-17）已实测直启不报 0xC0000142。
   剩下要验证的是"直启后与 mock/离线生态的兼容性"，见 §4 的 E-1…E-5。

## 1. 现状（已有证据，不再重复验证）

| 通道 | 已知 | 来源 |
|---|---|---|
| UDP `3478/3479` | **STUN 明文**（Binding Request/Response，XOR-MAPPED-ADDRESS 公网映射 + FINGERPRINT） | T7 前的 pcap 解码 |
| UDP `3075` → `mpsqosprod.*` | 每包 80 B、17 区探测；目标列表来自 `ListPartyQosServers` 响应 | T4 + T6a |
| UDP 高端口（`45368` 等） | **DTLS `application_data`（content_type 23）⇒ 加密** | T4（tshark） |
| Steam SDR | `steam_api64.dll` 是薄封装；真实 SDR 流量由 Steam 侧栈承载（`p2p-*.discovery` + STUN + 中继端口） | T4 + 交接档 §12 |
| PlayFab Party | 中继服务器由 `Party/RequestParty` 分配（FQDN/IP/端口/**DTLS 证书指纹**） | T6a |

**关键新增静态证据（本轮核对）**：

* `steam_api64.dll` 导出：`SteamAPI_ISteamNetworkingSockets_SendMessageToConnection` / `ReceiveMessagesOnConnection` /
  `ConnectP2P`、`SteamAPI_ISteamMatchmaking_*`（38 处）、`SteamAPI_ISteamFriends_*`（78 处）、`GetAuthSessionTicket`；
* `EOSSDK-Win64-Shipping.dll` 导出：`EOS_P2P_SendPacket` / `EOS_P2P_ReceivePacket`、`EOS_Connect_Login`、
  `EOS_Sessions_*`（53 处）、`EOS_AntiCheatClient_*`（35 处）；
* EAC 启动配置：`EasyAntiCheat/Settings.json` → `"executable": "bin/helldivers2.exe"`、`"wait_for_game_process_exit": "false"`。

⇒ 这意味着**不需要解密网络流量**也能拿到"游戏与 Steam/EOS 之间的明文数据"，只要在**函数边界**做 hook。

## 2. 问题①：内容怎么拿（四条路径，按性价比排序）

### 路径 A（推荐）：**进程内 API 边界 hook**（明文，不碰加密）

| 目标 API | 能拿到什么 | 对研究的价值 |
|---|---|---|
| `SteamAPI_ISteamNetworkingSockets_SendMessageToConnection` / `ReceiveMessagesOnConnection` | **游戏发送/接收的原始包**（引擎 netcode 负载） | 判定数据面是否走 Steam；包是明文还是压缩/加密 |
| `SteamAPI_ISteamNetworkingSockets_ConnectP2P` | 连接的对端 SteamID / 虚拟端口 / 选项 | 联机拓扑（房主/客机、是否走中继） |
| `SteamAPI_ISteamMatchmaking_*` | 大厅创建/搜索/加入/成员/属性的**调用序列** | 阶段2 信令面完整性核对（与我们 mock 的 lobby JSON 对照） |
| `SteamAPI_ISteamUser_GetAuthSessionTicket` | 票据长度/使用时机 | 与 B9 的登录体研究互相印证 |
| `EOS_P2P_SendPacket` / `ReceivePacket` | EOS 侧的明文包 | 判定数据面是否走 Party/EOS |
| `EOS_Sessions_*` / `EOS_Connect_Login` | 会话/身份调用面 | 阶段2 是否需保留 EOS 会话 |

**实现**：Frida（Python，`pip install frida frida-tools`）→ 按 `module!export` hook，
dump 「长度 + 前 N 字节 + 调用频率」到本地 `state/evidence/t8/`；不改游戏文件、不注入 `steam.exe`。
**前提（重要）**：hook 必须**在 EAC-off 会话里**做（EAC 在场时注入会触发反作弊）⇒ **§4 的 EAC-off 是路径 A 的前置**。
**成本**：0.5–1 天（装 frida + 2 个 hook 脚本 + 判据脚本）；**风险**：低（EAC-off、不改盘、不碰 Steam 进程）。

### 路径 B（10 分钟试探）：`SSLKEYLOGFILE` 环境变量

EOS SDK 静态链接 OpenSSL、游戏/curl 系用 Schannel。若某栈支持 keylog 回调，设 `SSLKEYLOGFILE` 后
Wireshark/tshark 可直接解 TLS/DTLS。**判据**：`tshark -o dtls.keylog_file:<file>` 能解出 handshake/application data。
**成本**：极低；**预期**：Schannel 不支持（Steam/官方 API 那条大概率不行），EOS 的 OpenSSL 视编译选项，**值得一试但不抱期望**。

### 路径 C（不建议现在做）：伪造 PlayFab Party 元数据 + 自建 DTLS 中继

已知客户端从 `Party/RequestParty` 响应里取 `DTLSCertificateSHA2Thumbprint`（T6a）。理论上可以：
MITM PlayFab 返回"我们中继"的指纹 → 客户端 DTLS 连我们 → 解密。但要**实现 Party 协议栈 + DTLS 中继**，
工程量等同重做传输层，只应作为阶段2 的"极限方案"长期研究，**不作为取证手段**。

### 路径 D（最低成本，先做）：明文 UDP 统计

用**已有** T4 pcap 直接分析：STUN 全量（映射/中继探测节奏）、QoS 80 B 结构与区域选择、
DTLS record 统计（握手时机/包长分布）。不需要重新抓包、不需要游戏。

### Steam 进程本身（`steam.exe` / `steamwebhelper.exe`）

**不建议**注入或解密（闭源 + 账号/VAC 风险）。路径 A 已能从"游戏要求 Steam 做什么/传什么"这一侧拿到研究所需信息，
足够支撑阶段2 的架构判断。

## 3. 问题②：拿到后对研究路线有什么帮助

### 对阶段1（单人离线）：**帮助≈0，不建议投入**

T3/T3b/T7b 已证单人整局只依赖官方 API（+ EAC 网络）；这些通道里没有游戏进度数据。
拿内容只是补全画像，**不会改变阶段1 的任何结论**。

### 对阶段2（自建联机）：**是关键证据，能定两件事**

| 问题 | 为什么现在答不了 | hook 后怎么答 |
|---|---|---|
| **Q1：游戏数据面走 Steam SDR 还是 PlayFab Party？** | 两条通道都出现过（交接档 §12 静态判断 Steam 优先；T6a 又实测到 Party 中继分配） | 看 `SendMessageToConnection` 与 `EOS_P2P_SendPacket` 谁有真实游戏负载、包长/频率、与局内事件的相关性 |
| **Q2：游戏包是明文还是压缩/加密？** | 网络层看到的是密文 | 函数边界拿到明文后可直接判断（引擎序列化格式？压缩？） |

**Q1 决定 B1 实验设计与架构可行性**：
* 若走 **Steam SDR** ⇒ "自建信令 + 沿用 Steam 传输"（交接档 §5.1 的方案）成立，PlayFab 只需保大厅/发现；
* 若走 **PlayFab Party** ⇒ 自建联机必须保留 PlayFab 云（T6a/T7 已证），Steam 只是辅助；
* **Q2** 若游戏包明文 ⇒ 未来自建中继/替换传输的概率上升；加密 ⇒ 只能依赖官方传输，自建边界收缩到信令层。

**附带收益**：`ISteamMatchmaking*` 调用序列可用于校验阶段2 信令面是否完整（我们 mock 的 lobby 结构是否覆盖全部必要调用）。

## 4. 问题③：EAC-off 直启——可行性、实现方式与验证清单

### 4.1 可行性：**可以**，路径明确

`EasyAntiCheat/Settings.json` 写明 `"executable": "bin/helldivers2.exe"`，
即 `start_protected_game.exe` 的职责是"**加载 EAC → 启动这个 exe**"。
直接运行 `bin\helldivers2.exe` 就跳过了 EAC 加载（历史 2026-11-17 已实测直启不报 0xC0000142、进程存活 20s）。
**前提**：Steam 客户端需在运行（SteamAPI 初始化 + 票据）；Steam 是否必须"在线"待验证（见 E-3）。

### 4.2 实现方式（按推荐顺序）

| 方案 | 做法 | 适用 |
|---|---|---|
| **E1 直启（研究首选）** | `Start-Process "…\bin\helldivers2.exe"`（工作目录设为游戏根目录） | 研究/验证 |
| **E2 启动器封装（产品形态）** | 一个小 launcher（bat/exe）：确保 Steam 在跑 → 直启 `bin\helldivers2.exe`；再在 Steam"添加非 Steam 游戏"指向它，保留库入口体验 | 阶段1 产品若采用 EAC-off 形态 |
| **E3 Steam 启动选项（最优雅，待探）** | 静态看 `start_protected_game.exe` 是否支持"跳过 EAC"参数；若支持则一条启动选项即可 | 待 T8-E0 静态确认 |

### 4.3 验证清单（T8-E）

| 编号 | 验证 | 判据 | 成本/风险 |
|---|---|---|---|
| **E-1** | 直启基线：Steam 在线 + 官方 API=mock + 旁路放行 | 到主界面、mock 登录 200 | 5 min / 低 |
| **E-2** | 直启 + 旁路全黑洞（PlayFab/EOS/Sony 不可达） | 到主界面 + **整局任务链**通过 | 15 min / 低（需用户打一局） |
| **E-3** | 直启 + 真断网（仅保留 localhost/LAN mock）：Steam 离线模式是否可行 | 能否拿到票据/进主界面 | 20 min / 中（需改网卡或防火墙，需你批准） |
| **E-4** | 副作用观测：登录体大小、存档/成就是否写、联机/好友功能状态 | 与 EAC-on 基线对比无异常（或记录差异） | 随 E-1/E-2 顺带 |
| **E-5** | 产品化提示：EAC-off 模式下 UI 需明示"仅单机/无官方联机" | 产品决策项 | 设计 |

### 4.4 风险与边界

* EAC-off ⇒ **不能进官方多人**（预期，且本计划只做单机）；
* 游戏可能检测 EAC 缺失并限制部分功能（E-4 记录）；
* 不改游戏文件；Steam 完整性校验/更新不影响直启方式；
* E-3 若需断网，改为**防火墙出站规则**或临时禁用网卡，执行前单独说明并恢复。

## 5. 执行顺序与门禁（建议排期）

| 阶段 | 任务 | 依赖 | 预计 |
|---|---|---|---|
| **P0**（离线，无用户操作） | T8-Q1：用 T4 pcap 做 UDP/STUN/QoS/DTLS 统计<br>T8-E0：静态确认直启命令行/start_protected_game 参数 | — | 40 min |
| **P1**（需你在场） | T8-E1 直启基线 → T8-E2 直启+旁路全黑洞整局 → T8-E4 副作用 | P0 | 25–30 min |
| **P1.5**（可选） | T8-E3 直启 + 断网/离线模式 | P1 通过 | 20 min |
| **P2**（阶段2 启动时做） | T8-S1 frida 环境 + hook 脚本（先统计调用面，不 dump 负载）<br>T8-S2 单人/主界面下判定 Steam SDR vs EOS P2P 谁有数据 | P1（必须 EAC-off） | 半天 |
| **P3**（低成本试探） | T8-D1 `SSLKEYLOGFILE` 试探（在 T8-S 之前顺手做） | P1 | 10 min |
| **P4**（暂不做） | T8-D2 Party DTLS 中继伪造（长期研究） | — | 不建议 |

**门禁**：每个任务产出 `state/evidence/t8/*` 证据 + 三态判定；结论写 `docs/` 并提交；
涉及系统改动（断网/防火墙）或需你操作（打局）的步骤，**先说明再做**。

## 6. 结论（要不要做的建议）

* **阶段1**：只做 **P0 + P1**（EAC-off 直启与"旁路全断整局"），把"完全无外网/无 EAC 网络"形态验证清楚，
  这对阶段1 产品形态（是否需要 EAC-off 启动模式）有直接价值；
* **P2/P3**（Steam/EOS 明文取证）**等阶段2 启动再做**——它对阶段1 无收益，但对阶段2 的架构判断是必需的；
* **P4** 暂不做。
