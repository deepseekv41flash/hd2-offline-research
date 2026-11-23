# 存档系统 — 早期规划（2026-11-23）

> **阶段编号待定**（建议作为「阶段3」）。本文只做规划与取证，**不改代码**。
> 触发点：阶段2 双人实测时发现「志愿者看到的进度像是我的存档的镜像」。
>
> 结论先说：**这不是同步 bug，而是"服务端只有一份档"**。修法不是加同步，是给 mock 加**玩家维度 + 持久化**。
> 本文把「存档边界」「身份问题」「写回路径」「里程碑」用实测数据框出来，供拍板后再动手。

## 0. 一页速览

| 问题 | 现状（已验证） | 规划结论 |
|---|---|---|
| 为什么志愿者看到我的档 | mock 按 method+path 回放**单一**抓包，profile 里只有一个 `identity.account_id` | ✅ 根因确认；S1 就能修掉"镜像" |
| 进度存在哪 | 客户端本地 **没有**进度（`saves/` 只有 1.4 KB 的 `user_settings.config`） | 进度必须由服务端（我们）拥有 |
| 哪些数据算存档 | 用两份不同账号的真实抓包对照得出（§2 表） | 9 个端点族；其余是全局/时间数据 |
| 谁是谁（身份） | mock 完全不识别调用者；登录票据不能解析 | 走「每台机器一个玩家位」+ 运维映射表（§3） |
| 进度怎么变 | 实测 6 类写路径（§4） | 第一版只做「结算 + 库存消耗 + 成就上报」 |
| 工作量 | — | **约 5–6 人日 + 1 次实机窗口**（§9），其中 S0–S3 全部离线可验 |

**需要你拍板的 4 件事在 §11**（身份语义 / 初始档来源 / 第一版写回范围 / 是否与 3 人计划绑定）。

## 1. 现象与根因（✓✓ 已验证）

**现象**：阶段2 双人实测中，志愿者进游戏后，军械库/等级/统计显示的是**主号**的数据
（= 我的存档镜像），两人完全相同。

**根因（结构性，不是偶发）**：

| 层 | 事实 | 位置 |
|---|---|---|
| 回放层 | 按 `exact → base → template → fallback` 匹配**单一捕获条目**，与调用者无关 | `mock/rules.py:60-96`（`ReplayIndex`） |
| 状态层 | `MockState` 只有会话/大厅/作战/任务链；**没有任何玩家档案** | `mock/state.py:37-57` |
| 配置层 | profile 里 `identity` 是**单一** `{"account_id","player_name"}`，不是映射表 | `profiles/offline-mock.json` |
| 数据层 | 数据源就是主号的抓包 `samples/store-season`（脱敏后） | profile `data_dir` |

⇒ 两个客户端拿到的 `GET /api/Progression`、`/api/Progression/inventory`、
`/api/Stats/profile/summary` **逐字节相同**，所以看起来"我的档被镜像给了对方"。

**客户端侧补充事实**（今天在本机实测，只读）：

- `%APPDATA%\Arrowhead\Helldivers2\saves\` 只有 `c0b8c67f93500302_user_settings.config`（1.4 KB）
  与另一个账号前缀 `a50da53b1ab77c2f_…`（同为 1.4 KB）——**只有用户设置，没有进度**；
- ⇒ 进度是**服务端权威**，不会出现"本地档和服务端档打架"的问题。这对我们有利：只要服务端正确，客户端就正确。

## 2. 「存档」的边界（用两份真实账号抓包对照得出）

**方法**：拿本机两份不同账号的直通抓包做逐端点对照（2026-11-23 分析，脚本见 §12）：

- 主号：`D:\mod\tmp\capture-store-season-20261118\raw`（2026-11-18）
- 新号（空号）：`D:\mod\tmp\capture-new-account-20261117\raw`（2026-11-17，README 记录为"什么都没解锁"的账号）

对照产物：`state/analysis/save-scope.json`（gitignore）。

### 2.1 属于存档（账号相关）

| 端点 | 主号 → 新号（实测） | 存档里的角色 |
|---|---|---|
| `GET /api/Progression` | 12921 B → 1500 B；`experience` **2759938 → 1**；`achievements` 4→0；`assignments` 24→2；`packageProgress` 不同；`purchasedSeasonPassItems` 18+→`[]`；`onboardingFlags` 7962491→0；`onboardingState` 128→0；`shipName` `[7,47]`→`[]`；`location` 不同 | **核心档案**（经验/等级来源、季票包进度、教程状态、船名、位置） |
| `GET /api/Progression/inventory` | 69120 B → 3267 B；`items` **970 → 55**（含货币 `amount`，如 699/250/315/814/500）；`customization` 149→0；`avatarCustomization` 12 键全 0；`loadouts` 两份都空 | **库存 + 装备 + 外观**（货币也存在这里） |
| `GET /api/Progression/Items/Customize/v2` | 15853 B → 12 B；`items` **74 → 0** | **外观/配件自定义** |
| `GET /api/Stats/profile/summary` | 730 B → 644 B；`totalEnemyKills` 356065→0、`totalDeaths` 3490→0…（19 项） | **终身统计** |
| `GET /api/Stats/profile/{n}/summary` | 同上（带 war 维度） | 同上 |
| `GET /api/SeasonPass/{n}` | 26 条 → 1 条；`pageRequirements` 3 项→10 项；`medalsSpentMixId`、`premiumTokenMixId(s)` 不同 | **季票进度** |
| `GET /api/v2/Assignment/Player` | 645 B → 865 B；数组 2→3；`progress`/`expiresIn`/`setting` 不同 | **合约/任务进度** |
| `GET /api/FriendsV2/Block` | 344 B → 68 B；`entries` 2→0 | 黑名单（小） |
| `GET /api/Episode/{n}/status`、`GET /api/NewsFeed/{n}` | 结构同、值不同 | 小体量账号态 |
| `GET /api/Operation` | `nextOperationId` **1283 → 0** | 作战计数器（可能属账号或属机器，待确认） |

### 2.2 不属于存档（全局 / 时间相关）

| 端点 | 判定 | 说明 |
|---|---|---|
| `GET /api/Progression/items` | **目录全局** | 2026-11-17 的对照实验里，11-16 主号抓包与新号抓包**逐字节相同**（均为 3020 行 / 559950 B / sha256 `d71655e6…`，见 `capture-new-account-20261117/RESULTS.md` §1）——目录与账号无关已证 |
| `GET /api/Progression/ProgressionPackages`、`ItemPackages`、`levelspec` | 全局 | 226120 / 4858 / 12604 B，两账号相同 |
| `GET /api/SeasonPass`（根）、`StoreFront*` | 全局 | 商品与季票定义 |
| `GET /api/WarSeason/*`（warinfo / GalacticWarEffects / NewsTicker / WarId） | 全局 | 战况数据；`Status`/`timeSinceStart` 是**时间**相关，走现有时间平移层 |
| `GET /api/Mission/RewardEntries` | 全局 | 5812 B，两账号相同（徽章/倍率定义，不是战果） |
| `POST /api/Mail/inbox`、`FriendsV2/Recent|Request`、`Pes/GetConfiguration`、`Configuration/GameClient` | 全局 / 空 | 与账号无关 |
| 身份类（不是存档） | — | `POST /api/Pes/GetMotd` 响应里的 `accountId`（Steam64 形态 `7656119…`）、`POST /api/Account/Login` 的 `sessionId` |

> **待查的 1 行差异**：本文用的主号抓包（**11-18**）`Progression/items` 有 **3021** 行 / 560112 B，
> 而新号（11-17）是 3020 行 / 559950 B，只差一行为
> `itemId=2688443218`（`progressionCategory=3`，非消耗品，`requiredLevel=0`）。
> 注意这不是"账号差异"：11-16 的主号抓包与新号**完全一致**（3020 行，见上表）。
> 两份相隔 1 天 ⇒ **倾向目录版本差异**（游戏/服务端在这两天加了 1 条目录项），
> 但需要一次**同一天**的两账号对照才能定论（§10 第 1 项）。

## 3. 核心未决：身份（谁是谁）

### 3.1 可用信号（实测/已知）

| 信号 | 形态 | 稳定性 | 能否在**登录瞬间**拿到 |
|---|---|---|---|
| `token`（登录体） | 116 ascii | **每次登录都变**（B9 五组 sha8 全不同） | ✅ 但不可用作稳定键 |
| `publicKey`（登录体） | 44 base64 | 每**会话**一变（5/5 不同） | ✅ 只能标识会话 |
| `instanceId`（登录体） | 6 位数字 | 两个账号**相同**（= 机器级）；同一账号两次登录不变（B9 证据） | ✅ **最有希望的机器键** |
| 来源 IP | — | 家里宽带通常稳定，但会变 | ✅（第 1 跳就有） |
| `ct` 头 | 16 字符 | 语义未知 | ✅ |
| `x-signature` 头 | 44 字符 | 语义未知（疑似每请求签名） | ✅ |
| HD2 `accountId` | 20 位数字 | 账号级稳定（E3 实测我方/志愿者各一个） | ❌ 只在 `PUT /api/lobby` 出现，实测晚 ~63 s |
| SteamID64 | 17 位数字 | 账号级稳定 | ❌ 只出现在 `Pes/GetMotd` 的**响应**里（服务端→客户端方向） |

> 关键限制（B3 已定论，别重开）：**自建私服不解析登录票据** —— 票据由 Steam 签名/加密，
> 我们无法从中解出 SteamID64。所以「真正的账号级身份」这条路 ✗，只能做**近似身份**。

### 3.2 推荐：分层身份 + 运维可编辑映射表

```
playerId = resolve(machine_key)          # machine_key 优先 instanceId，其次来源 IP
machine_key(instanceId/IP) ──映射──> { playerId, 显示名, 来源存档, 首次见到时间, hd2AccountId? }
                                  存放在 state/players/index.json（原子写、可手改）
```

- 首次见到一个 `machine_key` → 建新玩家（初始档见 §11 决策 2），并在日志里打印 `playerId`；
- 若后续从同机看到新的 HD2 `accountId`（PUT 到达）→ 记到该玩家画像里，**不**自动换档（换档要人确认）；
- 同机两个 Steam 账号会共用一份档 —— 这是**已知限制**，写进文档；2 人场景是两台机器，不受影响。

**为什么不用 `x-signature`/`ct` 做身份**：语义未证（也许能验签 → 会话级身份，能解决同一 NAT 下多人，
但我们当前场景不需要）；列为 §10 的待取证项，若将来要 3 人同网再启用。

## 4. 写回路径（实测清单）

从 E3 两轮真实会话日志里枚举出的**所有会改变存档的请求**（其余都是读）：

| 写路径 | 实测次数 | 当前 mock 行为 | 存档系统要做的事 |
|---|---|---|---|
| `POST /api/Operation/Mission/end` | 1 | 模板回填（`state.py:mission_end`），带 `experience:936`、`badges[].itemRewards[{mixId,amount}]`、`lootedItemRewards`、`levelUpRewards`、`progressionPackageUpdates` | 把经验/物品奖励/包进度**累加进档案**，再落盘 |
| `PUT /api/Progression/Achievements` | 2 | 回 `200 {}` | 合并客户端上报的成就计数（客户端在结算后主动上报） |
| `POST /api/Progression/inventory/consume` | 2 | 回 `200 {}` | 扣减对应 `mixId` 的 `amount`（会话内消耗 → 落盘） |
| `PUT /api/Progression/Items/Customize/v2` | 规则已建模（`state.set_customization` 回显+模板合并） | 只在内存，不落盘 | 落盘到该玩家档 |
| `PUT /api/Monetization/Steam/RedeemStoreContent` | 7–8 | 回放/空 | 购买/兑换 → 改库存 |
| `POST /api/Operation/Create` / `Abandon` / `Mission/Start` / `Mission/SetMaxRewards` / `Mission/Session` / `ReportPosition` | 各 1–2 | 已建模（内存） | 需要和档案联动（任务链的 operationId/missionId 已够用，暂不改） |

**客户端刷新行为（决定验收怎么测）**：任务结束后客户端拉的是
`GET /api/Stats/profile/summary`、`/api/Stats/profile/801/summary`（各两次）并 `PUT Achievements`，
**不会**重新拉 `GET /api/Progression` —— `Progression` 只在启动/回主界面时拉。
⇒ 存档写入的**验收点应当是「重启游戏后经验/货币是否还在」**，而不是"结算画面里数字变了"；
会话内的显示由 `Mission/end` 响应驱动（这部分现在就是通的）。
（实测依据：该轮会话里 8 次 `GET /api/Progression` 时刻都对应一次启动/回主界面，
`Mission/end` 前后 ±40 条请求里没有它。）

## 5. 方案选项

| 方案 | 做法 | 能解决"镜像" | 能跨重启 | 成本 | 判定 |
|---|---|---|---|---|---|
| **S-A 单档** | 只把静态回放改成"一份可写档" | ❌（两人仍同档） | ✅ | 0.5 人日 | 不够 |
| **S-B 每机一档** | machine_key → playerId → 各自档案 + 持久化 | ✅ | ✅ | 2–3 人日 | **推荐** |
| **S-C 账号级** | 解析票据/拿 SteamID64 真区分账号 | ✅ | ✅ | 高 + 需破 Steam 票据 | ✗ 不做（B3 定论） |
| **S-D 运维预配置** | 手工在 profile 里给每台机器写 playerId | ✅ | ✅ | 0.5 人日 | 作为 S-B 的兜底开关保留 |

**推荐 S-B，并保留 S-D 作为"运维强制指定"覆盖项**（`profiles/*.json` 里加 `players.force_map`）。

## 6. 数据模型与目录（草案）

```
state/players/
  index.json                  # { "<machine_key>": {playerId, name, template, hd2AccountId?, at} }
  p-<slug>/
    profile.json              # /api/Progression 根（experience/achievements/assignments/packageProgress/…）
    inventory.json            # /api/Progression/inventory（items/limitSettings/loadouts/customization/avatarCustomization）
    customize.json            # /api/Progression/Items/Customize/v2
    stats.json                # /api/Stats/profile(/801)/summary
    seasonpass.json           # /api/SeasonPass/{n}
    assignments.json          # /api/v2/Assignment/Player、/Assignment/War/{n}
    meta.json                 # schema 版本、创建时间、来源模板（main / fresh-baseline）、最后写入
```

- **持久化直接复用发现层的成熟设施**：`mock/discovery.py` 已有 JSON 原子落盘（tmp + `os.replace`）、
  `.clean` 标记、损坏回退 —— 存档层照抄同一套（`state/players/*.json`），不再造轮子。
- 每次写都留 `*.bak`（保留最近 N 份），并提供 `mockctl players export/import/reset`。
- **schema 版本**放在 `meta.json`；加载失败**不崩服务**，回退到该玩家的模板档并记 `load_error`（与发现层策略一致）。

## 7. 里程碑与验收（离线优先）

| # | 里程碑 | 内容 | 验收（都能离线跑） | 估时 |
|---|---|---|---|---|
| **S0** | 基线固化 | 把 §2 的"账号相关端点清单"写成测试基线：两份模板档（主号抓包 / 新号基线）入库为 fixtures，断言当前 mock 对两人返回同一份 | 新测试 `tests/test_players_*.py` 先红（证明现状） | 0.5 人日 |
| **S1** | 玩家维度（只读） | `players` 层 + `index.json`；9 个账号相关端点按 playerId 返回；其余走全局 | 两个探针身份 → `experience` / `inventory.items` 行数**不同**；全局端点仍相同 | 1–1.5 人日 |
| **S2** | 写回①：消耗与成就 | `inventory/consume` 扣减、`Achievements` 合并 → 落盘 | 单测 + 重启后数值保持 | 1 人日 |
| **S3** | 写回②：结算 | `Mission/end` 的经验/物品奖励/包进度写入档案；`progressionPackageUpdates` 回包与档案一致 | 单测：一局结算 → 重启 → 经验增长 | 1.5 人日 |
| **S4** | 实机验收 | 一局：进图 → 结算 → 退出重启 → 经验/货币保留；两人互不串档 | 日志 + 截图/数值 | 0.5 人日 + 窗口 |
| **S5** | 运维面 | `mockctl players list/export/import/reset`；`force_map`；文档与回滚 | 命令级自测 | 1 人日 |

**S1 就能消掉"镜像"**（志愿者看到自己的档）；S3 才能做到"打了有收益、重启还在"。

## 8. 风险与红线

1. **不要伪造社区战果**：结算里的 `campaignWon` / `othersImpact*` 继续按现有「结算中性化」策略归零
   —— 存档只改**自己的**经验/物品/进度，不假装影响星系战争。
2. **schema 不能手填**：模板档必须来自真实抓包（主号 + 新号基线），手填字段会让客户端 UI 崩或卡读条。
3. **存档损坏要有兜底**：原子写 + `.bak` + 加载失败回退模板档并记日志；绝不让服务起不来。
4. **隐私**：志愿者档案落在 `state/players/`（gitignore），公开仓只放脱敏 fixtures；`index.json` 里 IP 只存哈希前缀。
5. **EAC**：只改服务端响应，不碰游戏文件/内存 ⇒ 与阶段1/2 同风险等级（低）。
6. **同机多账号共享一份档**：这是 S-B 的已知限制，要在用户文档里写明（避免以后被当成 bug）。
7. **不要试图解票据**：B3 已否证；想做账号级请先推翻那节结论。

## 9. 排期建议

- **第 1 周**：S0 + S1（全离线，能在 mock 里用两个探针身份验收）
- **第 2 周**：S2 + S3（全离线单测）→ S4 实机窗口（可与下一次联机测试合并进行）
- **之后**：S5 运维面 + 文档

合计约 **5–6 人日 + 1 次实机窗口**；与阶段2 的 A+B 复测**互不阻塞**（一个改发现层，一个改进度层）。

## 10. 待取证清单

| # | 问题 | 怎么取 | 需要实机？ |
|---|---|---|---|
| 1 | `Progression/items` 那 1 行（`itemId=2688443218`）是版本差异还是账号授权 | 同一天抓两个账号的 `items` 对照 | 需要（10 分钟） |
| 2 | `instanceId` 跨**重启**是否稳定（B9 只验了同一次运行内的两次登录） | 开两次游戏，比登录体里的 `instanceId` | 需要 |
| 3 | `ct` / `x-signature` 的语义（能否验签 → 会话级身份） | 开 `body_shapes` 采登录 + 抓两三条请求的头部原值（本地，不入公仓） | 需要 |
| 4 | `inventory.loadouts` 何时非空（装备/换装后是否写入） | 抓包里换一套装备再取 inventory | 需要 |
| 5 | `Mission/end` **请求体**字段（现在只有响应）→ 能否从中算经验 | profile 开 `req_paths` 采集一轮结算 | 需要 |
| 6 | 结算后经验是否即时显示（客户端不重取 `/api/Progression`，靠本地累加？） | 实机看结算后等级条 | 需要（S4 顺带） |
| 7 | 同机切账号时本地 `saves/*_user_settings.config` 前缀与 `instanceId` 的对应关系 | 本机两个前缀已见，可离线比对配置内容 | 可离线 |
| 8 | `Operation.nextOperationId` 账号级还是机器级（1283 vs 0） | 同机两账号交叉验证 | 需要 |

> 1–8 里只有 #7 完全离线；其余需要一次 10–15 分钟的实机抓包窗口（可与 A+B 复测同一轮做，
> 但注意：开 `req_paths` 会让抓包变大，**不要去抓票据类请求体**，只抓 `Mission/end`、
> `Progression/*` 的请求体）。

## 11. 需要你拍板的 4 件事

1. **身份语义**：接受「**每台机器一个玩家位**」（同机切 Steam 账号会看到同一份档）吗？
   还是要为"同机多号"付更高成本（需先解 `ct`/`x-signature`，且账号级大概率做不到）？
2. **初始档来源**：新玩家默认给「**新号基线**」（55 项库存、经验 0/1、教程未过），
   还是给「主号档副本」（直接全解锁）？建议**默认新号基线 + 可配置**（`players.template`）。
3. **第一版写回范围**：只做「结算经验+奖励」（S3），还是同时做「库存消耗 / 季票领取 / 成就上报」？
   建议第一版只做 S3，其余放 S5 之后（写路径越多越容易出 schema 问题）。
4. **是否与 3 人计划绑定**：如果近期要测 3 人，身份表最好一开始就支持「一台机器多玩家位」，
   否则以后要迁移。我的建议：**先按单玩家位实现，但 `index.json` 的 schema 预留 `players: []` 数组形态**。

## 12. 证据与可复现性

- 端点对照方法与结果：两账号抓包逐端点比对（本文 §2 的全部数字）。
  分析脚本为一次性脚本（`state/tmp/save_scope_probe.py`，gitignore）；
  输入是仓库外的两份抓包目录，因此**不在公开仓保存原始数据**。
  若要长期复用，可提升为 `tools/compare_accounts.py`（加脱敏门禁 + 单测）——建议随 S1 一起做。
- 抓包目录（只读、本地）：
  - 主号 `D:\mod\tmp\capture-store-season-20261118\raw`（`index.txt` + `<sha8>_<slug>.json`）
  - 新号 `D:\mod\tmp\capture-new-account-20261117\raw`（含 `RESULTS.md`，已证明目录与账号无关）
- 会话写路径清单：`state/evidence/e3prep/e3-mock-watch.log`、`e3-r2-mock-watch.log`（1227 / 777 条请求）
- 本地存档确认：`%APPDATA%\Arrowhead\Helldivers2\saves\`（仅 `*_user_settings.config`，两个账号前缀
  `c0b8c67f93500302` / `a50da53b1ab77c2f`）。
