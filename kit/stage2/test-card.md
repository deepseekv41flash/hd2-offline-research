# 阶段2 双机测试卡（E2/M2 · 发现层已部署到公网）

> **2026-11-21 更新**：发现层已部署（服务器 A，nginx 反代形态，域名 `api.live.prod.thehelldiversgame.com`）。
> 本卡同时给志愿者与主持方。两人同时在线，全程约 30–60 分钟。游戏照常从 Steam 启动，EAC 保持开启。
>
> **本版新增的三件事（都是为了让游戏能正常登录/联机）**：
> 1. `install-hosts.ps1` 会**临时注释** hosts 里已有的 Steam 屏蔽行（`#S302` 等），结束时 `rollback-all.ps1` 原样恢复；
> 2. 会在本机 `127.0.0.1:18081` 起一个**只读 CRL 服务**（测试证书吊销列表；取不到它 Windows 会拒绝 TLS 握手）；
> 3. 测试前需要**同步系统时间**（`w32tm /resync`），否则证书校验会莫名失败。

## 0. 测试前 10 分钟（双方各自）

| # | 动作 | 通过标准 |
|---|---|---|
| 1 | 管理员 PowerShell 跑 `w32tm /resync`（**时钟必须同步**） | 无报错；`w32tm /query /status` 的 Leap 指示符为 0/正常 |
| 2 | 以管理员运行 `install-hosts.ps1 -ServerIp <发现层IP>` | 看到「已临时注释 N 条 Steam 屏蔽行」「hosts 已写入」「CRL 服务已启动」 |
| 3 | 跑 `selfcheck.ps1 -ServerIp <发现层IP>` | 全部 `[OK]`（尤其：Steam 屏蔽行=0、CRL 可取、时间已同步、Steam 在运行） |
| 4 | 断开 Radmin VPN / 任何 VPN、加速器 | `selfcheck` 里 VPN 项通过 |
| 5 | **先启动 Steam 并确认已登录**（不要用离线模式），再正常启动一次游戏，确认 EAC 不弹错、能进主界面 | 进到主界面后退出 |

> 注意：`install-hosts.ps1` 起的 CRL 服务窗口**测试期间不要关**；`rollback-all.ps1` 会自动停掉。

## 1. 启动（主持人先）

1. 主持人启动游戏，进入**飞船/主界面**；
2. 打开游戏内社交/大厅界面，**创建大厅**（Public 或 Friends only 均可，建议先 Friends only）；
3. 记录：创建时刻、界面有无报错、`%APPDATA%\Arrowhead\Helldivers2` 下最新日志文件名；
4. 观察 30 s：大厅是否稳定存在（不闪退、不自动关闭）。

## 2. 加入（志愿者）

1. 志愿者的游戏在主界面 → 社交界面应能看到主持人的大厅（好友列表或大厅浏览器）；
2. **加入** → 观察：是否进入同一大厅、成员列表是否出现两人；
3. 记录：加入时刻、是否有报错弹窗（截图）、多久进入大厅。

## 3. 进局

1. 主持人选择任务 → 邀请/等待志愿者 → 两人都进入同一任务；
2. 记录：任务加载耗时、是否出现不同步/掉线、能否完成结算；
3. 结算后回到飞船，确认大厅还在。

## 4. 需要记录/回传的东西

| 项 | 谁 | 怎么给 |
|---|---|---|
| 主持方：发现层后端日志（服务器 `/opt/hd2-discovery/state/logs/mock.log`） | 主持方 | 附在报告里（含 `PUT /api/lobby`、`GET /api/lobby/accounts` 等请求） |
| 双方：`collect-logs.ps1` 生成的 zip | 双方 | 各自发回 |
| 关键时刻截图（创建大厅/加入/进局/报错弹窗） | 双方 | 发回 |
| 时间线（每个动作的本地时间，精确到秒） | 双方 | 写在回执里 |
| 抓包（主持人侧，可选） | 主持方 | pcap + `tools/analyze_e2_capture.py` 输出 |

## 5. 失败止损顺序（按链路排查）

1. **游戏进不去 / 一直连不上后端** → 先确认 `selfcheck` 里 CRL 与时间两项仍 OK；看 `hosts.txt` 里我们那行 IP 对不对；
2. 游戏能进主界面但**看不到大厅** → 看后端日志有没有 `PUT /api/lobby` / `GET /api/lobby/accounts` 请求；
3. 能看到但**加入失败** → 看 `Party/RequestParty`、`Lobby/CreateLobby` 请求是否 200（PlayFab 云侧）；
4. 加入成功但**进不了局** → 看数据面：抓包判定走 Steam SDR 还是 PlayFab Party（`tools/analyze_e2_capture.py`）；
5. 任一步游戏异常/报错 → 先 `rollback-all.ps1` 还原（**它会恢复 Steam 屏蔽行、停 CRL 服务**），再收集日志，不带改过的环境继续试。

## 6. 测试后（必须）

1. 双方 `collect-logs.ps1` → 发回 zip；
2. 双方 `rollback-all.ps1` → 确认输出：CRL 服务已停、Steam 屏蔽行已恢复、hosts 标记行已删、证书已卸；
3. 回执模板：`结果（通过/失败）+ 卡在哪一步 + 双方时间线 + 日志 zip + 截图`。
