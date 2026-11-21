# 阶段2 正式测试包（客户端脚本）

两条接入路线，二选一；都需要包里附带的 `ca.cer`（测试根证书，**用户级安装**、可一键卸载）。

| 路线 | 适用 | 需要的权限 | 脚本 |
|---|---|---|---|
| **A. hosts** | 志愿者能提权（推荐） | 管理员（仅改 hosts） | `install-hosts.ps1 -ServerIp <发现层IP>` |
| **B. 系统代理** | 志愿者是标准用户 | 无（只写 HKCU 代理） | `install-proxy.ps1 -ProxyHost <IP> -ProxyPort 18080` |

## 使用顺序

1. `install-hosts.ps1`（A）或 `install-proxy.ps1`（B）
2. `selfcheck.ps1 -ServerIp <IP>`（B 路线加 `-RouteProxy`）—— **全绿再开游戏**
3. 按 `test-card.md` 执行（启动游戏 → 建房 → 另一位加入）
4. 结束：`collect-logs.ps1` 收日志 → `rollback-all.ps1` 一键还原

## 安全边界

- 只改三处：**hosts 中带 `# HD2-STAGE2` 标记的行**（原文件先备份）、**当前用户代理设置**、**当前用户根证书库**；
- **不修改游戏文件、不注入、不关闭/绕过反作弊**；游戏启动方式与平时完全相同（Steam → 开始游戏）；
- 回滚：`rollback-all.ps1`；hosts 原文件备份在 `backup\hosts-*.bak`。

## 文件

| 文件 | 用途 |
|---|---|
| `lib.ps1` | 公共函数（备份/还原、证书、代理、TCP 探测） |
| `install-hosts.ps1` | A 路线：写 hosts 标记行 + 装用户级证书 |
| `install-proxy.ps1` | B 路线：设当前用户代理 + 装用户级证书 |
| `selfcheck.ps1` | 版本指纹（buildid/SHA256）+ 接入状态 + 环境自检 |
| `collect-logs.ps1` | 收集 hosts/备份/游戏日志并打包 |
| `rollback-all.ps1` | 一键还原（hosts + 代理 + 证书） |
| `ca.cer` | 测试根证书（由发现层 `deploy/discovery/install.sh` 生成，随包分发） |
| `test-card.md` | 测试步骤卡 |

## 本地演练（我方自测，非破坏）

```powershell
.\selfcheck.ps1 -DryRun
.\install-hosts.ps1 -ServerIp 127.0.0.1 -DryRun
.\install-proxy.ps1 -ProxyHost 127.0.0.1 -ProxyPort 18080 -DryRun
.\rollback-all.ps1 -DryRun
```
`-DryRun` 只打印将要做的事，不改任何设置。
