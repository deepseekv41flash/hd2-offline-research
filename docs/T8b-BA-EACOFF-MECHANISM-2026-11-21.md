# T8b 参照分析：Broken Arrow（EAC 游戏）的「Anti-Cheat Disabled」是怎么实现的（2026-11-21）

> 触发：用户提问「宿主机上别的 EAC 游戏 Broken Arrow 支持关闭 EAC 运行，分析一下是怎么实现的」。
> 目的：给 HD2 的 EAC-off 结论（[T8-EACOFF-RESULT](T8-EACOFF-RESULT-2026-11-21.md) ✗ 否证）做**同宿主机、同反作弊产品**的对照，
> 判断「关 EAC」是通用技巧还是**厂商给的路径**。
> 三态：`✓✓ 实机` / `✓ 静态+配置证据` / `⚠ 未证` / `✗ 否证`。全程**只读**，未启动 Broken Arrow，未改任何游戏文件。

## 0. 一句话结论

Broken Arrow 能关 EAC **不是技巧，是厂商自己留的两条路径**：

1. **Steam 启动项层**：官方 appinfo 里有一条 `Anti-Cheat Disabled` 启动项，**直接启动游戏本体 `BrokenArrow.exe`、不传任何参数**
   —— 也就是**根本不经过 EAC 启动器 `EACLauncher.exe`**（该 exe 与 HD2 的 `start_protected_game.exe` 是同一族的 EOS EAC bootstrapper）。
2. **游戏代码层**：客户端有一套可替换的反作弊抽象，EOS 实现叫 `EosAntiCheatService`，**空实现叫 `NoAntiCheat`**，
   并有 `ProtectedLobbyTypes` + `BlockReason { NoProductId, NoAntiCheat, IntegrityViolation }` ⇒ 无 EAC 时降级为「空反作弊 + 只封锁受保护的大厅」，
   游戏本体照常运行（单机/离线可玩）。

HD2 两条**都不具备**：零售包里的游戏 exe 只有一条「EAC 初始化失败就退出」的硬路径，且它的非 EAC 启动项指向**零售包未附带的开发版引擎二进制**。
⇒ T8 的 `✗ 否证` 结论不变，且现在能说清**差在哪一层**。

## 1. Broken Arrow 侧证据

### 1.1 Steam 启动项（官方配置原文，✓ 静态证据）

解析器：本轮新增 [`tools/steam_appinfo.py`](../tools/steam_appinfo.py)（只读解析 Steam `appinfo_<h>.vdf` 分片格式）。

```
$ python -X utf8 tools/steam_appinfo.py --file "E:\Steam\appcache\appinfo\appinfo_84.vdf" --appid 1604270 --launch
=== appid 1604270  Broken Arrow ===
  option 0   type=option1  exe='EACLauncher.exe'  args=None desc='Broken Arrow'        branch=None
  option 2   type=option1  exe='BrokenArrow.exe'  args=None desc='Anti-Cheat Disabled' branch=None
  option 3   type=option1  exe='BrokenArrow.exe'  args=None desc='Offline Mode'        branch='private_test_2 devtest '
  option 4   type=default  exe='EACLauncher.exe'  args=None desc='EAC MODE'            branch='private_test_2'
  option 5   type=default  exe='EACLauncher.exe'  args=None desc='EAC MODE'            branch='private_test_3'
  option 6   type=option1  exe='BrokenArrow.exe'  args=None desc='Anti-cheat disabled' branch='private_test_3 devtest '
```

要点：

- 两条 public 启动项（用户截图里的「玩 Broken Arrow」/「玩 Anti-Cheat Disabled」）**`arguments` 都是空**，
  **唯一差别是 `executable`**：EAC 模式走 `EACLauncher.exe`，关 EAC 模式直启 `BrokenArrow.exe`。
- option 3/4/5/6 是**开发分支（`private_test_2` / `private_test_3`）的 beta 启动项**，同样成对出现
  （`EAC MODE` ↔ `Offline Mode` / `Anti-cheat disabled`）⇒ 厂商内部就是靠「换启动目标」做 EAC/非 EAC 切换。
- 因此 Steam UI 里出现「在开始游戏时询问」，只是因为该游戏有多条 `option1` 启动项（Steam 对多选项游戏的标准行为）。

### 1.2 两个 EAC 启动器是同一族（✓ 静态证据）

| 项 | HD2 `start_protected_game.exe` | BA `EACLauncher.exe` |
|---|---|---|
| 大小 | 3,993,896 B | 3,975,920 B |
| `Easy Anti-Cheat` 字串数 | 13 | 13 |
| `EasyAntiCheatLauncherSemaphore` | ✓ | ✓ |
| `EasyAntiCheat-Client/1.0` | ✓ | ✓ |
| EOS EAC 安装脚本 | `EasyAntiCheat_EOS_Setup.exe install f1637333…` | 同款 `… install 62a6c936…`（`installscript.vdf` / `install_eos.bat`） |
| `EasyAntiCheat/Settings.json` | `executable: bin/helldivers2.exe` | `executable: BrokenArrow.exe` |
| `wait_for_game_process_exit` | `false` | `false` |

⇒ 两款游戏的 EAC 集成方式**结构完全一致**：EAC bootstrapper 负责把反作弊模块注入/加载进 `Settings.json` 指定的 exe。
**BA 的「关 EAC」= 不跑这个 bootstrapper。**

### 1.3 游戏代码层的降级路径（✓ 静态证据，IL2CPP 元数据名称表）

`BrokenArrow_Data\il2cpp_data\Metadata\global-metadata.dat`（27.5 MB）里与反作弊相关标识符（节选，含偏移）：

```
0x0015A963  EosAntiCheatService            ← EOS 反作弊服务实现
0x0015A9AC  get_IsIntegrityViolated
0x0015AA18  BeginSession / EndSession       ← 对应 EOS_AntiCheatClient_BeginSession/EndSession
0x0015AA3D  OnMessageToServer / ReceiveMessageFromServer
0x0015AA74  OnIntegrityViolated
0x0015AAC6  ProtectedLobbyTypes             ← 「需要反作弊保护的大厅类型」
0x0015AADA  _antiCheatInterface             ← EOS_Platform_GetAntiCheatClientInterface 的句柄
0x0015AB7A  BlockReason
0x0015AB86  NoProductId                     ┐
0x0015AB92  NoAntiCheat                     ├ BlockReason 枚举值：没有 productid / 没有反作弊 / 完整性违规
0x0015AB9E  IntegrityViolation              ┘
0x0015ABB1  BlockReasonExtensions
0x0015ABC7  GetLocalizationKey              ← 这些 block 原因都有本地化文案
0x001C3A1D  AntiCheatService
0x001C3A2E  BrokenArrow.Client.Ecs.AntiCheat   ← 独立命名空间
```

解读：客户端把反作弊做成**可替换服务**——正常走 `EosAntiCheatService`，反作弊不可用时用 **`NoAntiCheat`（空对象实现）**，
并通过 `BlockReason.NoAntiCheat` 只拦「受保护大厅」（联机），**不阻断游戏启动**。另有 `GetBlockReason()` + `BlockReasonExtensions.GetLocalizationKey()`
把原因翻成玩家可见文案。

> 边界说明：以上是**结构证据**（类型/成员名+EOS 导入符号），不是反编译出的控制流；
> 「无 EAC → 选 NoAntiCheat」这一步的**运行层**未实测（未启动 BA）⇒ 记为 `⚠ 未证（运行层）`。

## 2. HD2 侧对照（同一宿主机、同一天、同款 EAC EOS）

### 2.1 HD2 的官方启动项里**确实有**不经过 EAC 启动器的条目

```
$ python -X utf8 tools/steam_appinfo.py --file "E:\Steam\appcache\appinfo\appinfo_f6.vdf" --appid 553850 --launch
=== appid 553850  HELLDIVERS™ 2 ===
  option 0   type=default  exe='start_protected_game.exe'          args='--bundle-dir data --release'  desc=None
  option 7   type=option1  exe='bin\dev\stingray_win64_dev.exe'    args='--bundle-dir data'            desc='development'
  option 8   type=option3  exe='bin\release\stingray_win64_release.exe'  args='--bundle-dir data --release'  desc='stingray_win64_release.exe'
  option 9   type=option2  exe='bin\profile\stingray_win64_profile.exe'  args='--bundle-dir data --release'  desc='stingray_win64_profile.exe !THIS PRODUCES A LOT OF DATA!'
  option 10  type=None     exe='start_protected_game.exe'          args=...                            desc='Easy Anti Cheat'（开发分支）
  option 12  type=None     exe='start_protected_game.exe'          args='-anticheat_settings=SettingsProfileProtected.json …'  desc='profile'
```

**关键：option 7/8/9 指向的是 Stingray 引擎的 dev / release / profile 二进制，零售包里没有这些文件。**

```
$ ls "D:\SteamLibrary\steamapps\common\Helldivers 2\bin"   # 无 release/ dev/ profile/
$ 全盘匹配 stingray_win64* → 无
$ bin\plugins → 只有 level_generation_pluginw64_release.dll / wwise_pluginw64_release.dll
```

⇒ HD2 的非 EAC 启动项是**引擎开发配置的残留**，只在厂商内部构建里可用；零售玩家选到也只会「文件不存在」。

### 2.2 HD2 游戏本体只有「EAC 失败就退出」这一条路

| 观测 | HD2 | BA |
|---|---|---|
| 本体硬退出字符串 | `bin\helldivers2.exe` @0x0173C188 **`EOS EAC Failed to init, exiting`**（全文唯一一条 `Failed to init, exiting`） | 无对应硬退出串 |
| 反作弊抽象/空实现 | `runtime.dll` / `helldivers2.exe` 中 `NoAntiCheat` / `AntiCheatService` / `BlockReason` / `ProtectedLobby` **各 0 命中** | 全套都有（§1.3） |
| 无 EAC 时的行为 | `✗ 否证`（实机）：直启 → ~50 ms 退出 `0xC0000244`；null-client 环境变量也救不了游戏进程（T8 §3） | 官方启动项允许直启；代码层有空实现 |

### 2.3 顺带澄清（本轮新增，避免后人再绕）

- HD2 `bin\GameGuard\` 里是一整套 nProtect GameGuard（`npgg*.des`/`npsc64.des`/加密的 `SONYGame.ini` + 大量 `.erl`），
  属于 **PSN/主机侧遗留**，本机没看到它被拉起（T2/T3 实机链路的进程与网络快照里没有 GameGuard）；
  与 EAC 无关，也不构成第二条「可关的反作弊」。→ `⚠ 未证`（未做进程级确认），但不影响结论。
- `BrokenArrowOnline.exe`（2.6 MB）静态扫不到任何可打印字符串（打包/加密），`BrokenArrowOnline.ini` 只写
  `ApplicationPath=EACLauncher.exe`；不影响主结论（Steam 启动项直指 `EACLauncher.exe`，不经过它）。

## 3. 机制归纳（可复用的判断口径）

一个游戏能不能「关 EAC 运行」，取决于**厂商是否在零售版里同时留下两件事**：

| 层 | 条件 | BA | HD2 |
|---|---|---|---|
| L1 启动路径 | 存在一条不经 EAC bootstrapper 的官方启动项（换成游戏 exe 本体即可） | ✓ `option 2` 直启 `BrokenArrow.exe` | ✗ 仅剩 dev 引擎二进制的悬空项 |
| L2 代码容忍 | 游戏代码允许反作弊不可用（空实现/降级），只封锁受保护内容 | ✓ `NoAntiCheat` + `ProtectedLobbyTypes` + `BlockReason` | ✗ 硬依赖 `EOS_AntiCheatClient`，失败即 `exit` |

⇒ 「关 EAC」不是可移植的技巧，而是 **L1+L2 的产品决策**。反作弊厂商（EOS EAC）本身对两家的约束是一样的。

## 4. 对 HD2 研究线的影响

1. **T8 结论不变**：零售版 HD2 无合法 EAC-off 路径，阶段1 = 「官方 API 接管 + EAC 网络可达」。
2. **理由更精确了**：不是「EAC 太强」，而是 (L1) 零售包不含非 EAC 可执行目标 + (L2) 游戏代码没有降级分支。
3. **仍然不做的**：给 `helldivers2.exe` 打补丁跳过 `EOS_AntiCheatClient` 初始化 —— 属绕过反作弊，越出本研究线
   「不注入、不改游戏文件、只做本地 API 接管」的边界（T8 §4 已记为不纳入计划）。
4. **对阶段2 无害**：自建联机服务形态下 EAC 仍需可达（与 T7b 一致），BA 的 L2 降级设计只是说明「另一家厂商选了另一条路」，
   不能反过来给 HD2 提供捷径。

## 5. 三态判定

| 判定项 | 结论 | 证据等级 |
|---|---|---|
| BA 的「Anti-Cheat Disabled」= Steam 官方启动项直启游戏本体、无参数 | ✓ | 官方 appinfo + 用户截图一致 |
| BA 的 EAC 模式 = `EACLauncher.exe`（与 HD2 `start_protected_game.exe` 同族） | ✓ | 字串/大小/安装脚本/Settings.json 对照 |
| BA 游戏代码具备无 EAC 降级路径（空实现 + 只封锁受保护大厅） | ✓ 结构证据 | IL2CPP 元数据名称表 |
| BA 无 EAC 时**实际能跑起来**（运行层） | ⚠ 未证 | 未启动该游戏（只读边界） |
| HD2 零售包存在非 EAC 可执行目标 | ✗ 否证 | option 7/8/9 指向的 dev 引擎二进制不存在 |
| HD2 游戏本体可容忍无 EAC | ✗ 否证 | 实机退出码 + 唯一硬退出字符串 + 无降级抽象 |

## 6. 复现命令（本机可跑，只读）

```bash
# 1) 解析任意 Steam 游戏的官方启动项
python -X utf8 tools/steam_appinfo.py --file "E:\Steam\appcache\appinfo\appinfo_84.vdf" --appid 1604270 --launch
python -X utf8 tools/steam_appinfo.py --file "E:\Steam\appcache\appinfo\appinfo_f6.vdf" --appid 553850  --launch
#    不知道 appid 落在哪个分片时：扫全目录
python -X utf8 tools/steam_appinfo.py --list-apps

# 2) HD2 非 EAC 启动项目标是否存在
ls "D:\SteamLibrary\steamapps\common\Helldivers 2\bin"     # 无 release/ dev/ profile/

# 3) 硬退出字符串定位（HD2）
python - <<'PY'
from pathlib import Path
b = Path(r"D:\SteamLibrary\steamapps\common\Helldivers 2\bin\helldivers2.exe").read_bytes()
i = b.find(b"EOS EAC Failed to init, exiting")
print(hex(i))            # 0x173c188
PY
```

## 7. 本轮新增产物

| 文件 | 说明 |
|---|---|
| `tools/steam_appinfo.py` | Steam `appinfo_<h>.vdf` 分片（magic `0x0756442a`）只读解析器：定位字符串表→按 uint32 键下标解析 KV 树→导出 `config.launch` 启动项 |
| `tests/test_steam_appinfo.py` | 用合成 appinfo 固化解码口径（字符串表定位 + 启动项导出 + 多 app 分离） |
| 本文档 | BA ↔ HD2 的 EAC-off 机制对照 |

> 缓存口径：Steam 的 appinfo 分片是客户端缓存（会随 Steam 更新变化），本文引用的 `appinfo_84.vdf`(BA) / `appinfo_f6.vdf`(HD2)
> 与解析器输出为 2026-11-21 当时的快照；结论依赖的是「启动项存在性」这一稳定事实，不受缓存刷新影响。
