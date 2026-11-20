# A2 侦察：游戏进程除 API 主机外还连哪些主机（静态部分）

> 研究线：离线单机 / 自建联机（A2 = 完整主机依赖清单）
> 日期：2026-11-21 | 性质：**只读静态侦察**（未启动游戏、未起监听、未改 hosts/证书/任何仓库文件）
> 唯一写入：本文件
> 三态：`✓✓ 实机`（有运行期实据）/ `✓ 离线+数据`（静态证据+数据推导）/ `⚠ 未证`（线索存在但未确认）/ `✗ 否证`
>
> **2026-11-21 T4 实测补充（本文档的运行时答案已出）**：正式抓包（180 s，DNS+SNI+UDP）确认
> **E1 = 不存在第二条游戏自有主机**，游戏自有 API 只连本机 mock；旁路清单（PlayFab / EOS+EAC /
> Steam / Sony PC `wwsga.me`·`pss-cloud.net`）见 [T4-RUNTIME-EGRESS-2026-11-21.md](T4-RUNTIME-EGRESS-2026-11-21.md)。

---

## 一句话结论

**除 `api.live.prod.thehelldiversgame.com` 外，静态层面没有找到第二条「游戏自有 HTTP API」主机；但确认游戏进程同时加载了 3 条非 API 旁路：Steam 网络（联机传输）、PlayFab Party/Multiplayer（跨平台组队/网络，运行时线程在跑）、Sony RECAP（崩溃上报，实机上传成功），另挂着 EOS/EAC、Sony PSPC/PSN、GameGuard 残留等组件。这些旁路的主机名大多是运行时动态下发或加密字符串，静态无法穷举——A2 的「完整主机清单」必须以游戏运行时的 DNS/TCP/UDP 抓取为准。**

---

## 0. 方法与环境（只读）

| 项 | 值 |
|---|---|
| 游戏安装 | `D:\SteamLibrary\steamapps\common\Helldivers 2\`（appmanifest `buildid=26568081`，BetaKey public） |
| 扫描对象 | `bin\*.exe/*.dll`、`data\game\game.dll`（49,075,800 B）、`data\game\*.dl_bin`、`data\settings.ini`、`data\bundles.*.nxa`+`*.stream`（共约 23 GB）、`GameGuard\`、`EasyAntiCheat\` |
| 运行期只读取证 | 崩溃 dump（`%APPDATA%\Arrowhead\Helldivers2\dumps\`，674 个/1.9 GB，最新 2026-11-11 21:08）、`%LOCALAPPDATA%\HD2ProxyStudio\logs\`、`%LOCALAPPDATA%\Sony Interactive Entertainment Inc\PSPC_SDK\`、`%APPDATA%\Arrowhead\Helldivers2\crash_data\`、`D:\Downloads\variant_mod\logs\`、`D:\mod\tmp\api_ip.txt` |
| 判定工具 | Python 3.13（mmap + 正则）、PE 导入表解析、MiniDump 流解析、`grep -a`、PowerShell `Get-Service`/`Get-NetTCPConnection`/`Get-DnsClientCache` |
| 本机可用抓包工具 | `C:\Program Files\Wireshark\dumpcap.exe`、`tshark.exe`、内置 `C:\Windows\System32\pktmon.exe` |

---

## 1. 证据表 A：关键阴性证据 —— API 主机名不在任何明文位置

**结论：`thehelldiversgame` / `api.live` / `live.prod` 在全部已扫描的可执行/数据明文中 0 命中 ⇒ 该主机名存放在「压缩的 bundle 资源（Lua/配置）」或运行时拼装，静态穷举游戏自有主机不可行。**

| # | 文件 / 目标 | 结果 | 强度 |
|---|---|---|---|
| A1 | `bin\helldivers2.exe`（28,743,256 B） | `thehelldiversgame`、`thehelldivers`、`api.live`、`live.prod` 均未命中 | ✓ 离线 |
| A2 | `bin\runtime.dll`（64,870,488 B） | 同上，全未命中 | ✓ 离线 |
| A3 | `data\game\game.dll`（49,075,800 B，游戏本体模块） | 同上全未命中；且无 `/api/`、`WarSeason`、`Account/Login`、`https://` ⇒ **API 端点路径/基址也不在游戏代码 DLL 里**（应在 Lua 资源） | ✓ 离线 |
| A4 | `data\game\*.dl_bin`（数据表）+ `data\settings.ini` + `data\manifest.json` 等 | 无主机名 | ✓ 离线 |
| A5 | `bin\` 下其余 12 个 exe/dll（steam_api64、EOSSDK、PartyWin、PlayFabMultiplayerWin、libcurl、crs-client、crs-handler、preloader、plugins/*） | 无 `thehelldiversgame` | ✓ 离线 |
| A6 | `data\bundles.*.nxa` + `*.stream`（约 23 GB 全量 grep） | `thehelldiversgame`/`api.live`/`playfabapi`/`epicgames`/`nprotect` 全 0 命中；**控制组**：674 MB 的 `bundles.00.nxa` 中 `function` 命中 0、`http` 仅 2 次 ⇒ bundle 已压缩，明文 grep 不可作为「不存在」证据 | ⚠ 仅证「明文不可得」 |

> 复验（任意文件的片段搜索）：
> ```powershell
> cd 'D:\SteamLibrary\steamapps\common\Helldivers 2\bin'
> python -c "import mmap;f=open('helldivers2.exe','rb');m=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ);print([hex(m.find(s)) for s in [b'thehelldiversgame',b'api.live',b'playfabapi']])"
> ```

---

## 2. 证据表 B：游戏进程真实加载的网络相关模块（实机 dump，✓✓）

dump：`%APPDATA%\Arrowhead\Helldivers2\dumps\dump-2026-11-11-21.08.29-a89fa2f0-DESKTOP-P0CP18R-41.dmp`（9,833,873 B，MDMP 格式，173 个已加载模块；2026-11-11 真实对局会话）。

| 类别 | 已加载模块（dump 模块表 / 路径） | 强度 |
|---|---|---|
| 游戏自有 | `helldivers2.exe`、`game.dll`（路径 `D:\SteamLibrary\...\data\game\game.dll`）、`runtime.dll`、`preloader.dll`、`libcurl.dll` | ✓✓ 实机 |
| Steam | `steam_api64.dll`、`steamclient64.dll`（`E:\Steam\`）、`tier0_s64.dll`、`vstdlib_s64.dll`、`gameoverlayrenderer64.dll` | ✓✓ 实机 |
| PlayFab | `PartyWin.dll`、`PlayFabMultiplayerWin.dll` | ✓✓ 实机 |
| EOS | `EOSSDK-Win64-Shipping.dll` | ✓✓ 实机 |
| Sony | `crs-client.dll`（Sony Crashpad 分支，PDB 路径 `ient_crs-crashpad_feature_v2.6.2\...\crs-client.pdb`） | ✓✓ 实机 |
| HTTP/WS | `winhttp.dll`、`webio.dll`、`websocket.dll`、`libcurl.dll`、`dnsapi.dll`、`ws2_32.dll`、`mswsock.dll`、`schannel.dll` | ✓✓ 实机 |
| 反作弊 | **无** `GameGuard`/`GameMon64`/`EasyAntiCheat*` 模块（本次会话） | ✓✓ 实机 |

**关键线程名**（同一 dump 的 ThreadNamesStream，type=24；70 线程）：
`HttpManagerThread`、`LibwebsocketsThread`、`ThreadedTickWebSocketThread`、`PartyNetworkingThread`、`PartyChatWorkerThread`、`PartyAudioThread`、`MuThread`、`steam_cloud`、`hang_detector`、`save_system`。

⇒ **PlayFab Party 的组网/语音/聊天线程在真实对局中处于运行状态**（不只是「盘上有 DLL」）；同时存在 HTTP 管理线程与 WebSocket 线程。
⇒ dump 为 MiniDumpNormal（无堆内存），全文 0 个域名串，**不能从 dump 直接读出主机名**。

> 复验（模块/线程提取，只读）：
> ```powershell
> $f = Get-ChildItem "$env:APPDATA\Arrowhead\Helldivers2\dumps\dump-2026-11-11-21.08.29*.dmp" | Select-Object -First 1
> python -c "import sys,struct,re;d=open(sys.argv[1],'rb').read();n,r=struct.unpack_from('<II',d,8);print([ (struct.unpack_from('<III',d,r+i*12)[0]) for i in range(n)][:20])" $f.FullName
> ```

---

## 3. 证据表 C：各网络栈的静态证据（偏移为十进制文件内字节偏移的 hex）

| # | 证据 | 文件 @ 偏移 | 观察值 | 强度 |
|---|---|---|---|---|
| C1 | Steam 网络 API（引擎网络层建在 Steam 上） | `bin\helldivers2.exe` @ `0x1739504` / `0x17394e9` / `0x173955c` / `0x17387d8` | `SteamNetworkingSockets012`、`SteamNetworkingMessages002`、`SteamParties002`、`SteamMatchMaking*` | ✓✓（handoff §12.4 已证，本轮复核偏移一致） |
| C2 | SDR 中继状态字段 | `helldivers2.exe` @ `0x173899e` | `POP relay`（同段含 `POP remote`、`FindingRoute`、`Address remote`） | ✓✓ |
| C3 | 引擎 Steam 包装源码路径 | `helldivers2.exe` @ `0x1738856` | `...\stingray\runtime\steam\c_api\c_api_steam.cpp` | ✓✓ |
| C4 | PlayFab 会话前置条件（引擎网络层原生支持 PlayFab 后端） | `helldivers2.exe` @ `0x1725af0` | `You must login to playfab before making a game session! (ignore this if you have disabled playfab in runtime)` | ✓✓ |
| C5 | 加载清单（PlayFab Party + Multiplayer 是引擎模块表成员，非巧合同目录） | `helldivers2.exe` @ `0x1704930` / `0x1704940` | `PartyWin.dll`、`PlayFabMultiplayerWin.dll`（同段：`game.dll`、`steam_api64.dll`、`libcurl.dll`…） | ✓✓ |
| C6 | PlayFab HTTP 域名后缀常量 | `bin\PartyWin.dll` @ `0x344711`；`bin\PlayFabMultiplayerWin.dll` @ `0x15ab39`、`0x15cfe1` | `playfabapi.com`（运行时以 titleId 拼成 `https://<titleId>.playfabapi.com/...`） | ✓ |
| C7 | PlayFab Party / Lobby API 版本 | `PartyWin.dll` @ `0x3257b0`（另 `XPlatCppSdk-3.60.211025` @≈`0x3446f0`）；`PlayFabMultiplayerWin.dll` @ `0x156510`/`0x15ab20` | `PlayFabParty/1.0`、PlayFab SDK 3.60.211025；`PlayFabLobby/1.0` | ✓ |
| C8 | **PlayFab LIVE titleId** | `data\settings.ini` 第 181–188 行 | `playfab = { title_ids = { LIVE = "974A9", BETA = "7C75A", DEV/CERT/BIO/TECH = "BCDDE" } }` ⇒ 候选主机 `974A9.playfabapi.com`（⚠ 未实机确认） | ✓ 离线数据 |
| C9 | EOS SDK 通道名（P2P/通知） | `bin\EOSSDK-Win64-Shipping.dll` UTF-16 @ `0xf96420` / `0xf96348` / `0xfdf318` | `com.epicgames.p2p.*`、`com.epicgames.notifications.*`、`eos-sdk:`、`Epic Online Services SDK` | ✓（仅证 SDK 内含 P2P；域名串已被处理，静态取不到） |
| C10 | 引擎 EOS 集成开关 | `data\settings.ini` 第 11–12 行 | `eos = { verbose = true }` | ✓ |
| C11 | Sony 崩溃上报（CRS/RECAP）上传 URL 模板 | `bin\crs-client.dll` @ `0x63c81` | `https://upload.recap.tools/crashrecorder/rest/recv/upload/v1/<prod_guid>/TESTTITLE?localhost`（占位 title 运行时替换；prod_guid 来自 settings.ini，已脱敏） | ✓✓ 静态 + 实机 |
| C12 | 崩溃上报启用 + prod guids | `data\settings.ini` 第 76–77 行、`win32.crs = {...}` | `crs_enabled = true`、`data_path=...\crash_data`、`prod_guid=[...]`、`capture_image = true` | ✓ |
| C13 | Sony 侧工具身份 | `bin\crs-handler.exe` @ `0xed937`、`0xe622c`、`0xe7ccc` | `https://git.ship.scea.com/bitbucket/projects/DTE/repos/crs-client-public/`（源码地址，**非**运行端点）、`Sony Interactive Entertainment ("SIE")`、`asked_user_consent`/`user declined consent` | ✓ |
| C14 | PSN 认证分支（跨平台账号） | `data\game\game.dll` @ `0x21538f0` 附近 | `InvalidSteamAuthCode`、`InvalidPSNAuthCode`、`Playfab (%s) [Platform (%s)]`、`title_ids`、`steam_player_group` | ✓ |
| C15 | HTTPS 客户端栈 | `bin\runtime.dll` PE 导入表 | `WINHTTP.dll`（引擎 HTTP(S) 客户端）；dump 另有 `libcurl.dll`/`websocket.dll`/`webio.dll` | ✓✓ |
| C16 | GameGuard 残留（非启用证据） | `bin\GameGuard\` + `helldivers2.exe` @ `0x1704b38` | 目录含 `GameMon64.des` 等；`GameGuard.ver` = `Tue Apr 22 09:37:40 2025`；exe 仅有 Lua 绑定名 `gameguard_error_check`；**2026-11 dump 无 GameGuard 模块** ⇒ 当前启动方式下未启用 | ✗（对「当前是否使用」为否证） |
| C17 | EAC(EOS) 存在 | `EasyAntiCheat\Settings.json`、`start_protected_game.exe`、服务 `EasyAntiCheat_EOS` | title=Helldivers 2、executable=`bin/helldivers2.exe`、productid 前缀 `f1637333…`；服务当前 `Stopped`；dump 无 EAC 模块 ⇒ 本轮会话非 EAC 启动 | ✓（存在）/ ⚠（是否启用） |

---

## 4. 证据表 D：运行期日志/状态（已有、只读）

| # | 来源 | 观察值 | 强度 |
|---|---|---|---|
| D1 | `%LOCALAPPDATA%\HD2ProxyStudio\logs\proxy.log`（以及 `audit.log`、`captures\index.txt`） | 出现的唯一游戏主机 = `api.live.prod.thehelldiversgame.com`（34 次）；另有 `cloudflare-dns.com` 204 次，是**引擎自身 DoH 失败日志**（`doh cloudflare-dns.com failed`），不是游戏流量，勿误判 | ✓✓ |
| D2 | `C:\Windows\System32\drivers\etc\hosts` 第 16 行 | `100.67.169.17 api.live.prod.thehelldiversgame.com # hd2proxy` | ✓✓ |
| D3 | `D:\mod\tmp\api_ip.txt` | `172.200.240.121`（该 API 主机的历史解析结果） | ✓ 数据 |
| D4 | `%APPDATA%\Arrowhead\Helldivers2\crash_data\attachments\...\crash-report.txt` | `Uploading crash report...` → `Upload returned status(200) after 3390ms`（2025-12-02，SEA 侧 RECAP 实际可达） | ✓✓（Sony 通道真实外连） |
| D5 | `%LOCALAPPDATA%\Sony Interactive Entertainment Inc\PSPC_SDK\titles\PPSA30556_00\logs\pspc_sdk_log.txt`（92 KB，2026-03-20 ~ 2026-04-14） | `Game Title ID=PPSA30556`、`Game Distribution Channel=steam`、`Env=np`；启动 `PsPcSdkCore/UI/Telemetry/VoiceChat/SttTts/AppMgr/UpdateChecker.exe`；1 条 `Connection timeout`。**当前盘上无 PsPcSdk*.exe，2026-11 未再启动** | ✓ 历史 / ⚠ 当前 |
| D6 | `data\settings.ini` 第 3–5、85 行 | 引擎网络日志/控制台日志路径：`%APPDATA%\Arrowhead\Helldivers2\network_logs\network-*.ndp`、`logs\console_*.txt`。**这两个目录当前不存在（0 文件）**，疑似仅开发/调试构建产出；运行时值得再验 | ⚠ |
| D7 | 当前 `Get-DnsClientCache` / 运行进程 | DNS 缓存无相关条目（时间已过，仅参考）；当前未运行游戏；`EasyAntiCheat_EOS` 服务 `Stopped` | 参考 |

---

## 5. 候选主机清单（A2 核心交付）

### 5.1 游戏自有 API（必须接管）

| 主机 | 证据 | 强度 | 说明 |
|---|---|---|---|
| `api.live.prod.thehelldiversgame.com` | hosts + 抓包 + `api_ip.txt` | ✓✓ 实机 | 已接管；HTTPS/443，走本机 MITM |
| 其它 `*.thehelldiversgame.com`（`telemetry.`/`status.`/`cdn.`/`patch.`/`auth.` 等） | **未发现任何明文实例**；bundle 压缩无法静态排除 | ⚠ 未证 | **A2 的最大未知点**：只要运行时出现同后缀主机，按「游戏自有」处理 |

### 5.2 游戏相关非 API 旁路（是否接管需按目的决定）

| 通道 | 主机/形态 | 证据 | 强度 | 对离线线的影响 |
|---|---|---|---|---|
| Steam 网络（联机传输） | `*.steampowered.com`、`*.steamcontent.com`、`*.steamstatic.com`、`*.steamcommunity.com`、SDR 中继（UDP 27015–27050 / 3478 / 4379–4380 等，IP 动态）、P2P 直连 | C1–C3 + handoff §12.4 + dump 模块 | ✓✓ | **属于 Steam 边界，不接管**；联机传输依赖它 |
| PlayFab API / Party | `974A9.playfabapi.com`（推导）；Party 中继/网络端点由服务端动态下发（HTTPS + WebSocket + UDP） | C6–C8 + dump 线程名 | ✓ 推导 / ✓✓ 线程在跑 | 跨平台组队/语音通道；若断网，可能登录/组队超时 → 需实测是否阻塞单人 |
| EOS（Epic Online Services） | `*.epicgames.dev` 一类（SDK 内字符串被处理，静态取不到） | C9–C10 + dump 模块 | ⚠ 未证 | 与 EAC(EOS) 可能共用；反作弊/身份通道 |
| Sony RECAP（崩溃上报） | `upload.recap.tools`（HTTPS，需用户同意，`asked_user_consent`） | C11–C13 + D4 实机 200 | ✓✓ | 只在崩溃上报时外连；与游戏运行无关，可放行或忽略 |
| Sony PSPC/PSN（跨平台账号/云配置） | 主机未静态取得（PSPC SDK 日志仅生命周期，无 URL） | C14 + D5 | ⚠ 未证 | 若 PSN 关联/跨平台开启才可能启用 |
| nProtect GameGuard | 无外连证据（文件为 2025-04 残留，dump 无模块） | C16 | ✗ 当前未用 | 可忽略 |
| NVIDIA telemetry | `NvTelemetryAPI64.dll`（驱动层，非游戏自有） | dump 模块 | ✓✓（存在） | 忽略 |

### 5.3 结论：**是否存在第二条「游戏自有」通道？**

- 第二条「游戏自有 **HTTP API**」：**静态证据为否（⚠ 未证，不能完全排除同后缀子域）**。
- 但**非 API 旁路确实存在且活跃**：PlayFab Party（✓✓ 线程运行）、Steam 网络（✓✓ 引擎网络层）、Sony RECAP（✓✓ 实机上传成功）、EOS（✓✓ 模块加载）。
- 对「离线单机」的净影响：**真正需要 mock 的仍然只有那一个 API 主机**；旁路只需要「不阻塞」——即断网/被墙时不能让 PlayFab 登录、EOS、Steam 初始化把流程卡死。这必须靠运行时实测（本轮给出方案）。

---

## 6. 运行时验证方案（供游戏运行时执行；只读、可复制）

> 原则：**抓包要在启动游戏之前开始**（DNS 查询和首批 TLS 都在登录阶段），并**同时按进程与全系统**看——只按 `helldivers2.exe` PID 会漏掉子进程/壳进程（`PsPcSdk*.exe`、`crs-handler.exe`、EAC 服务、`GameMon64.des`、`steamwebhelper.exe`）。

### 6.1 方案 A：进程级 DNS/TCP/UDP（最快，PowerShell 管理员）

```powershell
# 0) 基线
$before = @{}
Get-DnsClientCache | ForEach-Object { $before[$_.Entry] = $_.Data }
$t0 = Get-Date

# 1) 启动游戏、进主界面、打一局后，取进程树
$game = Get-Process helldivers2 -ErrorAction Stop
$pids = @($game.Id)
$pids += (Get-CimInstance Win32_Process |
          Where-Object { $_.ParentProcessId -eq $game.Id } | Select-Object -ExpandProperty ProcessId)
$pids += (Get-Process steam, steamwebhelper, PsPcSdk*, crs-handler, GameMon64, EasyAntiCheat* `
          -ErrorAction SilentlyContinue).Id | Select-Object -Unique
"pids=$($pids -join ',')"

# 2) TCP 连接（含远端 IP:端口）
Get-NetTCPConnection | Where-Object { $_.OwningProcess -in $pids -and $_.RemoteAddress -notin '127.0.0.1','::1' } |
  Select-Object LocalPort,RemoteAddress,RemotePort,State,OwningProcess |
  Sort-Object RemoteAddress | Format-Table -AutoSize | Out-String -Width 200

# 3) UDP 本地端口（Party/SDR/中继多为 UDP；**无远端信息**，远端必须靠抓包）
Get-NetUDPEndpoint | Where-Object { $_.OwningProcess -in $pids } |
  Select-Object LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize

# 4) DNS 缓存增量（无 PID 关联，靠时间窗 + 前后 diff）
Get-DnsClientCache | Where-Object { $_ -and $before[$_.Entry] -ne $_.Data -and $_.TimeToLive -gt 0 } |
  Select-Object Entry,Data,TimeToLive | Format-Table -AutoSize

# 5) 远端 IP 反查 PTR（辅助把 IP 映射回名字，SDR/PlayFab 可能无 PTR）
Get-NetTCPConnection | Where-Object { $_.OwningProcess -in $pids -and $_.RemoteAddress -notin '127.0.0.1','::1' } |
  Select-Object -ExpandProperty RemoteAddress -Unique |
  ForEach-Object { Resolve-DnsName $_ -Type PTR -ErrorAction SilentlyContinue } |
  Select-Object Name,NameHost | Format-Table -AutoSize
```

### 6.2 方案 B：dumpcap/tshark 抓 DNS + TLS SNI（最准，本机已装 Wireshark）

```powershell
& 'C:\Program Files\Wireshark\dumpcap.exe' -D            # 记下接口编号
# 抓 DNS(53) + HTTPS(443) + Steam/Party 常见 UDP 端口；分片 60s，避免单文件过大
& 'C:\Program Files\Wireshark\dumpcap.exe' -i 5 -f "udp port 53 or tcp port 443 or udp portrange 27015-27050 or udp port 3478 or udp port 4379 or udp port 4380" `
  -w D:\mod\tmp\hd2-a2.pcapng -b duration:60

# 汇总：DNS 查询/应答 + TLS SNI（= 客户端真正要连的主机名）
& 'C:\Program Files\Wireshark\tshark.exe' -r D:\mod\tmp\hd2-a2.pcapng `
  -Y "dns.flags.response==1 || tls.handshake.extensions_server_name" `
  -T fields -e frame.time_relative -e ip.src -e ip.dst -e dns.qry.name -e tls.handshake.extensions_server_name |
  Sort-Object -Unique
```

判读：**`dns.qry.name` 与 `tls.handshake.extensions_server_name` 是主机名的权威来源**；UDP 远端 IP 只能配合 PlayFab/Steam 的公开段位人工归类。

### 6.3 方案 C：内置 pktmon（无第三方依赖；需管理员）

```powershell
pktmon filter remove
pktmon filter add -p 443 -t TCP
pktmon filter add -p 53  -t UDP
pktmon start --capture --pkt-size 256 -f D:\mod\tmp\hd2-a2.etl
# …启动游戏、进主界面、打一局，再 stop
pktmon stop
pktmon etl2pcap D:\mod\tmp\hd2-a2.etl -o D:\mod\tmp\hd2-a2.pcap
# 然后同上用 tshark 过滤 SNI/DNS
```

### 6.4 判据（哪些必须接管 / 放行 / 忽略）

| 类别 | 判据 | 动作 |
|---|---|---|
| 游戏自有 API | 主机名以 `.thehelldiversgame.com` 结尾，或出现在游戏 TLS 会话中且非 Steam/PlayFab/EOS/Sony 域 | **必须接管**（hosts → 本机 443 + 证书 SAN + mock/回放） |
| Steam 边界 | `*.steampowered.com`/`*.steamcontent.com`/`*.steamstatic.com`/`*.steamcommunity.com`，SDR/P2P 的 UDP 段 | **不接管**（用户边界外，照常放行） |
| PlayFab | `*.playfabapi.com` + Party 动态中继端点（HTTPS/WS/UDP） | 先放行；若断网阻塞登录/进图 → 记录为「必须 mock 或禁用」 |
| EOS/EAC | `*.epicgames.dev` 等（实际名以抓包为准） | 放行；若离线导致启动失败 → 记录 |
| Sony RECAP | `upload.recap.tools` | 可忽略/放行（仅崩溃上报） |
| Sony PSPC/PSN | 若出现 `PsPcSdk*.exe` 外连 | 记录；是否影响进图待测 |
| 反作弊 | `EasyAntiCheat_EOS` 服务/`GameMon64.des` 进程是否出现 | 记录启动方式（EAC-on/EAC-off）后再比对 |

### 6.5 复验清单（一次运行后应产出）

1. DNS 查询名集合（含子域）→ 与「唯一 API 主机」对照；
2. TLS SNI 集合 → 确认是否有第二个 `.thehelldiversgame.com`；
3. `helldivers2.exe` 的 TCP 远端 + UDP 本地端口；
4. 进程树里是否出现 `PsPcSdk*.exe` / `crs-handler.exe` / `GameMon64.des` / EAC；
5. `%APPDATA%\Arrowhead\Helldivers2\network_logs\` 是否出现 `network-*.ndp`（若出现，它可能直接记录连接目标，是最高价值证据）；
6. 若崩溃：`crash-report.txt` 是否再次出现 `Upload returned status(200)`（验证 RECAP 通道）。

---

## 7. 风险与边界

1. **只读纪律**：本轮未改游戏目录/产品仓库/`%LOCALAPPDATA%\HD2ProxyStudio%`/hosts/证书，未启游戏、未起监听；运行时验证由用户执行。
2. **不要接管 Steam**：Steam 主机在用户边界外；对 Steam 域做 MITM 会牵连客户端更新/DRM/EAC 判定，且自建联机方案明确「传输沿用 Steam」。
3. **接管 PlayFab/EOS/Sony 的代价**：需要额外 hosts 行 + 证书 SAN + 回放/空响应策略；若这些通道被 mock 得不一致，可能引发登录超时或反作弊误判（EAC 虽主要校验文件，但联网校验策略未证）。
4. **本轮 dump 的会话是「无反作弊模块加载」的启动方式**（无 EAC/GameGuard 模块）：stock（EAC-on）启动可能多出 EOS/EAC 通道，**不能用本轮 dump 直接代表 stock 行为**。
5. **bundle 压缩**导致静态白名单不可穷举：任何「游戏自有主机只有一条」的断言都必须由运行时抓包背书。
6. **脱敏**：本文件已隐去 crash 报告中的 client/session/report GUID、账号与连接串等原值。

---

## 8. 仍需实测的点（交给游戏运行时）

| # | 未知 | 预期判据 |
|---|---|---|
| E1 | 是否存在第二个 `*.thehelldiversgame.com`（telemetry/status/cdn/patch/auth） | 运行时 DNS/SNI 出现同后缀域名 → 必须纳入接管 |
| E2 | `974A9.playfabapi.com` 是否真的被连、何时连（登录/组队/跨平台开关） | DNS/SNI 命中；关闭 `crossplay_enabled` 后再测对比 |
| E3 | PlayFab Party 中继端点形态（HTTPS/WS/UDP，动态域名） | pcap 中 Party 线程对应连接的远端与 TLS SNI |
| E4 | EOSSDK 到底为谁服务（EAC 认证？跨平台？） | 抓 `*.epicgames.dev` 命中 + 关闭 EAC 启动对比 |
| E5 | 断网/被墙时 PlayFab/EOS/Steam 初始化是否阻塞「进主界面 / 单人进图」 | 分阶段屏蔽测试，记录第一个卡住的请求/超时 |
| E6 | `network_logs\*.ndp` 是否在 release 构建产出 | 运行后目录是否出现文件；若出现优先读它 |
| E7 | stock（EAC-on）启动的额外通道 | 用 Steam 正常启动（不绕 EAC）对比模块/连接集合 |
| E8 | Sony PSPC/PSN 是否在「PSN 关联」场景启用并外连 | 出现 `PsPcSdk*.exe` + 抓 DNS；记录其对进图的影响 |

---

## 附：本轮关键复现命令

```powershell
# 1) 引擎模块/线程（实机 dump）
$d = Get-ChildItem "$env:APPDATA\Arrowhead\Helldivers2\dumps\dump-2026-11-11-21.08.29*.dmp" | Select-Object -First 1
python -c "import sys,struct,re;d=open(sys.argv[1],'rb').read();p=re.compile(rb'(?:[A-Za-z0-9_.+-]\x00){2,60}\.\x00(?:d\x00l\x00l\x00|e\x00x\x00e\x00)');n=sorted(set(m.group().decode('utf-16-le') for m in p.finditer(d)));print(len(n));print([x for x in n if re.search('party|playfab|eos|steam|gameguard|sony|crs|winhttp|libcurl|game\.dll',x,re.I)])" $d.FullName

# 2) Steam/PlayFab/EOS/CRS 字符串偏移
cd 'D:\SteamLibrary\steamapps\common\Helldivers 2\bin'
python -c "import mmap;f=open('helldivers2.exe','rb');m=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ);print([(s,hex(m.find(s))) for s in [b'SteamNetworkingSockets012',b'POP relay',b'c_api_steam.cpp',b'PartyWin.dll',b'PlayFabMultiplayerWin.dll',b'You must login to playfab']])"
python -c "import mmap;f=open('PartyWin.dll','rb');m=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ);print('playfabapi.com',hex(m.find(b'playfabapi.com')),'Party',hex(m.find(b'PlayFabParty/1.0')))"
python -c "import mmap;f=open('crs-client.dll','rb');m=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ);print(m[m.find(b'https://upload.recap.tools'):][:120])"

# 3) 配置里的 PlayFab titleId / 网络日志路径 / EOS / CRS 开关
Select-String -Path 'D:\SteamLibrary\steamapps\common\Helldivers 2\data\settings.ini' -Pattern 'network|ndp|eos|title_ids|LIVE|crs_enabled|build'

# 4) 历史运行期证据
Get-Content "$env:LOCALAPPDATA\HD2ProxyStudio\logs\proxy.log" | Select-String 'host=|doh'
Get-ChildItem "$env:APPDATA\Arrowhead\Helldivers2\crash_data\attachments" -Recurse -Filter crash-report.txt |
  ForEach-Object { Select-String -Path $_.FullName -Pattern 'Uploading|Upload returned' }
```
