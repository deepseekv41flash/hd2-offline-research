# 阶段2 联机实测：志愿者环境侦察清单（2026-11-21）

> 用途：阶段2（自建联机）的 E2 双机实验需要两位玩家。**在我们把测试包交给志愿者之前**，
> 先用这份清单收集他那边的环境信息，判断这次测试能不能做、需要提前修什么。
> 提交方式见 §6。**不需要提供任何密码、令牌、支付信息**；SteamID64 可以私下单独发。
> 我方边界（请志愿者知悉）：**不改游戏文件、不注入、不绕过反作弊（EAC 保持开启）、不连接官方服务器**；
> 只临时修改 hosts 与安装一张可随时卸载的测试证书，测试结束按脚本还原。

## 0. 为什么需要这份报告（给对方的一句话）

我们要在你和另一位玩家的机器上各改一条 hosts 记录、装一张临时证书，让游戏把「登录/大厅」请求发给我们的测试服，
然后测试两人能否进同一个大厅、同一局。改错一个环境（系统版本、游戏版本、代理、权限）这次测试就白跑，
所以先确认环境，避免浪费你的时间。

## 1. 必填项（任何一项缺失都会让 E2 无法安排）

### A. 系统与虚拟化

| 项 | 怎么拿 | 为什么要 |
|---|---|---|
| Windows 版本与内部版本（如 `Windows 11 24H2 26100.xxxx`） | `winver` 或 Win+R 输入 `winver` 截图 | EAC 对某些 Insider/预览版有已知冲突（曾导致 `0xC0000142` 启不来） |
| 是否 Insider 预览版（设置 → Windows 更新 → 预览体验计划） | 截图或说明 | 同上 |
| 是否 **虚拟机 / 云电脑 / 远程桌面 / 串流**里玩 | 直接回答 | EAC 在虚拟化/远程环境通常不可用，测试直接作废 |
| CPU / 内存 / 安装盘剩余空间 | 任务管理器 → 性能，或 `systeminfo` 摘要 | 25 GB 空间 + 8 GB 内存是游戏基本盘 |

### B. 游戏与反作弊状态

| 项 | 怎么拿 | 为什么要 |
|---|---|---|
| Steam 库中 HD2 的安装路径 | Steam → 库 → 右键 HD2 → 管理 → 浏览本地文件 | 判断是否非默认盘、是否有权限问题 |
| **游戏 buildid 与分支** | 打开 `Steam\steamapps\appmanifest_553850.acf`，贴出 `"buildid"` 与 `"StateFlags"` 两行；Steam → HD2 → 属性 → 测试版 是否为「无」 | 我的研究基线是当前零售 build；版本不一致会白测 |
| 是否装过 **mod / ReShade / 其它注入类工具** | 游戏 `bin\` 目录里是否有 `dinput8.dll`、`reshade-shaders\`、`ReShade*.ini`、额外 dll | 有的话先删/Steam 验证文件，否则反作弊与文件校验都不可信 |
| EAC 是否正常 | 游戏目录 `EasyAntiCheat\Settings.json` 是否存在；管理员 CMD 跑 `sc query EasyAntiCheat_EOS` | 反作弊必须是「真实、正常」的状态，我们不做任何绕过 |
| 最近一次能正常启动游戏是什么时候、有没有弹过反作弊报错 | 直接回答 | 排除环境本身有问题 |
| 游戏是否装在中文/空格路径、是否有杀软拦截过 | 直接回答 | 影响脚本与证书判断 |

### C. Steam 账号与联机条件

| 项 | 怎么拿 | 为什么要 |
|---|---|---|
| Steam 是否在线、能否加好友 | 给好友链接或 SteamID64（**私下发**，不要贴公开群） | 双机联机测试需要互加；大厅也可用公开大厅，但好友最稳 |
| 该 Steam 账号**是否绑定 PSN**（游戏里有没有绑过索尼账号） | 直接回答 | 登录请求体会因绑定状态不同（6 字段 vs 9 字段），我们的测试服要都接受；也影响排障 |
| 是否通过**家庭共享**玩 HD2 | 直接回答 | 家庭共享账号的联机权限/票据行为可能不同 |

### D. 网络环境

| 项 | 怎么拿 | 为什么要 |
|---|---|---|
| 宽带运营商 + 上下行速率（大致即可，如「电信 500M/50M」） | 回答或测速截图 | 联机对**上传**敏感；我们判断是否够 |
| 所在省市 | 回答（到城市即可） | 估算互相延迟 |
| **是否在用加速器 / VPN / 代理 / 公司网络** | 回答；如有，测试时必须关闭或明确告知 | 会劫持游戏流量并干扰 hosts/证书判断，是测试失败的头号原因 |
| 路由器能否开 UPnP / NAT-PMP（或是否知道自己是公网 IP / 大内网 CGNAT） | 回答「能 / 不能 / 不知道」 | 影响 P2P 打洞；打洞失败会走官方中继（不影响本测试，但要记录） |
| 防火墙 / 安全软件名称（Windows Defender、火绒、360、卡巴等） | 回答 | 可能拦截 hosts 修改、自签证书或测试服端口 |
| 是否允许临时修改 hosts 文件 | 回答 | 我们的接入方式就是 hosts + 临时证书；有回滚脚本 |

### E. 权限与配合

| 项 | 怎么拿 | 为什么要 |
|---|---|---|
| 是否有**管理员权限**（装证书、改 hosts、查服务需要） | 回答 | 没有的话只能改走「你方不用改机」的备选方案（速度慢、成功率低） |
| 是否接受安装一张**测试用根证书**（仅用于测试服域名，测试后一条命令卸载） | 回答 | 游戏用 HTTPS 连大厅服务，本地测试服必须被系统信任 |
| 可配合的**测试时间窗口**（日期 + 时段 + 时区） | 回答 | 双机测试要两人同时在；每次约 30–60 分钟 |
| 沟通渠道（QQ / Discord / 语音是否方便） | 回答 | 排障需要实时沟通 |
| 是否同意日志/截图脱敏后用于研究（不公开账号名、SteamID） | 回答 | 研究纪律要求 |

## 2. 只读采集命令（可复制，全部只读、不上传）

在 PowerShell 里逐条运行，把输出贴给我方即可（`>` 重定向保存也行）。
**不要**运行来路不明的脚本；以下命令都只读。

```powershell
# 1) 系统版本（贴出前三行即可）
[System.Environment]::OSVersion.VersionString
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').DisplayVersion
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuild

# 2) 是否虚拟机（看 Manufacturer/Model，VMware/VirtualBox/Hyper-V/KVM 字样即虚拟化）
Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model

# 3) 游戏 buildid 与安装状态（把 <Steam库盘> 换成实际路径，例如 E:\Steam）
Get-Content '<Steam库盘>:\Steam\steamapps\appmanifest_553850.acf' | Select-String -Pattern 'buildid|StateFlags|installdir'

# 4) 游戏主程序大小与日期（与我方基线比对）
Get-ChildItem '<游戏目录>\bin\helldivers2.exe', '<游戏目录>\data\game\game.dll' | Select-Object Name, Length, LastWriteTime

# 5) 是否装过注入类工具（有输出就要先清理）
Get-ChildItem '<游戏目录>\bin' -Filter 'dinput8.dll'
Test-Path '<游戏目录>\bin\reshade-shaders'
Get-ChildItem '<游戏目录>\bin' -Filter 'ReShade*'

# 6) EAC 状态
Test-Path '<游戏目录>\EasyAntiCheat\Settings.json'
sc.exe query EasyAntiCheat_EOS

# 7) hosts 现状（确认没有已存在的 HD2 相关行）
Get-Content "$env:SystemRoot\System32\drivers\etc\hosts" | Select-String -Pattern 'thehelldiversgame|playfabapi|epicgames'

# 8) 网络与代理
Get-NetIPConfiguration | Select-Object InterfaceAlias, IPv4Address, IPv4DefaultGateway
Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' | Out-Null
(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings').ProxyEnable
(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings').ProxyServer
```

## 3. 我方会怎么判定这份报告（给用户看）

| 报告结果 | 判定 | 处置 |
|---|---|---|
| 虚拟机 / 云电脑 / 远程桌面 | **✗ 不可测** | 换人/换机器 |
| Insider 预览版且与 EAC 有已知冲突 | ✗ 先修 | 退回正式版或换机器 |
| buildid 与我方基线不同 | ✗ 先统一 | Steam 更新/切回「无测试版」后重报 |
| bin\ 有 mod/ReShade 等注入物 | ✗ 先清理 | Steam「验证文件完整性」后重报 |
| 使用加速器/VPN/代理 | ⚠ 测试时必须关 | 关掉后重跑本机连通性检查 |
| 无管理员权限 / 拒绝装临时证书 | ⚠ 降级 | 只能做「志愿者自己跑后端」的备选，成功率低 |
| 家庭共享账号 | ⚠ | 与 Steam 联机/票据相关，先记录，测试时优先用主号 |
| 网络为宜 CGNAT 大内网 | ✓ 可测（发现层放公网服务器） | 数据面打洞失败由官方中继兜底，记录即可 |
| 其余项齐备 | ✓ 进入打包 | 我方出「测试包 + 交接档」交给志愿者 |

## 4. 我方会在测试包里给志愿者什么（拿到报告后）

1. **测试包**（zip）：安装脚本、回滚脚本、README（逐步截图级说明）、我方服务器地址、测试步骤卡。
2. **临时根证书**（只对测试域名生效，有效期短，测试后 `certutil -delstore` 卸载）。
3. **hosts 修改脚本**：自动备份原 hosts，测试后一键还原。
4. **采集脚本**：测试后一键导出本机日志与抓包摘要（脱敏后回传）。
5. **交接档**：写给志愿者的「做什么/不做什么/失败怎么回滚/怎么联系我们」。

## 5. 风险与回滚（请志愿者确认已知悉）

- 改动只有两处：**hosts 文件一行**、**一张测试根证书**；都有一键还原脚本。
- **不修改游戏任何文件**、不注入进程、不关反作弊；游戏启动方式与平时完全相同（Steam → 开始游戏）。
- 测试期间游戏**不会连接官方服务器**（域名被 hosts 指向我方测试服）；测试结束还原后一切如常。
- 可能遇到的临时现象：登录/大厅报错、进不了大厅、EAC 网络提示——都可回滚后恢复正常。
- 如遇 EAC 弹窗或游戏异常，先执行回滚脚本，再联系我方。

## 6. 提交方式与隐私

- 报告直接发给我方对接人（用户）转交即可；**不要**发到公开群/仓库。
- 可公开的信息：OS 版本、buildid、目录结构、网络类型、速度。
- 请**务必屏蔽**：Steam 账号名、SteamID64（如需提供请单独发）、任何令牌、聊天记录、支付信息。
- 我方在研究仓里只记录脱敏后的结论（如「志愿者：Win11 24H2 / buildid 一致 / 电信 500M」），不出现个人标识。
