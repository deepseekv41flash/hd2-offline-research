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
