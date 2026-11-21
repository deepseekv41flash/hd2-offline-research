# 阶段2 志愿者预检 + 脚本审计：结论与处置（2026-11-21）

本文汇总 2026-11-21 收到的三份反馈：**人工回答**、**预检报告 v1.1**、**预检包审计报告 v1.0→v1.1**，
以及我们对每一项的判定与后续动作。个人标识（Steam 好友链接、用户名、本机路径）一律不入库。

## 1. 人工回答（脱敏）

| 问题 | 回答 | 对阶段2 的影响 |
|---|---|---|
| Steam 账号绑定 PSN？ | **没绑过** | 登录体为 **6 字段未绑定** 形态（B9 已证）；我方 mock 已同时兼容 6/9 字段 |
| HD2 是家庭共享吗？ | **不是** | 票据/联机权限按自有账号，测试可信度 ✓ |
| 可用时段 | **每日 14:00–16:59 / 20:00–23:59**（北京时间） | 排期容易；注意证书 2 天有效期 |
| 能否断开 VPN / 正常启动游戏 / 不动已有 Steam 屏蔽行 | **能** | 前两项 ✓；第三项已被新发现推翻（见 §2.3），改为「允许脚本临时注释并原样还原」 |

## 2. 预检报告 v1.1（关键判定）

报告来自脚本 v1.1（志愿者按审计建议改过，机器侧 `ADMIN=True`、`CANARY_RAN=True`）：

| 项 | 结果 | 判定 |
|---|---|---|
| 管理员权限 | **True** | ✓ **hosts 路线确定可行**（此前的唯一硬缺口消失） |
| hosts canary | 写入成功 + SHA256 还原一致 | ✓✓ 改机能力实测通过 |
| 用户级根证书库 | 可写 | ✓ 装测试证书不需要管理员 |
| 游戏版本 | buildid `26568081`；`helldivers2.exe` / `game.dll` SHA256 与本机逐字节一致 | ✓ 双方同构建 |
| bin dll | 15 个，与基线一致；仅 `ReShade.log` 卸载残留 | ✓ 无注入 |
| 网络 | 以太网直连、Radmin VPN 已断、IE/WinHTTP 代理均直连、防火墙全开、IPv6 1 张网卡启用 | ✓ 干净 |
| **hosts 现状** | 161 条有效行，其中 **11 条 `127.0.0.1 <steam域名> #S302`**，含 **`login.steampowered.com`** | ⚠ **E2 阻塞项** → 已解决（§2.3） |
| **系统时间** | w32time Running/Manual、源 `time.windows.com`，但 **Leap=3 未同步、当前源 Local CMOS Clock** | ⚠ TLS/证书会莫名失败 → 测试前 `w32tm /resync` |
| UAC | `EnableLUA=1`、`ConsentPromptBehaviorAdmin=0` | ✓ 能提权 |

### 2.1 顺带确认：上一轮侦察的「3 条 #S302」是漏报

上一轮侦察报告写「hosts 有 3 条生效中的 Steam 屏蔽行」，本次预检（含审计修正的正则）实测 **11 条**。
原因：旧采集脚本的 hosts 匹配正则只覆盖 `thehelldiversgame|playfab|epicgames|steamcommunity|steamstatic|steamcontent`，
漏掉 `login.steampowered.com`、`*akamaihd.net`、`*akamaized.net` 等 8 条。**结论：以预检报告的 11 条为准。**

### 2.2 时钟问题的根因（审计 P0#2）

`00.bat` 里 `chcp 65001` 后，PowerShell 按 UTF-8 解码原生命令输出，而 `w32tm.exe` 输出是 GBK（OEM 936）
→ 整段乱码 → 旧的 `-match '源|上次成功'` 一条都不中，时间同步状态**静默消失**。
修正：按 `HKLM\...\Nls\CodePage\OEMCP` 解码原生输出（`lib.ps1` 的 `Get-TimeSyncStatus` 已按此实现）。

### 2.3 hosts 里的 Steam 屏蔽行（E2 阻塞项，已定处置）

`127.0.0.1 login.steampowered.com #S302` 会让 Steam 客户端登录请求出不去；联机测试需要 Steam 在线。
这些行不是我们写的（来源待志愿者确认），但**测试期间必须临时失效**。

处置（已落进客户端测试包）：
- `install-hosts.ps1` 自动把**生效中的 Steam 域名屏蔽行**加前缀 `#HD2-STAGE2-DISABLED# `（先整文件备份）；
- 只加前缀、不改行内容；`rollback-all.ps1` 去前缀原样恢复；
- 原「人工回答」第 5 题「不改动 hosts 里已有的 Steam 屏蔽行」的说明**作废**，交接档已改为「允许脚本临时注释并还原」。

## 3. 预检包审计报告（v1.0 → v1.1）的采纳情况

审计方（志愿者侧，本机非管理员实跑 + 逐行静态核对）指出我们的 v1.0 预检脚本有 3 处会给出**错误结论**、
4 处误报/漏报、2 处设计问题。逐条判定：

| # | 级别 | 问题 | 我方处置 |
|---|---|---|---|
| 1 | P0 | hosts 正则漏掉全部 Steam 屏蔽行（报 0 条、不告警；最致命是 `login.steampowered.com`） | **采纳**；与 §2.3 一起成为测试包必查项 |
| 2 | P0 | 时间检查在中文 Windows 静默失效（chcp/UTF-8 vs GBK） | **采纳**；`Get-TimeSyncStatus` 按 OEMCP 解码 |
| 3 | P0 | hosts 写入失败时仍打「已完整还原」PASS（finally 无条件回写+校验） | **采纳**（后续 v1.1 预检包重写时必须带 `$wrote` 标志；当前测试包不写 canary，不受影响） |
| 4 | P1 | `ReShade*` 一律算 mod（`ReShade.log` 误报） | 采纳为规则：只认注入类 dll/ini/`reshade-shaders`/`*.patch_*`，`ReShade.log` 单独记信息 |
| 5 | P1 | `Get-Service EasyAntiCheat_EOS` 硬编码（非 EOS 渠道服务名不同） | 采纳：枚举 `*EasyAntiCheat*` 服务 + 驱动 |
| 6 | P1 | `netsh winhttp show proxy` 只取首行（拿到表头） | 采纳：折叠整段输出，区分 Direct / 真代理 |
| 7 | P1 | 按 ANSI 读 `appmanifest`，`HELLDIVERS™ 2` 乱码 | 采纳：`[IO.File]::ReadAllText(..., UTF8)` |
| 8 | P1 | `00.bat` 的 `-File %~dp0...` 未加引号，路径含空格直接失败 | 采纳：走 PowerShell 自提权，参数由 PS 拼引号 |
| 9 | P1 | 无 PAC / 环境变量代理 / CFA / UAC / IPv6 / 端口探测 | 采纳（后续 v1.1 预检包重写） |
| 10 | P2 | 基线硬编码：Steam 一更新全员 FAIL | 采纳：支持同目录 `baseline.txt` 覆盖 |
| 11 | P2 | bin 无 dll 白名单 | 采纳：内置 15 个基线 dll，多出来判 WARN |
| 12 | P2 | 发出的包里 `02-人工回答.txt` 是已填副本（含个人好友链接） | 采纳：分发包只放空白模板；本次原件不入库 |

**结论**：审计结论成立，我方 v1.0 预检脚本确有会误导的结论；后续如再发预检包，必须按上表重写（**backlog**，
当前不阻塞 E2——预检已拿到关键答案：管理员权限、hosts 状态、时钟、版本）。

**同时得到的教训（已固化为测试）**：`kit/stage2/*.ps1` 必须带 UTF-8 BOM（PS 5.1 中文依赖它）；
本轮改脚本时丢过 BOM，已加 `tests/test_kit_scripts.py` 防回归（缺 BOM / 出现 NUL 字节直接测试失败）。

## 4. 由此新增的客户端要求（已落进 `kit/stage2`）

| 要求 | 实现 |
|---|---|
| Steam 屏蔽行临时注释 + 原样恢复 | `lib.ps1: Disable-SteamBlockLines/Restore-SteamBlockLines`；`install-hosts.ps1` / `rollback-all.ps1` 调用 |
| 本机 CRL 服务（叶子 CDP=`http://127.0.0.1:18081/ca.crl`） | `crl-server.ps1`（TcpListener，普通用户即可）+ `Start-CrlServer/Test-CrlEndpoint` |
| 时钟同步检查 | `Get-TimeSyncStatus`（OEMCP 解码）；`install-hosts.ps1` 与 `selfcheck.ps1` 都会提示 |
| Steam 在线检查 | `selfcheck.ps1` 检查 `steam` 进程 |
| 排障信息收集 | `collect-logs.ps1` 增加 `w32tm-status.txt` / `hosts-steam-state.txt` / `crl-state.txt` |

## 5. 未决 / 后续

1. **预检脚本 v1.1 canonical 化（backlog）**：志愿者侧已有可用的 v1.1，但未回传脚本；下次需要预检时按 §3 表重写并入库（含 `baseline.txt` 覆盖、`$wrote` 标志、空白模板）。
2. **`collect-env.ps1` 同款盲区**：审计提到我方旧采集脚本 hosts 正则同样漏行；如仓库内再使用该脚本需同步修正（当前仓库无此文件，属历史分发物）。
3. **Steam 屏蔽行来源**：仍建议志愿者说明是谁加的（不影响测试，但影响「为什么要还原」的解释）。
4. **PSN 绑定状态**：志愿者未绑 → 6 字段；我方账号绑定状态不同也可以（mock 兼容两种，E2 顺带校验）。
