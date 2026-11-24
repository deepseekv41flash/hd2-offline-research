# 存档机制「单人测试」路线（2026-11-24 晚）— 不需要志愿者

> **结论先说**：**存档机制的验证不需要志愿者，也不需要服务器。**
> 存档层在**客户端引擎**（MITM 代理的账本 + 合并 + 写盘），服务端只负责"给出服务端数据"。
> 昨天 M6 验收就是这个形态：隔离引擎 + 本机明文 mock（`upstreamTest: 127.0.0.1:9099`）+ 用户单人开一局
> → 四条判据全过，**零志愿者、零服务器**。
>
> 需要志愿者的只剩"双客户端同局"这类**联机**行为（见 §4），存档本身不依赖它。

## 1. 当前环境实况（2026-11-24 20:45 核对，20:56 有更新见 §6.1）

| 项 | 状态 |
|---|---|
| 本机 443 / 9099 / 8080 / 17897 | **无监听**；`hd2proxy` 进程未运行（引擎已停） |
| 本机 hosts HD2 行 | 无（MITM 线恢复常规环境时清掉/未写） |
| 产品版本 | `D:\mod\release-test\current` = `v0.9.0-alpha.2`（含存档层 G1–G6 + 事件流）|
| 本机 mock | 代码就绪（`--plain`、`--players-dir` 覆盖层），随时可起 |
| save-export 样例 | `D:\mod\tmp\save-export-sample`（**210 口径 / 成就 4 / 统计 23 非零**，S4 用它）+ `-mission`（1146 口径 / 2 局）/ `-mainbaseline` / `-ledgeronly`（v0.9.0-alpha.2 口径，已 J01–J11 全绿）|
| A 机（106.52.201.42） | `hd2-discovery` active；证书有效至 **2026-11-25 20:07**；**尚未部署** `players.py` 覆盖层（handler 仍为 `6d86d00b…` 旧版）|

## 2. 单人可测清单（不需要志愿者）

| # | 测什么 | 怎么测（单人） | 判据 | 耗时 | 现状 |
|---|---|---|---|---|---|
| **S1** | 存档闭环：一局 → 结算 → 重启 → 进度保持 | 起隔离引擎 + 本机官方服/官方环境，单人打一局，退出重进 | exp/货币/成就/统计按响应累加且重启不回退；账本 `earned` 与响应逐字段一致 | 15–20 min | M6 已部分覆盖（官方服 ①②③），可作**常驻回归** |
| **S2** | **换号不混档**（MITM 线欠的 ④）| 同一台机器切换 Steam 账号（`76561198968648344` ↔ `76561199881579811`）各登录一次 | 每人只看到自己账本；`identity.source` 正确切换；不产生 orphan/串档 | 10–15 min | ⚠ 未验（MITM 线原以为需要第二人，其实单人切号即可）|
| **S3** | 服务端给得很少/全空时的兜底 | 本机 mock 用空号数据集（`state/dist/save-fixtures-fresh-raw`）+ 严格空 inventory | ①exp 抬到账本值 ②inventory 回填（897894480×1/3608481516×910）③stats 抬回 ④Mission/end 入账 | 15 min | M6 已完成一次；可脚本化为回归。⚠ 该数据集 GetMotd 是**副号** `76561199881579811`，要"主号口径"须换 `D:\mod\tmp\s4-fixtures-main` |
| **S4** | **存档回放的客户端可见性**（覆盖层最后一公里）| `save-export` 产物 → `mockctl serve --plain --players-dir <导出根>` → 客户端登录该账号（**定稿命令与口径见 §7**）| 游戏内**界面**显示该玩家进度/物品/成就（不是只看 mock 响应字节）| 20–30 min | ⚠ **从未验过**（我方此前只验证了 mock 返回正确 JSON）|
| **S5** | 跨会话/跨天持久性 | 结算后关闭引擎与游戏，次日再开 | pending 24h 上限、会话基准重建一次、启动不再等身份 | 跨天 | 部分单测覆盖，缺实机 |
| **S6** | 异常回滚 | 手工破坏账本/`.bak`/半截写，再启动 | 回退到 `.bak`、不丢整档、日志有 WARN | 10 min | 单测覆盖较多，实机未压 |
| **S7** | 购买/消耗写路径容忍度 | 在自建服（mock 回 `200 {}`）里买一件物品/消耗战备 | 游戏不崩、不重复扣、账本按响应/`expectedCost` 兜底；界面状态合理 | 15 min | ⚠ **未验**，自建服必踩（mock 对这两个端点当前是 fallback `{}`）|

## 3. 服务器（A 机）部署：能做什么、值不值得

**存档机制本身不需要 A 机**：引擎 `upstreamTest` 直连本机 `127.0.0.1:9099` 明文 mock 即可（M6 就是这么做的）。

**A 机部署只在下面这些维度有价值**（如果要做，需先部署覆盖层代码）：
1. **真实网络路径**：hosts → A:443 → nginx → mock，验证 TLS/反代链路下存档与发现层不互相干扰；
2. **按玩家分档在真实部署上跑**：A 机目前**没有** `mock/players.py`，需部署 3 个文件（`mock/players.py`、`mock/handler.py`、`tools/mockctl.py`）+ profile 配 `players_dir`，约 10 分钟；
3. **服务重启后的持久性**：A 机重启 nginx/mock 后，`players/` 文件不被覆盖、账本状态不串。

**前置/风险**：
- A 机证书 11-25 20:07 到期（可用）；若要本机起 443 与 MITM 线引擎互斥（窗口约定不变）；
- 部署后要同步 `.deployed-rev` 哈希（否则一致性检查会报 diff）；
- A 机发现层现有 1 个探针残留账号（`probe-fe8154a2`），做发现层相关测试前需重置。

## 4. 哪些确实需要志愿者/第二客户端（明确划线）

| 需要 | 原因 |
|---|---|
| 双客户端同局结算一致性 | 两个玩家打同一任务，双方账本各记一次、服务端回放是否一致 |
| 好友列表可见性 / 加入路径 | 志愿者客户端不持有我的 account id（已实测） |
| 3 人以上（B5） | 单未知绑定在候选 ≥2 时不猜，需第三个账号/人 |

## 5. 建议顺序（今晚可启动）

1. **S2（换号不混档）** —— 单人切号即可，正好替 MITM 线把 ④ 先验了；需要他们起一次**隔离状态**的引擎（他们已有关键配置，1 分钟）。
2. **S4（回放可见性）** —— 本机 mock 直接用现成 save-export 样例，最接近"自建服真能用"的验收。
3. **S7（购买/消耗）** —— 自建服必踩路径，做完就知道要不要给 mock 加写端点回显。
4. 之后把 S1/S3 固化成"一键回归"（起 mock → 起隔离引擎 → 打一局 → 跑判据），A 机部署排在最后。

> 依赖：S2/S4/S7 都需要 **MITM 线起隔离引擎**（他们的 `v0.9.0-alpha.2` + 隔离状态目录）；
> mock 侧与判据脚本我方随时可起，不需要额外人力。

## 6. MITM 线修正（2026-11-24 20:50，三处我原方案会踩空）

对照他们的隔离引擎说明（`D:\mod\tmp\save-test-20261124\README-for-research-line.md`）后修正：

| # | 我原方案 | 修正后 | 原因 |
|---|---|---|---|
| C1 | `--data state/dist/save-fixtures-fresh-raw` | **`--data D:\mod\tmp\s4-fixtures-main`** | `save-fixtures-fresh-raw` 的 GetMotd 是**副号** `76561199881579811`，拿它当主号基线会看到别人的界面 |
| C2 | `--players-dir save-export-sample-mission` | **`--players-dir D:\mod\tmp\save-export-sample`** | `-mission` 的 manifest 是 exp 1146 / missions 2 口径；`save-export-sample` 才是 exp 210 / 成就 4 / 统计 23 非零 |
| C3 | 期望库存 `3608481516=910` | **回放值 = 1000** | 空号基线 ×1000，`max(1000, 910) = 1000`；910 只在 M6「服务端空」口径出现（那是账本值，不是回放值）|

配套修正：
- **S4 判据**改为「引擎 **0 条 `SAVE REWRITE/INV/STATS`**」。save-export 产物已含账本值，若引擎还要「把账本写回服务端」说明合并没生效；
  而 M6 的 SAVE 行是「服务端空 → 引擎补写」的签名，**两者不能混用**。
- **`settlements.jsonl` 不是运行时产物**：运行时账本只在 `earned.missions.last_response`；
  `settlements.jsonl` 由 `hd2proxy save-export` 生成 ⇒ S2/S4 期间隔离目录里**不该**出现它（出现即 bug，或说明有人跑了导出）。
- **`_orphan/` 的前提**：换号前**本局有写入**（整档回滚 + 脏档另存，设计内）；只登录进船→切号**不该**出现。

### 6.1 20:56 环境更新（覆盖 §1 对应行）

| 项 | 20:56 实况 |
|---|---|
| 本机 443 | `100.67.88.219:443` LISTENING（pid 25220，引擎 `0.9.0-alpha.2`，S2 隔离配置；20:54:08 重启过一次，旧 pid 38852 于 20:53:03 退出）|
| 本机 hosts | HD2 行在（测试期间**不能清**，游戏靠它连引擎）|
| 9099 | 空闲（留给 S4 的 mock）|
| 游戏进程 | 未运行（Steam 在跑）|
| S2 状态 | before 快照已取，**尚未开跑**（无第二账本、日志无换号记录）|

## 7. S4 定稿执行口径（可直接照抄）

```bash
# ① MITM 线把隔离引擎切到 S4 配置（他们约 15 秒）：加 --config .../s4/legacy-cfg-9099.json
#    ⚠ v2 profile 没有 upstreamTest 字段，profile + legacy-cfg 两个都必须给
# ② 我方起 mock（明文，127.0.0.1:9099）
python tools/mockctl.py serve --plain \
  --data D:/mod/tmp/s4-fixtures-main \
  --players-dir D:/mod/tmp/save-export-sample \
  --port 9099 \
  --log-dir D:/mod/tmp/save-test-20261124/mocklogs
# ③ 用户主号（76561198968648344）启动游戏登录进船
# ④ 判定
python tools/mock_request_summary.py --log-dir D:/mod/tmp/save-test-20261124/mocklogs --json
```

**四条判据**：
1. 游戏**界面**显示：等级经验 **210** / 成就 **4** / 统计非零 **23 项**；
2. 装备/库存：`897894480 = 1`、`3608481516 = 1000`；库存总行数 **56**；
3. mock 日志命中 `[player:76561198968648344]`（证明按玩家分档生效，不是 fallback 到公共档）；
4. 引擎日志 **0 条** `SAVE` 行（幂等；出现即合并未生效）。

### 7.1 离线预跑证据（2026-11-24 20:57，mock 已按 §7 命令起过一次）

| 端点 | baseline（无覆盖层命中）| 主号覆盖层 | mock 日志标记 |
|---|---|---|---|
| `GET /api/Progression` | exp **1**（1500 B）| exp **210**（2468 B）| `[player:76561198968648344]` |
| `GET /api/Progression/inventory` | — | **56 行**、`3608481516=1000`、`897894480=1`（4574 B）| `[player:76561198968648344]` |
| `GET /api/Stats/profile/summary` | — | **27 项 / 23 非零**（813 B）| `[player:76561198968648344]` |
| `GET /api/Progression/Items/Customize/v2` | — | `{"items":[]}`（17 B）| `[player:76561198968648344]` |
| `GET /api/Progression/ItemPackages` | 4858 B | 同 baseline（**未覆盖**）| `[exact]` |

结论：**覆盖层在本地按 §7 命令完全可用**，四条判据的前三条在 mock 侧已可复现；
`ItemPackages` 是**目录数据**（不是玩家档），走 baseline 是设计而非缺口。

### 7.2 ★ 判据③：按请求记录解析依据（产品线 20:5x 对齐后定稿）

`[player:…]` 只证明"按玩家分档生效"，**不区分是哪条路径**。产品线确认他们**每个上游请求都盖头**，
只有 5 种情况（`server.py:38-53` 盖章、`:253-277` 调用点、`active_save.py:606-635` 取值）：

| 引擎状态 | 发出的头 | mock 侧期望 | 说明 |
|---|---|---|---|
| 有 `_last` 预绑定、未 GetMotd 校验（**S4 起步态**） | `Account:<主号>` + `Source: prebind` | **`header:prebind`** | 命中索引就照给，**不学 IP** |
| GetMotd 校验通过 | 同上 + `getmotd` | **`header:getmotd`** | 并学 IP |
| GetMotd 到但校验失败/换号异常 | **只发** `Source: uncertain` | `uncertain:…` → 回落 baseline | 设计内 |
| 冷启动无 `_last` | 不盖头 | `single`（索引 1 人）/ 空（≥2 人 ⇒ baseline） | 测试初始态才出现 |
| 客户端 lobby 自带头 | 不重复加（保留客户端值） | `header:<客户端 tag 或 unknown>` | 设计内，不是异常 |

⇒ **S4 里出现 `ip` = 事故**（说明引擎的身份头没到 mock），不是我原先以为的"可接受的降级"。
我原先的担忧（IP 学习"最后带头者获胜"会顶替身份头）在 S4 **不会触发**，因为 prebind 不学 IP、
且每个请求都带头。

**判据必须按请求看，不能事后读 `/health`**：`PlayerStore.last_source` 是 **sticky**（只保留最后一次解析），
多线程下还会被别的请求覆盖。为此 mock 已改为**每行日志带解析依据**：

```
21:07:17.937 GET    /api/Progression 0B -> 200 2468B [player:76561198968648344 via header:prebind]
21:07:17.987 GET    /api/Progression/inventory 0B -> 200 4574B [player:… via header:getmotd]
21:07:18.039 GET    /api/Progression 0B -> 200 2468B [player:… via ip]
```

实现：`PlayerStore.resolve_ex()` 与玩家**原子返回**依据 → `Response.resolution` → `_record["resolution"]`
→ 日志 `[source via resolution]`（`mock/server.py:_format_request_log`）。11 条单测锁死。

**实测解析分支**（本地干净重启后逐条验过，仅作背景语义）：
1. 无头 + 无学习 IP → baseline（exp 1）；
2. 带主号头 `getmotd` → exp 210，并学到 IP=主号；
3. 带副号头 → 副号档，IP 被改写成副号；4. 再发无头 → `ip` 路径拿到副号档。
（这两条只是说明 `ip`/`single` 兜底存在，**S4 不该走到**。）

### 7.3 S4 判据的收敛版（判据③细化）

| # | 判据 | 通过条件 |
|---|---|---|
| ① 进度 | 界面经验 210（不是 1）| 覆盖层生效 |
| ② 物品 | `3608481516=1000`、`897894480=1`、库存 56 行 | 覆盖层生效 |
| ③ 身份路径 | 日志出现 `[player:76561198968648344 via header:prebind]` 与 `via header:getmotd`；**出现 `via ip` 即失败** | 证明是**身份头**给的档，不是 IP 兜底 |
| ③′ 反证（可选）| `/health.players.learned_ips` 在 getmotd 后为 1、`last_header_source=getmotd` | 与请求日志互证 |
| ④ 幂等 | 引擎 0 条 `SAVE` 行 | 产物已含账本值 |

## 8. S2 判据（换号不混档）— 定稿

1. 主号账本 `steam_76561198968648344.json` **逐字节不变**（sha256 对比；`source=getmotd`、exp 210、库存 2 行不变）；
2. 新增 `steam_76561199881579811.json`（副号独立账本，identity 指向副号）；
3. `_last.json` 依次指向**最后登录**的账号；
4. 引擎日志出现 `⚠ 身份切换：…A（已识别）→ …B；不合并`；
5. `_orphan/` **不出现**（三趟都没打任务，本局无写入；出现了说明判据②的前提被破坏，要查日志）。

判据工具：
- 一条命令出结论（推荐）：
  `python tools/save_switch_verdict.py --state-dir <state> --before <before.json> --main 76561198968648344 --alt 76561199881579811`
  → 逐项 S2-1…S2-6 + 结论行，退出码 0=PASS / 1=FAIL（可直接进 CI）。10 条单测。
- 逐字段 diff（看细节）：`python tools/save_state_snapshot.py snapshot|diff`（9 条单测）。

### 8.1 判定实现（工具已固化，2026-11-24 20:5x）

| 项 | 依据 | 通过条件 |
|---|---|---|
| S2-1 | 主号账本 `identity.steam_id` | == 主号（**状态为准**，不看日志）|
| S2-2 | 副号账本存在性 + `identity.steam_id` | 已创建且 == 副号 |
| S2-3 | 主号 `earned` 字段级对比 before | exp / `inventory_rows` / `achievements_count` 三者不变；`identity` 除 `source` 外不许变 |
| S2-4 | `logs/proxy.log` | 两条换号痕迹齐全（主→副、副→主）；接受 `身份切换：A（已识别）→ B` 与 `身份确认：A（local/未识别）已合并进 B` 两种文案 |
| S2-5 | 日志「会话回滚」行 + `runtime/saves/_orphan/` | 都为 0/空（本局无写入）|
| S2-6 | `_last.json` | `last_steam_id == 主号`（最后登录）|

设计取舍（实测踩出来的）：
- **主号账本 sha 变化不算失败**：引擎重写同内容或刷 `updated_at` 都会变；真正的混档证据是 S2-3 的字段级变化。
- **S2-6 是弱判据**：测试开始前它天然为 PASS（最后登录本来就是主号），别把它当"换号成功"的证据。
- **S2-4 依赖产品文案**：文案在 `src/hd2proxy/engine/active_save.py:760`（warn 通道）与 `saves.py:797`（log 通道），
  两处措辞略有差异（`**不合并**` vs `不合并`），工具用 `身份切换：` / `身份确认：` 前缀匹配，改文案时需同步工具。
- 产品只在「**旧档已识别**」时才打 `身份切换`；副号档首次为 `local/未识别` 时走「已合并」，两种都算过。
- 产品线 2026-11-24 答复：**实跑只会出现 `active_save.py:760` 那条**；`saves.py:797` 的
  `身份切换：旧档 X（已识别）→ 新档 Y` 变体目前只在单测里（`tests/unit/test_saves.py:353/363`），
  但将来可能合并两条路径 ⇒ 工具正则已顺带容错 `旧档/新档` 前缀（1 行，防未来变化）。
