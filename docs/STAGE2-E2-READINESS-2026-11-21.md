# E2 就绪记录（2026-11-21 21:24）—— 含一项网络侧新发现与绕过方案

> 目的：E2（今晚 21:40–23:00）前的双向环境就绪状态、实测证据、以及一项**影响我方机器接入方式的网络侧发现**。
> 三态：✓✓ 实机；✓ 离线/数据；⚠ 未证。

## 1. 双方就绪状态

| 侧 | 状态 |
|---|---|
| 发现层（服务器 A，nginx 反代） | ✓✓ `active`；公网 `/health` 200；B→A 直连 200；已开 `--log-headers`（HDR 行只记字段名+长度，不记值） |
| 志愿者（DESKTOP-QJ0SO7U） | ✓✓ 包 12 个文件 SHA256 全部与我方分发一致；ADMIN=True/canary PASS；A 路线可执行；等时间窗 |
| 主持方（本机） | ✓✓ selfcheck **14/14 全绿**；CA 已装；CRL 18081 在跑；时间已同步（Leap 0，21:10:48 对时）；hosts 已指向本机中继 |
| 时间窗 | 今晚 **21:40–23:00**（证书 11-23 20:32 到期） |

## 2. 新发现：本机所在网络拦截「游戏域名 → 非官方 IP」的 TLS

主持方机器直连 A 时 TLS 握手被 RST，逐项排除后确认是**网络侧按 SNI + 目标 IP 拦截**：

| 实验 | 结果 | 排除的假设 |
|---|---|---|
| 业务域名（dsh/maibot，DNS→A）TLS 打 A | ✓ 通过 | 服务器/证书/端口问题 |
| `api.live...`、`mpsqosprod.playfabapi.com`、`9749.playfabapi.com`（DNS→Azure）TLS 打 A | ✗ **RST** | —— |
| 同 `api.live...` SNI 打真实 Azure 端点 | ✓ 通过 | 「域名被封」 |
| 反向不一致（DNS→A 的域名打 Azure） | ✓ 通过 | 「通用 DNS 一致性校验」 |
| 明文 HTTP(Host=测试域名) 打 A:443 | ✓ 400（nginx 正常应答） | 「TCP/HTTP 层被封」 |
| 经 B 的 SSH 隧道做同样的 TLS（SNI 不变） | ✓✓ 200 + mock JSON | 服务器侧 SNI 过滤 |
| 1.1.1.1 / 8.8.8.8 :443 TCP | ✗ 超时 | ——（受限网络特征） |

**判定**：本机所在网络（校园/企业网关类）对**游戏/私服类域名指向非官方 IP** 的 TLS 连接做 RST 拦截；
同理，志愿者侧未出现该问题（其报告已实测 A:443 200）。这条对阶段2 有直接影响：**主持方不能直连 A**。

## 3. 绕过方案（已实机验证 ✓✓）

本机中继 + SSH 隧道，让网关只能看到加密 SSH：

```
游戏 --hosts: api.live -> 100.67.88.219--> tools/e2_tunnel.py:443
     --(paramiko direct-tcpip 经服务器 B)--> 服务器 A:443 (nginx -> mock)
```

- 工具：`tools/e2_tunnel.py`（独立进程运行，脱离 DSH 会话；PID 见下；日志 `state/evidence/e2prep/tunnel.log`）
- hosts：`100.67.88.219 api.live.prod.thehelldiversgame.com # HD2-STAGE2-TUNNEL`（阶段1 的 `# hd2proxy` 行已加前缀暂停，备份 `hosts.stage2prep-bak` / `hosts.stage2prep2-bak`）
- 端到端验证：`curl.exe https://api.live.prod.thehelldiversgame.com/health`（无 `-k`，证书校验开启）→ 200 mock JSON
- 影响：A 侧看到的我方来源 IP = **服务器 B 的公网 IP**；账号归属仍可区分（志愿者 = 其宽带 IP，我方 = B 的 IP）
- 回滚：停中继进程 → 恢复 hosts 备份 / `rollback-all.ps1`（客户端侧）

> 该方案是**测试期临时设施**：E2 结束后撤掉中继、恢复 hosts、卸证书与 CRL 服务。
> 长期结论（写进阶段2 报告）：若正式产品要面向受限网络用户，需要「可配置的加密转发/中继」选项，或改用不被该策略覆盖的接入形态。

## 4. E2 期间主持方检查清单

1. 中继进程在监听（`netstat -ano | findstr 100.67.88.219:443`）、CRL 18081 在听；
2. 后端日志 `tail -f /opt/hd2-discovery/state/logs/mock.log`：关注 `PUT /api/lobby`、`GET /api/lobby/accounts`、`POST /api/lobby/*/member` 与 HDR 行；
3. 志愿者 `selfcheck` 全绿后我再建房（Friends only）；
4. 失败按链路止损：发现层请求 → PlayFab 大厅（Party/RequestParty）→ 数据面（Steam SDR / PlayFab Party）；
5. 结束后：双方 `collect-logs.ps1` + `rollback-all.ps1`；我方下载 mock.log 与 body-shape 证据、停中继、写 E2 报告。
