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

### 7.1 离线预跑证据（2026-11-24 20:57 起 21:13 复核 sha，mock 已按 §7 命令起过三次）

| 端点 | baseline（无覆盖层命中）| 主号覆盖层 | mock 日志标记 |
|---|---|---|---|
| `GET /api/Progression` | exp **1**（1500 B）| exp **210**（2468 B）| `[player:76561198968648344]` |
| `GET /api/Progression/inventory` | — | **56 行**、`3608481516=1000`、`897894480=1`（4574 B）| `[player:76561198968648344]` |
| `GET /api/Stats/profile/summary` | — | **27 项 / 23 非零**（813 B）| `[player:76561198968648344]` |
| `GET /api/Progression/Items/Customize/v2` | — | `{"items":[]}`（17 B）| `[player:76561198968648344]` |
| `GET /api/Progression/ItemPackages` | 4858 B | 同 baseline（**未覆盖**）| `[exact]` |

**sha256 全等（21:13 复核，硬判据）**：mock 按玩家分档时把响应体 sha256 写进日志，
与导出文件逐位比对**全等**，且与产品线经其引擎独立测得的哈希**完全一致**：

| 端点 | 字节 | sha256（前缀）| mock 日志 |
|---|---|---|---|
| `/api/Progression` | 2468 | `0f4893f9f853` | `[player:76561198968648344 via header:getmotd sha:0f4893f9f853]` |
| `/api/Progression/inventory` | 4574 | `c1583d25c160` | `… via header:getmotd sha:c1583d25c160` |
| `/api/Stats/profile/summary` | 813 | `02e6e6633d13` | `… via header:getmotd sha:02e6e6633d13` |

⇒ "同字节数可能是不同字节"，所以 S4 用 **sha256 全等**判定，不用字节数。

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

### 7.3 S4 判据的收敛版（一条命令 + 人看部分）

```bash
# 起 mock 后、用户登录并进船之后跑：
python tools/check_s4_replay.py   --mock-log D:/mod/tmp/save-test-20261124/mocklogs   --engine-log D:/mod/tmp/save-test-20261124/s4/state/logs/proxy.log   --export D:/mod/tmp/save-export-sample --steam 76561198968648344
```

| # | 判据 | 通过条件 | 谁判 |
|---|---|---|---|
| ① 进度 | 界面经验 **210**（不是 1）| 覆盖层生效 | **人看**（游戏内界面）|
| ② 物品 | 界面/军械库显示 `3608481516=1000`、`897894480=1` | 覆盖层生效 | **人看** |
| ③ 内容等同 | 三个玩家端点响应 **sha256 全等**导出文件 | 确实原样转发了这份档 | 工具（S4-1）|
| ④ 身份路径 | 日志 `via header:prebind` **且** `via header:getmotd`；**`via ip` 出现即失败** | 是身份头给的档，不是 IP 兜底 | 工具（S4-2）|
| ⑤ 幂等 | 引擎 **0 条 `SAVE`** 行 | 产物已含账本值，引擎不该往回写 | 工具（S4-3）|

### 7.4 ★ S4-1 的失效条件（产品线指出，违反必致假阳/假阴）

`sha256 全等` 的前提是 **S4 隔离态账本与导出产物同口径 ⇒ 引擎合并零改动**。若账本某项**高于**产物，
引擎按"只抬不降"合并 ⇒ 响应改变是**正确行为**，sha 自然不等。因此：

1. **固定用预置的 `s4/state`**（内含主号档副本），不要换成"用户刚打了几局"的新账本；
2. 若必须换账本，判据退回"④ `via` + ①②界面数值"，工具会把 sha 不符标成 **SKIP（说明可能是合并）** 而不是 FAIL；
3. `customize_v2.json` 同口径可比（`{"items":[]}`，17 B）——它**不在** sha 三端点里，仅作旁证。

工具实现：`tools/check_s4_replay.py`（10 条单测）；mock 侧 `Response.digest` + 日志 `sha:<12>`（10 条单测）。

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
| S2-3 | 主号 **`earned` 整块逐字段等**（exp / 成就数 / inventory_rows / inventory_top / inventory_amount_sum / stats_nonzero / missions_count / missions_last / purchases_count …）| 任意一项变化即 FAIL；`identity` 除 `source` 外不许变。**不拿 sha 当判据**（身份重写必刷 `updated_at`）|
| S2-4 | `logs/proxy.log` | 两条换号痕迹齐全（主→副、副→主）；接受 `身份切换：A（已识别）→ B` 与 `身份确认：A（local/未识别）已合并进 B` 两种文案 |
| S2-5 | 日志「会话回滚」行 + `runtime/saves/_orphan/` | 都为 0/空（本局无写入）|
| S2-6 | `_last.json` | `last_steam_id == 主号`（最后登录）|

设计取舍（实测踩出来的）：
- **主号账本 sha 变化不算失败**：引擎重写同内容或刷 `updated_at` 都会变；真正的混档证据是 S2-3 的字段级变化。
- **`.bak` 出现/更新 = 正常**（`set_identity()` 无条件写盘会轮转 `.bak`；产品线实测 A 档每次身份重写都刷）——工具只打 `info-bak`，不判失败。
- **S2-6 是弱判据**：测试开始前它天然为 PASS（最后登录本来就是主号），别把它当"换号成功"的证据。
- **S2-4 依赖产品文案**：文案在 `src/hd2proxy/engine/active_save.py:760`（warn 通道）与 `saves.py:797`（log 通道），
  两处措辞略有差异（`**不合并**` vs `不合并`），工具用 `身份切换：` / `身份确认：` 前缀匹配，改文案时需同步工具。
- 产品只在「**旧档已识别**」时才打 `身份切换`；副号档首次为 `local/未识别` 时走「已合并」，两种都算过。
- 产品线 2026-11-24 答复：**实跑只会出现 `active_save.py:760` 那条**；`saves.py:797` 的
  `身份切换：旧档 X（已识别）→ 新档 Y` 变体目前只在单测里（`tests/unit/test_saves.py:353/363`），
  但将来可能合并两条路径 ⇒ 工具正则已顺带容错 `旧档/新档` 前缀（1 行，防未来变化）。

## 9. S2 实机结果（2026-11-24 21:16–21:21，判定 PASS）

**操作**：主号 `76561198968648344` 进船→退；Steam 切副号 `76561199881579811` 进船→退；
切回主号进船→退。三趟均**未打任务**。引擎 = MITM 线隔离配置（`100.67.88.219:443`，pid 25220），
状态目录 `D:\mod	mp\save-test-20261124\s2\state`。

**判定**（`tools/save_switch_verdict.py`，6/6 PASS）：

| 项 | 判据 | 结果 |
|---|---|---|
| S2-1 | 主号账本在、`identity.steam_id` = 主号 | PASS |
| S2-2 | 副号账本新建、`identity.steam_id` = 副号 | PASS |
| S2-3 | 主号 **`earned` 全等**（exp 210 / 库存 2 行 / 成就 4 / missions 1）| PASS |
| S2-4 | 日志两趟换号齐全（A→B、B→A）| PASS |
| S2-5 | 主号档**未被回滚、未被另存** | PASS |
| S2-6 | `_last.last_steam_id` = 主号（最后登录）| PASS |

**旁证（`save_state_snapshot.py diff`，8 处变化全部可解释）**：
新增副号账本；主号 `updated_at`/`file_sha` 变化 + `.bak` 出现（身份重写的正常副作用）；
`_last.pending` 清空（校验通过后消失）；`_session-start` 以新 `run_id` 重建（会话基准在换号点重建）。

### 9.1 ★ 实机新发现一：只进船也可能 `本局写入=有`（回滚命中副号）

切回主号时日志：

```
21:21:14 ⚠ 身份切换：steam_76561199881579811（已识别）→ steam_76561198968648344；**不合并**。
         本局写入=有，回滚=restored，脏档另存 …\_orphan61124-212114-steam_76561199881579811-identity-switch.json
```

orphan 的 `discarded` 只有 1 项 `stats.profile`（首屏**安全合并**写入的服务端自算字段）。
即：**新账号首次合并建账本身就算会话写入**，所以即使不打任务，换走时也会整档回滚。

- 设计上正确（脏档全文 + 丢弃摘要留在 `_orphan/` 可审计），**不是混档**；
- 但副号账本从 1669 B 回滚到 802 B，下次副号登录会重新从服务端合并 —— 已请产品线确认是否符合预期；
- ⇒ **判据语义修正**：`_orphan/` 出现不是失败信号，"**被回滚的是谁的档**"才是判据
  （主号被动 = FAIL；其他账号 = info）。工具 S2-5 已按此实现。

### 9.2 ★ 实机新发现二：`_last.last_hd2_account_id` 会变 null

切回主号那一刻本局尚未观测到 HD2 别名，`_last.json` 写成
`{"last_save_id": "steam_…A", "last_steam_id": "…A", "last_hd2_account_id": null}` 且之后未刷新。
账本内 `identity.hd2_account_id` 仍正确（`11893343864225233967`）。
影响很小（下次 prebind 只用 `save_id`），已作为可选改进报给产品线（GetMotd 校验通过后补写 `_last`）。

### 9.3 结论

**换号不混档（MITM 线欠的 ④）在实机上通过**：两个账号各自独立账本、主号 `earned`/`identity` 零污染、
换号两趟都有日志痕迹、无跨账号数据串档。副产品是上面两条对 G2 行为的精确化认知。

## 10. S4 实机结果（2026-11-24 21:33，机器判据 PASS）

**环境**：引擎 `100.67.88.219:443`（pid 4980，S4 profile，`--config legacy-cfg-9099.json` ⇒ 上游 `127.0.0.1:9099`）；
我方 mock `127.0.0.1:9099`（`--data s4-fixtures-main --players-dir save-export-sample`）；
用户用**主号** `76561198968648344` 登录（未切号、未打任务）。

**判定**（`tools/check_s4_replay.py --health-url http://127.0.0.1:9099/health`）：

| 判据 | 结果 | 证据 |
|---|---|---|
| S4-1 内容等同 | **PASS** | `/api/Progression` 2468B `0f4893f9f853`、`inventory` 4574B `c1583d25c160`、`Stats/profile/summary` 813B `02e6e6633d13` —— mock sha == 导出文件 sha（产品线经引擎抓包第三方比对亦全等）|
| S4-2 身份路径 | **PASS** | 5 条玩家档请求全部 `via header:prebind`；**无 `ip`/`single`/空** |
| S4-3 幂等 | **PASS** | 引擎 **0 条 `SAVE`** 行（另有 `earned 改动 0 字段`、`verified=true`）|
| ①② 界面数值 | ⚠ **未确认** | 用户主动收工，未做人眼确认（exp 210 / 成就 4 / 库存 56 行 / 统计 23 非零）|

### 10.1 ★ 判据修正：`via header:getmotd` 不能作为硬判据

真实时序（我方 mock 日志 + 产品线 `s4/evidence.md` 双向一致）：

```
21:33:46.605 GET  /api/Progression            [via header:prebind]   ← 首屏 5 条玩家档读全在这一秒
21:33:46.778 GET  /api/Stats/profile/summary  [via header:prebind]
21:33:49.786 POST /api/Pes/GetMotd            ← 比首屏晚 3.2 秒，且是 POST 不是 GET
21:33:50+     lobby/friends…                  ← 此后不再重读玩家档
```

⇒ **GetMotd 永远晚于首屏玩家读**（这正是 prebind 存在的理由），之后游戏只在 lobby/friends 端点解析身份
（体现为 `/health.last_header_source=getmotd`），不会再有玩家档请求带 `getmotd`。
所以 S4-2 定为：**所有玩家档请求必须靠身份头返回**（出现 `ip`/`single`/空即失败），
"身份升级为 getmotd"降为**信息项**。

### 10.2 另一个别当证据缺失的点

引擎抓包按**内容 sha 命名且跳过已存在文件**（`hooks/capture.py:69-71`），所以本轮没有新的 `gwe-dump` 行。
**判据不要依赖"出现新的 gwe-dump 行"**——同 sha 的抓包文件本来就在。

### 10.3 结论

**存档回放可见性（自建服最后一公里）在实机上打通**：客户端首屏读到的就是 `save-export` 产物本身
（逐字节等同），身份路径是引擎身份头而非 IP 兜底，引擎全程零合并写入。
唯一未闭环的是**界面数值的人眼确认**（用户主动收工）；机器判据已足以支持"回放数据确实到了客户端"。
