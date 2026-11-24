# EAC/EOS 网络足迹归属速查（2026-11-25，答复 EAC-off 规划线四问）

> 用途：把"某个 IP/域名到底属于谁、能不能切断"的实测结论固化，避免各条线重复推导。
> 全部数据来自本仓既有取证（T4 出网清单、T6a MITM 内容取证、T7 必要性实验），本文只是**索引 + 结论**。

## 1. 四个常见 IP 的归属

| IP | 归属 | 出处 |
|---|---|---|
| `68.154.15.59` | **`api.live.prod.thehelldiversgame.com`**（官方游戏 API，Azure 美国）—— **不是** EOS/EAC | `STAGE2-REAL-JOIN-FLOW-2026-11-22.md:234`（flushdns 后 DNS 直解）、`STAGE2-RELAY-OPTIMIZATION:148` |
| `54.244.195.76` | **`pss-cloud.net`**（Sony PC 服务，AWS us-west-2，Amazon RSA 签发，SAN `*.pss-cloud.net`）| `T4-RUNTIME-EGRESS:56`、`state/evidence/t4-sni.txt:53` |
| `104.18.124.108` | **`api.epicgames.dev`**（Cloudflare）＝ **EOS SDK 前端 + EAC 反作弊通道**（同一个 host）| `T4:49`、`OFFLINE-PRIVATE-SERVER-RESEARCH:260` |
| `32.200.4.29` | `testament.api.wwsga.me`（Sony PSPC 遥测，AWS us-east-1）—— 同族的 `32.x` 在本机环境下更像 Sony PSPC 侧 | `T4:52` |
| `32.193.189.50` | **未定**（本仓无记录）；需 SNI / 证书 SAN 才能定 | — |

## 2. EAC 流量：按进程看不到，按 URL 可以精确归因

`EasyAntiCheat_EOS.exe`（session 0 服务）**零 socket** 是对的 —— EAC 的 usermode 部分在**游戏进程体内**。
但它的流量在 HTTPS 层可精确归因，且**与 EOS 共用 host** `api.epicgames.dev`（`T6-MITM-SIDECHANNEL:63-67`）：

| 通道 | 路径 | 频次 | 体量 |
|---|---|---|---|
| ① 会话主通道（双向）| `POST /anticheat/v8/games/client/message/` | 每 **~12 s**（5–30 s 抖动），**持续整局** | 上行 388 B–33,472 B、下行 1,289–9,634 B；run1 156 s / 13 条 = 上行 114.7 KB + 下行 49.8 KB |
| ② 服务状态 | `GET /anticheat/v6/service/status/` | 每 **~60 s** | 响应固定 682 B JSON |
| ③ 模块下发 | `modules-cdn.eac-prod.on.epicgames.com`（CloudFront）| **启动时按需**（已缓存不发生）| 模块本体 |

- **反作弊占用 EOS 侧字节 ~77%**（164.4 / 214.8 KB），≈ **1.05 KB/s**，整局 ≈ **6 MB/小时**（`T6:79-80`）。
- EOS 遥测在同 host 的 `/telemetry/data/datarouter/...`；独立主机 `datarouter.ol.epicgames.com`（AWS）也见过，但量小。

## 3. "能不能只切 EAC、不动别的"——不能，且断也死、伪也死

| 实验 | 处置 | 结果 |
|---|---|---|
| T7 **R1** | PlayFab 黑洞 | **不阻断**（24 s 到主界面，稳态 35 行）|
| T7 **R4** | PlayFab 伪 `200 {}` | **不阻断**（但 Party 栈第一步放弃）|
| T7 **R2** | **EOS/EAC 黑洞** | **✗ 阻断**：EAC 弹「无法连接反作弊网络，游戏将退出」，2 次复现 + 截图 |
| T7 **R6** | **EOS/EAC 伪 `200 {}`** | **✗ 同样阻断** |
| T7 **R3** | Sony（`*.wwsga.me` / `pss-cloud.net`）| **不阻断** |

⇒ 结论（可直接引用）：**EAC 与 EOS 共 host，按 IP/域名无法只切 EAC**；整域切断是"断也死、伪也死"，
只有 PlayFab / Sony 侧才是"可切"的。

## 4. ★ "注入风险"的真正边界：可以拦，不可以伪造

T6a 用**本仓 CA 对 13 个域名（含 EOS/EAC）做 TLS 终止**，游戏**正常启动并跑通**，我们拿到了真服务的正常响应。

- **可信 CA + 原样转发（MITM）= 可接受**（T6a 实测）；
- **伪响应（如一律 `200 {}`）= 阻断**（R6 实测）。

所以做任何"对冲/降风险"的方案时，边界不是"能不能拦 EAC 通道"，而是"**绝不能伪造它的应答**"。

## 5. 仍未证的一块：任务中（in-mission）的差分采样

- 已有窗口都是**启动窗口**（T6a 156 s / 78 s，到主界面），不是"仅任务中"；
- 间接证据：① 被描述为"持续整局"，整局量级按外推 ~6 MB/小时；
- 任务中**独有**的新连接是 **PlayFab Party 的 UDP(DTLS) 数据面**（每局一个专用 Azure VM），**属 Party 不属 EAC**；
- 要补这条：进任务后用 `scripts/eac_net_probe.py` 采 5–10 分钟即可（需起游戏，得先与 RE 线错开单实例锁）。
