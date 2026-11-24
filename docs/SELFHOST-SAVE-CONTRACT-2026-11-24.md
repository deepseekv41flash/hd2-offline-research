# 自建服 ↔ 引擎：结算入账数据格式契约（提案）— 2026-11-24

> **背景**：MITM 线在产品侧问「结算（Mission/end）数据要以什么形态交给自建服」。
> 本文是**研究线（自建服侧）的回答**：选形态、定目录/字段、答三个细节、列出待拍板的点。
> 状态：⏳ 提案，等产品侧确认范围与版本（若含 `save-export` 属能力变更 → `0.7.0-alpha.x`）。

## 1. 结论：选 **2（合并后的端点文档）为主** + **3（JSONL 事件流）为辅**

| 形态 | 采用 | 理由 |
|---|---|---|
| 1 原始账本（`earned`）| ❌ 仅作**输入** | 自建服是"按路径回放端点响应"的 mock 架构，需要的是**响应体本身**；若只给增量账本，服务端要自己实现 `base ⊕ earned` 合并 + 按官方 schema 重序列化，等于把合并逻辑抄一遍，且极易与官方的键集/默认值漂移 |
| **2 合并后的端点文档** | ✅ **主形态** | 直接得到 `progression.json` / `inventory.json` / `stats_profile.json` —— 正是自建服要回放的三个响应体；合并语义由引擎（已被 30+ 对账用例覆盖）负责，服务端零逻辑 |
| **3 结算事件流** | ✅ **辅形态** | 服务端已跑起来时，每局只推一条 JSONL 就能增量更新，不必每次全量重导；也可用于审计/回放 |
| 4 HTTP 控制面 | ⏸ 暂不做 | 自建服当前是**启动时加载文件**；HTTP 拉取会引入运行时依赖。产品化再做（最小形态 `GET /players/<id>/export` 返回同一套文件）|

## 2. 目录与文件布局（服务端可直接加载）

```
<server-data-root>/
  shared/                       # 与玩家无关的公共数据（world/war/catalog），沿用现有抓包目录
  players/
    <steamId64>/
      manifest.json             # 见 §3
      progression.json          # GET /api/Progression 的完整响应体（值已合并）
      inventory.json            # GET /api/Progression/inventory
      stats_profile.json        # GET /api/Stats/profile/summary
      stats_profile_war.json    # GET /api/Stats/profile/<warId>/summary（按战区分档，可选）
      customize_v2.json         # GET /api/Progression/Items/Customize/v2（可选）
      mission_end.json          # 该玩家最近一局 POST /api/Operation/Mission/end 的响应体（可选）
  index.json                    # {"players":[{"steamId64","hd2AccountId","saveId","updatedAt"}]}
```

服务端解析顺序：`players/<该玩家>/<端点>.json` → 无则回落 `shared/` 的公共数据 → 再无则 `200 {}`。
（研究线 mock 端只需加一个「按账号 overlay」的小扩展：`--players-dir` + 从 `X-HD2-Account` / 登录会话 / GetMotd 解析出账号键。）

## 3. `manifest.json` 必需字段

```json
{
  "schema": 1,
  "saveId": "steam_76561198968648344",
  "identity": {"steamId64": "76561198968648344", "hd2AccountId": "11893343864225233967"},
  "catalogBuild": "93e7575a/3021/1007203",
  "generatedAt": "2026-11-24T13:10:00+08:00",
  "engine": {"version": "0.7.0-alpha.1", "mergePolicy": "max-monotonic/v1"},
  "source": {"baselineSha256": "…", "ledgerSha256": "…", "baselineDir": "fresh|main|<path>"},
  "counts": {"experience": 1146, "inventoryRows": 57, "statsNonZero": 23, "missions": 2}
}
```

`counts` 不是冗余：自建服启动时拿它做一致性自检（缺字段/被截断能立刻发现）。

## 4. 三个细节的回答

### 4.1 按玩家区分？——**是，必须**
- **主键 `steamId64`**（18 位，`7656119…`；来自 `Pes/GetMotd.accountId`，最稳），**副键 HD2 20 位账号 id**（`11893…`，来自 `Pes/GetMessages.accountId`，会话内出现晚、常缺失）。
- 自建服按 `index.json` 建 `steamId64 ↔ hd2AccountId` 映射；请求侧能拿到哪个就用哪个，两者都缺时按"单人在线"兜底（2 人局以上不猜）。
- **一人一文件、一人一目录**；不要把多玩家合并进一个文件。

### 4.2 基线从哪来？——**由自建服提供，引擎只负责"基线 ⊕ 账本"**
- 研究线现有两套基线，都可直接当 `--baseline` 输入：
  - **空号基线**（新玩家默认）：`state/dist/save-fixtures-fresh-raw/`（53 条端点记录，exp=1、库存 55 行、统计全 0）
  - **主号基线**（导入/演示用）：`samples/store-season/`（exp=2759938、库存 970 行、统计 23/27 非零）
- 引擎 `save-export` 请接受 `--baseline <dir>`：**只按语义替换值，不重建 JSON**（见 §5 的 shape 要求）。没给基线时按空号处理。
- ⚠ 别把"官方服的该玩家真实进度"当基线：官方进度不在我们手里，自建服用的是自己的初始数据。

### 4.3 文件加载还是运行时查询？——**文件加载**
- 自建服 mock 启动时把 `players/` 读进内存（按账号索引），请求时按账号取响应体。
- 所以 **CLI 导出到 §2 目录结构**最合适；不做运行时 HTTP 拉取（至少这一版不做）。
- 若后续玩家在线期间进度需要"准实时"更新：**事件流（选项 3）→ 服务端热加载**，而不是轮询 HTTP。

## 5. 对 `save-export` 的硬要求（这几条不做就会踩坑）

1. **shape 保真**：输出必须是**基线的深拷贝只改值**——键集/嵌套结构/数组元素字段与官方响应一致。
   客户端对缺字段很敏感（历史踩坑：缺 `hasInfo`/`numLobbyMembers` 会让 UI 静默异常）。**不要"按语义重建 JSON"**。
2. **合并语义**（沿用你们已实现的）：单调量取 max（experience、stats、achievements、package_progress）、
   库存款**只抬不降**、购买扣减按负增量、集合类取并集；`recent_ids` 去重不变。
3. **原子写 + `.bak`**（已有）；导出同样要原子（先写 `.tmp` 再 rename）。
4. **可复现**：同一 `baseline + ledger` 两次导出应逐字节一致（除 `generatedAt`），manifest 记源 hash。
5. **购买/解锁**：`purchases` 要合并进 `purchasedSeasonPassItems` + 相应货币/物品值（观察到的语义：负=扣、正=授予；
   `+85` 是 `medalsSpentMixId` 计数器，别写成"获得"）。
6. **成就/统计映射**：ledger 里是 per-key max，响应体里是数组/对象（如 `[{value,id}]`、27 键对象）——请按基线键集填回，不要新增/丢弃键。

## 6. 待拍板的两个设计点（需要产品侧与我们共同定）

1. **结算权威归属**：短期（研究/演示）由引擎账本记录、自建服回放 `mission_end.json` 模板；
   中期是否让**自建服自己算结算**（服务端权威），引擎只做记录/显示？这决定 `mission_end.json` 是"服务端产物"还是"客户端产物"。
   - 当前 2 人局的现实：每个玩家各自的账本记录自己的收益，**不会互相双计**（per-player 文件）；同一客户端重试用 `recent_ids` 防重。
2. **写路径归属**：客户端会写 `AcquireUnlockableItem`/`SeasonPass/item/buy`/`Achievements PUT`/`OnboardingState` 等。
   自建服最终应自己接受并持久化这些写（真正服务端权威）；过渡期先由引擎账本合并进导出文档。
   - 现状已知：研究线 mock 对这些写端点是 `200 {}`（不回显、不落盘）——只影响"设置类写回显"，不影响存档判据。

## 7. 建议的最小交付切片（先跑通，再扩）

**v0.7.0-alpha.1（最小）**：`hd2proxy save-export --ledger <out> --baseline <dir> --players-dir <root>/players`
→ 只产 3 个文档 + `manifest.json` + 追加更新 `index.json`。
验收：拿主号基线 + 现有账本导出 → 与我们 11-24 mock 验收的期望值逐字段一致（exp=210、成就 4 条、库存回填 2 行、统计 23 项）。

**后续**：+ `customize_v2.json`、`mission_end.json`、JSONL 事件流、`shared/` 拆分。

## 8. 机器化校验器（2026-11-24 加）

`tools/validate_save_export.py`：只读校验 `save-export` 产物，发版前跑一遍即可。

```
python tools/validate_save_export.py \
  --export D:/mod/tmp/save-export-sample \
  --baseline state/dist/save-fixtures-fresh-raw \
  --shape-ref samples/store-season     # 空号基线里 achievements 为空 → 用主号基线学元素键
```

检查项（10 类，每玩家各跑一遍）：目录/索引齐全（S01–S03）、manifest 身份与 index/目录名一致（M01）、
schema/时间/engine（M02）、源 hash 格式（M03）、`catalogBuild` 键存在且为字符串或 null（M04）、
**三个响应体键集与键序 = 基线深拷贝**（F01–F05，含数组元素键）、库存不丢行/只抬不降（V01–V02）、
统计单调不减（V03）、`counts` 自检（V04）。

**首次跑样例结果（2026-11-24）**：
- A 样例（空号基线 ⊕ 两个玩家账本，`D:\mod\tmp\save-export-sample`）：**PASS 36 / FAIL 0**
- B 样例（主号基线 ⊕ 主号账本，`D:\mod\tmp\save-export-sample-mainbaseline`）：**PASS 19 / FAIL 0**
- 报告留存：`state/logs/save-export-check-20261124/sample-{A,B}.{txt,json}`

配套 10 条回归测试（`tests/test_validate_save_export.py`）：含键序漂移、丢行/降额、统计回退、
counts 不一致、身份不一致、缺 index、catalogBuild 缺失、shape-ref 学元素键等负例。

### 8.1 对 `save-export` 的三条追加建议（非阻塞）

1. `manifest.source` 增加：`baselineId`（`fresh|main|custom:<名>`）+ `baselineDirResolved`（绝对路径），
   并给出 `baselineFiles: {progression|inventory|stats: {name, sha256}}` —— 现有 `baselineSha256` 的哈希
   配方没有写进文档，消费方无法独立复算；按文件给 hash 更自描述。
2. `manifest.counts` 增加 `achievements`（progression.achievements 条数）——① 验收要对比成就条数。
3. 可选端点 `stats_profile_war.json` / `customize_v2.json` 建议**默认导出**（各 ~0.6 KB / ~16 KB）：
   两者都是**按玩家**数据，缺省会让服务端回落到公共数据、把 A 玩家的定制显示给 B。

## 9. 身份解析缺口（重要，决定多人自建服能不能正确分档）

### 9.1 实现状态：mock 侧覆盖层已就绪 ✓

`mock/players.py` + `mockctl serve --players-dir <save-export 根>` 已实现（10 条测试）：
解析顺序 **`X-HD2-Account` 头 → 本进程学到的「来源地址→玩家」映射 → 索引里只有一个玩家**；
命中则直接回该玩家的 `progression/inventory/stats_profile[/stats_profile_war][/customize_v2][/mission_end].json`，
未命中回落公共抓包数据（**不会回空响应**）。`/health` 新增 `players` 诊断块（只报数量与键名）。

**用 MITM 线样例 A 实测（2026-11-24 13:20）**：
```
X-HD2-Account: 11893343864225233967 → experience=210、成就 4、库存 56 行（含 897894480×1）、统计 23 非零（击杀 356105）
X-HD2-Account: 13887067702231565058 → experience=1、成就 0
无身份头（索引 2 玩家）              → 回落公共数据 experience=1（不猜）
```

### 9.2 缺口：**首次读档时，服务端从协议上无法知道这是谁**

实测证据（2026-11-21/23 的登录体外层形状 + 全量请求头）：

| 事实 | 证据 |
|---|---|
| 登录体里**没有账号 id** | `POST /api/Account/Login` 346 B：`{buildIdentifier, publicKey, platform, instanceId, token, clientVersion}`；437 B 变体多 `isSuspended/isBanned/betaToolkitAccountId`。无 SteamID64、无 HD2 20 位账号 id |
| 身份头只在 **lobby 类端点**出现 | `X-HD2-Account` 仅出现在 `PUT /api/lobby`、`DELETE /api/lobby/clear`、`GET /api/lobby/accounts`、`lobby/<id>/member`、`lobby/<id>` —— **存档类端点（`/api/Progression`、`/api/Stats/*`、`/api/Progression/inventory`）一个都没有** |
| 存档读发生在身份之前 | 启动时序：登录 T+0 → **T+1 s** 读 `/api/Progression` → … → T+21 s 才 `PUT /api/lobby`（那时才有身份头） |

⇒ 多人自建服如果只靠现有客户端协议，**玩家进船首屏那次读档必然分不清是谁**；
若服务端"猜"或"给默认档"，玩家会看到别人的/空的进度，之后即使学到身份，客户端也已缓存了首屏数据。

### 9.3 三个解法（我方建议 a 先行、b 产品化）

| 方案 | 做法 | 代价 | 评价 |
|---|---|---|---|
| **(a) 客户端引擎加盖身份头** | MITM 引擎已经知道 identity（`identity.source / identified_at`），对所有上游请求补 `X-HD2-Account: <steamId64 或 HD2 id>` | 引擎侧小改动（一行注入）；服务端零改造（覆盖层已支持）| **推荐 v1**：可信自建服（自己人/朋友）阶段够用；头可伪造，属于"信任边界靠人" |
| **(b) Steam 票据校验** | 登录体里 `token`(116 字符) + `platform` 走 Steam Web API `ISteamUserAuth/AuthenticateUserTicket` 换 SteamID64 | 需要 Steam Web API key；多一次外部调用与失败处理 | **产品化正解**：服务端自己知道是谁，且不可冒充 |
| (c) IP 持久映射 | 记住「来源 IP → 账号」（内存/落盘）| 首次无解、NAT 下会串 | 仅作兜底（覆盖层已实现内存版）|

> 若走 (a)：头名与语义务必与现有用法一致（`X-HD2-Account` = 账号 id，可 SteamID64 或 HD2 20 位），
> 我方发现层（A 机 discovery）已在用同一个头做账号归属，**两边语义一致才不冲突**。


## 10. alpha.1 样例复核 + 身份头实现（2026-11-24 13:40 更新）

### 10.1 `save-export` v0.7.0-alpha.1 样例：**PASS 42/42（A）、22/22（B）**

产品侧按建议补齐了 `baselineFiles` / `baselineId` / `baselineDirResolved` / `counts.achievements` /
可选端点默认导出，样例已重生成。校验器新增三项（M05–M07）：

| 新增检查 | 结果 |
|---|---|
| M05 `baselineFiles` 结构（每端点 name+sha256 合法）| A/B 均 ✓（5 个端点）|
| M06 `baselineSha256` **可复算** | A `f4a71a39…`、B `b2043e21…` 复算一致 ✓ |
| M07 **与本地基线逐文件一致** | A/B 各核对 5 个端点、差异 0 ✓ |

> **哈希配方已用实测数据反推确认**：`baselineSha256 = sha256("端点名:文件sha256
" 按端点名排序拼接)`
> —— 与产品侧发布说明一致，消费方可独立复算。
>
> **额外证据（同源核对）**：样例 `baselineFiles` 里的 sha256 与我们仓库存的抓包文件**逐字节相等**
> （fresh: progression `78db2b64…`、inventory `85bebe5a…`、stats `dccbac58…`、customize `eef46741…`；
> main: `f288f166…` / `a1e6d9db…` / `dfdad048…` / `544b4168…`）⇒ 产品侧 fixtures 与我们的抓包同源，
> 两边判据可以对到同一份字节。
>
> 报告留存：`state/logs/save-export-check-20261124-v2/sample-{A,B}.txt`。
> 校验器向后可用：旧导出（无 `baselineFiles`）只提示、不判失败（14 条测试）。

### 10.2 身份头 `X-HD2-Account-Source` 的语义（覆盖层已实现）

产品引擎（>=0.7.0-alpha.1）对所有上游请求补两个头：

| 头 | 值 | 覆盖层行为 |
|---|---|---|
| `X-HD2-Account` | SteamID64 优先，否则 HD2 20 位 id | 命中索引 → 回该玩家档 |
| `X-HD2-Account-Source` | `prebind`（仅预绑定，**未校验**）/ `getmotd` / `getmessages`（本局已校验）| 见下 |

覆盖层规则（`mock/players.py`，4 条测试覆盖）：

1. **只要带了身份头就视为"已给断言"**：命中索引 → 回该玩家；**命中不到 → 回落公共数据，绝不用"唯一玩家"去猜**；
2. **`prebind` 不写 IP 映射**（未校验断言不该污染后续无头请求）；命中索引时仍可照给（信任边界由服务端决定）；
3. `getmotd`/`getmessages` 与"无 Source（老客户端/探针）"→ 正常解析并写 IP 映射；
4. 无任何身份头时维持原逻辑：IP 映射 → 唯一玩家 → 否则回落公共数据。

`/health.players` 现在同时报 `last_source` 与 `last_header_source`（如 `header:getmotd` / `header-unresolved:prebind` / `ip` / `single`）。

**实测（用 alpha.1 样例 A）**：
```
getmotd + 76561198968648344   → experience=210、成就 4
getmessages + 13887067702231565058 → experience=1、成就 0
prebind + 未知 id             → 回落公共数据 experience=1（不猜）
无头 + 同 IP（已被可信头学过）→ 按 IP 映射回 210
```

### 10.3 v0.7.0-alpha.2：身份不可信时引擎"不盖头"，以及"只发 Source"的约定（2026-11-24 13:50）

产品侧 alpha.2 新增：`_getmotd_seen` / `_identity_uncertain`；GetMotd 校验/切档异常时记
`⚠ 身份校验失败（…）：本局不再向上游盖身份头` + 事件 `identity-uncertain`（`/status.save.events` 可见），
`identity_header()` 守卫保证**不确定时完全不盖头**。这与我们"宁可回落公共数据，也不给错人的档"一致。

**我方对应实现（已加，16 条测试）**：

| 上游请求形态 | 覆盖层行为 |
|---|---|
| 带 `X-HD2-Account`（+ 任意 Source）| 命中索引 → 回该玩家；命中不到 → 回落公共数据（不猜）|
| `X-HD2-Account-Source: prebind` + Account | 同上；但**不写 IP 映射** |
| 只带 `X-HD2-Account-Source: uncertain`（不带 Account）| **跳过 IP 映射与"唯一玩家"兜底**，直接回落公共数据 |
| 什么都不带 | IP 映射 → 唯一玩家 → 回落公共数据 |

> **更正一条**：上一轮我方建议"引擎盖头时也带上 `X-HD2-Account-Source`"，实际上 **alpha.1 起就已同时发送**
> （对方 e2e 已断言假上游收到两个头），是我方误读——已在本档更正，避免后续复盘误判。
>
> **新增约定（可选增强）**：若引擎希望"不确定时连 IP 兜底也禁用"，可在 uncertain 时只发
> `X-HD2-Account-Source: uncertain`、不发 Account 头；我方已实现并测试该路径。
