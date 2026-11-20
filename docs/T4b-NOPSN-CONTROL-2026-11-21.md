# T4b：无 PSN 绑定账号的对照抓包（2026-11-21）

> 目的：验证用户假设 —— **「PSN 绑定可能并非必须项」**。
> 方法：换成**未绑定任何 PSN 账号**的 Steam 号，按与 [T4](T4-RUNTIME-EGRESS-2026-11-21.md) **完全相同的参数**
> 重做一次 180 s 抓包（DNS / 全部 UDP / HTTPS / 本机代理 CONNECT），与 T4 基线逐项比对。
> 三态：`✓✓ 实机` / `✓ 多源一致` / `⚠ 未证` / `✗ 否证`。

---

## 0. 对照条件

| 项 | T4（基线） | **T4b（本次）** |
|---|---|---|
| Steam 账号 | **账号 A（已绑定 PSN）** | **账号 B（未绑定任何 PSN）** |
| SteamID | `7656119•••••••••`（脱敏） | `7656119•••••••••`（脱敏） |
| 游戏内 PSN 状态 | 正常进船 | **弹出「账号关联状态 ×」页**（`ACCOUNT LINK STATUS ×`，有 `LINK ACCOUNT` 与可跳过的 `SHIP`） |
| 截图 | — | `state/evidence/t4b-psn-unbound.png`（本地，不入库；含二维码，不公开） |

> 截图内容：`Scan the QR code or press the button below to begin linking your accounts from Steam and
> PlayStation™Network` / `LINK ACCOUNT` / `ACCOUNT LINK STATUS ×` / `SHIP`。
> ⇒ 未绑定账号**不会被硬拦**，UI 明确提供「跳过进船」路径（PSN 非单机必需的第一条证据）。

---

## 1. 抓包条件（与 T4 一致）

```powershell
& 'C:\Program Files\Wireshark\dumpcap.exe' -i 9 -i 11 `
  -f "udp or tcp port 443 or tcp port 7897" -a duration:180 `
  -w D:\mod\tmp\t4\acp-20261121-noPSN-015146.pcapng
```

| 项 | 值 |
|---|---|
| 时间窗 | **2026-11-21T01:51:46.5 → 01:54:46.1（+0800）**，整 180 s |
| 期间动作 | 启动游戏（EAC-on 引导 → 登录 → 主界面/账号关联页 → 轮询） |
| 文件 | 17.7 MB（本地、不入库） |
| 日志对账 | mock 侧同窗口 `POST /api/Account/Login` **346 B → 200 52 B**，随后 34+ 端点全部 200、0 错误 |

> 注：同一台机器、同一局域网、同一 mock、同一时间尺度；两账号都是全新登录（无 DNS/连接缓存可比性问题）。

---

## 2. 官方 API：两个账号完全一致（✓✓）

| 观测 | T4（绑定） | T4b（未绑定） |
|---|---|---|
| `api.live.prod.thehelldiversgame.com` 的 SNI 目的 | `100.67.169.17`（本机 mock） | **`100.67.169.17`（本机 mock）** |
| 该域名的 DNS 查询次数 | 0（hosts 生效） | **0（hosts 生效）** |
| 是否出现在代理 CONNECT | 否 | **否** |
| 其它 `*.thehelldiversgame.com` | 无 | **无** |

⇒ 登录身份换成「无 PSN」账号后，**官方 API 仍是纯本地接管**，不需要任何官方后端配合。

---

## 3. Sony / PSN 相关域名：**两账号都出现（关键否定）**

| 域 | T4（绑定 PSN） | T4b（未绑定 PSN） | 判定 |
|---|---|---|---|
| `testament.api.wwsga.me`（遥测） | ✓ 出现 | **✓ 出现** | 与 PSN 绑定状态**无关** |
| `clientconfig.api.wwsga.me`（配置） | ✓ 出现 | **✓ 出现** | 同上 |
| `crs.api.wwsga.me`（崩溃上报） | ✓ 出现 | **✓ 出现** | 同上（与 `crs-handler.exe` 对应） |
| `pss-cloud.net`（Sony PC 服务） | ✓ 出现 | **✓ 出现** | 同上 |
| `upload.recap.tools`（Sony RECAP） | 本次两轮都没崩，未出现 | 未出现 | 仅崩溃时 |

**结论**：`wwsga.me` / `pss-cloud.net` 是 **Sony PC SDK 自带的通用遥测/配置/崩溃通道**，
**未绑定 PSN 的账号照样连** ⇒ 这些域名的存在**不能**作为「PSN 绑定是必须项」的证据（用户假设成立）。
它们也**不属于**游戏自有后端：离线单机不依赖它们（T3 已证整局任务只连 mock）。

---

## 4. 其它旁路：集合基本一致，只有网络路径差异

| 类别 | T4（绑定） | T4b（未绑定） | 说明 |
|---|---|---|---|
| PlayFab 前端 | `974a9.playfabapi.com` | ✓ 同 | 两账号都连 |
| PlayFab QoS UDP 3075 | 16 个 Azure 区 | ✓ 同 16 区 | 两账号都连 |
| EOS | `api.epicgames.dev`、`datarouter.ol.epicgames.com` | ✓ 同 | 两账号都连 |
| EAC 模块 CDN | `modules-cdn.eac-prod.on.epicgames.com` | ✓ 同 | 两账号都连 |
| Steam P2P 发现 | `p2p-hkg1.discovery.steamserver.net` | **`p2p-lax1.discovery.steamserver.net`**（POP 变） | 路由/POP 分配差异，非账号差异证据 |
| Steam 其它 | `shared.steamstatic.com`、`steamcommunity.com` | `api.steampowered.com`、`steamcommunity.com`（本次未查 steamstatic） | 缓存/路径差异，非结论性 |
| Steam UDP | 3478/3479 STUN → SDR 中继 | ✓ 同；另见 `27015–27050` 段到 Valve IP（`155.133.248.36`、`212.102.63.139`、`103.10.124.116`、`103.10.125.40`） | Steam 网络（边界外） |

---

## 5. 数字差异：登录体 346 B vs 437 B（✅ 已由 B9 解释，见 `B9-LOGIN-BODY-SCHEMA-2026-11-21.md`）

| 账号状态 | `POST /api/Account/Login` 请求体 |
|---|---|
| 已绑定 PSN（账号 A） | **437 B / 9 字段**（T2/T2b/T3 共 4 次独立登录，全部 437 B；B9 补第 5 次实测 schema） |
| **未绑定 PSN（账号 B）** | **346 B / 6 字段**（B9 补测 2 次独立样本，字段长度完全一致） |

差 **91 B**，B9 已实测分解清楚（本文原列的三条候选原因 → 判定）：

1. ~~Steam 会话票据内容/长度随账号权益变化~~ → **✗ 否证**：两账号 `token` 都是 **116 字符**、字符类相同；
2. **已绑定账号的登录体里带账号关联字段** → **✓✓ 实机**：账号 A 多且只多 3 个字段
   `isSuspended=false` / `isBanned=false` / `betaToolkitAccountId`（19 位数字）；
3. 其它字段差异 → **✗ 否证**：`publicKey` 44 / `buildIdentifier` 61 / `instanceId` 6（机器级，
   两账号同值）/ `platform` 3 / `clientVersion` 1007203 全部一致。

91 B = 3 个新增字段的 JSON 本体 82 B + 分隔符风格 9 B（`tools/compare_shapes.py` 对账 MATCH）。
**残留未证**：这 3 个字段的值来自"PSN 绑定态"还是"历史真服务器下发的账号态缓存"——两者都能解释现状，
判别实验（让账号 B 真服务器登录一次再抓）未做，见 B9 §4.2。

⇒ **对 mock 无影响**：mock 不校验登录体，346 B / 437 B 都返回 200 + `sessionId`。

---

## 6. 结论（对线 A / 线 B）

1. **PSN 不是离线单机的必需项**（游戏 UI 允许跳过绑定直接进船；官方 API 仍是 mock 支撑）；`✓✓`
2. **Sony PC 服务域名的存在与 PSN 绑定状态无关**（未绑定账号照样连）⇒ 不能用它们推断"必须 PSN"；`✓✓`
3. **换账号不影响离线接管**：登录 → 主界面全流程在未绑定账号下同样成立；`✓✓`
4. 登录体大小随账号状态变化（437→346 B）—— **✅ 已由 B9 解释**（多 3 个可选账号态字段，票据长度无关）；
   字段值的最初来源（PSN 绑定态 vs 历史服务器缓存）`⚠ 未证`，见 B9 §4.2
5. 对线 B（自建联机）：登录/大厅链路与账号 PSN 状态无关这一点是**利好**（对手方不需 PSN 绑定），但
   `connectionString` 入厅仍依赖 PlayFab（B8），与 PSN 无关。

---

## 7. 局限

- 每账号各 1 个时间窗样本（未绑定账号仅 1 次登录；绑定账号有 4 次 437 B 的稳定性证据）；
- 未点击 `LINK ACCOUNT`（未触发真实 PSN 绑定流程），所以"绑定动作会产生哪些域名"未测；
- 未在未绑定账号上跑任务（T3 的结论来自绑定账号）；若要证明"未绑定账号也能完整单机"，可补一局 T3b；
- 抓包是整机口径，系统其它应用域名已排除（清单见 T4 文档）。

---

## 8. 复现命令

```powershell
# 抓（同 T4）
& 'C:\Program Files\Wireshark\dumpcap.exe' -i 9 -i 11 -f "udp or tcp port 443 or tcp port 7897" `
  -a duration:180 -w D:\mod\tmp\t4\acp-noPSN.pcapng

# 游戏相关 DNS
& 'C:\Program Files\Wireshark\tshark.exe' -r D:\mod\tmp\t4\acp-noPSN.pcapng `
  -Y "dns.flags.response==1" -T fields -e dns.qry.name -e dns.a -e dns.aaaa |
  Sort-Object -Unique | Select-String 'playfab|epicgames|steam|wwsga|pss-cloud|mpsqos'

# SNI
& 'C:\Program Files\Wireshark\tshark.exe' -r D:\mod\tmp\t4\acp-noPSN.pcapng `
  -Y "tls.handshake.extensions_server_name" -T fields -e ip.dst -e ipv6.dst -e tcp.dstport -e tls.handshake.extensions_server_name |
  Sort-Object -Unique
```
