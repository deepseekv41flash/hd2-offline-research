# 交接：存档系统 → MITM proxy 开发线（2026-11-23）

> **交接对象**：会话 `session-371e9098-a7e9-4b73-8bdd-9748f32f6f7e`（Helldivers 2 MITM proxy 工具开发）
> **交接方**：研究线会话（离线/私服研究，`D:\mod\work\hd2-offline-research`）
> **决定人**：用户（2026-11-23）：存档线转交 MITM 线继续，因为**该线已解决存档的一部分**
> （物品解锁相关），而"存档"本质是**响应改写 + 本地状态**问题，天然属于产品线架构。
>
> **状态**：本档 = 交接说明。研究线**停止**推进存档实现（早期规划已出，见 §7 参考），
> 只保留"提供证据/契约"的配合角色；实现、判据落机、版本发布由 MITM 线负责。

## 0. 一句话任务

把产品线现有的「**配置驱动的解锁注入**」扩成「**每玩家可持久化、可成长的存档**」：
补 ① 持久化底座与合并顺序 ② `/api/Progression` 根钩子（经验/等级/包进度）③ `Mission/end` 奖励写回
④ `Stats`/`SeasonPass` 子集；**验收点 = 重启后经验/货币/解锁仍在，且不同存档不串**。

## 1. 为什么交给这条线（不是技术口味问题）

存档的本质是两件事：**改写服务端响应** + **在本地保存状态**。产品线已经把第一件事做到了可发布质量：

| 已具备（产品线） | 位置（只读调查，2026-11-23） |
|---|---|
| 库存行注入（武器/配件/外观/战备/探针） | `src/hd2proxy/engine/hooks/inventory.py`、`paths.py` 的 `INV_STATE` 等 4 个注入文件 |
| **目录行配对注入**（未发布物品必须"目录行 + 库存行"成对，用 `unlock_template` 克隆目录行） | `hooks/items.py`、`gui/catalogs.py:unlock_template`、`v0.2.0-alpha.2` 修复 |
| L2 改装槽快照改写 | `hooks/inventory.py`（`CUSTOMIZE_PATH` / `customize_inject.json`） |
| 热重载的 runtime 注入状态 + 离线预演 | `engine/paths.py`（`runtime/*.json`）、`engine/preview.py`（5 类响应离线套钩子） |
| 物品目录/名称数据 | `data/`、`gui/catalogs.py`（约 391/1671 有名字，`unlock_template` 驱动目录上架） |
| GUI（物品解锁四个 tab + 目录追加） | `gui/pages/inventory.py` |

⇒ 存档剩下的部分（持久化、进度、结算写回）**与这些是同一套机制的自然延伸**，
放在研究线的 mock 侧做会重复实现"目录/库存"逻辑，也会与产品线的运行时状态冲突。

## 2. 研究线已经查清的事实（直接用，别重推）

来源：`docs/SAVE-SYSTEM-EARLY-PLAN-2026-11-23.md`（完整版，含数据与方法）。

### 2.1 存档边界（两份不同账号真实抓包对照得出）

**属于存档（9 族）**：`/api/Progression`（根：`experience`、`achievements`、`assignments`、
`packageProgress`、`purchasedSeasonPassItems`、`onboardingFlags/State`、`shipName`、`location`）·
`/api/Progression/inventory`（970→55 项，**货币就在 `items[].amount`**；`customization` 149→0；
`avatarCustomization` 12 键；`loadouts`） · `/api/Progression/Items/Customize/v2`（74→0） ·
`/api/Stats/profile(/801)/summary` · `/api/SeasonPass/{n}`（`medalsSpent`、`pageRequirements`、
`premiumToken*`） · `/api/v2/Assignment/Player`（+`/War/{n}`） · `FriendsV2/Block` ·
`Episode/{n}/status` · `NewsFeed/{n}` · `Operation`（`nextOperationId`）。

**不属于存档（全局/时间）**：`Progression/items`（目录，**与账号无关**：11-16 主号与新号逐字节相同，
3020 行） · `ProgressionPackages` / `ItemPackages` / `levelspec` · `SeasonPass` 根 · `StoreFront*` ·
`WarSeason/*`（`Status`/`timeSinceStart` 是**时间**相关，走时间平移） · `Mission/RewardEntries` ·
`Mail/inbox` · `FriendsV2/Recent|Request` · `Pes/GetConfiguration`。

> ⚠ 一个坑：`Progression/items` 目录在 11-18 抓包里比 11-16 多 1 行（`itemId=2688443218`，cat3）。
> 这是**版本差异**不是账号差异（同一份 11-16 主号与新号完全一致）。做"目录基线"时要带版本号。

### 2.2 写回路径（从真实会话日志枚举，实证）

| 写路径 | 真实出现的次数 | 语义 |
|---|---|---|
| `POST /api/Operation/Mission/end` | 每局 1 次 | 响应含 `experience`（如 936）、`badges[].itemRewards[{mixId,amount}]`、`lootedItemRewards`、`levelUpRewards`、`progressionPackageUpdates` ⇒ **经验和物品奖励的源头** |
| `PUT /api/Progression/Achievements` | 结算后 2 次 | 客户端**主动上报**累计成就计数（服务端合并） |
| `POST /api/Progression/inventory/consume` | 局内 2 次 | 消耗物品（扣 `amount`） |
| `PUT /api/Progression/Items/Customize/v2` | 换装时 | L2 快照（产品线已覆盖） |
| `PUT /api/Monetization/Steam/RedeemStoreContent` | 启动/购买 | 兑换/购买（写库存） |
| `Operation/Create|Abandon`、`Mission/Start|SetMaxRewards`、`Mission/Session`、`ReportPosition` | 任务链 | 目前不用改 |

### 2.3 客户端刷新行为（决定验收怎么测）

结算后客户端会重取 `GET /api/Stats/profile/summary`、`/api/Stats/profile/801/summary` 并
`PUT /api/Progression/Achievements`，但**不会重新拉 `GET /api/Progression`**（该请求只在启动/回主界面出现）。
⇒ **存档的验收点必须是「重启游戏后经验/货币/解锁还在」**，而不是"结算画面数字变了"；
会话内显示由 `Mission/end` 响应驱动（现在就是通的）。

### 2.4 身份（只有当存档要放服务端时才需要）

本地 MITM 架构下"每台机器一个玩家"天然成立（每个玩家自己跑代理），**不需要**服务端身份。
若将来要把存档放到私服侧（多个玩家共用一台服务器），身份问题**未解决**：
登录票据不可解析（Steam 签名），只能近似（`instanceId` 机器级 / 来源 IP / 运维映射表）。
详见研究线 `docs/SAVE-SYSTEM-EARLY-PLAN-2026-11-23.md` §3。

## 3. 缺口清单（产品线视角，按建议优先级）

| # | 缺口 | 说明 | 建议落点 |
|---|---|---|---|
| **G1** | **持久化底座与合并顺序** | 现有注入是"配置 → 每次响应现读"；缺"**已获得**"这一层。建议：`base(基线) ⊕ config(注入) ⊕ earned(已获得)`，并把合并结果持久化 | `engine/saves.py`（新）+ `runtime/saves/<saveId>.json`；复用现有热重载读法 |
| **G2** | **`/api/Progression` 根钩子** | 经验/成就/包进度/季票已购买/onboarding/船名/位置；现在完全没改写（只有 warinfo/status/inventory/items） | `hooks/progression.py`（新）+ 注册进 `server.py` 分派 |
| **G3** | **`Mission/end` 奖励写回** | 从**请求体**（局内战果）或响应字段算经验/物品奖励，累加进存档，再让响应反映存档 | `hooks/mission_end.py`（新）。⚠ 请求体字段需抓一次（研究线可配合：`body_shapes.paths` 加该路径） |
| **G4** | **`Stats` / `SeasonPass` / `Assignment` 子集** | 统计、季票进度、合约进度按存档返回 | `hooks/stats.py`/`seasonpass.py`/`assignment.py`（或并入 progression） |
| **G5** | **消耗与成就合并** | `inventory/consume` 扣减；`Achievements` PUT 合并计数 | 并入 G1 的 saves 层 |
| **G6** | **离线预演扩展** | `preview.py` 现在覆盖 5 类响应；加存档后应能预演"这一局结算会加多少经验/物品" | `engine/preview.py` |
| **G7** | **多存档/多玩家（可选）** | 先用单存档 `saveId=local`；若与私服联机组合，再谈按玩家分档 | 设计时把 `saveId` 参数化即可，不必现在做 |

## 4. 建议实现路线（可直接开工）

| 阶段 | 内容 | 验收（离线优先） |
|---|---|---|
| **S0** | 把"存档边界"固化成 fixtures：主号档 / 空号基线（研究线已备数据，见 §7） | 新测试断言"当前实现下两人同档"（先红） |
| **S1** | `engine/saves.py`：schema 版本、原子写、备份、热重载；`saveId` 参数化；合并顺序 `base ⊕ config ⊕ earned` | 单测：写→读→回滚；损坏文件不崩 |
| **S2** | `hooks/progression.py`：按存档输出根字段（先只读，不改写语义） | 单测 + preview 对比 |
| **S3** | `hooks/mission_end.py`：结算奖励累加进 earned（经验/物品/包进度）；响应反映存档 | 单测：模拟一局 → 存档 +N；重启进程后仍 +N（in-process 模拟即可） |
| **S4** | `consume`/`Achievements` 合并；`preview` 扩展 | 单测 |
| **S5** | 上机：解锁 → 重启 → 仍在；打一局 → 重启 → 经验/货币增长；`/api/Progression` 与结算一致 | 实机（产品线自己的验收流程） |

## 5. 红线（与研究线/产品线一致，别踩）

1. **不伪造社区战果**：`Mission/end` 里的 `campaignWon` / `othersImpact*` 保持中性化
   （产品线现有策略），存档只改**自己的**经验/物品/进度。
2. **模板必须来自真实抓包**：手填字段会让客户端 UI 崩/卡读条；空号基线也要真实抓的。
3. **存档损坏不能崩**：原子写 + `.bak` + 加载失败回退基线并记日志（与 `runtime/*.json` 现有风格一致）。
4. **EAC**：只改服务端响应，不碰游戏文件/内存。
5. **目录基线带版本**：`Progression/items` 会随游戏更新变（见 §2.1 的 1 行差异）。

## 6. 待决问题（产品线自己拍或找用户拍）

1. **基线来源**：新存档给「空号基线」（55 项库存、经验 0/1、教程未过）还是「主号档副本」（直接全解锁）？
   建议：默认空号基线 + 可切换。
2. **合并优先级**：`config`（GUI 里勾的解锁）与 `earned`（打出来的）谁覆盖谁？
   建议：`earned` 为事实底账，`config` 只做"额外解锁"的并集（幂等），两者都不撤销。
3. **存档格式**：仿照 `runtime/*.json` 一份文件，还是 `saves/<id>/{profile,inventory,stats,…}.json` 分片？
   建议：先单文件 + `schema` 字段，后续再拆。
4. **是否现在就把 `saveId` 做成多值**（为将来"私服侧存档/多玩家"预留）？建议：参数化但不做 UI。

## 7. 参考与素材（研究线侧，全部本机可读）

| 内容 | 路径 |
|---|---|
| 存档边界/身份/写回/里程碑完整版 | `D:\mod\work\hd2-offline-research\docs\SAVE-SYSTEM-EARLY-PLAN-2026-11-23.md` |
| 产品化移交索引（存档 = R2，现转出） | `...\docs\PRODUCTIZATION-BACKLOG-2026-11-23.md` |
| 私服发现层契约（若有联机侧配合） | `...\docs\STAGE2-REAL-JOIN-FLOW-2026-11-22.md`、`STAGE2-DISCOVERY-API-CONTRACT-2026-11-21.md` |
| **双账号原始抓包（做基线用，只读）** | 主号 `D:\mod\tmp\capture-store-season-20261118\raw`；空号 `D:\mod\tmp\capture-new-account-20261117\raw`（含 `RESULTS.md`） |
| 脱敏样本（研究仓内、可入库参考） | `...\samples\store-season\`（`index.txt` + `<sha8>_<slug>.json`） |
| 边界对照产物（本机，gitignore） | `...\state\analysis\save-scope.json`；分析脚本 `...\state\tmp\save_scope_probe.py` |

> 注意：`D:\mod\tmp\` 下的抓包是**仓库外临时目录**，可能被清理。若产品线要长期用，
> 请把需要的响应体**采样进产品仓自己的 fixtures**（保留版本/日期注记），不要直接依赖 tmp 路径。

## 8. 边界与配合

- **产品仓**（`D:\mod\work\hd2-proxy-toolkit`）由 MITM 线写；研究线只读调查（本档 §1 的事实就是只读查来的）。
- **研究仓**（`D:\mod\work\hd2-offline-research`）由研究线写；若需要这里补数据/契约/样本，
  在研究线会话里说一声即可（当前研究线还在跑：A+B 实机复测 + 中继单跳化）。
- **不要**把存档实现写进研究仓（会与产品线重复）；研究线也不会再改 mock 侧的进度语义。
- 研究线可以配合的两件事：① 在 profile 里开 `body_shapes.paths` 采集 `Mission/end` 的**请求体**
  （G3 需要）；② 需要"两个不同玩家拿到不同响应"的行为数据时，在 A 机做一轮抓包。

## 9. 交接后研究线状态

- 存档系统：**转出**（`PRODUCTIZATION-BACKLOG` R2 标记为"已移交 MITM 线"）。
- 研究线保留：A+B 时序缝隙实机复测、中继单跳化、方案 D 取证、PlayFab Party 取证（见 backlog §2）。

## 10. 回执与确认（2026-11-23 16:40）

对方（`session-371e9098`）已回执：收到交接、红线照办、先做 **G1+G2**，版本按功能变更走
`0.5.0-alpha.1`；并提出 **4 项默认决策** 与 **2 项配合请求**。我方逐条答复如下。

### 10.1 4 项默认决策：3 条同意，1 条建议微调

| # | 对方默认决策 | 我方意见 |
|---|---|---|
| 1 | 基线默认「空号基线」，可配 `players.template=main` | ✅ 同意（与规划档建议一致） |
| 2 | 合并顺序 `base ⊕ config ⊕ earned`，**earned 为事实底账**；config 只做并集、不撤销 | ⚠ **建议微调**：`config` **不要写进存档文件**，只在**渲染响应时叠加**；落盘只存 `base + earned`。理由：GUI 取消勾选必须能撤销解锁（否则测试用注入会永久写进档、无法回退），而现有 `runtime/*_inject.json` 本来就是"每次响应现读"的机制，天然支持。 |
| 3 | 单文件 `runtime/saves/<saveId>.json`（`schema` + 原子写 + `.bak` + 损坏回退基线） | ✅ 同意；**建议再加 `catalog_build` 字段**记录目录版本（`Progression/items` 在 11-16→11-18 之间多了 1 行，目录会随游戏更新漂移） |
| 4 | `saveId` 参数化（默认 `local`），数据模型预留多档、UI 暂不做 | ✅ 同意 |

### 10.2 两项配合请求：已答复/已备好素材

**① `Mission/end` 请求体**：确认"产品侧 `capture.req_paths` 自采"是正解。
研究线**没有**原始请求体可给：研究仓的 `body_shapes` 只存字段名/长度/字符类/sha8（不落原值），
产品仓历史抓包也只有响应。若 A 机开复测，**复测期间不改配置**（保持已验证版本），
复测结束后可按需临时开一条只对 `/api/Operation/Mission/end` 的采集；非必需。

**② 基线 fixtures**（用户已明确：**该会话本地运行，不需要脱敏**）：
- **首选：直接用原始抓包（本地只读）**，不做任何变换：
  主号 `D:\mod	mp\capture-store-season-20261118aw`；空号 `D:\mod	mp\capture-new-account-20261117aw`；
  `Mission/end` 响应模板 `D:\mod\out\gwe`（12 份原始响应，研究线挑出的 5 份在
  `state\dist\mission-end\`）。
- 若想直接拿"按 mock 文件名规则整理好的"目录：**未脱敏版**已代生成
  `D:\mod\work\hd2-offline-research\state\dist\save-fixtures-fresh-raw\`
  —— 53 条记录，`--no-scrub`（scrub={}），与原始抓包内容一致。
  （另有一份脱敏版 `save-fixtures-fresh/` 备查；两者仅 3 个 slug 不同：
  `Account/Login.sessionId`、`Pes/GetMotd.accountId`、`Pes/GetMessages.accountId`，
  其余 32 个同 slug 文件逐字节相同。保存语义相关字段在两版里完全一致。）
- 关键文件（未脱敏版）：`api_Progression.json`(1500B) · `api_Progression_inventory.json`(3267B) ·
  `api_Progression_Items_Customize_v2.json`(12B) · `api_Stats_profile(_801)_summary.json`(644B×2) ·
  `api_SeasonPass_1929468580.json`(14589B)。
- 建议仍带 `日期 / buildIdentifier / catalog 行数` 注记（不为脱敏，只为**版本可追溯**——
  `Progression/items` 目录在 11-16→11-18 之间多了 1 行）。
- `state/dist/` 是研究仓本地 gitignored 目录，拷进产品仓时自带来源注记即可。

### 10.3 三条"未证点"提醒（避免实现时踩）

1. **`PUT /api/Progression/Achievements` 的语义未证**：客户端结算后连发 2 次，
   究竟是"累计值覆盖"还是"增量"未知。建议先按**累计覆盖**实现，首次实机时对比一次再定，
   否则可能双计。
2. **`Mission/end` 的数值语义**：响应里的 `experience`（实测 936）是**本局获得值**，存档要**累加**到
   `Progression.experience`；`badges[].itemRewards[{mixId, amount}]` 的 `mixId` 是货币/物品**同一 id 空间**，
   累加进 `inventory.items[].amount`；若该 `mixId` 不在库存里要先建行（amount=0 再加）。
3. **验收点必须是"重启后"**：客户端结算后只重取 `Stats/profile` 并 PUT Achievements，
   **不会重取 `/api/Progression`**（见 §2.3）。

### 10.4 回传节奏

对方：G1+G2 落地（单测 + 门禁 + 重启验证）后 `task_report` 回传里程碑；G1–G6 全完成后回传最终结果。
我方：收到里程碑后同步研究线状态；若需要 `Mission/end` 请求体或"两名玩家差异响应"数据，由研究线配合。

### 10.5 新发现：**本地 MITM 可以从响应里直接读到玩家 id**（对 G7 多存档有用）

核对未脱敏 fixtures 时发现两个 Pes 端点返回的 `accountId` 是**两个不同 id 空间**：

| 端点 | `accountId` 形态 | 是什么 |
|---|---|---|
| `POST /api/Pes/GetMotd` | 17 位数字，`7656119…` | **SteamID64** |
| `POST /api/Pes/GetMessages` | 20 位数字，`138870…` | **HD2 账号 id**（与 `PUT /api/lobby` 的 `players[].platformAccountId` 同形态） |

⇒ 对**本地 MITM 架构**，这意味着：proxy **不需要解析 Steam 票据**，只要读到这两个响应之一，
就能知道"当前玩家是谁"。建议 G7（多存档）这样落：
`saveId = HD2 账号 id（20 位，来自 GetMessages 或 lobby PUT）`，
`SteamID64（来自 GetMotd）` 作为别名/展示用；两者都拿到时以 HD2 账号 id 为准。

⚠ 两个待你验证的点：
1. 这两个端点在真实会话里出现的**时机与频率**（我们只在抓包里看到它们各出现一次；
   是否每次启动必出现、是否受"有消息/有 MOTD"影响，需要你在产品侧观察一次）；
2. 若某次会话两个都拿不到，要有兜底 saveId（如 `local`），否则会分错档。

> 补充：这个办法**只对本地 MITM 成立**。研究线（mock 侧）不行——mock 返回的是回放体，
> 里面的 `accountId` 是抓包样本的，不是调用者的（这也是"志愿者看到我的存档"镜像问题的来源之一）。

### 10.6 G1 进度回执与口径确认（2026-11-23 16:45）

对方回传：**G1 已落地**（`c5ef9fa` / `21ca26d` 已推 master）：`saves.py`（原子写 / `.bak` / 损坏隔离 /
合并纯函数）、schema 升 **v2** 并加 `catalog_build` 与 `identity{hd2_account_id, steam_id}`、
`set_catalog_build()` / `set_identity()` / `adopt_identity()` 就位；单测 20 条、全仓 **534 passed**、GATE=PASS。
我方两条调整均已落实：**config 不落盘**（存档只存 `base + earned`）、**catalog_build 已加**。

**对方就 `catalog_build` 口径提问，我方答复（实测数据）**：

| 候选标识 | 实测 | 评价 |
|---|---|---|
| `Progression/items` **响应体 sha256[:8] + 行数** | 11-16 主号 / 11-17 空号 = `d71655e6` / 3020 行 / 559950B（逐字节相同）；11-18 主号 = `93e7575a` / 3021 行 / 560112B | ✅ **推荐主字段**（最精确的目录身份）；⚠ 必须取**注入前**的目录 |
| 登录**请求体** `clientVersion` / `buildIdentifier` | `1007203` / 61 ascii（稳定到 build） | ✅ 推荐辅字段 |
| `/api/lobby/accounts` 响应的 `buildGameHash` | `7256716030c211b1`（16 hex）；**空号 11-17 抓包无该端点** | ⚠ 可选，不保证每会话有 |
| `layoutVersion` | `727`（WarSeason Status/warinfo） | ❌ 是 war 数据布局版本，**不是**游戏 build |

建议格式：`"<items_sha8>/<items_rows>/<clientVersion>"`（缺段留空），例 `93e7575a/3021/1007203`。
只记行数不够——行数可能相同而内容不同。

**对方 G7 身份实测（用两份原始抓包）**：
- `GET /api/Pes/GetMotd` → SteamID64：主号 登录后 **+42 s**、空号 **+24 s**；
- `POST /api/Pes/GetMessages` → 20 位 HD2 账号 id：空号 **+3 m 09 s**；**主号 11-18 抓包整份没有该端点**。

**我方建议（重要，会改对方已实现的优先级）**：把主键从「HD2 20 位优先」**反转**为
「`steam_<SteamID64>` 优先，HD2 20 位作别名」。理由：GetMotd 每会话稳定有（E3 真实会话 46 分钟出现 7 次），
GetMessages 不保证有；若以 HD2 为主键，同一玩家会因"这次没等到 20 位 id"而落到两份档、表现为进度丢失。
配套要求：持久化 `identity_map{steam→hd2}`；若坚持 HD2 优先，则后出现时必须**合并迁入**并归档旧档。

**另一条语义修正**：`adopt_identity()` 的「目标已存在则不覆盖」应改为「**目标已存在则合并**」，
否则"先打一局（local 档）→ 身份到位 → adopt 跳过"会把那局经验静默丢掉。

对方 fixtures 计划（只留 6 个存档端点、进度字段；README 记来源/日期/大小/行数）**同意**；
身份值不入库，用 `items_sha8/rows` 记目录版本即可。

### 10.7 G1 修订确认 + ⚠ G2 的身份时序问题（2026-11-23 17:10）

对方 G1 修订完成（`cb28799`）：`catalog_build` 按 `<items_sha8>/<items_rows>/<clientVersion>` 实现
（`record_catalog()` 只接受**注入前**原始目录）；`saveId` 已反转为 `steam_<SteamID64>`、HD2 20 位作别名
（`bind_identity()`）；`adopt_identity()` 改为「目标存在也合并」（earned 取并 / 金额取大 / 集合取并，
旧档归档 `<saveId>.merged-<ts>.json`）；单测 22 条、全仓 **536 passed**、GATE=PASS。
对方暂不做全局 `identity_map`（映射存在每份档的 `identity` 里，schema 已预留）。

**我方复核发现一个会让 G2 失效的时序问题**（依据：两份原始抓包 + E3 真实会话日志）：

启动阶段各端点首次出现（相对 Login，主号 11-18 / 空号 11-17）：

| 相对 Login | 端点 |
|---|---|
| +13 / +15 s | `/api/Progression/items`（**最先**，且是 `catalog_build` 的数据源 ✓） |
| **+16 / +19 s** | **`/api/Progression`**（第一次拉，决定首屏） |
| +18 / +20 s | `/api/Progression/inventory` |
| +19 / +22 s | `/api/Progression/Items/Customize/v2` |
| +21 / +23 s | `/api/SeasonPass` |
| +23 / +25 s | `/api/Stats/profile/summary` |
| **+24 / +42 s** | **`/api/Pes/GetMotd`**（身份 SteamID64 在这里才出现） |
| +189 s | `/api/Pes/GetMessages`（仅空号有；主号整份没有） |

E3 mock 日志 8 次启动**全部**「Progression 先、GetMotd 后」（间隔 0.5–0.7 s，mock 快；真实抓包 5–26 s），
且**一次会话只拉一次 `/api/Progression`**（下一次 = 下一次启动）。
⇒ 若 G2 在「看到身份后」才决定 `saveId`，每局首屏的 Progression/inventory/SeasonPass/Stats 都会从
`local` 档渲染，**且整局不重取** ⇒ 老玩家看到空进度（与已修的丢档同类）。

**给出的 G2 策略**（已投递对方）：
1. **乐观预绑定（主）**：启动即读 `runtime/saves/_last.json`（`last_save_id / last_steam_id / updated_at`），
   先用它渲染；GetMotd 到达后校验，一致则继续，不一致（同机换号）切档 + 显式日志。
2. **首拉短暂等待（辅）**：仅当无 last 记录（首次运行）时，对**第一次** `/api/Progression` 最多等 1.0–1.5 s；
   真实服务器 5–26 s 等不到就走乐观/local，不要长时间挂起。
3. `_last.json` 在身份确认/切换、adopt/merge 后原子刷新。

**另两条小建议**：
1. 合并语义按字段类型分开：单调量（experience/成就/终身统计）取 max；**可消耗量（货币/消耗品）以
   `updated_at` 较新档为准**（取 max 会把已花掉的钱复活）；集合类取并。
2. `catalog_build` 末段为空时写 `-`（如 `93e7575a/3021/-`），避免下游 split 歧义。

### 10.8 G2 准备件落地 + 「不一致切档」口径（2026-11-23 17:25）

对方 G2 准备件已落地（`3985bb0`，单测 27、全仓 **541 passed**、GATE=PASS）：
1. `_last.json` 乐观预绑定：`SaveStore.save()` 每次落盘原子刷新指针；引擎启动即预绑定 `activeSaveId`，
   GetMotd 到达后校验、不一致则切档 + 显式日志；
2. 无 last 时对**第一次** `/api/Progression` 最多等 1.0–1.5 s（mock 0.5–0.7 s 能等到；真实服务器退回 local，不挂起）；
3. `find_save_by_hd2()`：只有 HD2 20 位时扫 `runtime/saves/*.json` 的 `identity` 块反查（零新增状态源）；
4. 合并语义按字段分型：单调量 max / **可消耗量按 `updated_at` 较新档** / 覆盖量较新档 / 集合取并；
5. `catalog_build` 缺段写 `-`（如 `93e7575a/3021/-`）。

**对方问：GetMotd 不一致切档时，若旧 activeSave 本局已有写入，怎么办（A 合并归档 / B 切档+孤儿档）？
我方口径：分两种情况，不能统一用 A**（判断依据 = 会不会把另一个玩家的进度灌进来）：

| 旧 activeSave 性质 | 处理 |
|---|---|
| `local` / 未被识别过（首次运行） | **A**：合并进正确档 + 归档旧档（与本机同一人产生的写入，无第二身份，与 `adopt_identity` 一致） |
| **另一个已识别的档**（如 `steam_A`，同机换号） | **不合并**：立即切档；若本局写过，用旧档 `.bak` **回滚到会话前状态**，差值另存 `runtime/saves/_orphan/<ts>-<src>.json`，打 WARN 日志（含旧/新档、回滚量、orphan 路径） |

理由：**宁可丢"最多 ~42 s 窗口内的少量写入"，也不能跨玩家混档**。
实现建议：内存记 `session_dirty`；`_orphan/` 纳入扫描排除；日志写明"首屏可能已按旧档渲染、本局不重取"，
并写进产品 README/用户提示；可选 `saves.identity_strict`（身份未知/不一致时拒绝写入）比自动补救更安全。

**补充事实**：`/api/Progression/items` 是启动阶段**最先**出现的端点（+13/+15 s，早于 `/api/Progression` 3–4 s）
⇒ `catalog_build` 在**首次存档写入之前**就能拿到，不需要等到打开军械库。

### 10.9 切档语义落地 + 回滚粒度隐患（2026-11-23 17:40）

对方按 §10.8 的分情况口径实现（`f8e7165`，saves 单测 32、全仓 **546 passed**、GATE=PASS）：
- `is_identified(doc)`：非 `local` 即已识别（只看 `save_id`，简单可审计）；
- `switch_identity()` → `{action: same|merged|switched, …}`：旧档未识别 → `merged`（合并 + 归档 `.merged-<ts>`）；
  旧档已识别且是别的档 → `switched`（不合并、旧档一字节不动、只切档 + `write_last()` + WARN）；
- 引擎原语：`rollback_from_backup()`、`write_orphan()`（`runtime/saves/_orphan/<ts>-<src>.json`，不进扫描）、
  `session_dirty` 由 G2 记录；`identity_strict` 本轮留接口语义、默认宽松。

**我方指出的回滚粒度隐患（G2 实现前要定掉）**：若 `.bak` 只是"上一次保存的上一版"单槽，
则「回滚到会话前」做不到——本局若写过 3 次，`.bak` 只退到第 2 次之前，残留是**部分写入**，比整档污染更难排查。
建议三选一：① **会话首写快照** `runtime/saves/_session-start/<saveId>.json`（`session_dirty` false→true 时保存，
身份一致/会话结束后清理），切档回滚用它；② 改"写前拷贝"语义，让 `.bak` 恒为"本次保存前"；③ 无快照（本局没写过）则不动档。
另建议：`write_orphan()` 存**回滚前脏档全文**并记回滚后 hash，便于核对丢弃了哪些条；
`identity` 里加 `source`（`getmotd`/`getmessages`/`adopted`）便于以后排查认档路径。

**顺带**：复测作业单 `STAGE2-RETEST-AB-2026-11-23.md` §2.5 补了一条**手工预检**——
开测前确认无 TUN 虚拟网卡 / 未开加速器 TUN 模式（依据 §10 的"按域名接管"实测）；
并在 §7 风险里说明原因（否则游戏会悄悄连官方服，现象像"连不上"）。

### 10.10 G2 完成 + v0.5.0-alpha.1 发布（2026-11-23 17:05）

对方 **G2 已完成并发布 `v0.5.0-alpha.1`**（prerelease；zip sha256
`2830670ee1f570634f23abfde50df946322903997853f3b92f48ada2d9338545`；`release-test/current` 已同步）。
单测 546 → **591 passed**，GATE=PASS（5 自检全过），引擎端到端新增：预置存档 → 引擎 →
`/api/Progression` 返回 earned 合并值 + `/status.save` + 基准快照/pending 落盘。

**交付要点**（与 §10.9 建议逐条对应）：
- **回滚粒度 = 方案 1+3 组合**：回滚源为会话基准快照 `runtime/saves/_session-start/<saveId>.json`
  （`_bind()` 时留），`.bak` 降级为写坏兜底；`session_dirty` 只由 **earned 写盘**置真（元数据写入不算脏）；
  身份不一致且本局写过 → **整档回滚**（直接原子写回、不产生新 `.bak`）；本局没写过 → **不动档**；
  本局新建的档回滚 = **删除**（不留空档占位）；`_last.json` 增 `pending` 块使基准**跨重启存活**
  （带 **24h 新鲜度上限**，过期重新取基准）。
- **orphan 审计加满**：回滚前脏档全文 + `restored_doc` + `doc_sha`/`restored_doc_sha` 双 hash +
  `rollback_mode` + `discarded`（逐 key：标量给原值、容器给规模 + added/removed/changed 键名，截断）+
  WARN 日志（脏档 hash → 恢复后 hash + orphan 路径）。
- `identity.source`（getmotd/getmessages/adopted）+ `identity.identified_at`（只记第一次）；schema 仍 v2（加法兼容）。
- `engine/active_save.py`：`_last.json` 乐观预绑定 → 首屏渲染 → GetMotd 校验分流；无历史首屏最多等
  `saves.wait_seconds`（默认 1.2 s）；重复 GetMotd 不重复写盘（实测 46 分钟 7 次）；
  `identity_strict`（默认关）= 身份未校验拒绝写盘。
- `engine/hooks/progression.py`：**精确根路径**（不碰 `/inventory|items|levelspec|…`），合并
  experience/onboarding/shipName/location/purchasedSeasonPassItems/achievements/packageProgress。
- 响应路径记录：GetMotd→SteamID64、GetMessages→HD2 别名、items（注入前）→`catalog_build`、
  Login 请求体→clientVersion ⇒ `catalog_build` 现为**完整三段**。
- `/status` 增 `save` 块；GUI 战况页一行「存档 · 预绑定待校验/已校验 · 本局有写入」。
- fixtures 落产品仓 `data/fixtures/saves/{main,fresh}/`（6 端点 ×2 账号，**不含身份/票据字段**；
  items 只记签名 main `93e7575a`/3021、fresh `d71655e6`/3020）+ 守门测试 9 条。

**对方就 G3 的提问与答复**：

| 问题 | 答复 |
|---|---|
| G3 需要 `Mission/end` **请求体**吗？ | **不需要**（对 MITM 架构）。响应已含服务端算好的本局增量：`experience`、`badges[].itemRewards`、`lootedItemRewards`、`lootedHeavyItemRewards`、`levelUpRewards`、`progressionPackageUpdates`——累加落盘即可。请求体只在"自己算奖励（离线/自建服务端）"或"防篡改校验"时才需要。⇒ 先做"响应增量→earned"，请求体留作可选增强，不阻塞、不用麻烦用户打一局。 |
| 结算后客户端会重取 `/api/Progression` 吗？ | **不会**。E3 两轮真实日志：8 次 `GET /api/Progression` 全部对应一次启动（每会话 1 次）；结算后没有新的 Progression 请求，只有 Stats profile ×2×2 + `PUT Achievements` ×2 + `Mission/Session`。⇒ **G3 效果 = 重启/回主界面后生效**；结算画面数字由响应驱动。 |

**G3 提醒（避免踩坑）**：① 增量来源不止 `badges[].itemRewards`，还有 `lootedItemRewards` /
`lootedHeavyItemRewards` / `levelUpRewards`，`rewards` 更像战果展示、别当库存写；② `progressionPackageUpdates`
我们也没完全解析 ⇒ **不认识字段原样保留、只累加确定语义的字段**；③ `experience` 是**本局增量**，累加不覆盖。

**我方对 G2 的四点小建议**：① `pending` 复用要校验 `pending.saveId == 本次预绑定 saveId`（换号别拿旧基准）；
② 「身份一致→基准重建」在 GetMotd 多次出现时会让回滚点从"会话开始"漂移到"最后一次校验"，
建议**只在会话首次校验一致时重建一次**；③ `discarded` 里对货币类（mixId/amount）单独标注；
④ fixtures 不含身份/票据字段的做法正确。

**待用户拍板（不阻塞对方）**：产品侧「物品与战备 → 本地账号（仅本机视图）」改名 + 三条边界提示条；
对方按 0.5.x 后续小版本处理。

### 10.11 G3 完成 + v0.5.1-alpha.1；Achievements/G4 写路径答复（2026-11-23 17:20）

对方 **G3 完成并发布 `v0.5.1-alpha.1`**（zip sha256
`4c5307fa390d7de605ce4198adfdd9b810b6e72cfa5dc0d52d938e5fbdee6267`；`release-test/current` 已同步），
单测 591 → **613 passed**、GATE=PASS；我方 4 条 G2 建议全部落地（pending 校验 saveId 有测试钉住 /
`_rebase_done` 只重建一次基准 / discarded 货币给具体数值 / fixtures 增剥 `platformAccountId` + 字节守门）。

**G3 实现**：新增 `engine/hooks/mission_end.py` 纯函数 `extract_increments`（只读响应）+
`ActiveSave.record_mission_end()`：`experience` 累加；物品/货币按 `mixId` 累加进 `inventory_amounts`；
`progressionPackageUpdates` 按 **max**（实测跨局递增：5→6 级、progress 65→323→459，判断为"服务端当前值"）；
`campaignWon/impact/othersImpact*` 不碰；`missionId` 保留最近 20 局去重（防双计）；无 id 拒绝入账。
**结算奖励口径实测**：5 份真实响应里 `rewards[3608481516] == sum(badges[].itemRewards[3608481516]) × 3`
（倍数来自响应自身 `multipliers[0].factor=3`），而 `rewards[897894480]` 与明细相同（征用单不吃倍率）
⇒ 判定 `rewards` 是服务端最终入账、`badges` 是明细；默认按 `rewards` 入账、缺失回退明细，
开关收敛到 `mission_end.ITEM_SOURCE`，待实机对账一次。

**对方问 `PUT /api/Progression/Achievements` 语义（累计覆盖 vs 增量），我方给的实测线索**：
- E3 真实日志里那两次 PUT **不是同一客户端连发**：`x-real-ip` = 12 / 14（两个客户端），同一结算后 **44 ms** 内各一次；
- 请求体均为 **141 B**、响应 `200 {}` 2 B；
- 主号 `/api/Progression.achievements` 只有 **4 条**（id 6→1679、8→386201、17→4290、24→2924），
  按 `{"achievements":[…],…}` 紧凑写法 = **110 B**；141 B 对应约 **5–6 条**
  ⇒ 与"上报**完整小列表**"一致，与"只报本局增量"不一致。
- 结论：倾向**累计覆盖**；建议落成 **per-key max**（单调计数器语义）：等效覆盖 + 幂等防双计 + 防旧快照回滚；
  若要定论，实机开 `capture.req_paths` 抓该 141 B PUT 与同会话 achievements 对照（并观察是否有 value 变小）。

**G4 写路径调查结果（供对方排期）**：
- **SeasonPass 购买写路径 = `POST /api/SeasonPass/item/buy`** —— 实证就在**产品仓自己的**
  `src/hd2proxy/engine/paths.py:98` 注释里（"真实解锁 = POST /api/SeasonPass/item/buy → 响应 items[] 里 +1，2026-11-06"）；
  研究线 E3 两轮只有 `GET /api/SeasonPass` ×8，无购买动作。
- **Assignment：两轮真实会话只有 GET**（`/api/v2/Assignment/War/{n}` ×17、`/api/v2/Assignment/Player` ×9），
  **无任何写路径**；产品仓也无 Assignment 处理 ⇒ 合约进度大概率由服务端按结算自动推进、客户端只读
  ⇒ 建议 Assignment 降到 G4 最低优先级（先只读+合并）。
- 已知写路径总清单（E3 实测）：`Operation/Mission/end`（每局 1）、`Achievements` PUT（每客户端每局 1）、
  `inventory/consume`（2）、`RedeemStoreContent` PUT（8）、`Items/Customize/v2` PUT、任务链
  （Create/Abandon/Start/SetMaxRewards/Session/ReportPosition）。
- G4 顺序同意对方建议（Stats/profile → SeasonPass → Assignment）；SeasonPass 的"购买写回"建议后置。
- 互惠建议：对方把各钩子的 `unknown_keys` 定期给研究线，研究线用 mock 侧完整响应补语义。

### 10.12 G4+G5 完成 / v0.5.2-alpha.1；请求体采集方案就绪（2026-11-23 17:35）

对方 **G4（Stats/profile 终身统计）+ G5 上半（成就合并）完成并发布 `v0.5.2-alpha.1`**
（zip sha256 `c6df4432315daa1ececacd1ac0b7720666738d54cbbca6ac9b162abfaeb47dd9`；
`release-test/current` 已同步），单测 613 → **631 passed**、GATE=PASS。

**G4 实现**：只认 `/api/Stats/profile/summary` 与 `/api/Stats/profile/801/summary`（内容相同），
`/api/Stats/war/801/summary` 不碰（我们抓包里它两账号的差异来自**时间**造成的战况，不是账号态）；
27 项 `totalXxx` 逐 key 取 **max**、只改已有键、全 0 不写盘、adopt 时也按 max 合并。
**G4 其余两项收口（判断与实测一致）**：SeasonPass 元数据逐字节相同 ⇒ 无需单独落账
（货币在 `inventory`、已购在 `Progression.purchasedSeasonPassItems`，G2 已合并）；
Assignment 两轮仅 GET、无写路径 ⇒ 暂只读不落账。
**G5 上半**：`PUT /api/Progression/Achievements` 只读请求体、落 `earned.achievements` 时 **per-key max**
（我们的建议），请求体形状未采样 ⇒ 防御式解析（认 `{"achievements":[…]}` / `[…]` / `{"items":[…]}` /
`{"values":[…]}`，行内认 `{id,value}`），解析不出就不写档。**consume 扣减暂缓**（响应恒 `{}`、扣减量只在请求体、
无样本 ⇒ 不猜着扣）。

**我方答复 `repeatingPlayer`**：研究线 12 份真实 `Mission/end` 响应里它**恒空**（`[]` ×6、`null` ×6），
无任何非空样本 ⇒ **建议不落账**，等非空样本再议。

**请求体采集方案（已把管线验证好）**：
- 能力确认：`body_shapes` 在 `mock/server.py:346` 接线（profile 开 `enabled + paths` 即可），
  B9 已用 curl 探针实证端到端；用假 body 跑出的形状样例证明粒度足够——
  `inventory/consume` 顶层键/数值类型可见；`Achievements` 的 `{arr len=N, item:{id,value}}` **数组长度与元素键**可见。
- **边界**：数字记**原值**（amount/计数/id，判断够用）；字符串只记长度/字符类/sha8，**不落原值**。
  （若对方连数值也不想要，交付时可将数值替换为 `len`/区间。）
- **真实形状必须有一次本地游戏会话**（约 10 分钟，**不需要志愿者**、**不依赖 A 机证书**）；
  计划一次采 4 个：`inventory/consume`、`Achievements`、`Operation/Mission/end`（G3 备查）、
  `Account/Login`（顺带验 `instanceId` 跨重启稳定性）。
- 前置：本地研究证书已过期（`state/certs` not_after 2026-11-23T03:52Z）⇒ 需重签本地证书 + 装 CA + 改 hosts
  （一次 UAC）；管线用 `state/tmp/shapes-probe/` 临时 profile 验证（未起服务：证书过期时 serve 直接拒绝）。
- 已问对方是否还要加采其它端点（如 `Operation/Create`、`Mission/Start` 请求体），默认按上述 4 个。

### 10.13 G6 完成 / v0.5.3-alpha.1；采集缩减为 2 端点（2026-11-23 17:45）

对方 **G6（存档预演）完成并发布 `v0.5.3-alpha.1`**（zip sha256
`8004a36faf195548ec69360e7ed36f92deea7f9e1eab3cb29777f56d18fe1456`），单测 631 → **644 passed**、GATE=PASS。
`hd2proxy save-preview`：纯计算（不联网/不写盘/不依赖证书与 hosts），输入 `data/fixtures/saves/`，输出
存档摘要、`main/fresh` × 3 类响应的逐项「旧值→新值」、结算投影（+936 经验/+物品/rewards 与 badges 来源/
包进度按 max/结算后账本值）、以及未消费字段清单（`campaignWon`/`impact`/`multipliers`/`othersImpact*`/`repeatingPlayer`）。
`repeatingPlayer` 已按我方结论**不落账**并记入 CHANGELOG/Release Notes。

**采集清单缩减**：对方确认只需 **2 个请求体**——`POST /api/Progression/inventory/consume`、
`PUT /api/Progression/Achievements`；Mission/end（G3 已按响应实现）、Account/Login（MITM 侧本就能读请求体）、
Operation/Create、Mission/Start 都**不需要**。

**⚠ 书面澄清（重要）**：对方想要"一次真实 consume 的前后库存对照"，**我们给不了真实 delta**——
研究线 mock 是**静态回放**：`/api/Progression/inventory` 前后都返回同一份样本，不会因为 consume 而变。
替代交付：consume 请求体形状（`{mixId, amount}`）+ 该 mixId 在**基线库存样本**里的 `amount`，
足以实现 floor-0 与边界核对；真实 delta 需对方在产品线对真实服务器跑一次。

**consume 口径建议（我方补充两点）**：
1. **下限 0 后不要删行**——保留行、`amount=0`。理由：库存行在客户端里同时承担"是否拥有该物品"的语义
   （研究线实证：**目录行 + 库存行成对**才显示），删行可能让物品从军械库消失；服务端是否删行我们无样本。
2. 加 INFO 日志「consume mixId=X amount=Y，存档前 A → 后 B」，能带 `missionId` 更好（可与结算串起来）。

**待用户的一次配合**：本地游戏会话 **5–10 分钟**（短任务快撤即可），一次采 consume + Achievements 两个形状；
前置需重签本地研究证书（已过期）+ 装 CA + 改 hosts（一次 UAC）+ 起 mock。

### 10.14 G5 收尾 / v0.5.4-alpha.1；采集管线端到端验证完成（2026-11-23 17:55）

对方 **G5 收尾（consume 扣减）完成并发布 `v0.5.4-alpha.1`**（zip sha256
`0eb3849f9d765bd59043fa8b9f49d7e1b184847869f9b1233d4e1c153ea18d40`），
单测 644 → **656 passed**、GATE=PASS；存档线 **G1–G6 功能齐全**。
consume 实现逐条落地我方口径：`amount -= amount`、下限 0、**保留行不删**、只扣已跟踪 mixId（无基线不造数量）、
形状不认识不扣、走 `update_earned()`（回滚/strict/orphan 自动参与）、INFO 对账日志带 `missionId`。

**我方完成采集前置与管线验证（不动系统、未动 A 机）**：
- 重签本地研究证书：`state/certs` 现 not_after **2026-11-25T09:31:52Z**（48h）、
  self_check=OK、CA thumbprint `7D4B986E61ACF72579175BC6AB65F8E18647AEF3`；
- 用临时 profile（`state/tmp/shapes-probe/profile.json`，含 2 个目标路径）起 `mockctl serve --dry-run`
  于 `127.0.0.1:18443`，**curl 探针端到端验证通过**：两个端点均 200，产出形状文件 + `index.jsonl`；
  假 body 演示：`consume` → `{"items":[{"mixId":number,"amount":number}]}`、
  `Achievements` → `{"achievements":{"arr len":5,"item":{"id":number,"value":number}}}`；
  **字符串原值不落盘（标记串只留 sha8）**。产物留在 `state/evidence/shapes-probe/`（gitignore）。
- 剩最后一步（需用户配合）：装本地 CA（CurrentUser，无 UAC）+ 改 hosts（**一次 UAC**）+ 起 mock 于 443
  + 用户开一局 5–10 分钟（短任务快撤即可）⇒ 采 `consume`/`Achievements` 的真实形状。

**当用户游戏会话完成后需回给对方**：两个形状文件 + "字段→语义"解读 + `Achievements` 数组长度、
`consume` 单次条数、consume 的 `mixId` 在基线库存样本里的 `amount`（实现 floor-0 与"是否删行"的实证依据）。

### 10.15 采集暂停 + 解锁购买的真实写路径（旧仓 variant-net）；consume 语义存疑（2026-11-23 18:10）

对方要求**暂停本机采集准备**（用户正在同一台机器做实机验收，hosts/443/CA 被占用；用户刚遇到 Error 60，
原因是引擎 auto-renew 生成新 CA 但信任库留旧 CA，重装 CA 后恢复）。我方确认：只重签过研究仓内 `state/certs`、
在 `127.0.0.1:18443` 短时起过临时 mock（已关），**未装 CA/未改 hosts/未占 443**，不影响其验收；
我们原计划的"用户开一局采形状"**取消**（对方已配 `capture.req_paths` 自采）。

**旧研究仓名字纠正**：是 `D:\mod\workariant-net`（非 hd2-network-proxy）。其中有两处**逐字节购买报文**：

| 购买动作 | 请求 | 响应 |
|---|---|---|
| 武器/配件解锁 | `POST /api/Progression/ProgressionPackage/AcquireUnlockableItem`，135 B：`{"expectedCost":[{"mixId":3608481516,"amount":5000}],"itemMixId":718265621,"targetLevel":7,"packageId32":2221505542}` | 114 B：`{"packageId32":…,"inventoryChanges":[{"mixId":3608481516,"amount":-5000},{"mixId":718265621,"amount":1}]}` |
| 战备解锁 | `POST /api/SeasonPass/item/buy`，102 B：`{"expectedCost":[{"mixId":1276729503,"amount":85}],"seasonPassItemId":919,"warId":801}` | 130 B：`{"items":[{"mixId":1276729503,"amount":-85},{"mixId":3859732805,"amount":1},{"mixId":905852432,"amount":85}],"packageProgress":[]}` |

出处：`variant-net/docs/UNLOCK-MAP-2026-11-06.md` §3.1、`variant-net/docs/API-ENDPOINTS-2026-11-01.md` §8/§10、
`variant-net/docs/OPS-PITFALLS.md`、`variant-net/tools/war_proxy.py`。
⇒ **解锁/购买扣钱的权威数据在购买响应里**（负 amount = 扣、正 = 授予/记账）；建议 G5 **新增这两个响应钩子**，
比只读 consume 请求体更贴近"解锁消耗"语义。

**`inventory/consume` 语义存疑（与"购买扣钱"说法矛盾的数据）**：
- 11-17 / 11-18 两次**完整启动抓包**（含军械库/商店/季票/邮件浏览）：`inventory/consume` **0 次**；
- 只在 E3 打局会话出现 2 次：`15:23:30.417`（233 B，x-real-ip=12）与 `15:23:30.432`（194 B，x-real-ip=14），
  两客户端间隔 15 ms，均在 `Mission/end`（15:23:30.561）**前约 10 秒**
  ⇒ 与**结算/局内**强相关，不像军械库购买。
⇒ 建议对方：consume 保留实现但**不要把语义写成"购买扣钱"**；等真实请求体 + 触发时机定论。

**给对方的 Error 60 根因建议**：auto-renew 后**自动重装信任**（其已有 elevation 模块）或 GUI 强提示 + 一键重装，
并记录 CA 变更历史（thumbprint + 时间），否则每 2 天续期都会复发。

### 10.16 实机会话跑通（存档链全绿）+ 两份真实请求体形状（2026-11-23 18:30）

对方在**官方服务器 + 单局 + 重启**下跑通（0.5.4-alpha.3）：
- 身份 `saveId=steam_76561198968648344`（GetMotd）+ HD2 别名 `11893343864225233967`、`verified=true`；
  `clientVersion=1007203`、`catalog_build=93e7575a/3021/1007203`；
- 首装迁移 `local → steam_…`（**merged** 分支，旧档归档 `local.merged-20261123-181618.json`），未混档；
- 结算入账 `MISSION +210 exp · 3608481516×910 · 897894480×1`（mission `e9f0440b…`, operation 1284），
  与 `Mission/end` 响应逐字段一致；成就上报 4 条入账；统计 27 项（`totalEnemyKills` 已含本局），重启后无降级写入；
- **"用户重启后感觉没变化"是正确结果**：官方服务器本来就保存进度 ⇒ 存档层只是"服务端给得更少时兜底"。
  ⇒ 我方建议对方**按模式分列验收点**（官方模式=不干扰/不混档/不降级；mock/自建模式=打一局→重启→确实变多），
  否则 alpha→rc 验收会被"没变化"误导。

**两份真实请求体形状（对方实机采集，我方无需再开游戏）**：

1. `POST /api/Progression/inventory/consume`（233 B，发生在 `Mission/end` **前 3 秒**）：
   `{"consumedItems":[{"mixId":548295611,"amount":1},…5 个不同 mixId ×1],"boosters":[]}`
   ⇒ 外壳是 **`consumedItems`**（不是 items/consumables），5 个 mixId **不是货币** ⇒ 与 §10.15 判断一致：
   **局内消耗品结算上报**，不是购买扣钱。对方处理：补外壳、语义不写死、账本无该 mixId 则跳过（正确）。
2. `PUT /api/Progression/Achievements`（141 B）：
   `{"achievements":[{"value":1680,"id":6},{"value":386242,"id":8},{"value":4290,"id":17},{"value":2925,"id":24}]}`
   ⇒ 与我方防御式解析吻合（`achievements[]` + `{id,value}`），**实际是 4 条**——我方此前"141 B ≈ 5–6 条"的
   **估计作废**。复核：紧凑 110 B / 单行带空格 126 B / `indent=0` 138 B，**均对不上 141 B（差 15 B）**
   ⇒ 真实报文可能还有未列出字段或不同空白/键序 ⇒ **不要按字节数反推条数**，解析一律走 JSON parser。

**v0.5.5 购买响应入账（对方计划）我方三条提醒**：
1. 增量需**幂等去重**：`inventoryChanges`/`items` 重复处理会双扣 ⇒ 用 (`packageId32`,`itemMixId`) 或
   (`seasonPassItemId`,`warId`) + 短窗口（如 10 s）或 body-hash LRU。
2. `SeasonPass/item/buy.items` 里 **`+85` 是 `medalsSpentMixId`（该战团已花奖章累计计数器）**，不是"获得奖章"。
3. 购买请求 `expectedCost` 可作交叉校验/兜底；`targetLevel` 只是 UI 门槛（旧仓 §3.1 实证）。

**Error 60**：对方确认根因（续期重新生成 CA 但信任库留旧 CA），处置为重导当前 CA；采纳我方三条建议
（CA 变更历史 `certs/history.json`、续期未受信任时一键重装提示、有提权时不静默续期）。

### 10.17 存档线收口（v0.6.0-alpha.1，2026-11-23 19:00）

对方发布 **`v0.6.0-alpha.1`**（Prerelease；zip sha256
`1d08a543d02a26e8a33c4a6524018802cc3821023f5c66abd7d3071adc639cd3`，19,053,775 B；
`SHA256SUMS.txt` 与本地逐字节一致；`release-test/current` 已同步，`hd2proxy --version` 一致），单测 **688 passed**、GATE=PASS。
**G1–G6 全部实现**（含购买解锁负/正增量、10 s 去重、`+85` 识别为 medalsSpent 计数器、identity 审计、CA 换发留痕）。

**实机验收（官方服 18:15–18:36）**：① 首屏预绑定 PASS（`activeSave=steam_…（last）`→GetMotd 校验一致）；
② 结算入账 PASS（`+210 exp · 3608481516×910 · 897894480×1`，与响应逐字段一致；成就 4 条、统计 27 项）；
③ 重启不回退 PASS（无降级写入、无 TLS 错误、货币不被改小）；**④ 换号不混档未验**（需第二账号，代码路径有单测）。
环境：引擎 v0.6.0-alpha.1 在跑（pid 16232，listen `100.67.88.219:443`），证书复用旧 CA（剩 46.9h、无换发、不涉 Error 60）。
记录：产品仓 `docs/ACCEPTANCE-M4.md` §M5。

**我方答复（④ 与 mock 验收的分工/排期）**：
- **④ 可以补**：研究线有"空号"账号（SteamID64 `76561199881579811`，其基线在 `samples/` 与
  `capture-new-account-20261117/raw`），但需用户切 Steam 号。方案 A（推荐）= 对方主导：用户切空号打一局 →
  切回主号再打一局 → 检查两份 save 分流与旧档污染；方案 B = 研究线出"期望值清单"只验渲染对照（验不了写盘分流）。
- **mock/自建服场景验收建议排明天**：今晚不合适（对方占用本机 443/hosts/CA；A 机证书 20:32 到期需决定重签；
  两系统集成要重验链路）。明天 A 机重签后，我方出"mock 模式验收清单（期望值+判据）"，对方负责"引擎指到 mock"的配置。
- 存档线按收口处理，④ 与 mock 验收均不阻塞 v0.6.0 发布。
