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
