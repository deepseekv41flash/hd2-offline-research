# T8d：社区项目 HellForge 的「免反作弊启动」到底怎么做的（2026-11-21）

> 触发：用户给出 <https://github.com/SkyeShade/HellForge>，问「它如何实现免反作弊启动（且能直接读写受反作弊保护的内存）」。
> 方法：只读 clone（`state/evidence/hellforge/`，gitignored）→ 读 README/docs/源码/manifest → 与本机当前 build 做文件集对照。
> 三态：`✓ 已证` / `⚠ 未证` / `✗ 否证`。**未运行其程序、未下载其 release、未改任何游戏文件。**

## 0. 一句话结论（先纠正前提）

**它不是「绕过 EAC」，而是「根本不跑带反作弊的 build」。**

HellForge 固定（pin）了一个 **EAC/EOS 之前的旧 Steam build `25480438`**，按 Steam 官方 depot 清单把 135 个文件下到自己的目录、
逐文件用 Steam 的哈希校验，然后**直接启动 `game\bin\helldivers2.exe`**（`--bundle-dir <bundle> --release`），
全程没有 `start_protected_game.exe`、没有 `EasyAntiCheat/`、没有 `EOSSDK-Win64-Shipping.dll`、没有 `preloader.dll`（代码加密壳）。

⇒ 「能直接读写受反作弊保护的内存」的真相是：**那里没有反作弊，也就没有受保护的内存**；
进程只是一个普通的用户态进程（还被它主动降到 low integrity 沙箱里），
`OpenProcess` / `ReadProcessMemory` / `WriteProcessMemory` / `VirtualProtectEx` 自然全部可用。

它自己的 roadmap 把这条写得最清楚（`docs/roadmap.md:35`）：

> Not planned: a way onto the live servers, play with people outside HellForge, **support for the EAC builds, or getting
> around an active anti-cheat.**

## 1. 证据：它的「受支持 build」文件集里没有任何反作弊组件（✓ 已证）

`src/HellForge.Core/supported-build.json`（仓库内，明文 JSON）：

```
schemaVersion=1  appId=553850  buildId=25480438
exeSha256=F5FEE03D… gameDllSha256=2E2C3B7C… totalBytes=23,875,535,795
depots: 553851 manifest 1883032874832282552 / 553853 manifest 2566883558412717494 / 553854 manifest 9065746766815828060
files: 135 个（path + size + sha1 + depot）
```

135 个文件里，`bin/` 只有 18 个：

```
PartyWin.dll / PlayFabMultiplayerWin.dll / SONYGame.ini / WinPixEventRuntime.dll /
amd_fidelityfx_upscaler_dx12.dll / bink2w64.dll / crs-client.dll / crs-handler.exe /
dstorage.dll / dstoragecore.dll / helldivers2.exe / libcurl.dll / libxess.dll / lua51.dll /
nvngx_dlss.dll / plugins/*(2) / steam_api64.dll
```

**没有**：`start_protected_game.exe`、`EasyAntiCheat/`（含 `EasyAntiCheat_EOS_Setup.exe`、`Settings.json`）、
`bin/EOSSDK-Win64-Shipping.dll`、`bin/preloader.dll`、`bin/GameGuard/`、`bin/runtime.dll`。

对照本机当前 build（2026-11-11 装，含 EAC + EOS + GameGuard + 加密壳）：

| 文件 | HellForge build 25480438 | 本机当前 build | 说明 |
|---|---|---|---|
| `bin/helldivers2.exe` | 14,957,160 B | 28,743,256 B | 当前版把 EOS/EAC/preloader 链编进去了 |
| `data/game/game.dll` | 15,522,408 B | 49,075,800 B | 游戏逻辑模块，旧版小得多 |
| `bin/lua51.dll` | 469,080 B | **不存在** | 旧版 Lua 是独立 DLL，新版并进 `runtime.dll` |
| `bin/runtime.dll` | **不存在** | 64,870,488 B | Stingray 运行时，新版才拆出来 |
| `bin/EOSSDK-Win64-Shipping.dll` | **不存在** | 19,531,704 B | 旧版压根没有 EOS |
| `bin/preloader.dll` | **不存在** | 46,168 B | 旧版没有代码加密壳 |
| `start_protected_game.exe` + `EasyAntiCheat/` | **不存在** | 存在 | 旧版没有 EAC |
| `bin/GameGuard/`（nProtect） | **不存在** | 存在（`npgg*.des` 等 29 个文件） | 旧版没有第二层反作弊 |
| `bin/PartyWin.dll` / `PlayFabMultiplayerWin.dll` | 4,031,520 / 1,878,048 | **同尺寸** | PlayFab Lobby 1.8.7 / Party 1.10.16，两代未变 |

⇒ 两代架构完全不同：旧 build 是「PlayFab + lua51 + game.dll、无反作弊、无 EOS、无壳」；
当前 build 是「EOS SDK + EOS EAC + nProtect GameGuard + preloader 加密壳 + runtime.dll」。
**它跑的是前一代，所以「免反作弊」是天然的，不需要任何 bypass 技术。**

## 2. 它是怎么把这份旧 build 拿到手并按它跑的（✓ 已证，来自 README + `docs/steam-download.md`）

1. **不分发游戏文件**：仓库与 `HellForge-Setup.exe` 都不含游戏文件；setup 从玩家自己的 Steam 库复制，或
   用玩家自己的账号从 Steam 官方 depot 下载「指定 manifest 的旧 build」。
2. **固定 manifest**：`supported-build.json` 里写死 3 个 depot 的 manifestId；setup 通过
   Steam 的 depot 解密密钥 + `download_depot 553850 <depot> <manifest>`（SteamCMD 或 Steam 控制台）取回，
   用 135 个文件的 size/SHA-1 清单逐文件校验。⇒ 拿到的是**与 Steam 原始发布逐字节一致**的旧 build。
3. **所有权检查**：SteamKit2 登录做所有权验证，7 天内缓存一次；换 build 会重新验证。
4. **启动**：session runner（`src/HellForge.Session`，NativeAOT）直接
   `CreateProcessW/CreateProcessAsUser(exe = <copy>\game\bin\helldivers2.exe, "--bundle-dir \"<bundle>\" --release" …)`
   （`PlaySession.cs:1023`），**没有 EAC 启动器这一环**；sandbox 模式下还带一个降权 token
   （`HostBoundedProcess.cs`，`CreateProcessAsUser` + low integrity）。

## 3. 「直接读写内存」是怎么做到的（✓ 已证，代码级）

三条配合，缺一不可：

| 环节 | 实现 | 证据 |
|---|---|---|
| 进程无保护 | 没有 EAC 内核驱动/PPL ⇒ 普通用户态进程，同权限进程可全权访问 | §1 文件集；无 `EasyAntiCheat/` |
| 外部内存补丁 | session runner 以 `OpenProcess(0x1839)` 打开游戏 → 轮询到「构造边界」→ `NtSuspendProcess` 冻结 → `ReadProcessMemory`/`VirtualProtectEx`/`WriteProcessMemory`/`FlushInstructionCache` 改写 → `NtResumeProcess` | `src/HellForge.Session/EarlyConstructor.cs`（这些 import 全在） |
| 进程内 hook | 游戏会加载 `bin\steam_api64.dll`；该文件被换成 HellForge 的 stand-in，其 `DllMain` 在 loader lock 释放后起线程，按环境变量 `HELLFORGE_MODLOADER`/`HELLFORGE_MODLOADER_PLAN` `LoadLibrary` 进 `hellforge_modloader.dll`（MinHook），再加载各 code mod | `native/steam-cloud/steam_cloud.c:553-566`、`docs/internals.md`「The load chain」 |
| 地址从哪来 | 静态分析**未加密**的 `game.dll`/`helldivers2.exe`，把绝对偏移写成表：`native/mod-loader/*_sites.h`（如 `damage_sites.h`：`{0x694E4Eu,…}`、`game + 0x37C60C0` 的 DamageInfo 表）+ `mappings/`（33.6 万行 JSON）+ `functions.json` | `native/mod-loader/` 89 个文件、`mappings/` 等 |

**关键前提**：这些偏移之所以能写死，是因为 (a) 只有一个 build（25480438）被支持，SDK 会拒绝其它 build；
(b) 旧 build 的 `game.dll`/`helldivers2.exe` **没有加密**，可以静态扫出所有引用点。

## 4. 它怎么保证「不碰官方服务器」（✓ 已证）

- **WFP 出站阻断**：`native/network-block/hellforge_wfp.c` 直接往 Windows Filtering Platform 装持久对象
  （高权重 sublayer，压过安全软件/VPN 的 permit），只允许游戏程序访问**回环端口**，其余一律 block；
  需要一次管理员授权（README 明说「one administrator prompt」）。
- **本地 backend**：`src/HellForge.LocalBackend` 在 127.0.0.1 提供游戏要的 PlayFab 时代在线服务；
  联机靠 stand-in 的 `PartyWin.dll`/`PlayFabMultiplayerWin.dll`（`native/sdk-shims`，PlayFab Party 1.10.16 /
  Multiplayer 1.8.7 的公开 C API 签名）+ Steam 通道，而不是官方服务。
- **不改盘**：game 文件按 Steam 哈希校验；一个会话里只**临时替换** 3 个 DLL
  （`steam_api64.dll`、多人模式时的 `PartyWin.dll`/`PlayFabMultiplayerWin.dll`），原文件备份、会话结束还原
  （`PlaySession.cs` 头部注释、`SdkShimRecovery.cs`）。

## 5. 三态判定

| 判定项 | 结论 | 等级 |
|---|---|---|
| HellForge 通过「固定无反作弊旧 build」实现免 EAC，而非绕过 EAC | ✓ | 已证（manifest 文件集 + roadmap 明文 + 启动代码） |
| 其受支持 build 无 `start_protected_game.exe`/`EasyAntiCheat/`/`EOSSDK`/`preloader`/`GameGuard` | ✓ | 已证（135 文件清单） |
| 它能读写游戏内存 = 因为进程无保护 + 它主动做外部/进程内补丁 | ✓ | 已证（代码 import + 注释） |
| 它「绕过了正在运行的反作弊」 | ✗ 否证 | 该 build 无任何反作弊在跑；roadmap 明确不计划绕过 active anti-cheat |
| 其做法能直接搬到「当前 build + 需要联机/官方服务器」的场景 | ✗ 否证 | 旧 build 无 EOS、数据/协议/偏移全不同；官方服务器也不认旧 build |

## 6. 对 HD2 离线/私服研究线的启示

1. **反向验证了我们的结论**：当前 build 想「关 EAC」必须对抗 EAC+EOS+加密壳+完整性校验（见
   [T8c](T8c-EACOFF-PATCH-COST-2026-11-21.md)）；社区项目的可行解是**换一代 build**，不是 patch。
2. **两条路线不同目标**：HellForge 要的是「离线/私服 + mod + 合作」，它把整个在线服务本地化并锁死一个 build；
   我们的阶段1 要的是「当前 build + 只接管官方 API、EAC 保持可达」（T7b 已验证）。互不替代。
3. **可借鉴的三点**：
   - **build 锁定的代价**：它为偏移写死付出了 `mappings/` 33.6 万行 + 89 个 loader 源文件的维护成本——正是我们在
     [T8c](T8c-EACOFF-PATCH-COST-2026-11-21.md) 里估算「跟版本重做」的那部分代价的真实样本。
   - **低完整性沙箱**：`CreateProcessAsUser` + 降权 token 跑游戏，可作为产品侧「降低攻击面」的安全设计参考。
   - **WFP 出站阻断**：比 hosts/防火墙规则更硬（关掉 Windows 防火墙也生效），对「保证不误连官方」有参考价值。
4. **不要做的事**：不要因为它「能读写内存」就推断当前 build 也能——恰恰相反，当前 build 的 `.text` 是密文（T8c），
   而它读写的目标是**未加密旧 build** 的明文代码段。

## 7. 复现（只读，本机可跑）

```powershell
mkdir D:\mod\work\hd2-offline-research\state\evidence\hellforge -Force
cd D:\mod\work\hd2-offline-research\state\evidence\hellforge
git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 clone --depth=1 https://github.com/SkyeShade/HellForge.git

# 1) 受支持 build 的完整文件集（是否含 EAC/EOS/壳）
python -c "import json;d=json.load(open('HellForge/src/HellForge.Core/supported-build.json'));print(d['buildId'],len(d['files']));print([f['path'] for f in d['files'] if f['path'].startswith('bin/')])"

# 2) 官方立场（不计划支持 EAC build / 绕过 active anti-cheat）
Select-String -Path HellForge\docs\roadmap.md -Pattern "EAC"

# 3) 启动与内存补丁代码
Select-String -Path HellForge\src\HellForge.Session\PlaySession.cs -Pattern "helldivers2.exe|bundle-dir"
Select-String -Path HellForge\src\HellForge.Session\EarlyConstructor.cs -Pattern "WriteProcessMemory|NtSuspendProcess"
Select-String -Path HellForge\native\steam-cloud\steam_cloud.c -Pattern "HELLFORGE_MODLOADER"
```

> 说明：克隆物落在 gitignored 的 `state/` 下，不入库；仓库为 GPL-3.0，本文只做只读分析、不复制其代码。

## 8. 对「阶段2：自建联机服务」的参考价值（用户追问）

结论先行：**参考价值很大，而且它推翻/限定我们此前的一条判断**——但只限「PlayFab 那半边」，
当前 build 的 EOS 那半边它给不了参考（旧 build 完全没有 EOS）。

### 8.1 它证明了「完全自建联机」技术可行，前提是连客户端 SDK 一起换

我们 [T7](T7-STAGE1-NECESSITY-2026-11-21.md) 的结论是「PlayFab 不能糊弄，阶段2 必须保留 PlayFab 云」。
HellForge 给出了另一半答案：**它把 PlayFab 客户端 DLL 整个换掉了**。

| 项 | HellForge 的做法（旧 build） | 对当前 build 的含义 |
|---|---|---|
| PlayFab Lobby | `PlayFabMultiplayerWin.dll`(1.8.7) → 自实现替身（`lobby_shim.cpp`，762 行），大厅状态镜像 + 自造连接串 `hellforge1-lobby;<id>;<owner steamid hex>` | 我们 body/串的「不可伪造」结论只在**不换 SDK**时成立；换掉后语义由自己定义 |
| PlayFab Party | `PartyWin.dll`(1.10.16) → 自实现替身（`party_shim.cpp`，985 行），网络/端点/消息路由自管；描述符 `hellforge1;<network id>;<region>;<conninfo hex>` | Party 的「Azure 中继 + DTLS」不是必须；传输可换成任意自控通道 |
| 传输 | 后端之间用 Steam `ISteamNetworkingMessages`（只收 Steam 好友会话） | 正式因为「入厅/中继都在 PlayFab」才卡住的 B 线，出现第三条路：替身 + Steam 通道 |
| 规模 | 替身 4,414 行 C++ + 本地多人后端 7,835 行 C#（24 文件） | ≈**1.2 万行**才换来「零官方云」；这就是方案B 的真实成本 |
| 时间 | 2026-10-14 起步，10 步记录到 0.4.0（含两机联调；作者同期还做了 mod SDK/编辑器/单机） | 不是一个人的一个季度能顺带完成的事 |

补充证据：游戏对 `PFLobby*`/`Party*` 的静态导入共 179 个（22 + 157），HellForge 用 **Microsoft 公开 SDK 头**
生成日志桩 + 手写关键实现（`native/sdk-shims/lobby_shim.cpp:1-14`），**不需要逆 Party 的线协议**。
⇒ 我们 T7 里「完全自建等同重写 Party 协议与 DTLS 中继」要修正为「**不必逆协议，但必须重写客户端 SDK 的公开 API 面**」，
这是两个成本差一个数量级的判断。

### 8.2 但当前 build 多一层 EOS：可替的只有 PlayFab 两件套，EOS 不能替

- 旧 build 文件集里没有 `EOSSDK-Win64-Shipping.dll`（§1）⇒ HellForge 从不需要处理 EOS。
- 当前 build 的 EAC 依赖**真实 EOS 反作弊客户端**（[T8](T8-EACOFF-RESULT-2026-11-21.md) 实机 ✗），
  所以 EOSSDK **不能**整体替掉；否则 EAC 起不来 ⇒ 阶段2 若走替身路线，形态是
  「保留 EOS（identity/anticheat/cloud 用真的）+ 换掉 PlayFab Lobby/Party + 自建后端与传输」。
- 仍待判定的一件事：当前 build 的**对等传输**到底是纯 Party（可替）还是已与 EOS P2P 混合。
  T6a 记录到 `EOS_P2P_*` 导出与 Party 中继并存；T7 R4 又证明「PlayFab 一律 200 → Party 栈在 `ListPartyQosServers` 放弃」，
  说明 Party 仍是主传输，但替身路线要实测确认（见 §8.4 实验 2）。

### 8.3 可直接借鉴的 6 个设计模式（读设计，不抄 GPL 代码）

| 模式 | HellForge 做法 | 我们的阶段2 用法 |
|---|---|---|
| 替身 ↔ 后端 | 命名管道 `HELLFORGE_PIPE`，`hello [lobby\|party, pid]` 握手；帧 = u32 长度 + u16 字段数 + 长度前缀字段（`Multiplayer/Frame.cs`） | 若做替身，直接采用这套极简协议；也便于单测（他们用内存/回环 UDP 传输替身） |
| 连接串/描述符所有权 | lobby 串与 party 描述符都是自造前缀 + 自有语义，region 用常量 `HellForgeLocal` | 我们可定 `hd2proxy1;…`，把中继指向自建服务；只要「谁签发谁解释」闭环即可 |
| 端点 id 分配 | 本机端点从 1000 起；每个朋友后端一块 64 个 id，从 4096 起；大厅容量用游戏自身 `maxMembers` | 中继/房间的 id 规划可直接照用 |
| 后端间传输 | Steam `ISteamNetworkingMessages`（ch0，可靠/不可靠，只收 Steam 好友） | 自建中继的最省事通道；也是 B1「双机只靠自建信令 + Steam 建连」的现成答案 |
| 身份派生 | title player account = SteamID64 hex；master = SteamID64 + 0x1000000000000000 | 保持 PlayFab 云（方案A）时可直接用；替身时也能保持 ID 稳定 |
| 会话保活 | `peer.ping` 每 2 s，15 s 静默判死 → 清大厅/网络 | 自建中继的会话回收规则 |

### 8.4 对阶段2 的三条可执行建议

1. **主形态不变**：仍是「自建 HD2 后端 + 保留 PlayFab 云」（T7 §4.3）。HellForge 证明的「零官方云」方案是**方案B**，
   成本 ≈1.2 万行 + 双机联调 + 跟 build，现阶段不值。
2. **加一个半天级判定实验**（无风险、只读）：核对当前 build 的 `bin/PartyWin.dll`(4,031,520 B) 与
   `bin/PlayFabMultiplayerWin.dll`(1,878,048 B) 是否与 Microsoft 公开 SDK 发布版**逐字节一致**
   （HellForge 说旧的 1.8.7/1.10.16 是 byte-identical；我们当前 build 这两个文件尺寸与旧 build **完全相同**，很可能是同版本）。
   一致 ⇒ 方案B 的 API 面已知、风险可控；不一致 ⇒ 还要额外逆 API，方案B 更不值。
3. **「PlayFab web API」这半边我们可以比它更省**：它是用内存补丁把 base URL 改成 `http://127.0.0.1:<port>/playfab`
   （`LocalRoutingPatch.cs`，并做三条指令校验）；我们已有 hosts + 研究 CA + mock 的**网络层接管**（T1/T2 实机 ✓✓），
   可直接在 WinHTTP/TLS 层接管 `*.playfabapi.com`（证书由我们的 CA 签），**零注入、零内存补丁**。
   于是阶段2 的最小工作量被切成两段：REST 发现层（我们已有能力）＋ Party 传输（保留云 or 替身）。

### 8.5 不可照搬的部分 / 风险

- **许可**：仓库 GPL-3.0（SDK 部分 MIT），只能读设计与协议，我们的实现必须独立撰写。
- **build 差异**：它的内存偏移/替身都锁死 25480438；当前 build 代码段加密（[T8c](T8c-EACOFF-PATCH-COST-2026-11-21.md)），
  内存补丁路线不可行 ⇒ 我们若做替身，必须依赖「DLL 文件名替换」而不是「代码偏移替换」。
- **版本脆性**：替身依赖 PlayFab/Party SDK 版本与 API 面；游戏更新换版本即失效（它靠 SDK 拒绝其它 build 来兜底）。
- **不连官方**：它用 WFP 硬阻断官方服务器；我们的「自建联机」也要与「官方联机」严格分开，避免误连。

### 8.6 三态补充

| 判定项 | 结论 | 等级 |
|---|---|---|
| 「完全自建联机」在技术上可行（替客户端 SDK + 自建后端/传输） | ✓ | 已证（HellForge 实作 + 文档 + 10 步联调记录） |
| 该方案不需要逆向 Party 线协议/DTLS | ✓ | 已证（基于 Microsoft 公开 SDK 头实现替身） |
| 该方案能直接用于当前 build | ⚠ 未证 | 需先确认 Party DLL 版本一致 + 当前 build 的 Party 传输是否混入 EOS P2P |
| 方案B 的成本量级（≈1.2 万行 + 双机联调 + 跟 build） | ✓ | 已证（代码行数统计） |

## 9. 中继需求与服务器资源评估（用户追问：要不要公网服务器？我们那两台够吗？）

### 9.1 HellForge 不需要自建公网中继：它用的是 Valve 的 Steam 中继网络（✓ 已证）

代码级证据（`src/HellForge.LocalBackend/Multiplayer/SteamPeerTransport.cs`）：

```
// Backend-to-backend messages through Steam networking (ISteamNetworkingMessages over Valve's relays, peers named by
// SteamID). Runs in the backend process, which the network block does not list; the copied game itself only talks to
// loopback.
...
((delegate* unmanaged[Cdecl]<nint, void>)E("SteamAPI_ISteamNetworkingUtils_InitRelayNetworkAccess"))(utils);
```

联机链路是三层，全程没有"官方或自建的公网服务器"：

```
游戏进程 ──(loopback)──> 本机 HellForge backend ──(Steam SDR: ISteamNetworkingMessages)──> 好友的 backend
```

- 玩家侧要求（`docs/multiplayer.md`「How co-op works for players」）：Steam 在线、互为 Steam 好友（或开「Play with
  strangers」），Steam 好友/大厅用的都是**真实的 Steam**；**没有"输入 IP 直连"或"自建中继"开关**（全仓 grep 无 direct-IP/relay-server 选项）。
- 传输的 NAT 穿透与回落由 Valve 的 SDR 完成（`InitRelayNetworkAccess` 就是初始化这套网络）；中继成本由 Valve 承担，
  HellForge 作者不运营任何服务器。
- 它自己只有"host = 某个玩家的 PC"：`docs/multiplayer.md`「The creator's backend owns each lobby」「The host's backend
  owns each Party network」。

⇒ **结论：HellForge 的形态不需要带公网 IP 的服务器**。需要公网服务器的只有另一种选择：不依赖 Steam、
自己实现 UDP 中继/自建 Party 替身传输（那是方案B 的变体，HellForge 没走）。

### 9.2 如果换成"自建中继"：负载模型（估算）

中继本身是**极轻**的服务：单进程 UDP 转发，CPU 近似 0（几千包/秒只占一个小核的一小部分），内存 50–150 MB，
真正约束是**出方向带宽**和**月流量包**：

| 项 | 量级 | 说明 |
|---|---|---|
| 每局（4 人，无语音） | **≈0.3–1 Mbps 聚合**（待实测） | 中继对每局的出方向 ≈ 入方向；host 上行 + 各 client 入向汇总后扇出 |
| 语音（若也走自建中继） | +0.1–0.3 Mbps/局 | 原版 HD2 语音走 PlayFab Party（Azure 中继）；替身方案要自己带 |
| 每局月流量（若每天 2 h） | ≈15–45 GB/月 | 按 0.5–1 Mbps 连续算 |
| CPU/内存 | 可忽略 | systemd `MemoryMax=128M` 足够压住 |

### 9.3 两台现成服务器实测（本机 2026-11-21 15:45 只读探测；IP 已脱敏）

| 项 | 服务器 A（腾讯云，2 vCPU） | 服务器 B（阿里云，2 vCPU） |
|---|---|---|
| 规格 | Xeon 8255C ×2、**3.7 GB RAM**、59 GB 盘（75% 用） | Xeon Platinum 1C2T、**1.7 GB RAM**、40 GB 盘（67% 用） |
| 当前负载 | load 0.50 / 0.43 / 0.36（30 天） | load 0.12 / 0.12 / 0.35（38 天） |
| 可用内存 | **≈0.9 GB**（swap 4 GB 已用 2.7 GB） | **≈0.35 GB**（swap 4 GB 已用 1.7 GB） |
| 现有业务 | nginx(80/443/888) + mihomo 代理 + 6 个 docker（QQ bot/NAP CAT/sqlite-web 等） | nginx + maim bot 栈（3 个 docker） |
| 公网 IP | ✓（NAT 映射，内网 10.x） | ✓（NAT 映射，内网 172.30.x） |
| 本机→服务器延迟 | **avg 69 ms**（56–82） | **avg 55 ms**（51–63） |
| **出方向吞吐（实测）** | 单流 **4.1 Mbps**；4 并发 **2.2 Mbps** | 单流 **3.3 Mbps**；4 并发 **3.3 Mbps** |
| UDP 监听 | 无游戏类（仅 nginx QUIC 443、mihomo 1053/7890） | 无（仅 systemd-resolve/chrony） |
| 测速方法 | `ssh2` exec `cat <已有日志>` 5 s 计字节；再用 4 条并发流复核；本机下行基线另测为 **≈48 Mbps**（6 MB/s，排除本地瓶颈） | 同左 |

判读：

- **都有公网 IP、延迟可接受（55–69 ms），跑得动中继服务**；但**出方向带宽只有约 3–4 Mbps**（并发不叠加，说明是真带宽上限，不是单流限制），
  按 9.2 的每局 0.5 Mbps 估，A 约 **4–8 局**、B 约 **6–7 局**是带宽理论上限；扣掉现有业务余量，实际只该接 **2–4 局**。
- **内存是更现实的硬约束**：B 只剩 0.35 GB、swap 已用 1.7 GB；A 剩 0.9 GB、swap 已用 2.7 GB。塞一个 50–100 MB 的
  中继进程**能跑**，但两台都在 swap 上，抖动会影响中继时延（对游戏很敏感）。
- **都没开 UDP 入站**：云控制台安全组 + 系统防火墙要额外放行（当前 `ss -lunp` 无任何游戏端口）。
- 月流量包未知（控制台可见）：3–4 Mbps 常年跑满约 1.2–1.6 TB/月，多数廉价套餐的月流量包会先耗尽。

### 9.4 结论与建议

| 问题 | 结论 |
|---|---|
| HellForge 需要公网服务器中继吗？ | **不需要**（Valve Steam SDR 承担中继；只要 Steam 在线 + 好友） |
| 自建中继负载高吗？ | **算力/内存极低**（一个 UDP 进程即可），**带宽是唯一瓶颈**：每 4 人局约 0.3–1 Mbps |
| 两台现成服务器够吗？ | **只够"小规模验证"**（2–4 人、1–2 局）：公网 IP ✓、延迟 55–69 ms 可玩、算力有余；但出方向仅 3–4 Mbps、内存已被现有业务吃紧、UDP 未放行 ⇒ **不适合生产** |

给阶段2 的建议（按优先级）：

1. **主方案（保留 PlayFab 云）根本不需要服务器**——先不要为此动这两台机器。
2. 若要验证「自建联机」可行性，**先按 HellForge 的路子用 Steam SDR**（零服务器、零端口），验证"双机能进同一局"；
   这一步失败再谈自建中继。
3. 若确实要走自建中继（例如 CN 玩家要走加速、或非 Steam 好友场景）：优先 **B**（负载 0.12、延迟更低），
   systemd 限内存 128 MB、限 2–4 房；**先在云控制台确认带宽包/月流量包，并放行 UDP 入站**；A 上已有 nginx+mihomo+6 容器，
   不建议再压（要压先扩内存或迁容器）。
4. 中继实现建议：单进程 UDP relay（会话表按 lobby id + SteamID 绑定），端口可固定一个；
   与"官方服务器"严格隔离（研究仓内保持只读原则，部署另开）。

> 脱敏：本节按公开仓纪律隐去两台服务器的完整公网 IP；测速只用读取已存在的 journal 日志，未改服务器任何文件。

### 9.5 按实际套餐修正（用户提供套餐页截图，2026-11-21 16:0x）

服务器 A 的套餐已确认：**CPU 2 核 / 内存 4 GB / 系统盘 SSD 60 GB / 流量包 500 GB 每月（峰值带宽 5 Mbps）**，
与实机读取一致（3729 MB 可用内存、59 GB 盘、2 vCPU）。计费规则（同截图）：

> 仅统计服务器的**出流量**，每小时结算；超出售卖月流量包限额的部分产生额外费用；流量包每月重置一次。

⇒ **此前"月流量可能先耗尽"的判断要改：500 GB 不是瓶颈，5 Mbps 出方向峰值才是。**

#### 9.5.1 重新推算（5 Mbps 口径）

| 量 | 计算 | 结果 |
|---|---|---|
| 5 Mbps 满速换算 | 5 ÷ 8 = 0.625 MB/s × 3600 | **2.25 GB/小时** |
| 500 GB 可撑多久满速 | 500 ÷ 2.25 | **≈222 小时/月** |
| 折合每天满速多久不超额 | 222 ÷ 30 | **≈7.4 小时/天** |
| 实测对照 | A：单流 4.1 Mbps（=82% 峰值）、4 并发 2.2 Mbps | 与 5 Mbps 峰值一致（并发下降来自路径/争用，而非更高配额） |

⇒ 结论：**只要不是 24×7 满速中继，500 GB 用不完**（每天跑满 7 小时以内都不超额）；真正的天花板是同一时刻 5 Mbps 被所有会话+现有业务共享。

#### 9.5.2 "只限出方向"对架构的影响（关键）

服务商只对**出方向**限流/计流量，入方向（客户端→服务器）基本不限。这对中继设计有明确含义：

| 架构 | 出方向用量 | 5 Mbps 能带多少 | 适用性 |
|---|---|---|---|
| **全量中继（TURN 式，所有游戏/语音流量过服务器）** | 每局 ≈0.3–1 Mbps（估算） | **约 2–6 局**（按实测并发 2.2 Mbps 更保守） | 只够小圈子验证 |
| **信令/匹配/大厅 only（STUN + 发现 + 会话建立）** | 每局几十 kbps 级 | **上百局** | 5 Mbps 小水管的最佳用法 |
| **按需中继（P2P 打洞优先，打洞失败才回落中继）** | 全量中继 × 回落比例（常见 10–30%） | **4–10 倍于全量中继** | 性价比最高 |
| 服务器自己当"游戏主机" | 等于向所有客机的下行扇出 | 同全量中继 | 与中继同级 |

注意两点：

1. **入方向免费 ≠ 中继省带宽**：中继要转发，进多少就得出多少，出方向仍是上限；能省的是"不经过服务器的流量"（P2P/Steam SDR）。
2. **A 上还跑着 nginx 与 mihomo 代理**：代理的**出方向流量同样计入这 5 Mbps 与 500 GB**，与中继是同一池子——做中继前要先把共享额度算进去。

#### 9.5.3 修正后的建议

1. **纯中继不划算**：5 Mbps 出方向 + 与现有业务共享 ⇒ 全量中继只够 2–6 局，且会和代理/站点抢带宽。
2. **优先"少出流量"架构**：服务器只做 STUN/信令/大厅发现（出方向几十 kbps/局），P2P 打洞优先、失败才回落中继；
   或者直接沿用 HellForge 的 Steam SDR（零服务器带宽）。
3. 若要部署实测：**B 更适合**（负载 0.12、延迟 55 ms），但 B 的套餐未知（1.7 GB 内存/40 GB 盘，实测出方向 3.3 Mbps，
   约相当于 3–5 Mbps 级），需先在控制台核对带宽包；A 的 5 Mbps 已被 nginx+mihomo 占用一部分。
4. 每局真实带宽仍未实测——**两台机器开一局抓包 10 分钟即可校准**（当前 0.3–1 Mbps 是行业经验估算，不是本游戏实测）。
