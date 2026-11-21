# 阶段2 本地测试矩阵（不需要志愿者）— 2026-11-21

> 触发：E2 实测卡在 PlayFab 后，用户要求「把不需要志愿者的项全部做完」。
> 顺序：A4 → B6 → A1 → A2 → A3 → A5 → B7 → B8（用户后续要求先做需要上机实机的部分，
> 本文件记录**离线/基础设施**部分；B6/B7/B8 与 A3 实机验证需游戏在场，见 §6）。

## 1 A4 PlayFab 出口无认证探测（✓✓ 已证）

目的：判断「PlayFab 到底把我们挡在哪一层」——网络/出口 IP 还是应用层。

| 探测 | 结果 |
|---|---|
| DNS `974a9.playfabapi.com` | `150.171.110.68`（Azure） |
| DNS `playfabparty.com` / `974a9.playfabparty.com` | NXDOMAIN（Party 不走这个域名族） |
| 直连 `POST /Client/GetTitleData` | **HTTP 401** `NotAuthenticated` errorCode 1074（1.2 s，出口可达） |
| 经 Clash 同上 | 同样 401（代理链也通） |
| `GET /` 根路径 | 403（Front Door 通用拦截页） |
| 伪造 titleId `00000.playfabapi.com` | 仍 401（title 校验在认证之后） |

结论：**出口与网络可达没有问题**；卡点必然在应用层（PlayFab 认证/房间状态/隐私），
不是被墙或 IP 拦截。证据：`state/evidence/e2prep/playfab-probe/a4.log`。

## 2 A1 现场序列回归（✓✓ 已实现并跑通）

`tools/discovery_conformance.py`：把 E2 现场结论固化成 11 项一致性检查，
可进程内跑（无网络）或对**已部署后端**跑（E2 前置检查）。

覆盖：登录 / 新账号无厅 / 建房对端可见 / **加入方写入真实 accountId** /
**owner 旧 body 重发不抹成员** / `numLobbyMembers` 正确 / 重复加入幂等 /
抢注他人厅被拒 / clear 后回收 / 未知厅语义（empty 或 closed 两种契约）/ accountLobbies 索引。

```bash
python tools/discovery_conformance.py                     # 进程内
python tools/discovery_conformance.py --url https://api.live.prod.thehelldiversgame.com \
    --connect-to 100.67.88.219:443 --host api.live.prod.thehelldiversgame.com \
    --insecure --unknown-policy closed                     # 经中继验活
```

实测：进程内 11/11、**线上（A 机）11/11 PASS**，结果落在
`state/evidence/e2prep/conformance-live-20261121.json`。单测：`tests/test_discovery_conformance.py`。

## 3 A2 旧厅粘滞的 P0 实现（✓✓ 已部署 + 演练 PASS）

按 `docs/STAGE2-DESIGN-STALE-LOBBY-2026-11-21.md` 实现并部署：

| 项 | 实现 | 验证 |
|---|---|---|
| P0-1 未知/已关厅**终态** | `DiscoveryLobby` 增加 tombstone 表（left/expired/conflict/server_reset，保留 30 min）+ `resolve()` 三态；handler 按 `lobby.unknown_lobby` 返回关闭态 body（lobbyId/players=[]/numLobbyMembers=0/state=0） | 单测 8 项（`tests/test_discovery_lifecycle.py`）+ 线上 C10 PASS |
| P0-2 状态持久化 | `lobby.persist` JSON 原子落盘（大厅/账号/真实 id/tombstone/generation）；优雅退出写 `.clean` 标记；**非干净重启**把上一代大厅全部转 `server_reset` tombstone | 干净重启厅保留 ✓；SIGKILL 后旧厅变终态 ✓ |
| P0-3 重启演练门禁 | `deploy/discovery/restart_drill.sh`（Phase1 干净重启保状态、Phase2 SIGKILL 必须给终态）；`healthcheck.sh` 支持 `DRILL=1` 调用 | A 机实测 **PASS** |

附带修复：`_ExclusiveHTTPServer.allow_reuse_address` 改为平台相关（POSIX=True）。
原因：E2 深夜 `systemctl restart` 连续三次 bind 失败（TIME_WAIT 残留）导致 crash loop；
POSIX 下 SO_REUSEADDR 不会放宽「同一端口只能有一个 LISTEN」的纪律，Windows 下语义不同仍保持 False。

## 4 A3 PlayFab 明文抓包链路（✓✓ 基础设施 + curl 验证）

`tools/playfab_mitm.py`：本地 HTTP 代理（`127.0.0.1:18080`）
- `--inspect 974a9.playfabapi.com` 的 CONNECT 做 TLS 终止，明文请求/响应写 `state/mitm/capture-*.jsonl`；
- 其他 host 隧道：**本机/hosts 指过来/内网 → 直连**，其余 → 经 Clash，保证切系统代理不打断其它程序；
- 自签 CA（含 **CRL DP**，`127.0.0.1:18082/ca.crl`）——Windows Schannel 默认查吊销，缺 CRL 会
  「revocation status is unknown」直接拒链（实测踩过）；
- `tools/system_proxy.ps1 -Action Set|Get|Restore`：HKCU 系统代理切换 + 备份 + bypass 合并（含 127.*、中继 IP）。

curl 全链路验证：经 MITM 访问 PlayFab 得到 **401 明文**并落盘；`api.live` 自动直连中继返回 200；
外网 host 经 Clash 正常。CA 已装入 `CurrentUser\Root`（thumbprint `11CD5AA…`，5 年）。

## 5 A5 API 契约回填（部分完成，待真实 body）

已从 A 机 body-shape 得到 `PUT /api/lobby` 的 23 个字段与取值形状
（`lobbyId` 47 字符、`connectionString` 107 字符、`players[4]` 每项 8 字段…），
并确认 `member` 请求体是 `null`（4 B，sha `74234e98`）。
**完整值**（尤其 `connectionString` 是否真实可用）要等游戏在场时用 A 机的
`HD2_DUMP_BODIES=/opt/hd2-discovery/state/lobby-bodies` 抓真 body 后补完。

## 6 仍需游戏在场的项（B6/B7/B8 + A3 实机）

| 项 | 已备好的工具 |
|---|---|
| B6 单机加入失败迭代 | `tools/fake_lobby.py`（baseline/no-conn/garbage-conn/empty-players/num-mismatch 五个变体） |
| B7 旧厅持久化落点 | `tools/snapshot_client_state.ps1`（文件 SHA256 快照 + 注册表；已存基线 `snapshot-before-game.json`） |
| B8 关闭态响应验证 | A 机已部署 `unknown_lobby=closed`，`systemctl restart` 即可制造"旧厅变终态"现场 |
| A3 实机 | MITM + 系统代理已就绪；游戏启动后 PlayFab 流量自动落盘 |

## 7 测试与门禁现状

- `pytest`：**145 passed**（新增 lifecycle 8 项、conformance 3 项）
- 线上一致性：11/11 PASS
- 重启演练：PASS（干净/非干净两相）
- 研究仓改动尚未提交（见 `git status`）

## 8 实机捕获补充（2026-11-21 23:24-23:25，无志愿者）

游戏在 23:23 启动、23:25 退出，期间真实客户端完成了「登录 → 查大厅（112 个 id）→ 用旧 id rejoin 失败 →
用同一 id 重建大厅」，A 机 body dump 抓到 **真实 `PUT /api/lobby` body（1350 B）** 与
**`POST /api/lobby/accounts` body（2637 B，`{"ids":[...]}`）**。

关键发现（详见 `docs/STAGE2-DISCOVERY-API-CONTRACT-2026-11-21.md` §6）：
1. `connectionString` = `cv2:<lobbyId>|<6 位>|kv1:<32B key>` —— 客户端**本地生成**的会话描述符，
   我们可以合成合法形状（这为"不依赖 PlayFab 也能给对端一个可连描述符"提供了可能）。
2. `accounts` 请求体是 `{"ids":[...]}`（112 个账号）——发现层当前忽略该字段，需改为按 ids 过滤。
3. `memberAccountIds` 里允许 `null`；`privacyMode` 实测出现 0/1/2。

**B7 结论（本地文件部分）**：启动前/后 136 个文件快照 diff 只有 `user_settings.config`（及存档副本）
变化，**本地文件里没有旧 lobbyId**（全目录 grep `a75760fe` 无命中）→ 旧厅状态来自 **PlayFab 云侧**，
不是本地持久化。这与"重开游戏才清掉旧厅"的现象一致（重开 = 新 PlayFab 会话）。

**事故记录**：23:24 我方重启 MITM 时有约 90 秒空窗，正好覆盖该客户端启动，
期间它的 PlayFab 请求会打到没有监听的代理端口（客户端随即退出）。教训：抓包期间不要热重启 MITM，
或先把系统代理恢复再重启。
