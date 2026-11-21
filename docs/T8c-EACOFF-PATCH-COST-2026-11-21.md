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
