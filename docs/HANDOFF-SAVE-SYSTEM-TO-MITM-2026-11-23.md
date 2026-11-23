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

**② 基线 fixtures**：
- 主号基线：**允许**把研究仓 `samples/store-season/`（已脱敏、有 provenance）采样进产品仓
  `data/fixtures/saves/`，请带日期/build 注记。
- **空号基线已代生成**（用研究仓 `tools/sample_build.py`，含"原值残留"硬门禁）：
  `D:\mod\work\hd2-offline-research\state\dist\save-fixtures-fresh\`
  —— 53 条记录，**`leak_check = PASS`**（scrub: uuid×1、zeros×2）。关键文件：
  `api_Progression.json`(1500B) · `api_Progression_inventory.json`(3267B) ·
  `api_Progression_Items_Customize_v2.json`(12B) · `api_Stats_profile(_801)_summary.json`(644B×2) ·
  `api_SeasonPass_1929468580.json`(14589B)。
- **`Mission/end` 响应模板**（G3 参考）也已代生成：
  `D:\mod\work\hd2-offline-research\state\dist\mission-end\`（5 份，过同一脱敏门禁）。
- 注意：`state/dist/` 是**本机 gitignored 目录**，请拷进产品仓并注记来源/日期。

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
