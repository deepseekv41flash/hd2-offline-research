# 阶段1（单人离线）定稿摘要（2026-11-21）

> 定位：把 T1–T7b 的全部实测结论压成一页，作为**阶段2（自建联机）的输入**。
> 细节与证据见各专题档；三态：`✓✓ 实机` / `✓ 离线+数据` / `⚠ 未证` / `✗ 否证`。
> 仓库：`D:\mod\work\hd2-offline-research`（研究仓，HEAD 见 git log；不含真实账号/抓包原值）。

## 1. 一句话结论

**官方服务器不可达时，HD2 可以完整单机：登录 → 主界面 → 进图 → 局内 → 撤离结算 → 回船，
只需在本地接管一个主机（`api.live.prod.thehelldiversgame.com`），并保证 EOS/EAC 网络可达。
PlayFab 与 Sony 侧不需要接管。**

## 2. 部署形态（阶段1 的产品形态，已整局验收）

| 连接 | 处置 | 证据 |
|---|---|---|
| `api.live.prod.thehelldiversgame.com` | **本机接管**（capture→replay + 状态机 + 时间平移，TLS 用研究 CA） | T1/T2/T3/T3b |
| `api.epicgames.dev`（EOS + **EAC 反作弊**） | **必须可达**（不能断、也不能伪 `200 {}`；EAC 需真实协议交互） | T7 R2/R6 |
| `974a9.playfabapi.com`（PlayFab 大厅/Party/QoS） | **不接管**（不可达时客户端只是重试，不阻断单人） | T7 R1/R4 + T7b |
| Sony PC（`crs/testament/clientconfig/pss-cloud`） | **不接管**（单机流程里几乎不出现；出现也只是崩溃上报/配置） | T7 R3/R5 + T7b-2 |

**产品化复用**：上表的 TLS/证书/信任安装不需要阶段1 重做 —— 产品线已有完整实现（[hd2-proxy-toolkit](https://github.com/deepseekv41flash/hd2-proxy-toolkit)：自动续期 + `certs renew` + `install`/GUI「安装/修复」+ trusted 告警 + CRL）。

**整局验收**（T7b，两轮，均 59 请求 / 0 非 200 / 0 fallback）：

| 轮次 | 形态 | 主界面 | 进图 | 结算 |
|---|---|---:|---:|---:|
| T7b-1 | PlayFab 不可达 | 27.1 s | 120.2 s | 352.3 s（`Mission/end 866B→200 1617B`） |
| T7b-2 | **PlayFab+Sony 同时不可达（阶段1 部署形态）** | 24.0 s | 104.2 s | 340.3 s（`Mission/end 864B→200 1617B`） |

## 3. 验证矩阵（T1–T7b）

| 编号 | 内容 | 结论 |
|---|---|---|
| T1 | 研究 CA 装入 `CurrentUser\Root`；Schannel(curl)/.NET SslStream 均通过；73/73 回放经 LAN IP:443 | ✓✓ |
| T2/T2b | 游戏实机启动 → 登录（437 B → 200 52 B）→ 主界面；34–37 端点全 200、0 错误；真实 API IP 0 命中 | ✓✓ |
| T3 | 单人任务链（4 分钟局）：`Operation/Create → Mission/Start → Mission/end → Mission/Session(202) → Abandon` | ✓✓ |
| T3b | **未绑定 PSN** 账号整局：49 请求 / 0 非 200 / 0 fallback | ✓✓ |
| T4 | 180 s 正式抓包：无第二条 `*.thehelldiversgame.com`；旁路=PlayFab/EOS+EAC/Steam/Sony PC | ✓✓ |
| T4b | 无 PSN 对照：PSN 非必需；Sony 域名与 PSN 绑定无关 | ✓✓ |
| B9 | 登录体真实 schema：**437 B（绑 PSN，9 字段）/ 346 B（未绑，6 字段）**；91 B = 3 个可选字段（票据恒 116 字符） | ✓✓ |
| B9-判别 | 让账号 B 真登录官方服务器一次后再抓，仍 346 B（`delta=0`）⇒「历史缓存」假设 ✗，「PSN 绑定关系」✓ | ✓ / ✗ |
| T6a | MITM 内容取证（79 条记录、0 握手失败）：PlayFab=联机控制面、EOS=EAC+遥测、Sony CRS=崩溃上报 | ✓✓ |
| T7 | 3 组 × 2 种处置差分：PlayFab/Sony 不阻断；**EOS/EAC 两种处置都阻断** | ✓✓ |
| T7b | 整局验收两轮（见 §2） | ✓✓ |
| T5 | 收尾：游戏/mock/研究 CA 全部复原，hosts 产品行保留 | ✓ |

## 4. 可复用工程资产（研究仓）

* `mock/`：capture→replay「本地云端」（exact→base→template→fallback）、状态机（login/lobby/operation/mission）、
  时间平移（war/elapsed/unix_fresh/keep 四类基准）、TLS 配方（CA+CDP+AKI+多 SAN）、body schema 采集；
* `tools/`：`mockctl`（证书/TLS/服务/自检）、`sample_build`（脱敏+原值残留门禁）、`replay_client`（三种回放模式）、
  `compare_shapes`（B9 对账）、`tcp_passthrough`（透明转发）、`mitm_proxy`（旁路取证/blackhole/fake）、
  `analyze_mitm`、`t7_probe`、`t7b_probe`；
* 门禁：`pytest` **83 项** + `mockctl selftest`；样本/证据全部 `state/`、`samples/`（gitignore）。

## 5. 遗留项（不阻塞阶段1 结论）

> **证书生命周期已由产品线解决，不再是遗留项**：研究线用的「2 天临时 CA」只是实验手段；
> 产品化形态直接复用 [hd2-proxy-toolkit](https://github.com/deepseekv41flash/hd2-proxy-toolkit) 的既有方案：
> `config.cert = {days: 2, min_remaining_hours: 12, auto_renew: true}`（**自动续期**）、
> CLI `hd2proxy.exe certs status` / `certs renew`、`hd2proxy.exe install` 与 GUI「安装 / 修复（UAC）」
> （hosts + 根信任导入）、`cert_status` 的 trusted 状态与 <12 h 告警、以及内置 CRL 服务。
> ⇒ 阶段1 产品化时**不需要**研究线再实现证书续期；研究仓的 `mock/certs.py` 仅用于离线实验。


| # | 项 | 状态 |
|---|---|---|
| 1 | 「完全无外网」形态：EAC-on 会失败，需 EAC-off 直启（`bin\helldivers2.exe`）；历史实测可行但本轮未复测 | ⚠ 未证 |
| 2 | B9 里「给账号 B 绑定 PSN」正向对照（要动用户 PSN 账号） | ⚠ 未证（H1 记 ✓ 非 ✓✓） |
| 3 | UDP/DTLS 数据面（EOS/Steam 中继）与 `steam.exe` 进程内容：加密或不在 TCP 工具范围 | ⚠ 不可得 |
| 4 | 单人分支覆盖：长任务/失败撤离/被踢未测 | 部分覆盖 |
| 5 | ~~研究 CA 有效期 2 天需处理续期~~ **已由产品线承担、不构成问题** | 已解决（见下） |

## 6. 对阶段2（自建联机）的三条硬输入

1. **PlayFab 不能省**：`connectionString` 由 `Lobby/CreateLobby` 签发、中继服务器由 `Party/RequestParty`
   分配（含 FQDN/端口/DTLS 证书指纹）；给 PlayFab 空响应会让 Party 链在第一步 `ListPartyQosServers` 就放弃。
   ⇒ 自建联机 = **自建 HD2 后端 + 保留 PlayFab 云**；完全自建等同重写 Party 协议 + DTLS 中继，不建议。
2. **自建后端的最小接管面**：游戏自有 API 的登录/大厅/任务登记/战况（即现有 mock 已覆盖的那一层）；
   PlayFab 相关请求保持原样放行。
3. **EAC 依旧必须真实可达**（或接受 EAC-off 模式）：即便换成自建后端，EAC 的网络前置不会消失。
   双机实验（B1）前应先确认：两端都能连上 EAC + PlayFab，再用自建后端替换游戏 API。
