# 阶段2 正式测试包（客户端脚本）

发现层已部署在服务器 A（nginx 反代形态），域名 `api.live.prod.thehelldiversgame.com`。
包内附 `ca.cer`（测试根证书）与 `ca.crl`（吊销列表）。

两条接入路线，二选一；都需要包里的 `ca.cer` / `ca.crl`。

| 路线 | 适用 | 需要的权限 | 脚本 |
|---|---|---|---|
| **A. hosts** | 志愿者能提权（推荐，**已确认可行**） | 管理员（仅改 hosts） | `install-hosts.ps1 -ServerIp <发现层IP>` |
| **B. 系统代理** | 志愿者是标准用户 | 无（只写 HKCU 代理） | `install-proxy.ps1 -ProxyHost <IP> -ProxyPort 18080` |

## 使用顺序

1. 管理员 PowerShell：`w32tm /resync`（时钟必须同步）
2. `install-hosts.ps1 -ServerIp <IP>`（B 路线换成 `install-proxy.ps1`）
3. `selfcheck.ps1 -ServerIp <IP>` —— **全绿再开游戏**
4. 按 `test-card.md` 执行（启动 Steam → 启动游戏 → 建房 → 另一位加入）
5. 结束：`collect-logs.ps1` 收日志 → `rollback-all.ps1` 一键还原

## 这个包会改什么（全部可逆）

| 改动 | 细节 | 还原 |
|---|---|---|
| hosts：**新增一行** | `<IP> api.live.prod.thehelldiversgame.com # HD2-STAGE2 <时间戳>` | 删掉带标记的行 |
| hosts：**临时注释已有 Steam 屏蔽行** | 给 `127.0.0.1 <steam 域名> #S302` 这类行加前缀 `#HD2-STAGE2-DISABLED# `（原文件先备份）；**不改行内容**，只加前缀 | 去掉前缀，恢复原样 |
| 当前用户根证书库 | 装测试根证书 `ca.cer`（用户级，不需要管理员） | `certutil -user -delstore` |
| 本机 CRL 服务 | 在 `127.0.0.1:18081` 提供 `ca.crl`（测试证书的吊销列表；普通用户即可监听） | `rollback-all.ps1` 停掉 |
| 当前用户代理（仅 B 路线） | `ProxyEnable=1` + `ProxyServer=<IP>:18080` | `ProxyEnable=0` |

**不改**游戏文件、不注入、不关/绕反作弊；游戏启动方式与平时完全相同（Steam → 开始游戏）。

## 文件

| 文件 | 用途 |
|---|---|
| `lib.ps1` | 公共函数（hosts/Steam 屏蔽行/证书/代理/CRL/时钟/TCP） |
| `install-hosts.ps1` | A 路线：注释 Steam 屏蔽行 + 写 hosts 标记行 + 装证书 + 起 CRL 服务 |
| `install-proxy.ps1` | B 路线：设当前用户代理 + 装证书（+ 起 CRL 服务） |
| `crl-server.ps1` | 最小 CRL 服务（127.0.0.1:18081，只读，Ctrl+C 停止） |
| `selfcheck.ps1` | 版本指纹 + 接入状态 + Steam 屏蔽/CRL/时钟/Steam 在线 + 环境自检 |
| `collect-logs.ps1` | 收集 hosts/Steam 状态/时钟/游戏日志并打包 |
| `rollback-all.ps1` | 一键还原（CRL + Steam 屏蔽行 + hosts + 代理 + 证书） |
| `ca.cer` | 测试根证书（用户级安装） |
| `ca.crl` | 测试证书吊销列表（CRL 服务的数据源） |
| `test-card.md` | 测试步骤卡 |

## 本地演练（我方自测，非破坏）

```powershell
.\selfcheck.ps1 -DryRun
.\install-hosts.ps1 -ServerIp 127.0.0.1 -DryRun
.\install-proxy.ps1 -ProxyHost 127.0.0.1 -ProxyPort 18080 -DryRun
.\rollback-all.ps1 -DryRun
```
`-DryRun` 只打印将要做的事，不改任何设置（不起 CRL 服务、不写 hosts）。
