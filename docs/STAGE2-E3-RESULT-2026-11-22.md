# 阶段2 E3 结果：双人全流程跑通（2026-11-22）

> **结论：阶段2 的双人协作端到端打通。** 双向加入成功、公钥交换成功、两人进入同一任务、
> 结算回船，全场 **458 个请求 / 456×200 + 2×202 / 零错误**。
>
> 本轮关键前提是修掉 E2 的根因（`GET /Account/Keys` 公钥交换，见
> `STAGE2-REAL-JOIN-FLOW-2026-11-22.md`），并且在测试中发现一条**新的部署硬约束**：
> 房主大厅隐私必须设 **公开 / Public（`privacyMode=0`）**。
>
> 脱敏：本文所有他人 accountId / 公网 IP 均已掩码；未掩码原始证据在 gitignored 的 `state/evidence/e3prep/`。

## 0. 验收判定（三态）

| # | 能力 | 结果 | 证据 |
|---|---|---|---|
| 1 | 两端登录、发现层接管官方 API | ✓✓ 实机 | 458 请求全 2xx；`X-Mock-Source` 全部为 `discovery:*`/`exact` |
| 2 | **公钥交换**（E2 根因） | ✓✓ 实机 | 双方 `GET /Account/Keys` 均返回 **114 B / 115 B**（E2 时是 `200 2B` 空） |
| 3 | **用户 → 加入志愿者的大厅** | ✓✓ 实机 | 15:15:20 双向 Keys → 用户 `member` 对端厅 **200/1237 B（2 人厅体）** |
| 4 | **志愿者 → 加入用户的大厅** | ✓✓ 实机 | 15:17:23 双向 Keys → 志愿者 `member` 我方厅 **200/1237 B** |
| 5 | 房主感知并回写 2 人厅 | ✓✓ 实机 | 两次都在加入后数秒内出现 2 人 `PUT /api/lobby`（1237 B 回包） |
| 6 | **双人进同一任务** | ✓✓ 实机 | 15:23:37 `POST /api/Mission/Session` **两次、相隔 40 ms**，分别来自两端（1912 B / 1752 B） |
| 7 | 结算回船 | ✓✓ 实机 | `Operation/Mission/end` 1094 B → **200/1617 B**（结算模板回填），随后 `Operation/Abandon` |
| 8 | PlayFab Party 数据面细节 | ⚠ 未证 | Party 走 UDP 到 Azure，本机不经 mock，本轮无抓包（不影响"跑通"结论） |

## 1. 环境（本轮实测）

- 本机（主持方）：`hosts → 100.67.88.219`（SSH 中继经服务器 B）→ 服务器 A nginx → mock；研究 CA 装入用户根；本地 CRL `127.0.0.1:18081`。
- 志愿者：`hosts → 106.52.201.42`（直连 A），同一研究 CA + 本机 CRL（E2 的包原样复用，**未重发包**）。
- 两端来源 IP 不同 → mock 按来源 IP 归属账号；随后从各自 `PUT /api/lobby` 学到**真实 accountId**。
- 详见 `STAGE2-E3-READINESS-2026-11-22.md`（含踩坑：命令行验证必须 `--noproxy '*'`）。

## 2. 成功链路 A：用户 → 加入志愿者的大厅（15:15:19–15:15:32）

| 时刻 | 方向 | 帧 | 说明 |
|---|---|---|---|
| 15:15:19.874 | 志愿者 → mock | `GET /Account/Keys?id=1189…3967` → **115 B** | 他取我方公钥 |
| 15:15:20.273 | 用户 → mock | `GET /Account/Keys?id=2231…6623` → **114 B** | 我取他公钥 |
| 15:15:20.556 | 用户 | `DELETE /api/lobby/clear` | 清掉自己的厅（准备入厅） |
| 15:15:20.745 | 用户 | `POST /api/lobby/8b8697e7…/member` → **200 / 1237 B** | **入厅成功**，回包是 2 人厅体 |
| 15:15:22.238 | 志愿者 | 同一厅 `member` 保活 → 1237 B | 房主侧也看到 2 人 |
| 15:15:32.281 | 志愿者（房主） | `PUT /api/lobby` 1405 B → 1237 B | 房主回写 2 人厅 |

## 3. 成功链路 B：志愿者 → 加入用户的大厅（15:17:21–15:17:30）

与 A 完全同构（镜像方向）：

| 时刻 | 方向 | 帧 |
|---|---|---|
| 15:17:21.986 | 用户 → mock | `GET /Account/Keys?id=2231…6623` → **114 B** |
| 15:17:23.645 | 志愿者 → mock | `GET /Account/Keys?id=1189…3967` → **115 B** |
| 15:17:23.940 | 志愿者 | `DELETE /api/lobby/clear`（清自己的厅） |
| 15:17:23.963 | 志愿者 | `POST /api/lobby/be4fa672…/member` → **200 / 1237 B** |
| 15:17:30.184 | 用户（房主） | `PUT /api/lobby` 1405 B → 1237 B（回写 2 人厅） |

**从拿到对端公钥到入厅成功：318 ms**（15:17:23.645 → 23.963）；房主回写 2 人厅：入厅后 **6.2 s**。

## 4. 任务阶段（15:18:39–15:24:18）

| 时刻 | 来源 | 帧 | 说明 |
|---|---|---|---|
| 15:18:39.311 | 用户（房主） | `POST /Operation/Create` 301 B → 60 B | 选任务 |
| 15:19:50.930 | 用户 | `POST /Operation/Mission/Start` 190 B → 52 B | 开始任务 |
| 15:20:15.436 | 用户 | `POST /Operation/Mission/SetMaxRewards` → 200/2 B | — |
| 15:20:31 / 15:20:46 / 15:21:51 / 15:22:16 / 15:23:21 | 双方 | `member` 保活 → 1448/1457 B | 厅体随任务进行增长 |
| 15:23:30.561 | 用户 | `POST /Operation/Mission/end` 1094 B → **200/1617 B** | 结算（模板回填） |
| **15:23:37.076** | **用户** | `POST /Mission/Session` 1912 B → 202 | **双人同任务证据 ①** |
| **15:23:37.116** | **志愿者** | `POST /Mission/Session` 1752 B → 202 | **双人同任务证据 ②（相隔 40 ms）** |
| 15:24:18.811 | 用户 | `POST /Operation/Abandon` 27 B → 200/2 B | 收尾 |

## 5. 本轮新定论

### 5.1 公钥交换是"能不能入厅"的开关（E2 根因已闭环）
同一对客户端、同一后端：E2 里两次 `Account/Keys` 都回 `200 2B`（`{}`）→ 卡加载；
本轮回 **114/115 B** → 入厅成功。这是**因果级**对照，不再是推断。

### 5.2 `member` 是保活，不是加入动作
`POST /api/lobby/<id>/member` 按 ~20–90 s 周期反复出现（双方都有），请求体恒为 4 B（`null`）。
真正的"加入"发生在 PlayFab `JoinLobby`（本地不可见）；但**加入成功可以从 mock 侧读出**：
`member` 回包从 1 人（1184 B）变成 2 人（1237 B）。

### 5.3 部署硬约束：房主大厅隐私必须是 **Public（`privacyMode=0`）**
本轮第一次尝试（志愿者 → 加用户，用户厅 `privacyMode=1`）**失败**：志愿者拿到我方公钥后
`DELETE clear`，**但从未对我方厅发出 `member`** → 卡在 PlayFab `JoinLobby`。
把用户厅隐私改为 **0** 后，同一操作**成功**。
推断：`privacyMode=1`（仅好友）会让 PlayFab 大厅 `AccessPolicy=Friends`，而
**Steam 好友 ≠ PlayFab 好友**，非 PlayFab 好友被拒。
→ **自建发现层场景下，房主必须选 Public**。（写进部署文档与测试卡）

### 5.4 大厅 body 随状态增长，字段全部来自客户端 PUT
观测到的 `PUT /api/lobby` 体积/回包：`1184`（1 人）→ `1237`（2 人）→ `1340` → `1448`（任务开始）
→ `1457` → `1647`（任务中）。任务期 `planetId`/`seed`(96 字符) 等字段由客户端写入，服务端只补默认值。
→ 印证 §2 的契约结论：**服务端不需要造这些字段，只要原样存、按 ids 回显**。

### 5.5 双向成功指纹与真实官方环境同构
本轮两个方向的成功时序（Keys → 清厅 → `member` 拿 2 人厅体 → 房主回写）
与 2026-11-22 凌晨在真实官方服务器上抓到的成功链路（`STAGE2-REAL-JOIN-FLOW`）**完全一致**。

## 6. 本轮发现的两个 mock 缺陷（**已修并部署，2026-11-22 15:29**）

| # | 现象 | 影响 | 建议 |
|---|---|---|---|
| 1 | **`member` 首帧早于 `PUT`** 时被拒：15:09:21、15:15:50 各一次 `lobby_member:rejected`（回 `{} 2B`） | 客户端第一次保活可能先于它自己的 `PUT` 到达；被拒后客户端状态可能不一致（首次失败的候选原因之一） | `join()` 对未知厅改为**首接触即建占位厅**（owner=调用方），后续 `PUT` 合并 |
| 2 | 同上，新厅第一帧必被拒一次 | 噪声，可能诱发重试 | 与 #1 同一修法 |

### 6.1 修法（已实现）

`mock/discovery.py`：

- **`join()` 对未知厅不再返回 `None`**，改为 `_placeholder_lobby_locked()` 造**占位厅**
  （`provisional=True`，owner=调用方、成员=调用方、`state=1`），回一个「自己在厅里」的厅体；
- **`put()` 允许真房主接管占位厅**：同一 `lobbyId` 被另一账号写入时，若既有厅是 `provisional`
  则**丢弃占位内容直接接管**（`stats["takeover"]++`），否则仍按串号 `conflict` 拒绝（原行为保不变）；
- `provisional` 标志随状态持久化（干净重启不会把占位厅升格为权威）；
- 新增统计 `provisional` / `takeover`。

验证：

```
pytest                                       157 passed（+4 条新测试）
一致性套件（进程内）                          15/15 PASS（新增 C15）
A 机线上（--connect-to + cafile）             15/15 PASS
A 机 healthcheck.sh                          PASS
A 机重启后状态保持                            2 个真实账号 + 公钥 + 厅 全在（干净重启保状态）
```

边界：占位厅按 TTL（600 s）自然过期；`member` 里出现的**陈旧/串号厅**会短暂造出占位厅，
但不会覆盖任何已确认的大厅（takeover 只对 provisional 生效）。

## 7. 证据与复现

| 内容 | 路径 |
|---|---|
| 会话全量 mock 日志（去 HDR/SHAPE） | `state/evidence/e3prep/e3-mock.log` |
| 会话 nginx access 日志 | `state/evidence/e3prep/e3-access.log` |
| 双向成功指纹 | `state/evidence/e3prep/success-both-directions-20261122.log` |
| 首轮成功（用户加志愿者） | `state/evidence/e3prep/success-join-20261122-1515.log` |
| 实时跟随器抓取 | `state/evidence/e3prep/e3-mock-watch.log` |
| 服务器侧原始日志 | A 机 `/opt/hd2-discovery/state/logs/mock.log`、`/www/wwwlogs/hd2-discovery.access.log` |

关键命令：

```bash
# 按时间/端点筛会话（A 机）
awk '/^15:1[5-9]:|^15:2/' /opt/hd2-discovery/state/logs/mock.log | grep -vE 'HDR|SHAPE'
# 看公钥是否真下发（>20 B 才算下发；{"accountKeys":[]} 是 18 B）
grep 'Account/Keys' /opt/hd2-discovery/state/logs/mock.log | grep -v HDR
# 看双人同任务（应有两行相隔数十毫秒）
grep -A1 'Mission/Session' /opt/hd2-discovery/state/logs/mock.log | grep -E 'Mission/Session|HDR'
```

## 8. 统计

| 指标 | 值 |
|---|---|
| 会话窗口 | 15:07–15:25（含两次登录、场次重建） |
| 请求总数 | **458**（GET 285 / POST 146 / PUT 23） |
| 状态码 | **456×200 + 2×202**，**0 个 4xx/5xx** |
| 公钥交换 | 成功 6 次（114/115 B） |
| 加入成功 | 2 次（双向各 1 次） |
| 双人同任务 | 1 次（`Mission/Session` ×2，相隔 40 ms） |

## 9. 剩余风险 / 下一步

1. **修 §6 的两个 mock 缺陷**并回归（含 conformance 用例）。
2. **稳定性**：多跑几局、长时间在线（大厅 TTL、状态持久化、崩溃重启后的 tombstone）。
3. **3 人以上**：`players` 固定 4 槽、`max_members=4` 已支持，但未实测第 3 人加入。
4. **Party 数据面**：本轮跑通说明真实 PlayFab Party 可用；但没有抓包证据，E2 的 `4104/4379`
   是否还会在特定网络下出现仍未证。
5. **证书寿命**：叶子 11-23 12:32 UTC / CA 11-23 20:32 本地到期，之后需重签并重新分发 ca.cer。
6. **中继依赖**：本机所在网络对本域名的 TLS 需经服务器 B 中继；这是**我方网络环境**的限制，
   志愿者侧直连 A 无此问题。部署给第三方时需说明。
7. 文档收口：把 §5.3 的 Public 约束写进测试卡与部署 README；补 `docs/STAGE2-E3-TESTCARD` 的"已跑通"版本。
