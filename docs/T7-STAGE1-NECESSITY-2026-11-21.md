# T7：阶段1 的必要性差分——哪些旁路可以断、哪些是硬依赖（2026-11-21）

> 问题（用户）：阶段1 只做单人，PlayFab（Multiplayer Lobby / Party 中继区域选择）是多人相关的，
> 是否可以在阶段1 不接管？另外这些研究对阶段2 自建联机有什么帮助？
> 方法：用 T6a 的 MITM 能力做**受控断开**（blackhole=模拟不可达 / fake-200=模拟本地私服一律回 200 `{}`），
> 逐组跑「启动 → 主界面 → 稳定性观察」，判定是否阻断。三态：`✓✓ 实机` / `⚠ 未证` / `✗ 否证`。

## 0. 一句话结论

**PlayFab 与 Sony 侧在阶段1 可以不管（断掉、伪接管都能正常进主界面；PlayFab 断开的**完整一局**也已通过）；EOS/EAC 是硬依赖，
而且**两种处置都被拦**：断开 → EAC 弹「无法连接反作弊网络，游戏将退出」；伪 `200 {}` → 同样被拒。
⇒ EAC 需要与真实服务完成协议交互，"本地假服务"骗不过去。**
而 PlayFab 虽然阶段1 非必需，但它**不能被"空应答"糊弄**：Party/Lobby 链在第一步 `ListPartyQosServers`
就放弃 ⇒ 阶段2 要自建联机，**必须保留 PlayFab 云**（或完整实现其协议），"完全自建联机"不可行。

## 1. 方法

| 项 | 值 |
|---|---|
| 工具 | `tools/t7_probe.py`（起 MITM 代理 → 临时改 IE 代理 → 启动游戏 → 轮询 mock 日志 → 稳定观察 25 s → 关游戏 → 恢复代理） |
| 断开方式 | `tools/mitm_proxy.py --blackhole-hosts <域名>`：对 CONNECT 直接回 502（模拟不可达）<br>`--fake-hosts <域名>`：TLS 终止后所有请求回 `200 {}`（模拟本地私服） |
| 官方 API | 始终由本机 mock 应答（`--mock-hosts` + 研究 CA 校验上游），保证"只有被测旁路被改变" |
| 判据 | `stable` = 到主界面后 25 s 内游戏未退出；`steady_polling` = 该窗口 mock 请求 ≥ 15 行（主界面稳态轮询）；两者都满足才记 `stable`。**注意**：EAC 弹框时 `helldivers2` 进程仍存活，所以单看进程会误判——R2 的 `stable=True` 就是这种假象，用轮询行数（7 行）纠正 |
| 证据 | `state/evidence/t7/*.json`（每轮结论）、`mitm-*.jsonl`（该轮旁路日志）、`eac-error-r2-*.png`（EAC 弹框截图，用户提供） |

## 2. 结果（6 轮 = 3 组 × 两种断开方式，均为实机）

| 组 | 断开方式 = **不可达**（blackhole） | 断开方式 = **伪接管**（一律 `200 {}`） |
|---|---|---|
| **PlayFab** | R1：24 s 到主界面 / 35 行稳态 → **不阻断**（用户「正常进」） | R4：24 s / 39 行 → **不阻断**（用户「正常进」） |
| **EOS/EAC** | R2：7 行、EAC 弹「无法连接反作弊网络，游戏将退出」→ **✗ 阻断**（2 次复现） | R6：7 行、**同样弹框退出** → **✗ 阻断**（用户截图确认） |
| **Sony** | R3：28 s / 41 行 → **不阻断**（用户「正常进」） | R5：44.7 s / 42 行 → **不阻断**（用户「正常进」） |

**这张矩阵的含义**：

* PlayFab / Sony 组：两种处置都不阻断 ⇒ 阶段1 **可以完全不接管**，客户端会重试（R1 有 26 次 CONNECT 被拒）但不会卡住；
* **EOS/EAC 组：两种处置都被拦** ⇒ 它不是"连不上"的问题，而是 **EAC 需要与真实服务完成协议交互**，
  给个 `200 {}` 假响应对它无效（EAC 会对响应做真实性校验/解析）。
  ⇒ 阶段1 想绕开 EAC 网络，只能走 **EAC-off 启动**，不能靠"本地假服务"。

### 2.1 关键细节：PlayFab 断掉后，Party/Lobby 链是「优雅放弃」而不是「卡死」

R1（黑洞）与 R4（伪 200）里，**都没有出现** `Party/RequestParty` 和 `Lobby/CreateLobby`：

```
R1: ListPartyQosServers → CONNECT 502（重试多次，26 次黑洞）
R4: ListPartyQosServers → 200 {}（缺 QosServers 字段）
    → 两种情况 SDK 都在这一步放弃，后续 RequestParty/CreateLobby 不再调用
    → 游戏照常进主界面、单人可玩
```

对比 T6a 正常放行时的顺序：`ListPartyQosServers`(拿到 17 区) → `pubsub/negotiate` → `Party/RequestParty`(分配中继) →
`Lobby/CreateLobby`(签发 connectionString)。**第一步拿不到有效数据，后面整条链就不走了。**

### 2.2 T7b：整局验收（两轮，均 ✓✓：2026-11-21 12:39–12:45 / 12:49–12:55）

主界面不阻断只是第一步；阶段1 真正要证的是"整局也能打"。用 `tools/t7b_probe.py` 把流程包起来
（起 mock + MITM、PlayFab 黑洞、改 IE 代理、启动、等用户打局、收尾复原）：

| 时刻 | 端点 | 请求 | 响应 | 命中 |
|---|---|---|---|---|
| 12:39:57 | `POST /api/Operation/Create` | 301 B | 200 / 60 B | `state:operation_create` |
| 12:40:53 | `POST /api/Operation/Mission/Start` | 167 B | 200 / 52 B | `state:mission_start` **← 进图**（主界面后 120 s） |
| 12:44:45 | `POST /api/Progression/inventory/consume` | 233 B | 200 / 2 B | `state:progression_consume` |
| 12:44:45 | `POST /api/Operation/Mission/end` | 866 B | **200 / 1,617 B** | `state:mission_end` **← 结算**（进图后 232 s） |
| 12:44:52 | `PUT /api/Progression/Achievements` | 141 B | 200 / 2 B | `state:progression_achievements` |
| 12:44:52 | `POST /api/Mission/Session` | 1,584 B | 202 / 0 B | `state:mission_session` |

* 主界面 **27.1 s**；窗口内 **59 条请求、0 个非 200、0 fallback**；
* `Abandon` 未在本窗口出现（探针在结算后 20 s 收尾，T3b 里它是结算后约 42 s 才发，属时序问题，不影响结论）；
* ⇒ **PlayFab 完全不可达时，单人「进图 → 局内 → 撤离结算」全链路正常** ⇒ 阶段1 可以不接管 PlayFab（`✓✓`）。

**第二轮 = 阶段1 的真实部署形态：PlayFab + Sony 四域同时黑洞，EOS/EAC 保持可达**（12:49–12:55）：

| 时刻 | 端点 | 请求 | 响应 |
|---|---|---|---|
| 12:49:38 | `POST /api/Operation/Create` | 300 B | 200 / 60 B |
| 12:50:47 | `POST /api/Operation/Mission/Start` | 167 B | 200 / 52 B **← 进图**（主界面后 104 s） |
| 12:54:46 | `POST /api/Progression/inventory/consume` | 233 B | 200 / 2 B |
| 12:54:46 | `POST /api/Operation/Mission/end` | 864 B | **200 / 1,617 B ← 结算** |
| 12:54:52 | `PUT /api/Progression/Achievements` | 141 B | 200 / 2 B |
| 12:54:52 | `POST /api/Mission/Session` | 1,577 B | 202 / 0 B |

* 主界面 **24.0 s**；窗口内 **59 条请求、0 个非 200、0 fallback**；
* MITM 侧统计：`974a9.playfabapi.com` 被黑洞 **55 次**（客户端反复重试），`crs.api.wwsga.me` **仅 1 次**；
  `testament/clientconfig/pss-cloud` **本局一次都没被调用**（说明单机流程里 Sony 侧实际只有崩溃上报通道偶尔出现）；
* ⇒ **阶段1 部署形态（官方 API 接管 + PlayFab/Sony 不可用 + EAC 可达）下，完整单局通过** `✓✓`。
  这是**阶段1 接管面的最终验收**。

### 2.3 EOS/EAC 为什么是硬依赖

`api.epicgames.dev` 同时承载 **EOS 身份/配置** 和 **EAC 反作弊消息通道**（T6a §2.2：`/anticheat/v6|v8/*`）。
EAC 启动时要做网络检查；断开后 EAC 直接失败并令游戏退出（截图见 `state/evidence/t7/eac-error-r2-first.png`）。

## 3. 对阶段1 的直接结论

**阶段1 最小放行清单**：

| 连接 | 阶段1 处置 | 依据 |
|---|---|---|
| 官方 API（`api.live.prod…`） | **必须接管**（mock 已在做） | T1–T3b |
| **EOS/EAC（`api.epicgames.dev`）** | **必须可达**（不能断） | R2：断开即 EAC 报错退出 |
| PlayFab（`974a9.playfabapi.com`） | **可不接管**（断开/空应答不阻断启动，且**整局任务链已通过**） | R1/R4 + T7b |
| Sony（`*.wwsga.me` / `pss-cloud.net`） | **可不接管** | R3 |

⇒ 也就是说：**阶段1 的产品不需要把 PlayFab 纳入接管面**，省掉一大块复杂度；只有 EAC 的网络前置必须满足。
**该接管面已通过整局验收**（T7b 两轮：仅 PlayFab 黑洞 / PlayFab+Sony 组合黑洞，均 0 错 0 fallback）。
若目标是「**完全无外网** 也能单机」，则 EAC-on 启动会失败，需要走 **EAC-off 启动**（直接跑
`bin\helldivers2.exe`，绕开 `start_protected_game.exe`；本机此前已实测该路径可行）——这是一个产品取舍，需用户拍板。

## 4. 对阶段2 自建联机的直接结论（比 T6a 更进一步）

1. **PlayFab 不能"糊弄"**：R4 证明对它一律回 `200 {}`，Party 栈会在第一步放弃 —— 所以阶段2 不能靠"假 PlayFab 应答"。
2. **自建信令只能解决"互相看见"**：大厅/中继分配/`connectionString` 全在 PlayFab（T6a §2.1）；
   T7 又证明这条链是**前置依赖**（QoS→Party→Lobby 顺序不可跳）。
3. ⇒ **可行形态只剩一个**：**自建 HD2 后端 + 保留 PlayFab 云**（Lobby + Party 中继照用官方），
   "完全自建（不依赖任何官方云）的联机"需要重写 PlayFab Party 协议与 DTLS 中继，工程上等同重做传输层，**不建议**。
4. 由此，B8 的判定可以升级：**PlayFab 标题 974A9 的存活是阶段2 的硬前提**——它一旦下线，自建联机无从谈起
   （与"入厅依赖 PlayFab"是同一件事）。

## 5. 三态判定

| 断言 | 状态 | 依据 |
|---|---|---|
| PlayFab 断开/伪接管不阻断单人启动到主界面 | **✓✓ 实机**（用户目视确认 + 35/39 行稳态轮询） | R1/R4 |
| Sony 侧断开不阻断 | **✓✓ 实机** | R3 |
| **PlayFab 不可达时完整单人任务可通过** | **✓✓ 实机** | T7b-1：27.1 s 主界面 → 120.2 s 进图 → 352.3 s 结算，59 请求 / 0 非 200 / 0 fallback |
| **PlayFab+Sony 同时不可达（阶段1 部署形态）时完整单局可通过** | **✓✓ 实机** | T7b-2：24.0 s 主界面 → 104.2 s 进图 → 340.3 s 结算，59 请求 / 0 非 200 / 0 fallback |
| EOS/EAC 断开 ⇒ EAC 报错、游戏退出 | **✓✓ 实机**（2 次复现 + 截图） | R2 |
| 空 `200 {}` 就能顶替 PlayFab 的联机功能 | **✗ 否证** | R4：Party/Lobby 链在第一步放弃 |
| 阶段1 需要接管 PlayFab | **✗ 否证（不需要）** | R1/R4 |
| 用「本地假 EAC 服务（200 {}）」可绕过 EAC 网络检查 | **✗ 否证** | R6：伪接管同样被拦、游戏退出 |
| 完全无外网下的 EAC-on 启动 | **⚠ 未证**（本轮只做"断开/伪接管"；EAC-off 直启路径见历史记录，未在本轮复测） | —— |

## 6. 复现

```powershell
cd D:\mod\work\hd2-offline-research
# 前提：研究 CA 已装（cert-install）、mock 在跑（mockctl serve）、state\certs 为多 SAN 证书
python tools\t7_probe.py --name playfab-blackhole --blackhole 974a9.playfabapi.com
python tools\t7_probe.py --name eos-blackhole      --blackhole api.epicgames.dev
python tools\t7_probe.py --name sony-blackhole     --blackhole crs.api.wwsga.me,testament.api.wwsga.me,clientconfig.api.wwsga.me,pss-cloud.net
python tools\t7_probe.py --name playfab-fake200    --fake 974a9.playfabapi.com
python tools\t7_probe.py --name sony-fake200       --fake crs.api.wwsga.me,testament.api.wwsga.me,clientconfig.api.wwsga.me,pss-cloud.net
python tools\t7_probe.py --name eos-fake200        --fake api.epicgames.dev

# T7b：PlayFab 黑洞下打完整一局（用户操作，探针自动判定进图/结算并复原代理）
python tools\t7b_probe.py --timeout-mission 900
# 阶段1 最终验收：PlayFab + Sony 一起黑洞（= 阶段1 部署形态）
python tools\t7b_probe.py --name stage1-combo-mission --blackhole "974a9.playfabapi.com,crs.api.wwsga.me,testament.api.wwsga.me,clientconfig.api.wwsga.me,pss-cloud.net"
```

判据看 `state/evidence/t7/<name>.json` 的 `verdict`：`stable` = 未阻断；`eac_blocked` = 被 EAC 拦下。

## 7. 局限

* 判据里的「≥15 行稳态轮询」是经验阈值（R1=35 / R3=41 / R4=39 vs R2=7，分布很清楚），不是协议级判据；
* EAC 弹框需要人眼确认（本轮已由用户两次截图确认；`helldivers2` 进程在弹框期间仍存活，进程存活不能当判据）；
* 只覆盖「启动到主界面」；**未**覆盖「进图打一局」在该组断开下是否仍可（阶段1 若采用"不接管 PlayFab"，
  已由 T7b 补上（PlayFab 黑洞整局通过）；
* **Sony 黑洞的整局**已随 T7b-2 组合黑洞一起覆盖（且本局 Sony 侧只调用了 `crs` 一次，其余三域未出现）；
* 未测 EAC-off 启动在完全无网络环境的表现（历史记录显示直启可行，但本轮未复测）。
