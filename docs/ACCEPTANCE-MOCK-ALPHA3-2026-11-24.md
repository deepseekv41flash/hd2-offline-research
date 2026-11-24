# 存档层 mock 验收记录：alpha.3 × 研究线明文 mock — 2026-11-24

> **一句话**：MITM 线的产品引擎 `v0.6.0-alpha.3` 经 `upstreamTest` 打到研究线明文 mock
> （`127.0.0.1:9099`，数据集 = 主号身份 + 空号水平数据 + 严格空库存），**四条判据全 PASS**，
> 双方日志三方对账一致。本次验收把「服务端给得很少甚至为空」这一最坏情况下的存档层兜底**实机跑通**。

## 1. 验收设定

| 项 | 值 |
|---|---|
| 引擎 | 产品 `v0.6.0-alpha.3`，隔离状态目录 `D:\mod\tmp\mock-accept-20261124\state`（生产账本副本，测试结算不污染真档）|
| 传输 | 明文 HTTP：引擎 `upstreamTest: "127.0.0.1:9099"` → 研究线 mock（只绑回环）|
| profile | `mock-accept`（saves 开、`preverify_merge: full`、抓包开）|
| 数据集 | `state/tmp/accept-main-identity/` = 空号基线 `state/dist/save-fixtures-fresh-raw/` **只改 3 个文件**（见 §2）|
| 时间窗 | 2026-11-24 11:42:18 – 11:46:59（用户客户端一趟完整启动 + 一局结算）|
| 443/hosts | 验收期间归 MITM 线（研究线不用；mock 只走回环，零冲突）|

## 2. 数据集改动（只改 3 个文件，其余逐字节未变）

为什么要改身份：用户客户端登录的是**主号**，若 mock 报空号身份，存档层会判定"换号"并切空档，
①–③ 的"账本兜底"就演示不出来。改成主号身份 + 服务器数据退化成空号水平 = 模拟"官方服务器把主号数据弄丢了"。

| 文件 | 改动 | 证据 |
|---|---|---|
| `Pes/GetMotd` | `accountId` → `76561198968648344`（主号 SteamID64）| `state/logs/acceptance-20261124/dataset-diff.txt` |
| `Pes/GetMessages` | `accountId` → `11893343864225233967`（主号 HD2 20 位别名）| 同上 |
| `Progression/inventory` | → `{"items":[],"customization":[],"loadouts":[]}`（严格空，45 B）| 同上 |

其余保持空号基线：`Progression` 1500 B（exp=1、成就 0、pkgProgress=9、spi=0）、
`Stats/profile/summary` 644 B（27 key 全 0）、`Operation/Mission/end` 1614 B（固定模板 `experience=936`）。

diff 校验：`diff -rq state/dist/save-fixtures-fresh-raw state/tmp/accept-main-identity` 只有上述 3 个文件不同。

## 3. 四条判据（双方结论）

| # | 判据 | mock 输入 | 引擎结果（MITM 线） | 判定 |
|---|---|---|---|---|
| ① | Progression 兜底 | exp 1 / 成就 0 / pkg 9 / spi 0 | exp → **210**、成就 → **4 条** `{6:1680,8:386242,17:4290,24:2925}`、pkg **9**（账本无包进度，保持服务端值）、spi 0 | ✅ |
| ② | inventory 全量回填 | `items=[]`（45 B）| **`897894480×1`、`3608481516×910`**（恰 2 行）| ✅ |
| ③ | Stats 逐 key max | 27 key 全 0 | **23 项**被抬回（账本 27 项里 4 项本来为 0）| ✅ |
| ④ | Mission/end 入账 | 固定模板 `experience=936` | 账本 exp **210 → 1146**；库存 `897894480 1→8`、`3608481516 910→5344`；missions 1→2；包进度 `{277818845:{level:5,progress:459}}` | ✅ |

引擎侧关键日志（`state/logs/proxy.log`）：
`11:42:19 SAVE REWRITE +5 field(s)` / `SAVE INV +2 行` / `SAVE STATS +23 field(s)`；
`11:46:23 MISSION +936 exp · +2 种物品 · 3608481516×4434 897894480×7 · 1 个包进度`；
身份 `getmotd 身份校验一致 → steam_76561198968648344`，**无换号、无 orphan、warnings 空**。

## 4. 三方对账（研究线 mock 日志 × 引擎日志 × 引擎 capture）

研究线导出：`tools/mock_request_summary.py state/logs/acceptance-20261124/mock.log --since 11:42:00 --until 11:47:30`
→ `request-summary.txt` / `request-summary.json`（65 行、37 个端点模板）。

| 对方关心 | mock 侧实际 | 一致？ |
|---|---|---|
| `GET /api/Progression` 三次都命中 `exact`？ | **1 次**（11:42:19.563），`[exact]` 1500 B | ✓ 与"每次启动读一次"吻合 |
| `GET /api/Progression/inventory` | **1 次**（11:42:19.596），`[exact]` **45 B** | ✓ 注入前原始响应就是空数组 |
| `GET /api/Stats/profile/summary` | **1 次**（11:42:19.709）`[exact]` 644 B；`/801/summary` 另 1 次（11:42:19.710）644 B | ✓ 与引擎日志"两个 summary 各一次"吻合 |
| `POST /api/Operation/Mission/end` 只有 1 次？ | **1 次**（11:46:23.078）→ 1614 B `[state:mission_end]` | ✓ 与 `MISSION +936` 时间戳一致 |
| 身份 | `POST /api/Pes/GetMotd` 11:42:22 → 136 B `[exact]`，accountId=主号 | ✓ 与"getmotd 身份校验一致"一致 |

**额外发现（不影响判定，但值得记）**：验收窗口内有 3 次写端点落到了 `[fallback]`——
`PUT /api/Progression/OnboardingState`×2、`PUT /api/Progression/ShipName`×1，mock 回 `200 {}`（不落盘）。
它们是客户端→服务器的设置写，存档层不消费其响应；若将来验收要覆盖"设置类写回显"，需要给 mock 加对应回显。

## 5. 遗留与说明

| 项 | 状态 |
|---|---|
| "服务端更大时听服务端"语义 | ⚠ 本次按"严格空"验全量兜底；该语义由 MITM 线 e2e 单测（999999 用例）覆盖，未上实机 |
| 主号身份切换 | 是**测试构造**（真实场景里服务器不会把主号 identity 换成空号水平，但会丢数据）——记录在案，不要当成"真实故障复现" |
| 账本基准值 | 对方账本 `totalEnemyKills=356105`（昨晚主号一局 +40，从 356065 涨上来）；**356065 才是 11-18 原始基线**，勿混用 |
| 真账本 | 未被本次测试改动（对方已校验：exp 210 / 成就 4 / 库存 910+1 / 统计 27 / missions 1，`_last` 未变）|

## 6. 收尾

- 研究线 mock 已停（`127.0.0.1:9099` 释放）；本次验收不占 443/hosts/A 机。
- 数据集与日志留存：`state/tmp/accept-main-identity/`、`state/logs/acceptance-20261124/`（`mock.log`、`dataset-diff.txt`、`request-summary.{txt,json}`）。
- 对账工具：`tools/mock_request_summary.py`（支持 `--since/--until` 时间窗、`--json`；8 条测试）。
- 对方记录：`docs/ACCEPTANCE-M4.md` 新增 §M6、CHANGELOG 一条（docs-only）。

## 7. 对方收口回执（2026-11-24 12:0x）

- 四项对账 MITM 线复核**全部一致**，记为「三方对账 PASS」。
- 3 次 `[fallback]` 设置写确认对判据无影响；「设置类写回显」列入双方**遗留项**（需要时再约，我方加两个端点回显即可）。
- 对方已把三方对账表补进产品仓 `docs/ACCEPTANCE-M4.md` §M6，提交 `1ec1f71` 已推送；
  证据包 `D:\mod\tmp\evidence-m6-mock\`（含 `verification.json` 四条 pass 布尔）与隔离测试目录保留可复核。
- 生产引擎已恢复 alpha.3（默认 profile、443、`verify` 3/3 绿）；9099 已释放。
- **结论：mock / 自建服侧无未结项；存档线 ④ 与 M6 到此闭环。**
