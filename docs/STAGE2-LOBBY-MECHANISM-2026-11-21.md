# 联机大厅的真实机制（改写阶段2方案判断）— 2026-11-21 23:32

> 证据：游戏在场 + `tools/playfab_mitm.py` 明文抓包（`state/mitm/capture-20261121-232530.jsonl`）。
> 结论直接**改写**了 E2 之前的两条假设，必须先读本文件再看 E2 结论文档。

## 1 抓到的完整链路（单机，无志愿者）

### 1.1 大厅列表来自 **PlayFab Lobby 搜索**，不是我们的发现层

客户端打开大厅列表时发的是：

```http
POST /Lobby/FindLobbies
{"Filter":"string_key1 eq '<buildGameHash>' and number_key6 eq 0 and string_key5 eq '1'
  and number_key1 ne 4 and number_key1 ne 5 and number_key1 ne 3
  and number_key7 le 2147483647 and number_key7 ge <war时钟>
  and string_key6 ne 'NA' and string_key6 ne 'SA'
  and lobby/memberCount le 3 and number_key3 eq 801
  and number_key4 eq <planetId> and number_key5 ge 1 and number_key5 le 1",
 "Pagination":{"PageSizeRequested":50}}
```

响应里是**真实玩家的大厅**（含 `ConnectionString`）。→ 大厅列表 = PlayFab `FindLobbies` 的结果，
我们自建的 `/api/lobby/accounts` **不参与列表渲染**（这也解释了为什么探针造的"假大厅"在游戏里
永远看不到：它只存在于我们的 mock 里，不在 PlayFab）。

映射（据 SearchData 反推）：`string_key1`=build hash、`number_key3`=warId(801)、`number_key4`=planetId、
`number_key5`=难度、`number_key6`=0（未开始）、`number_key7`=war 时钟（时间窗过滤）、`string_key6`=区域。

### 1.2 加入 = **PlayFab Lobby JoinLobby**，实测成功（单机）

```http
POST /Lobby/JoinLobby
{"ConnectionString":"cv2:1b7cec90-…r-20260323|619689|kv1:…","MemberData":{},"MemberEntity":{…}}
→ 200 OK {"data":{"LobbyId":"1b7cec90-…r-20260323"}}
```

**这是一次真实加入别人的公开大厅并成功**（23:32:06）。说明：
- 我们的客户端 PlayFab 认证、Lobby 加入链路**完全可用**；
- 之前"PlayFab 必然失败"的推断被否证。

5 秒后客户端发了 `LeaveLobby`（需要现场确认：是玩家主动退出，还是加入后卡住/失败自动退出）。

### 1.3 房主侧

`CreateLobby`（AccessPolicy Public）→ `SubscribeToLobbyResource(LobbyChange)` → `GetLobby` → `UpdateLobby`
（`UpdateLobby` 会把 AccessPolicy 在 Private/Public 之间切换：进别人房前/回自己房后各切一次，
推测是"被加入时先下架自己房间"）。
Party 侧：`RequestParty` → 独立 Azure VM（实测 `4.193.214.175:30460/UDP`、DTLS、SoutheastAsia）。

## 2 对 E2 结论的改写

| 旧判断 | 新判断 |
|---|---|
| 大厅列表由自建发现层提供，加入靠发现层返回的 body | **错**：列表与加入都走 PlayFab，发现层不参与 |
| 卡点在 PlayFab JoinLobby | **不成立**：单机 JoinLobby 实测 200 |
| 发现层 body 的 accountId/成员字段是加入失败的主因 | 降级为**次要**（它只影响官方 API 侧公告/好友视图） |

现在最可能的 E2 卡点（按概率）：
1. **隐私/可见性**：E2 时房主的 PlayFab 大厅是 friends-only（或 filter 的 planet/时间窗不匹配），
   志愿者端 `FindLobbies` 根本搜不到 → 只能通过我们 mock 的公告板点进一个"PlayFab 不存在的厅" →
   JoinLobby 失败 → 卡 `Receiving Data`。
2. **Party 数据面 UDP**：即使 JoinLobby 成功，Party 网络要连房主被分配的 Azure VM（UDP/DTLS）；
   我们的网络出站 UDP 可用（房主 500 ms 连上），志愿者侧未知。
3. 我们发现层返回的 body 与官方语义不一致（次要）。

## 3 因此 E2 复测应该这样做（不需要改代码）

1. 房主在游戏内建房，**隐私 Public**，并**选定星球/任务**（让 `number_key4`/`number_key3` 有值；
   `planetId=0` 的厅会被 FindLobbies 的 `number_key4 eq <planetId>` 过滤掉）；
2. 在**同一星球/难度**下，志愿者打开大厅列表 → 应该能在 PlayFab 列表里直接看到房主的厅；
3. 双方都开着 PlayFab 抓包（房主侧用本工具；志愿者侧至少能看 JoinLobby 错误码）；
4. 若列表能看到但加入卡住 → 立刻查 Party 的 UDP/DTLS 连接（`RequestParty` 返回的 IP:UDP 端口）。

## 4 对阶段2 方案的影响（需要用户拍板）

- 如果「PlayFab 大厅列表 + JoinLobby」本身够用，那么**自建发现层对"入厅"可能完全不需要**，
  它的价值只剩：官方 API 侧的公告/好友视图、以及未来脱离 PlayFab 的可能。
- 下一步最小验证：两个**官方**客户端（用户 + 志愿者，不装我们的 mock）在同一星球 Public 建房/加入，
  看是否成功——这一步能区分"是我们的 mock 干扰"还是"PlayFab 本身/网络"。
  ⚠ 这需要志愿者，且意味着 E2 的改造方向可能要重估。

## 5 更正（2026-11-21 23:50，用户指出）

**FindLobbies 只是「公开匹配」路径**：房主在大厅列表里被路人看到时，需要选星球/任务让
SearchData 的 `number_key3`(warId)/`number_key4`(planetId)/`number_key7`(war 时钟) 有值。

但**好友联机不需要选星球**：好友在飞船上时状态显示 `On Ship`，可以直接点加入。
→ 因此「必须选星球」**不能**当作 E2 好友加入失败的原因；好友路径大概率仍走
官方 API 的大厅公告（我们的发现层：`/api/lobby/accounts` + `/member`），再进入 PlayFab `JoinLobby`。
E2 好友加入的真正卡点更可能是：
1. 两端 Steam 好友**没有同步成 PlayFab title_player_account 好友** → friends-only 大厅 `JoinLobby` 被拒；
2. 或 PlayFab/Party 成功之后的**游戏层握手**（单机加入陌生厅 10–20 s 后出现 `client_api_error_occurred`）。
