# 阶段2 发现层：API 契约与 M1 实现记录（2026-11-21）

> 用途：给阶段2 发现层（自建 co-op 后端）定**接口契约**，并记录 M1 的实现与离线验收。
> 状态：M1 雏形已完成（多客户端大厅服务 + 双客户端离线自测）；**真实客户端响应形状待 E2 校准**。
> 三态：`✓✓ 实机` / `✓ 离线+数据` / `⚠ 未证` / `✗ 否证`。

## 0. 一句话结论

发现层只需要承接 **5 个大厅端点**（登录 + 大厅 CRUD + 加入 + 列表 + 清理），其余交给回放/任务链；
其中 **2 个端点的响应形状尚未实机校准**（`PUT /api/lobby`、`POST /api/lobby/{id}/member`），
M1 已按「原样保存 + 可选回声」实现，E2 时用一次真机请求就能定死。

## 1. 端点契约（按 `mock/rules.py` 的 STATE_ROUTES + 抓包证据）

| 方法 | 路径 | 内部名 | 请求（已知/推断） | 响应（已知/未知） | 证据 |
|---|---|---|---|---|---|
| POST | `/api/Account/Login` | `account_login` | JSON 6 或 9 字段（`buildIdentifier/publicKey/platform/instanceId/token/clientVersion`[+3 个绑定账号字段]） | `{"sessionId": "<uuid>"}` | **✓✓ 实机**（B9 采集 schema；T2 实机 437 B→200 52 B） |
| PUT | `/api/lobby` | `lobby_put` | 大厅对象：`lobbyId`、`players[{slot,memberAccountId,isHost,hasInfo}]`、`memberAccountIds`、`maxMembers`、`connectionString` | **⚠ 未知**（当前默认 `200 {}`，可选回声保存后的对象） | 客户端有该请求（抓包 73 条含状态路由），响应体未采集 |
| GET | `/api/lobby/{id}` | `lobby_get` | 路径参数 = `lobbyId`（客户端有时只带 uuid 不带 `.r-…` 后缀） | 原样返回大厅对象；未知时 `200 {}` | **✓ 离线+数据**（阶段1 scenario 回放通过；`{}` 对未知不打断客户端） |
| POST | `/api/lobby/{id}/member` | `lobby_member` | body 为成员账号字符串或对象（`memberAccountId`/`accountId`/`account`/`id`） | **⚠ 未知**（当前返回更新后的大厅对象；可选 `{}`） | 客户端有该请求；响应形状未采集 |
| GET | `/api/lobby/accounts` | `lobby_accounts` | 无 | `{"accountLobbies":[{"account","lobbyId"}...],"lobbies":[<大厅对象>...]}` | **✓✓ 离线+数据**（`samples/store-season/*_api_lobby_accounts.json` 两条真实响应） |
| DELETE | `/api/lobby/clear` | `lobby_clear` | 无 | `200 {}` | **✓ 离线+数据**（阶段1 已用） |

### 1.1 `GET /api/lobby/accounts` 的真实形状（脱敏样本）

```json
{"accountLobbies": [
   {"account": "00000000000000000000", "lobbyId": "00000000-0000-4000-8000-000000000000.r-00000000"},
   {"account": "0000000000000000000"}, ...],
 "lobbies": [ ... ]}
```

- 样本来自阶段1 抓包，账号/大厅号已脱敏成 0；**同一账号条目允许缺 `lobbyId`**（没有活跃大厅时）；
- 样本里有多条旧条目（不同账号各一条）——说明服务端返回的是**全局活跃列表**，不只是自己的；
- 发现层的实现按这个结构返回：`accountLobbies` 覆盖所有活跃大厅的 owner + 自己，`lobbies` 是所有活跃大厅对象。

### 1.2 仍未知 / E2 要校准的 3 件事

| # | 未知 | 影响 | 校准方法（E2 现场） |
|---|---|---|---|
| 1 | `PUT /api/lobby` 的**响应体**是否被客户端读取 | 若客户端要求回声而我们回 `{}`，可能出现"建房后卡住" | profile 打开 `lobby.echo_put=true` 对比；用 `X-Mock-Source` 区分命中；观察客户端是否继续发 `GET /api/lobby/{id}` |
| 2 | `POST /api/lobby/{id}/member` 的**响应体** | 客机入厅后是否会拿响应更新本地状态 | 同上；必要时把应答切成 `{}`/echo 两档对比 |
| 3 | 客户端后续请求如何携带**身份**（无抓到的请求体） | 两台客户端的账号归属（见 §2.1） | 开 `body_shapes` 采集 `/api/lobby*` 的 schema（只记字段名/长度），看是否有 account/session 字段 |

## 2. M1 实现（已交付，`✓ 离线+数据`）

### 2.1 代码

| 文件 | 内容 |
|---|---|
| `mock/discovery.py` | `DiscoveryLobby`：多账号大厅服务（线程安全、幂等、TTL 过期、容量、冲突拒绝、GET 续期） |
| `mock/handler.py` | `lobby.mode=discovery` 时把 5 个大厅路由 + 登录委托给 `DiscoveryLobby`；`handle(..., client_addr=)` 新增来源地址参数 |
| `mock/server.py` | 把 `client_address` 传进 handler（账号归属用） |
| `profiles/coop-discovery.json` | 发现层 profile（`mode=discovery`、TTL 600、max 4、绑定 `0.0.0.0:443`、body_shapes 路径含 lobby） |
| `tools/discovery_probe.py` | 双客户端探针：`--self-test`（进程内起后端自测）/ `--url`（对已部署后端验活） |
| `tests/test_discovery_lobby.py` | 12 项单测：账号归属、跨客户端可见、加入幂等/容量、前缀查询、TTL/续期、冲突、清理、handler 接线 |

### 2.2 账号归属策略（当前最稳的工程选择）

1. 登录体 `instanceId` 记录到账号；
2. 无显式身份时按**来源 IP** 归属（两台机器天然不同）；
3. 显式头 `X-HD2-Account` 覆盖（我方探针/联调工具用；真实客户端没有该头）；
4. 都没有时懒建账号（保证不 500）。

> E2 若发现客户端其实带 session/account 字段，只需改 `DiscoveryLobby.account_for` 一处。

### 2.3 服务端语义（与阶段1 的差异）

| 语义 | 阶段1（单机） | 阶段2（discovery） |
|---|---|---|
| 大厅存储 | 单账号、PUT 原样存 | 多账号、同一 `lobbyId` 只允许 owner 写入（他人写 → 拒绝并计数 `conflict`） |
| 列表 | 可回放录制里的旧大厅 / `mode=empty` | **只返回活跃大厅**（TTL 内），不混入录制数据 |
| 加入 | 填充 `players` 空槽 | 同左 + **幂等**（重复加入不重复计数）+ **容量上限**（默认 4） |
| 过期 | 无 | TTL 600 s；`GET` 续期；过期后从列表和索引里一起清掉 |
| 并发 | 无锁（单客户端） | `threading.RLock` 全局锁；对外返回深拷贝 |
| 诊断 | 无 | `stats()`：登录/put/get/join/冲突/过期计数；`dump()` 脱敏快照 |

## 3. 离线验收（M1 gate）

```
$ python -X utf8 tools/discovery_probe.py --self-test
== 1. 登录（两个客户端各一次）==        [PASS] A 登录返回 sessionId
== 2. A 建房 ==                        [PASS] A PUT /api/lobby 成功
== 3. B 查看大厅列表 ==                [PASS] B 能看到 A 的大厅
== 4. B 加入大厅（两次，验证幂等）==    [PASS] B 入厅成功且响应含 B
== 5. A 看到 B，且成员不重复 ==        [PASS] A 看到 B 入厅 / [PASS] 无重复成员
== 6. A 清理大厅，B 再查应为空 ==       [PASS] 清理后大厅不可见
RESULT: PASS（双客户端发现层全流程通过）

$ python -m pytest -q
100 passed
```

## 4. 部署形态（M1 之后）

- 服务端：`mockctl.py serve --profile profiles/coop-discovery.json`（绑定 `0.0.0.0:443` + CRL `127.0.0.1:18081`）；
- 证书：沿用阶段1 的 CA/多 SAN 叶子（`mockctl cert-gen --hosts ...`），两台客户端都装同一张测试根证书；
- 发现层流量：每次 `/api/lobby*` 是几百字节 JSON，两台机器低频轮询 ⇒ 5 Mbps 出口完全够（T8d §9.5）；
- 观测：`X-Mock-Source` 响应头 + 服务日志可分辨每个请求命中的是 discovery 还是回放。

## 5. 待办（M2 之前）

1. E2 现场按 §1.2 校准三件事，并回填本档；
2. 抓包工具记录 lobby 请求体的 **sha8/字段名**（已有 `BodyShapeCapture`，profile 已配）；
3. 若客户端要求回声，把 `echo_put` 固化为 profile 默认并补单测；
4. 双机实测后，把「端到端进厅耗时」写入 M2 验收记录。
