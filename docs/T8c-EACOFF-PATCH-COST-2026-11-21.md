# T8c：给 `helldivers2.exe` 打「跳过反作弊初始化」补丁的复杂度评估（2026-11-21）

> 触发：用户提问「打这个补丁的复杂度高吗？难点在哪？」
> 方法：只读静态分析（PE 节熵/导入表/PEB 调试目录/全 .text 精确引用扫描）+ 一次无补丁 A/B 启动实验（直启 vs 直启+官方 null-client 环境变量）。
> 三态：`✓ 已证` / `⚠ 未证` / `✗ 否证`。**未修改任何游戏文件、未注入、未打补丁。**

## 0. 一句话结论

**复杂度高，而且不是「NOP 掉几个跳转」那种高**：目标代码在磁盘上就是**密文**
（`helldivers2.exe` 的 `.text` 21.5 MB 熵 **8.0000**、`.pdata` **7.9998**，由同目录 `preloader.dll` 在装载时解密），
exe 只导入 **1 个序号函数**、没有 PDB、没有 RTTI。⇒ **静态定位不可能**，必须先脱壳/动态执行，
而「动态改内存」在本研究线里就是**注入**（边界外），并且每次游戏更新都要重做。

配套事实：连「它为什么退出」目前都**没有定位**——同一退出码 `0xC0000244` 在三/四条启动路径下都出现，
且 Windows 事件日志里**没有**该 exe 的崩溃记录（⇒ 是主动 `ExitProcess`，不是未处理异常）。

## 1. 为什么「打补丁」这个前提本身不成立：代码是加密的（✓ 已证）

### 1.1 节熵对照

| 文件 / 节 | 大小 | 熵 | 可读串（≥8 字符） | 判读 |
|---|---|---|---|---|
| `helldivers2.exe` **.text** | 21,540,864 B | **8.0000** | 4891，全为随机噪声（`bm^WRZ$*`…） | **加密/压缩** |
| `helldivers2.exe` **.pdata** | 1,122,304 B | **7.9998** | 253，随机噪声 | **异常目录也加密** |
| `helldivers2.exe` .rdata | 4,931,584 B | 6.4722 | 含真实字符串（`EOS EAC Failed to init, exiting` 等） | 明文 |
| `start_protected_game.exe` .text（对照） | 2,272,768 B | 6.5547 | `d$ UAVAWH`（真实指令字节） | 正常代码 |
| `runtime.dll` | 64.9 MB | 段名 `packer0/1/2/30/31/40/42` + `.rwx` | — | 保护器重打包 |

### 1.2 装载链：一个 45 KB 的 `preloader.dll` 负责解密

- `helldivers2.exe` 的导入表**只有一个条目**：`preloader.dll` **序号 #1**（0 个符号名）。
- `preloader.dll`（45.1 KB，仅 11 KB 代码）导出 2 个**无名**函数，PE 内模块名 `preloader.unsigned.dll`；
  导入 `ZwCreateUserProcess` / `NtCreateSection` / `NtMapViewOfSection` / `LdrGetProcedureAddress` /
  `LdrFindResource_U` / `MD5Init` / `RtlQueryEnvironmentVariable` / `NtTerminateProcess` / `ZwDeleteFile`…
  ⇒ 它是**装载器 + 资源/环境校验器**，并具备**主动终止进程**的能力。
- `helldivers2.exe` 调试目录里只有内部构建路径（`/work/runtime.pdb`、`/work/preloader.pdb`），**无公开符号**；
  游戏两个二进制里 RTTI 类型名（`.?AV`）**0 命中** ⇒ IDA/Ghidra 里基本全是无名函数。

### 1.3 因此「静态交叉引用」拿不到东西

对 9 条反作弊日志串（含 `EOS EAC Failed to init, exiting`、
4 条 `EOS_AntiCheatClient_AddNotify* failed, exiting`、`Starting protected session`、`Easy Anti Cheat Error!` 等）：

- 穷举 `.text` 全部 21,540,860 个字节位置、检查「是否为指向该串的 RIP 相对 disp32」→ **0 命中**（方法自检：同样扫描对 `.rdata` 有 24,674 个合法命中）；
- 该串的 VA（`0x14173CB88`）在整个文件里作为 qword 出现 **0 次**，RVA 作为 dword 也 **0 次**；
- 窗口 `[0x173C000,0x173D000)` 内只有 20 个 disp32 命中，且目标都落在字符串**中间**（如 `+0x12`）⇒ 是**密文随机字节的假阳性**，不是引用。

⇒ 结论：**不是引用方式特殊，是引用方（.text）在磁盘上是密文**。

## 2. 「为什么退出」尚未定位（⚠ 未证）

| 观测 | 结果 |
|---|---|
| 直启 `bin\helldivers2.exe --bundle-dir data --release` | 退出码 **`0xC0000244`**（3221226052 / -1073741244），77 ms |
| 同上 + `EOS_USE_ANTICHEATCLIENTNULL=1`（EOS SDK 官方 null-client 调试开关） | 仍在 **188 ms** 退出，**同一退出码** |
| EAC 启动器 + 同一环境变量（T8 旧实验） | 启动器日志显示已切 null client，游戏子进程仍同码退出 |
| Windows Application 事件日志（近 8 h，含 helldivers2） | **0 条** ⇒ 主动 `ExitProcess`，**不是**未处理异常/WER 崩溃 |

因此候选退出源有三处，均**未被区分**：
① 游戏逻辑（`EOS_AntiCheatClient_*` 失败分支）；
② `preloader.dll` 加固壳的环境/完整性校验；
③ EAC 启动器↔游戏侧的握手缺失。

> 对 T8 的归因修正：T8 §3 把退出**归因**为「游戏本体硬依赖 `EOS_AntiCheatClient`」，
> 该归因来自 `.rdata` 里的日志字符串，**运行层并未证实确实走了那条分支**。
> 在抓到调用栈之前，应降级为 `⚠ 未证`。
> **但 EAC-off ✗ 的结论不受影响**：无论退出来自哪一层，零售版都不存在合法的「无 EAC 运行」路径
> （Steam 启动项里没有可用的非 EAC 目标，见 [T8b](T8b-BA-EACOFF-MECHANISM-2026-11-21.md)）。

## 3. 三条（半）技术路线与各自难点

| 路线 | 做法 | 主要难点 | 边界 |
|---|---|---|---|
| **A. 静态改 exe** | 脱壳（dump 解密后内存）→ 找到失败分支 → 改 → 重新加密 | ① 必须先动态运行才能拿到明文代码；② 改完要**重新加密/过完整性校验**（密钥与校验在 `preloader.dll` 内，无工具）；③ 不是改几个字节，是**重建保护后的映像** | ✗ 改游戏文件 |
| **B. 运行时内存补丁** | 等 preloader 解密后改内存；或 hook `NtTerminateProcess`/`ExitProcess`；或 hook EOS 导出 | 需要 x64 RE + 反调试对抗；地址每次更新变；**本质是注入** | ✗ 注入 |
| **C. 代理 `EOSSDK-Win64-Shipping.dll`** | EOSSDK 是**明文独立 DLL**（19.5 MB / **680 导出**）→ 写导出转发 DLL，把 `EOS_AntiCheatClient_*` 变空实现 | 不止 4 个 `AddNotify`：还要覆盖 `EOS_Platform_Create`→`GetAntiCheatClientInterface`→`BeginSession`/`RegisterPeer`/`ProtectMessage`/`UnprotectMessage`/`GetProtectMessageOutputLength` 的**全链语义**；且游戏若走 EOS P2P，消息保护语义也得对 | ✗ 换游戏目录 DLL |
| **D. 官方 null-client** | `EOS_USE_ANTICHEATCLIENTNULL=1` | **本轮 A/B 已证无效**（§2），且它只影响 EOS SDK 的 anti-cheat 实现，不解决 preloader/启动器层 | ✓ 无补丁（但无效） |

补充：若保留 EAC（EAC-on）再打补丁，EOS EAC 的 client integrity 会检测篡改——游戏 `.rdata` 里就有
`IntegrityViolation: %i, %s`、`...ted or missing. Please verify integrity of game file` 这类文案 ⇒ **封号风险**。

## 4. 成本估算（假设有 x64 RE/调试经验、且环境合法授权）

| 目标 | 估算 | 说明 |
|---|---|---|
| 让进程「不退出」（能活过几秒） | **2–5 人日** | 装调试器抓 `ExitProcess` 栈 → 判层 → 内存补丁；先要解决脱壳+反调试 |
| 让**单机能进船、进任务** | **1–3 周，且结果不确定** | 反作弊状态会被 protected session / peer 注册 / 遥测等多处消费，单点跳过通常只是把门禁后移 |
| 达到「可交付、跟更新」 | **不成立** | HD2 更新频繁，加密布局重排 ⇒ 每次重做；Steam 校验/更新会覆盖；分发=协助绕过反作弊 |

## 5. 如果一定要把难度钉死，只该先做这两个**只读**探针

1. **抓退出调用栈**（约 30 min，需装 Windows SDK Debuggers，cdb `~300 MB`，属系统变更需用户同意）：
   `cdb -g -G "bin\helldivers2.exe" --bundle-dir data --release` + `bp ntdll!NtTerminateProcess` / `bp kernel32!ExitProcess`，
   命中后 `kb` 看返回地址落在哪个模块 ⇒ 直接判定退出源是「游戏 / preloader / 其它」。
   本机现状：`cdb.exe`、`procdump` **均未安装**。
2. **对照「EAC 启动器正常路径」**：保留 EAC-on 时用同一手段抓一次，比较两者栈差异。

两者都**不改文件、不注入**（调试器加载属于分析行为，但会改变进程环境，需明确记录）。

## 6. 对研究线的结论

- **不做**。投入产出完全倒挂：现有阶段1 方案（EAC 网络可达 + 本地 API 接管）已 `✓✓ 实机` 验证，
  零游戏文件改动、跟更新零维护；打补丁的收益仅是「离线时不需 EAC 网络」，代价是不可控工时 + 合规/封号风险。
- 若用户**只是想知道能不能**：技术上「让进程多活几秒」可行但不便宜；「完整单机」不可控；
  「合法、可分发」不存在。
- 本轮另修正一处归因（§2 末尾）：T8 的「游戏硬依赖反作弊」应记为 `⚠ 未证`（运行层），EAC-off ✗ 结论不变。

## 7. 追加（用户追问）：假设 EAC-off 已经实现，「禁止访问官方服务器」的复杂度

> 前提：这是**假设性设计评估**（EAC-off 本身在零售版已 `✗ 否证`，见 §2/§3）。目的：如果哪天真的做了，
> 防止绕过手段被拿去连官方服务器，要付多少代价、能到什么程度。

### 7.1 先纠正类比：Broken Arrow 的"防滥用"不是防火墙，是游戏内 gating

[T8b](T8b-BA-EACOFF-MECHANISM-2026-11-21.md) 已证：BA 关 EAC 后能跑，是因为代码里有
`NoAntiCheat`（空实现）+ `ProtectedLobbyTypes` + `BlockReason{NoProductId, NoAntiCheat, IntegrityViolation}`
—— **没有反作弊时只能进"非受保护"的大厅/离线模式，受保护模式被拒绝**，并在 UI 上给出可本地化的 block 原因。
它**没有**装网络防火墙。

⇒ 所以"类似 Broken Arrow"有两种解读：

| 解读 | 可行性 | 说明 |
|---|---|---|
| (a) 游戏内拒绝受保护/官方模式 | **✗ 不可行** | 需要改游戏代码（HD2 没有这个降级分支，且无源码）；属已否证的补丁路线 |
| (b) 网络层阻断官方服务器（HellForge 的做法） | **✓ 可行，中等复杂度** | 见 §7.2；与我们的网络接管层天然同源 |

### 7.2 网络层阻断的四个层级与成本

| 层级 | 做法 | 工作量 | 效果/弱点 |
|---|---|---|---|
| L1 域名层 | hosts 重定向/DNS blackhole（**我们已有 hosts 接管**） | **0.5–1 人日** | 只防误连；IP 直连、DoH、改 hosts 都绕过 |
| L2 防火墙规则 | `netsh advfirewall` 按程序出站 block（+ 允许 mock/loopback） | **1–2 人日** | 用户关防火墙/安全软件覆盖即失效；IPv6/多网卡/子进程易漏 |
| L3 WFP 过滤器 | 高权重 sublayer + 按程序允许表（参考 HellForge `native/network-block/hellforge_wfp.c`：**420 行 C** + 约 **230 行 PowerShell** + 探针） | **1–2 周**（含安装/卸载/status/多实例隔离/TCP+UDP/权限与崩溃恢复） | 关掉 Windows 防火墙仍生效；能压过安全软件/VPN 的 permit；需管理员一次 |
| L4 服务端自证 | 引擎/后端要求"阻断已生效"才提供数据（WFP status + 出网探针） | **+2–3 人日** | 防误配置，不可防伪造（客户端全在用户手里） |

HellForge 的 WFP 设计可直接当规格参考（**读设计、不抄 GPL 代码**）：每个受控程序 8 条过滤器
（4 层 × 允许 loopback + block 其余）、一个共享 sublayer 置于最高权重、允许表只放行一个回环端口、
UDP 在首个出站包上完成分类 ⇒ TCP/UDP 一并覆盖。

### 7.3 HD2 场景下"官方服务器"到底要阻断什么

比 HellForge 简单：要防的官方后端只有两族（其余是身份/遥测基础设施，不含游戏会话）：

| 目标 | 作用 | 是否必须阻断 |
|---|---|---|
| `api.live.prod.thehelldiversgame.com`（官方游戏 API） | 登录/进度/战况/任务——**唯一游戏后端** | **必须**（我们已用 hosts 把它改写到本机 mock） |
| `974A9.playfabapi.com`（PlayFab Lobby/Party） | 联机会话 | **必须**（否则可进官方联机） |
| `api.epicgames.dev` + EAC CDN（EOS 身份/反作弊） | 身份/反作弊基础设施，不含游戏会话 | 可选：EAC-off 后若不依赖可一并阻断；若 EAC-off 补丁仍要 EOS 身份则放行 |
| 本机 mock（如 `100.67.169.17:443`）+ loopback | 我们的服务 | **必须放行** |

⇒ 允许表极小：**mock IP + loopback**；其余官方/云服务全部 block。这比 HellForge 的"只放行一个回环端口"只多一点。

### 7.4 能不能"防止非法滥用"？——客户端层面做不到，必须说清

1. **任何本地阻断都可被本机管理员移除**（删 WFP 过滤器、改 hosts、换网、换一台机器）。它是**防误连与合规姿态**，
   不是安全边界。
2. 真正的防线在三处，都不在客户端代码里：
   - **不分发**绕过手段（本研究线的边界本来就是"不注入、不改游戏文件"）；
   - **官方服务端**自己的校验（EAC/EOS 拒绝被改客户端）——不是我们能控制或替代的；
   - 用户协议/法律（ToS、DMCA §1201 类风险）。
3. 因此产品化时正确表述是"**默认离线、不提供连官方服务器的路径**"，而不是"保证无法滥用"。

### 7.5 成本结论

| 目标 | 复杂度 | 工时 |
|---|---|---|
| 让普通用户"不会误连官方"（hosts + 防火墙规则 + 启动自检） | **低–中** | **3–5 人日** |
| HellForge 级 WFP（关防火墙仍生效、抗 VPN/安全软件、安装/卸载/自检/多实例） | **中** | **1–2 周 / ~700 行代码 + 管理员权限 + Win10/11·IPv6·多网卡·VPN 测试矩阵** |
| 像 BA 那样在游戏内拒绝受保护模式 | **高/不可行** | 需改游戏（已否证路线） |
| 真正"防止恶意滥用" | **客户端不可能** | 只能靠分发/协议/法律 |

**建议**：若将来真做 EAC-off（不建议），按 L1→L2→L4 顺序落地，WFP 只在确实需要"关防火墙也生效"时再写；
同时把"默认阻断 + 引擎启动自检"做成产品默认行为，并在文档里明确它只是防误连，不是安全保证。
