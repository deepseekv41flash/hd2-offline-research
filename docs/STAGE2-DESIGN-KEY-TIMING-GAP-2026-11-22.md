# 阶段2 设计：`Account/Keys` 时序缝隙怎么解（2026-11-22）

> 现象：好友显示 `On Ship`，点"加入"**没反应**；再点一次就成功。
> 现场证据：客户端问公钥的请求比对方上报身份的那一帧**早 643 ms**，我们回
> `{"accountKeys":[]}`（18 B），客户端**静默放弃**这次加入。
>
> 相关：`STAGE2-REAL-JOIN-FLOW-2026-11-22.md`（公钥是加入门槛）、
> `STAGE2-E3-RESULT-2026-11-22.md` §10.4（缝隙记录）。

## 0. 一句话结论

**缝隙的本质不是"缓存过期"，而是"accountId 与公钥的绑定建立得太晚"。**
客户端自报公钥是在登录时（我们只知道来源 IP），而 accountId 只出现在它上报大厅的
`PUT /api/lobby` 里 —— 两者之间有几秒到几十秒的空窗。修复方向是**让绑定更早建立**，
而不是缓存旧值。

## 1. 现场证据

### 1.1 两次 miss 的时序

| 时刻 | 事件 | 结果 |
|---|---|---|
| 15:37:40.000 | 志愿者登录（新公钥已存，`real_id` 未知） | — |
| **15:37:54.403** | 我方客户端 `GET /Account/Keys?id=<志愿者>` | **18 B（空）** → 放弃 |
| 15:38:39.850 | 志愿者**再次**登录（又一把新公钥） | — |
| **15:38:56.773** | 我方客户端再次 `GET /Account/Keys?id=<志愿者>` | **18 B（空）** → 放弃 |
| 15:38:57.416 | 志愿者 `PUT /api/lobby` **← 我们在这里才学到 real_id** | 差 **643 ms** |

成功轮的对照（15:56）：两个 id 的查询都在对方 `PUT` 之后 → 返回 **114 B / 115 B**，入厅成功。

### 1.2 关键新事实：**公钥每次登录都不同**

A 机 body-shape 证据（同一账号五次登录，`publicKey` 的 sha8 全不同）：

| 时刻 | publicKey sha8 | 长度 |
|---|---|---|
| 15:07:35 | `e60d6300` | 44 |
| 15:37:40 | `c7038883` | 44 |
| 15:38:39 | `38b900c0` | 44 |
| 15:54:04 | `ef1f9d44` | 44 |
| 15:55:14 | `e7dee62f` | 44 |

⇒ 公钥是**会话级**的（很可能每次启动生成一对），**不是账号的长期属性**。

**这条直接否掉两种直觉方案**：
- ✗ "把 `real_id → 公钥` 持久缓存（长 TTL）" —— 缓存的是旧会话的 key，对端比对不上，
      和 miss 一样失败，还会把问题藏起来；
- ✗ "登录时顺手把 key 存进账号，等以后查" —— 存了，但**不知道它是哪个 accountId 的**。

## 2. 我们手上有什么、缺什么

| 时机 | 我们已知 | 我们还不知道 |
|---|---|---|
| `POST /Account/Login` | 来源 IP、本会话**新公钥**、instanceId、token | 这个客户端的 **accountId** |
| `POST /lobby/accounts` 请求 | 客户端关心的**好友 id 列表** | 它自己的 id（列表里没有自己） |
| `POST /lobby/<id>/member`（保活） | 它认为自己在哪个厅 | 它的 accountId（请求体是 `null`） |
| `PUT /lobby` | **accountId**（`players[].isHost=true`）、大厅串、资料 | —— |
| `GET /Account/Keys?id=X` | 客户端想知道 **X 的公钥** | —— |

缺口的唯一原因是：**accountId 只有一个来源（PUT），而它比登录晚**。

## 3. 候选方案

| # | 方案 | 能解决哪些 miss | 风险 | 成本 |
|---|---|---|---|---|
| **A** | **未命中时短暂等待**（1.5–2 s，50 ms 轮询） | "差 643 ms"这类**刚刚错过**（15:38:56 那次） | 低（服务端是 `ThreadingHTTPServer`，等待只占本次连接；不会阻塞全局） | 小 |
| **B** | **单未知绑定**：命中不到 X 时，若「有且仅有一个刚登录、还没上报过身份的账号」，就把 X 临时绑到它、返回**它当前这把** key | 15:38:56 与 15:37:54 **两次都能救**（当时志愿者正是唯一未知项） | 中：若 X 其实是第三者，会返回错 key → 失败（但与原来一样失败，不会更糟）；需保证"只有一个未知"才动手，且绑定可被真 PUT 纠正 | 小中 |
| **C** | **跨客户端交叉推断**：A 问 `Keys(X)`、B 问 `Keys(Y)` ⇒ B 的 id = X、A 的 id = Y（两边都问时白拿两个 id） | 两端都进入加入流程时成立 | 低（等价于 B 的双客户端特例） | 中 |
| **D** | **挖更早的 id 来源**：把 `/api/Mission/Session`、`/api/Account/ReportPosition`、`/api/Operation/*` 也纳入 body-shape 采集，看有没有请求体带自己的 accountId | 若有，则可彻底消除窗口 | 无（只读采集） | 小（采集）+ 需一轮实测 |
| E | 缓存旧 key 长期兜底 | ✗ 见 §1.2，公钥会话级 | 高（把失败伪装成成功） | — |
| F | 让客户端重试 | 改不了客户端 | — | — |

## 4. 推荐方案（A + B，D 并行做）

### 4.1 行为

```
GET /Account/Keys?id=X
  ① 查 A1：某个账号 real_id == X         → 直接返回它的当前 key
  ② 查 A2：已建立的绑定表 binding[X] → 账号 → 若该账号仍在/有新 key → 返回
  ③ 未命中 → 若启用等待：50 ms 轮询，最多 W 秒（默认 1.5 s），期间别的线程的 PUT 会补齐 ①②
  ④ 仍未命中 → 若启用单未知绑定：
        候选 = {账号 : 有 public_key 且 real_id 未知 且 最近 T 秒内登录}
        若 |候选| == 1 → 建 binding[X] = 该账号（标记 provisional，记 stats）
                          返回该账号**当前**（本次登录）的 key
  ⑤ 否则 → 返回 {"accountKeys": []}（并记 stats.keys_miss，便于量化）
```

关键点：
- 返回的永远是**该账号当前会话的 key**（登录时已更新），所以不存在"旧 key"问题；
- `binding` 只是**id → 账号**的指向，账号换会话时 key 自动跟着更新；
- 真 `PUT` 到达后 `_learn_identity_locked` 会写入权威绑定，**覆盖/并存** heuristic 绑定；
- 若候选 ≥ 2（3 人以上场景）→ 不做猜测，退回 miss（保守）。

### 4.2 配置项（profile.lobby）

```json
{
  "account_keys_wait_seconds": 1.5,
  "account_keys_single_unknown_bind": true,
  "account_keys_unknown_window_seconds": 180
}
```

### 4.3 新增统计

`keys_hit` / `keys_waited` / `keys_bind_hit` / `keys_miss` —— 部署后能直接看出缝隙是否还存在
（`/health` 的 `discovery.stats` 里同名可见；`keys_waited` = 走等待路径后命中）。

### 4.4 持久化

`binding: { "<real_id>": {"account": "<内部账号>", "at": <ts>, "provisional": bool} }`
随状态落盘（干净重启保留），并按 `binding_ttl_seconds`（默认 6 h）清理。

## 5. 验收方法

| # | 用例 | 期望 |
|---|---|---|
| 1 | 单测：未知 id + 唯一未上报账号 | 返回该账号当前 key；`keys_bind_hit` +1 |
| 2 | 单测：未知 id + **两个**未上报账号 | 返回空数组（不猜） |
| 3 | 单测：绑定后该账号重新登录（新 key） | 返回**新** key |
| 4 | 单测：真 `PUT` 覆盖 heuristic 绑定 | 以 PUT 学到的为准，不冲突 |
| 5 | 单测：等待路径（另一个线程在等待期内完成 PUT） | 返回 key；`keys_wait_hit` +1 |
| 6 | 一致性套件 C17：`Keys` 未命中时**不**返回错误 key（无候选时为空数组） | PASS |
| 7 | 实机复测：对方重启后**立刻**点加入（不等它上报） | 首次点击即成功；`keys_miss` 不增 |

## 6. 风险与取舍

1. **单未知绑定的误绑**：只在 `|候选| == 1` 时生效；误绑的后果是"给错 key"，与 miss 一样导致加入失败，
   不会造成更坏的结果；且真 PUT 到达即纠正。→ 接受。
2. **等待的延迟**：最坏给该请求加 1.5 s；只影响这一次 `Account/Keys`，客户端本来就在加载态。→ 接受。
3. **3 人以上**：候选 ≥2 时不猜 → 回到 miss；这类场景应优先走方案 D（更早的 id 来源）。→ 记录限制。
4. **公钥会话级**这一点必须写进文档，防止后人再次尝试"缓存 key"。

## 7. 实施状态

| 项 | 状态 |
|---|---|
| **A 等待**（50 ms 轮询 ≤ `account_keys_wait_seconds`，默认 1.5 s） | **✓ 已实现（2026-11-22）** |
| **B 单未知绑定**（唯一候选才绑、真 PUT 覆盖并清除错误猜测） | **✓ 已实现** |
| 统计埋点 `keys_hit` / `keys_bind_hit` / `keys_miss` / `keys_waited` | **✓ 已实现** |
| 绑定持久化（`bindings` 随状态落盘，TTL 6 h，账号消失即清） | **✓ 已实现** |
| conformance C17（Keys 未命中回 200 空数组） | **✓ 已加** |
| D（更早的 id 来源采集：Mission/Session、ReportPosition、Operation/*） | ⏳ 未做 |
| 实机复测（对方重启后立刻点加入） | ⏳ 未做（等下一轮） |

### 7.1 实现要点（与设计一致的三处修正）

1. **绝不缓存 key**：binding 只存「id → 账号」的指向，返回时从账号**当前会话**读 key；
   账号重新登录换 key 后自动生效（有单测锁定）。
2. **真相优先**：`PUT` 学到真实 id 时写权威绑定，并**丢弃该账号的其它临时猜测绑定**
   —— 猜错了不能让错 key 一直生效。
3. **保守兜底**：候选 ≥2 时不做猜测；等待只占本连接（`ThreadingHTTPServer`）。

### 7.2 离线验收（2026-11-22）

| 项 | 结果 |
|---|---|
| `pytest` | **169 passed**（新增 8 条：B 唯一候选/歧义不猜/A 等待命中/等待禁用即时/重登录新 key/错误猜测被清/绑定跨重启/现场场景验收） |
| conformance | empty / closed / error404 三策略各 **17/17**（新增 C17） |

### 7.3 部署与线上验证（2026-11-23 13:57）

- 已部署 A 机（`mock/discovery.py`、`mock/handler.py`、`profiles/coop-discovery-nginx.json`、`tools/discovery_conformance.py`），
  `systemctl restart` 后 `healthcheck.sh` **PASS**、线上一致性套件 **17/17 PASS**。
- **线上实测 B**：探针账号只登录（只有 key、未上报 real_id）→ 另一端立刻 `GET /Account/Keys?id=<未知 id>`
  → 返回该探针的 key（`live-probe-key-…`）⇒ 单未知绑定在部署环境生效。
- **新增 `/health` 的 `discovery` 诊断块**（计数 + 统计，无任何账号标识）：
  `{accounts, identities, keys_known, bindings, lobbies, tombstones, generation}` + `stats.keys_*`。
  → 下一轮复测只需读 `/health` 的 `keys_miss` / `keys_bind_hit` / `keys_waited`。
- ⚠ 线上注意：单未知绑定是**全局唯一候选**才生效；服务器上残留的「已登录未上报」探针账号会让候选 ≥2
  从而**不触发**兜底。**实机复测前应清一次发现层状态**（停服 → 删 `state/discovery-state.json` → 起服）。
- ⚠ C17 已改为契约式断言（200 + 数组 + 不回错 id），因为启用绑定后「幽灵 id 被绑到唯一未知账号」是**预期行为**。

### 7.4 仍待办

1. **实机复测**（唯一未验证项）：对方重启后**立刻**点加入 → 首次即成功；`keys_miss` 不增、
   `keys_bind_hit`/`keys_waited` 有增量。
   → **作业单已就绪**：`STAGE2-RETEST-AB-2026-11-23.md`（准备 4 步 + 实机顺序 + 判据自动化）。
   配套工具 `tools/stage2_retest.py`（`check`/`snapshot`/`judge`，全部只读）；A 机发现层已重置为全 0
   （2026-11-23 14:00），缝隙方向与「不要测错方向」已在作业单 §1 写清。
2. D（更早的 id 来源采集：`Mission/Session`、`ReportPosition`、`Operation/*` 纳入 `body_shapes.paths`）。
