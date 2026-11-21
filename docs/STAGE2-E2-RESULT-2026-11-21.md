# 阶段2 E2 结果（含 23:30 后的机制改写与单机抓包）— 2026-11-21

> 本文是 E2 的最终记录。**先读 `docs/STAGE2-LOBBY-MECHANISM-2026-11-21.md`**：23:32 的单机抓包
> 推翻了 E2 当晚"卡在 PlayFab JoinLobby"的结论。

## 1 三态判定总表

| 项 | 判定 | 证据 |
|---|---|---|
| 登录 / 73 端点回放 | ✓✓ 实机 | 两台客户端多次登录，0 非 200；`state/evidence/e2prep/e2-mock-watch.log` |
| 自建发现层部署（nginx 反代） | ✓✓ 实机 | 公网 /health、双客户端探针、业务站回归正常 |
| 官方 API 大厅公告（`PUT /api/lobby`） | ✓✓ 实机 | 真实 body 1350 B 抓取（`state/evidence/e2prep/real-bodies/`） |
| 发现层成员加入（`POST /member`） | ✓✓ 实机 | 22:09/22:12 志愿者 200；23:57 后我方成员写入真实 accountId |
| **大厅列表来源** | ✓✓ 实机（改写） | **PlayFab `/Lobby/FindLobbies`**，与自建发现层无关 |
| **加入机制** | ✓✓ 实机（改写） | **PlayFab `/Lobby/JoinLobby`**；单机实测多次 `200 OK` |
| 数据面 | ✓✓ 部分 | 单机加入后 Party 网络连接成功（500 ms）、DTLS 认证成功、**与远端 P2P 直连 27 ms** |
| 双人完整入局 | ⚠ 未证 | 单机加入在 10–20 s 后由客户端自行退出（`client_api_error_occurred` 4104/4379） |
| 线上一致性门禁 | ✓✓ | `tools/discovery_conformance.py` 对 A 机 11/11 PASS |
| 重启韧性 | ✓✓ | 干净重启保状态、SIGKILL 后旧厅给终态（restart_drill PASS） |

## 2 E2 当晚结论为什么被推翻

旧结论："发现层/大厅链路全通，卡在 PlayFab JoinLobby"。
依据是"member 之后房主没有收到成员变更、客户端每 90 s 重试、整段没有 Party/UDP 流量"。

23:32 起的单机抓包表明：
- 客户端的大厅列表来自 PlayFab `FindLobbies`，**不是**我们的 `/api/lobby/accounts`；
- 客户端加入走 PlayFab `JoinLobby`，**单机实测连续多次 200 OK**；
- 加入后 Party 网络连接成功，并与房主建立了 **P2P 直连（27 ms）**。

因此"发现层返回体/成员字段导致 PlayFab 加入失败"这条因果链不成立；发现层在入厅路径上**几乎不参与**。
（那晚志愿者"卡 Receiving Data"更可能是：房主的 PlayFab 大厅没出现在志愿者 `FindLobbies` 的结果里——
过滤器要求 `number_key4 eq <planetId>`、`number_key3 eq 801`、时间窗等，房主在"未选任务"状态建房时
这些键缺失/为 -1，导致大厅对别人**不可见**；志愿者从我们 mock 的公告板点进一个 PlayFab 不存在的厅，
自然只能卡住。）

## 3 单机加入实验（B6 的替代实验，已完成部分）

| 观测 | 结果 |
|---|---|
| `FindLobbies` 搜索 | 返回真实玩家大厅（含 connection string） |
| `JoinLobby` | **200 OK**，连续多次不同厅均成功 |
| Party 网络 | `client_connected_to_network` Succeeded，500–1657 ms |
| DTLS 认证 | Succeeded |
| P2P | `client_join_with_p2p_connectivity_completed` ErrorCode 0，直连延迟 27 ms |
| 之后 | 10–20 s 内 `client_api_error_occurred`（4104 / 4379），随后 `LeaveLobby` |
| 房主自建厅 | `CreateLobby`/`RequestParty`/`Subscribe`/`GetLobby`/`UpdateLobby` 全 200 |

## 4 现在最可能的双人卡点（按概率）

1. **大厅可见性**：房主建房时未选星球/难度 → `FindLobbies` 过滤掉 → 志愿者看不到；
2. **游戏层握手**：PlayFab/Party 都通，但入局前还需要游戏自身的会话数据（与 `connectionString`、
   `state`、`serverStartTime` 等相关），单机加入陌生厅 10–20 s 后被客户端放弃，说明**握手在游戏层失败**；
3. **Party 代理机型**：P2P 直连已成功，说明 UDP/DTLS 出站可用；若志愿者侧 UDP 受限会退化为 relay，
   但那是"慢"不是"卡死"。

## 5 下一步（需要志愿者，按新机制）

1. 房主：银河地图**选中星球/任务** → 建房（Public）；
2. 志愿者：在同一星球的大厅列表里找房主的厅（不再依赖我们 mock 的列表）；
3. 两端都开 PlayFab 抓包（房主用 `tools/playfab_mitm.py`；志愿者至少记录 `JoinLobby` 结果）；
4. 若 `JoinLobby` 200 且 Party 连接成功但入局失败 → 查 `client_api_error_occurred` 的具体 ErrorDetail
   （4104/4379）对应的 Party/游戏层含义，这是目前唯一未解释的一环。

## 6 产出物（可复用）

- 工具：`tools/discovery_conformance.py`、`deploy/discovery/restart_drill.sh`、`tools/playfab_mitm.py`、
  `tools/system_proxy.ps1`、`tools/fake_lobby.py`、`tools/snapshot_client_state.ps1`、`tools/net_watch.ps1`、
  `tools/clash_watch.ps1`、`tools/e2_watch.py`
- 测试：`tests/test_discovery_lifecycle.py`、`tests/test_discovery_conformance.py`、`tests/test_discovery_lobby.py`（共 145 passed）
- 文档：本文件、`STAGE2-LOBBY-MECHANISM`、`STAGE2-PLAYFAB-FLOW`、`STAGE2-LOCAL-TESTS`、
  `STAGE2-CAPTURE-RUNBOOK`、`STAGE2-DESIGN-STALE-LOBBY`、契约档 §6
- 服务器 A 仍在线（`/opt/hd2-discovery`，状态持久化 `state/discovery-state.json`）。
