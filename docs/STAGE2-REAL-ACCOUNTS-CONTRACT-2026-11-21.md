# 真实官方 API 的"好友在线/可加入"契约（全通基线抓包）— 2026-11-21 23:52

> 条件：hd2proxy 引擎 `mode=passthrough`（不改写），hosts 指向本机 100.67.88.219，
> 客户端登录**真实官方服务器**，PlayFab 仍走本地 MITM。
> 证据：`state/evidence/e2prep/real-official-api/`（4 份 `/api/lobby/accounts` 响应、FriendsV2、index.txt）。

## 1 好友列表刷新时客户端实际发的请求

- `/api/lobby/accounts`（**约每 30–90 s 一次**，与"列表每隔一段时间刷新"一致），请求体是
  `{"ids": ["<18~20 位 accountId>", ...]}`（此前在我们 mock 上截到 112 个 id，与本轮 `accountLobbies` 数量一致）。
- `/api/FriendsV2/{Request,Recent,Block}`：其中 **Block 只有 2 条**（真实好友条目形状）：
  ```json
  {"totalEntries":2,"entries":[{"blockedTime":1715954812,"name":"…","platformAccountId":"12377519709835467151","sourcePlatform":1,"experience":1337}]}
  ```
  → 好友/黑名单条目的形状是 `name + platformAccountId + sourcePlatform + experience`。
- **没有任何"列出我的好友"的接口**：好友名字/在线来自 Steam 侧；游戏用上面的 id 列表去问
  "这些人有没有厅"。

## 2 `/api/lobby/accounts` 的真实响应（18427 B，关键契约）

```json
{
  "accountLobbies": [ {"account":"<id1>","lobbyId":"<uuid>.r-20260323"},
                      {"account":"<id2>"},
                      ... 112 条（**每个请求的 id 一条**，无厅时不带 lobbyId） ],
  "lobbies": [ { ...厅 body... } × 8 ]
}
```

实测：112 个 id → 112 条 `accountLobbies`（9 个账号有厅）→ `lobbies` 去重后 8 个厅。

**这就是"好友显示 On Ship / 可加入"的来源**：客户端拿到 id→lobbyId 映射后，把有厅的好友显示成可加入，
点击时用 `lobbies[].connectionString` 走 PlayFab `JoinLobby`。

## 3 真实 lobby body 比客户端 PUT 多 7 个服务端字段

客户端 PUT 我们的是 23 个字段；真实响应是 **30 个**，多出：

| 字段 | 例子 | 含义（推测） |
|---|---|---|
| `numSessionMembers` | 0 | 已进任务的成员数 |
| `planetType` | — | 星球类型 |
| `operationType` / `operationSeed` / `seed` | — | 行动类型与种子 |
| `operationMissionStates` | — | 任务状态列表 |
| `completedObjectives` | 0 | 已完成目标数 |
| `transitionTarget` | — | 状态迁移目标 |

真实厅样例：`privacyMode 0`、`numLobbyMembers 4`、`planetId 200`、`state 2`、`players[]` 带
`playerName/isHost/memberAccountId/sourcePlatform/…`。

## 4 对我们 mock 的直接结论（E2 卡点的最可能解释）

我们的 `DiscoveryLobby.accounts()` 目前**忽略 `ids`**，`accountLobbies` 只给请求者自己一条。
→ 客户端拿不到"好友的 id → lobbyId"映射，好友列表里就无法把对方显示成"On Ship / 可加入"，
E2 时志愿者只能从大厅列表硬点，链路与真实好友路径不一致。

**修复方案（明确、可离线完成）**：`accounts(account, ids)` →
1. 对请求里每个 id 输出一条 `accountLobbies`（有厅则带 lobbyId）；
2. `lobbies` 返回这些账号所在厅的 body（去重）；
3. 厅 body 补齐 7 个服务端字段（用真实样本做默认值，客户端 PUT 值优先）。

## 5 其它

- 真实 `/api/lobby/accounts` 响应 18 KB，而我们的 mock 只有 2–3 KB —— 量级差就是"缺了 per-id 映射"。
- 抓包目录：`D:\mod\tmp\capture-full-passthrough-20261121`（每响应一个 json + index.txt）。
- 回滚：`hd2proxy.exe uninstall` + `taskkill /F /IM hd2proxy.exe` + `tools/system_proxy.ps1 -Restore`。
