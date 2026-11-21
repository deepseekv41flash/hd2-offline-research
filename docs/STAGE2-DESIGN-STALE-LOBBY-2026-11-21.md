# 阶段2 发现层设计修订：旧厅粘滞（stale lobby stickiness）

- 日期：2026-11-21（E2 实测当晚）
- 状态：设计提案（未实现）；P0 项为下一版必做
- 证据来源：`state/evidence/e2prep/e2-mock-watch.log`、E2 现场操作记录

## 1 现象（✓✓ 实机）

1. **后端重启（内存态清空）后，客户端粘住旧厅**：客户端继续 `GET /api/lobby/<old_id>.r-20260323`，
   我们回 `200 {}`（2 字节，`discovery:lobby_get:unknown`）；客户端不退出该厅，Social/大厅界面持续
   转圈，无法正常重新建房；**只有完整重开游戏**才恢复（重启后端无用）。
   实测轮询节律约每 10–15 s 一次，两个旧 id（`84717906…` / `4ba1ebd8…`）交替出现。
2. **客户端重启也会带出旧厅 id**：某次仅重开客户端、未恢复后端状态时，仍出现对早已不存在的厅轮询
   → 说明"当前所在一厅"的 id 被客户端**持久化**在自己的状态里。
3. **客户端有显式离开语义**：`DELETE /api/lobby/clear`（body `null`）今晚被调用 3 次
   （21:46:31 / 22:00:06 / 22:08:25，均来自志愿者侧），我们已实现为 `discovery:lobby_clear`
   → 客户端离开时服务端**能**知道；粘滞不是"客户端从不通知"，而是"服务端状态消失后无法给出终态"。

## 2 归因（四条，按影响排序）

| # | 归因 | 说明 |
|---|---|---|
| A | **未知大厅的响应语义错误** | 回 `200 {}`。客户端无法区分「这个厅已经不存在」与「厅还在但数据空/正在加载」，于是进入等待/重试，永不收敛。 |
| B | **状态易失（无持久化）** | 大厅、账号、account→lobby 索引全在内存。`systemctl restart` 即全部清空；客户端世界与服务端世界从此分叉。 |
| C | **身份不稳定** | 账号原由来源 IP 合成（`7656119…`），重启即丢映射。今晚已改为「从客户端自己的 `PUT /api/lobby` 学真实 accountId」，但学习结果仍在内存。 |
| D | **重启没有"仪式"** | 服务端无 drain/close 流程：既不把旧厅标为 closed，也不通知或拒绝；客户端在"服务端已不存在"的世界里继续轮询。 |

共同点：**我们把发现层当"回放/回声中间层"，而它在多人场景下必须是一个有生命周期语义的权威服务端。**
所有客户端可能持有的状态，服务端都要有权威副本 + 明确的终态回答。

## 3 下一版设计

### P0-1 未知大厅必须回答「终态」，不再回 `{}`

- 保留 **tombstone 表**：`lobby_id -> {closed_at, reason}`（reason ∈ `left` / `host_left` / `expired` /
  `server_reset` / `conflict`），TTL 建议 ≥ 30 min（远大于客户端 90 s 重试窗口）。
- `GET /api/lobby/<unknown>` 命中 tombstone → 返回**明确的关闭态**，二选一（需校准，见 §5）：
  - 方案 a：`200` + 关闭态 body：`{"lobbyId": <id>, "state": <closed 值>, "players": [], "numLobbyMembers": 0}`
  - 方案 b：API 错误码（4xx/业务错误体），让客户端走错误分支退出该厅
  未命中 tombstone（纯未知 id）→ 同样按关闭态回答，并记一条 `unknown_lobby_get` 计数。
- 触发 tombstone 写入的地方：`DELETE /api/lobby/clear`、TTL 过期回收、`PUT` 冲突拒绝、
  服务端启动时发现上一代状态不可恢复（见 P0-2）、host 离开。
- **验收**：客户端在该响应后应自动退出大厅/回到大厅列表；不再需要重开游戏。

### P0-2 状态持久化（跨重启）

- 用 SQLite（`state/state.db`，已在 systemd `ReadWritePaths` 内）落盘：
  `accounts(real_id, profile, ip→account)`、`lobbies(body, owner, updated)`、
  `account_lobbies`、`tombstones`、`generation`。
- 写策略：每次变更同步写（数据量极小：个位数大厅）+ 关停时快照；`ExecStop` 走一次干净 flush。
- 启动策略：
  1. 加载状态；
  2. 若上次是**非干净退出**（flag 文件缺失）→ 无法保证一致性 → `generation += 1`，
     把旧代所有大厅写入 tombstone（reason=`server_reset`），客户端收到终态而非空对象；
  3. 若干净退出（我们主动重启）→ 直接沿用状态，客户端轮询无感。
- **验收**：`systemctl restart` 后，正在轮询的客户端**无需重开游戏**，界面恢复正常。

### P0-3 「重启演练」进部署门禁

- `deploy/discovery/healthcheck.sh` 增加一轮 drill：起一个合成客户端轮询某个大厅 → `systemctl restart` →
  断言重启后第一次 `GET` 拿到的是**继续态或终态**（绝不能是 `200 {}`），且合成客户端能重新建房。
- 离线侧加 pytest 用例：进程内起停 `MockServer`（随机端口）模拟"轮询中重启"，断言上述契约。

### P1-1 生命周期语义补齐（显式错误，而不是静默 200）

- `PUT /api/lobby`：非 owner 覆盖他人厅 → 明确错误（现状：`None`→客户端看到什么需校准）；
  owner 更新 → 合并成员/人数（今晚已做 `numLobbyMembers` 刷新）。
- `POST /api/lobby/<id>/member`：区分 `unknown` / `closed` / `full` / `privacy_refused` 四类拒绝原因，
  日志与响应都带原因（现状只有 `rejected`）。
- `DELETE /api/lobby/clear`：删除本账号所在厅 → 写 tombstone（P0-1）。
- TTL 过期：写 tombstone 而非静默 pop。
- 另：确认是否还有未实现的厅相关端点（本轮实测只观察到 `PUT /api/lobby`、
  `GET /api/lobby/<id>`、`POST /api/lobby/<id>/member`、`GET|POST /api/lobby/accounts`、
  `DELETE /api/lobby/clear` 五个；以抓包全量端点表复核）。

### P1-2 客户端侧「重置厅状态」工具（运维兜底）

- 目标：把"必须重开游戏"（≈2 min）降为"点一下工具"（≈5 s）。
- 做法：定位客户端持久化该状态的落点（候选：`%APPDATA%\Arrowhead\Helldivers2\saves\`、
  客户端 settings/缓存文件），用「建房前后文件 diff」定位 key，kit 增加
  `reset-lobby-state.ps1`（备份 → 只改该 key → 校验）。
- 注意：这是兜底，不能替代 P0-1/P0-2。

### P2 epoch/generation 与契约校准

- 每个响应带内部 `generation`（自定义 header 或 `accountLobbies` 内字段），我方工具据此判断
  "服务端状态被重置过"，从而给操作者**明确提示**（而不是神秘卡死）。
- 用真实官方会话抓包校准 §5 的未知项；校准后把 `200 {}` 从全链路彻底移除。

## 4 验收清单（下一版退出条件）

- [ ] 服务端重启后，正在轮询的客户端不重开游戏即可恢复（含 host 与 joiner 两侧）。
- [ ] `GET /api/lobby/<unknown>` 永不返回 `200 {}`；命中 tombstone 时返回终态。
- [ ] 离开 / TTL 过期 / 冲突拒绝 三条路径都会写 tombstone，且客户端收到终态。
- [ ] 部署门禁跑重启 drill 通过；pytest 覆盖"轮询中重启"。
- [ ] 客户端重置工具可用（可选，P1）。

## 5 需实机校准的未知项（⚠ 未证）

1. 真实服务端对**不存在的大厅**返回什么？（HTTP 码 / body 形状）—— 需在官方会话里制造一次
   "厅已关闭后再 GET"，或从未收录的 4xx 响应里找证据。
2. 客户端收到"关闭态"后是否**自动退出该厅**？（P0-1 的方案 a/b 哪个有效，取决于此）
3. 客户端把"当前厅 id"持久化在哪个文件/键？（P1-2 依赖）
4. `PUT /api/lobby` 冲突/被拒时，客户端期待什么响应？今晚只观察到成功回放。
5. 客户端是否还有其他厅生命周期调用（如 leave/kick）未被我方观察到 —— 需抓包全量端点复核。

## 6 与今晚已修 bug 的关系

今晚修的是**同类问题的另一个面**：`POST /api/lobby/<id>/member` 请求体为 `null`，我们原先把合成的
`7656119…` 写进 `players[]`，加入方看到"厅里不是我"→ 每 90 s 重试。修复为「从客户端自己 PUT 的真实
accountId 学习并写入 + 刷新 numLobbyMembers」。
两者的共同根因一致：**服务端缺少权威身份与权威生命周期**。下一版应把二者统一到同一份
"账号/大厅/终态"模型里，而不是各自打补丁。

## 7 实现状态（2026-11-21 深夜，已部署 A 机）

| 项 | 状态 | 证据 |
|---|---|---|
| P0-1 tombstone + 终态响应 | ✅ 实现并部署 | `mock/discovery.py::resolve/_remember_closed_locked`、`mock/handler.py::_closed_lobby_body`；线上 C10 PASS；`tests/test_discovery_lifecycle.py` |
| P0-2 状态持久化（JSON 原子落盘 + clean 标记） | ✅ 实现并部署 | 干净重启厅保留、SIGKILL 后旧厅变终态（A 机实测） |
| P0-3 重启演练门禁 | ✅ 实现并在 A 机 PASS | `deploy/discovery/restart_drill.sh`、`healthcheck.sh DRILL=1` |
| 附带：TIME_WAIT 导致重启 crash loop | ✅ 修复 | `_ExclusiveHTTPServer.allow_reuse_address` 平台相关（POSIX=True） |
| P1-1 member 拒绝原因细分 | ⬜ 未做 | 现在仍统一 `rejected` |
| P1-2 客户端重置厅状态工具 | 🔄 工具已备（`tools/snapshot_client_state.ps1`），落点待 B7 实机定位 | — |
| P2 generation 下发 | 🟡 内部已有 generation 统计，未对外暴露 | — |

仍待校准的未知项见 §5（真实服务端回包、客户端收到关闭态的行为），B8 实验会回答第 2 条。

## 8 实机 A/B：`200 关闭态` vs `HTTP 404`（2026-11-21 23:40，结论：404 更好）

客户端在飞船里一直轮询一个已关闭的旧厅 `a75760fe`（A 机日志每 ~15 s 一次）。现场做了 A/B：

| 策略 | 客户端行为 |
|---|---|
| `200` + 关闭态 body（lobbyId/players=[]/numLobbyMembers=0） | **继续每 15 s 轮询**，永不停止（整晚持续） |
| `HTTP 404` + `{"error":"lobby not found"}` | **23:40:32 收到 404 后立即停止轮询**（此后 60 s 内 0 次请求） |

→ 设计结论修订：**未知/已关闭大厅应回 HTTP 错误（404），而不是 200 关闭态 body**。
客户端的真实服务端很可能就是 4xx（它能正确识别并停止重试）。本仓库 profile 默认值已改为
`unknown_lobby: "error:404"`（`profiles/coop-discovery-nginx.json`），P0-1 的"终态 body"仅保留作对照。
