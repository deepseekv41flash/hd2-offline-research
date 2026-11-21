# T8 执行结果（P0 + P1 首轮）：EAC-off 直启 ✗ 否证（2026-11-21）

> 承接 [T8-PLAN-DATAPLANE-EACOFF-2026-11-21.md](T8-PLAN-DATAPLANE-EACOFF-2026-11-21.md)。
> 本轮只跑了 **P0（离线统计/静态确认）** 与 **P1 的 EAC-off 部分**；P3（SSLKEYLOGFILE）与 P2（hook 取证）未做。
> 三态：`✓✓ 实机` / `⚠ 未证` / `✗ 否证`。

## 0. 一句话结论

**EAC-off 直启在当前零售版 HD2 上不可行**：游戏本体硬依赖 `EOS_AntiCheatClient` 初始化，
失败即打印 `"EOS EAC Failed to init, exiting."` 并在 ~50 ms 内以 **`0xC0000244`** 退出；
EAC 启动器的官方调试开关 `EOS_USE_ANTICHEATCLIENTNULL`（null client）只在**启动器层**生效，救不了游戏。
⇒ 阶段1 的「完全无外网」形态**不能**靠 EAC-off 实现；EAC-on 需要真实可达的 EAC 网络（与 T7 R2 一致）。

## 1. P0-①：数据面明文统计（用已有 T4 pcap，`tools/analyze_pcap_dataplane.py`）

| 项 | 结果 |
|---|---|
| STUN（UDP 3478/3479） | **456 包**；Binding Request/Response；响应含 XOR-MAPPED-ADDRESS（公网映射）+ FINGERPRINT；对端为中国区 Steam SDR/STUN 节点（IP 已脱敏） |
| PlayFab QoS（UDP 3075） | **120 包**，全部 UDP 长度 88（80 B 载荷）；**60 请求（`ffff`+nonce）/ 60 响应（`0000`+同 nonce）成对** ⇒ 可直接按 nonce 配对算 RTT/区域选择 |
| DTLS（高端口） | 3487 包；`45368` 为主（1687）；按 DTLS 解析 2380 records，其中 **6 个 handshake(22)**、2 组 `22,22,22`、2 组 `22,20,22` ⇒ **抓包里有 DTLS 握手**（P3 keylog 有真实目标），其余 2369 为 application_data（加密） |

证据：`state/evidence/t8/pcap-dataplane.txt`。

## 2. P0-②：EAC-off 静态确认

| 观测 | 结果 |
|---|---|
| `EasyAntiCheat/Settings.json` | `"executable": "bin/helldivers2.exe"`、`wait_for_game_process_exit: false`、`hide_gui: false` ⇒ EAC 是「加载模块 → 启动该 exe」的启动器 |
| 启动器参数 | 反查 Steam 启动时的命令行：`start_protected_game.exe --bundle-dir data --release +connect_lobby 000`（后者是用户自加的跳过场选项） |
| 是否有"跳过 EAC"参数 | **无**（字符串里没有 `--noeac` 类开关；只有 `EOS_USE_ANTICHEATCLIENTNULL` 等环境变量） |
| SteamStub/DRM | **无**（PE 节名无 `.bind/.steam`，无 SteamStub 字符串） |
| EAC 运行时 | 已安装于 `C:\Program Files (x86)\EasyAntiCheat_EOS\`（`EasyAntiCheat_EOS.exe/.sys`）；服务 `EasyAntiCheat_EOS` 平时 Stopped/Manual |

## 3. P1：EAC-off 直启的实测（三轮，均失败）

| 尝试 | 结果 |
|---|---|
| ① 直启 `bin\helldivers2.exe`（无参数） | 退出 `0xC0000244`（缺 `--bundle-dir data` 等参数） |
| ② 直启 + 启动器同款参数（`--bundle-dir data --release +connect_lobby 000`），并补 Steam 环境变量（`SteamAppId/SteamGameId/SteamClientLaunch/SteamPath`） | 仍退出 `0xC0000244` |
| ③ 直启 **EAC 启动器**（`start_protected_game.exe`）+ `EOS_USE_ANTICHEATCLIENTNULL=1` | 启动器**成功**进入 null client（日志：`Debug environment variable (EOS_USE_ANTICHEATCLIENTNULL) was present, launching with null client`；`Launcher finished with: 301, 'Easy Anti-Cheat 在游戏中成功加载'`），**但游戏子进程 50 ms 内退出 `3221226052`(0xC0000244)** |

**游戏本体字符串（决定性证据）**：

```
Starting protected session
Failed to start protected session!
EOS_EAC.EOS_AntiCheatClient_AddNotifyPeerAuthStatusChanged failed, exiting.
EOS_AntiCheatClient_AddNotifyPeerActionRequire... failed, exiting.
EOS_AntiCheatClient_AddNotifyClientIntegrityViolated failed, exiting.
EOS EAC Failed to init, exiting.
Easy Anti Cheat Error!
Peer registration failed, %s
```

⇒ 游戏启动早期就要求 `EOS_AntiCheatClient` 初始化成功；null client 只作用于 EAC 启动器自己的 EOS 实例，
游戏内 EOSSDK 仍初始化失败 → 主动退出（`EOS EAC Failed to init, exiting.`）。

**旁证**：Steam 侧不是卡点 —— 用 ctypes 单独调用 `steam_api64.dll!SteamAPI_InitFlat` 返回 **0（k_EResultOK）**（Steam 客户端在线时）。

## 4. 对阶段1 的影响（结论变化）

| 之前 | 现在 |
|---|---|
| 「阶段1 若要求完全无外网，可走 EAC-off 直启」 | **✗ 否证**：零售版不存在合法的 EAC-off 启动方式 |
| 阶段1 部署形态 = 官方 API 接管 + EAC 可达 | **不变，且成为唯一形态**（T7b 已验证） |

**可选但不建议**：对游戏 exe 打补丁跳过 EOS AntiCheatClient 初始化 —— 属于绕过反作弊，与本研究线
「不注入、不改游戏文件、只做本地 API 接管」的边界冲突，**不纳入计划**。

## 5. 未做/下一步（供决策）

| 项 | 说明 |
|---|---|
| P3 `SSLKEYLOGFILE` 试探 | 未做；P0 已确认抓包里有 DTLS 握手，若某栈支持 keylog 可直接解数据面。**成本 10 分钟** |
| P2 Frida hook | 未做；可用于：①确认 `EOS_AntiCheatClient_Initialize` 的具体返回码（把 §3 的结论钉死到 API 级）②阶段2 的明文数据面取证（Steam/EOS） |
| E-3 断网/离线模式 | 未做；考虑到 §3 的否证结论，EAC-off 不可行，E-3 的意义只剩「EAC-on + 断网能否起来」→ 预计不能（EAC 需网络），价值低 |

## 6. 现场

未修改游戏目录、未改 hosts、未改回环/代理设置；研究 CA 装/卸由 `mockctl cert-install|uninstall` 管理；
EAC 启动器日志写入 `%APPDATA%\EasyAntiCheat\…`（系统行为，非本工具产生）。
