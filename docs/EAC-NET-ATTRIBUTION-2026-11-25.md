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

## 6. 实测（2026-11-25 00:35–00:39）：hosts 重定向对游戏侧的边界

**实验**：`hosts: 127.0.0.1 api.epicgames.dev` + 我们签的 4-SAN 证书做 **TLS 终结**
（`tools/mitm_proxy.py --tls-listen 127.0.0.1:443 --tls-host api.epicgames.dev --forward-ip 104.18.124.108`），
忠实转发到真实 IP；tshark 抓 loopback 取 JA3。用户以 `steam://rungameid/553850` 启动，主菜单停留 240 s。

### 6.1 结果：anticheat 通道 0 条，但抓到 EAC 启动器自己的遥测

| 观测 | 值 |
|---|---|
| `/anticheat/v8|v6` 请求 | **0 条**（`req_len_series=[]`）|
| 成功命中的唯一请求 | `POST /telemetry/data/datarouter/api/v1/public/data?…&AppID=EOSSDK.AntiCheatExt.Launcher&SessionId=…` → **200 / 666 B** |
| 该请求 UA | **`Easy Anti-Cheat EOS Bootstrapper/1.9.4`** |
| 到 `127.0.0.1:443` 的 ClientHello | **5 次**，其中 **4 次在握手中被重置**（`ConnectionResetError`），1 次成功 |
| JA3（4 次重置 / 1 次成功）| `060415e5bacbf8b80253f8e69b33255b`（Schannel 风格，4 次）/ `51c688a0a86d7dc48acfd8141af86002`（BoringSSL 风格，1 次）|
| hosts 还原 | `identical=True`（15/15 行，`missing`/`extra` 均空）|

### 6.2 结论（可引用）

1. **hosts 对"EAC 启动器"有效**：`Easy Anti-Cheat EOS Bootstrapper` 走系统解析，被我们完整看到
   （含 `AppID=EOSSDK.AntiCheatExt.Launcher` 与 URL 里的 `SessionId`）。
   ⇒ **"AntiCheatExt" 是 EOS 侧一个有独立 AppID 的组件**，与游戏进程内的 anticheat 上报**同 host、不同类目**。
2. **hosts 对"游戏进程内的 EOS/EAC"不完整**：游戏那侧有 4 次带 SNI 的 ClientHello 到我们的监听，
   但都在握手中被重置 —— 说明它**先试了系统解析**，随后中止并回落到自己的解析（DoH / 启动器下发的 IP 列表）。
   与 EAC-off 线 socket 观测（游戏直连 `44.233.151.84` / `184.194.175.202` 等真实 IP）一致：**"试了但没成"**，
   不是"完全没试"。
3. **可用的 JA3 指纹**：`060415e5…`（Schannel 系）与 `51c688a0…`（BoringSSL 系）已拿到，
   将来做"注入前后客户端指纹是否突变"就有了基线。

### 6.3 下一步（若要拿到游戏侧 anticheat 明文）

- **首选**：临时阻断到公共 DNS 的 `:53/:853/:443`（`1.1.1.1`、`8.8.8.8`、`dns.google`、`*.cloudflare-dns.com`），
  迫使 EOS 回落系统解析 ⇒ hosts 生效；**收尾必删规则**并把 `netsh advfirewall show rule` 前后快照写进 meta。
- 备选：IP 级透明重定向（WinDivert），Secure Boot 下需 WHQL 签名，风险高，暂不做。
- 注意：这条负结论能省别人一天——**别再只指望 hosts 抓游戏侧 EOS**。

## 7. 第 2 轮（00:44–00:48）：机器根 CA + 掐 DoH 仍无效；但拿到 Bootstrapper 明文体

**本轮变量**（相对 §6 只改两处）：
1. 研究 CA **加装到 `LocalMachine\Root`**（§6 时只有 CurrentUser）—— 假设"只读机器根的客户端拒了我们的叶证书"；
2. **掐公共 DNS/DoH**：`netsh advfirewall` 阻断到 `1.1.1.1/1.0.0.1/8.8.8.8/8.8.4.4` 的全部出站
   （规则名 `hd2-eac-baseline-tmp-*`，跑完即删）。

### 7.1 结果：游戏侧行为**完全没变**

| 观测 | 第 1 轮 | **第 2 轮** |
|---|---|---|
| `/anticheat/v8|v6` 请求 | 0 | **0** |
| 到 `127.0.0.1:443` 的 Reset | 4 | **4** |
| 成功命中的请求 | 1（Bootstrapper 遥测）| **1（同一条）** |
| 游戏进程到 `127.0.0.1:443` 的连接 | 0 | **0**（EAC-off 线 probe 独立确认）|

⇒ **机器根 CA 与掐 DoH 都不是原因**。游戏拿的是**另一套地址**（`172.200.240.121` / `108.138.26.39` /
`184.194.175.202` 等，每次启动可能不同），既不是 hosts 解析值也不是上一次的 IP。

### 7.2 ★ 收获：EAC 启动器的上报体是**明文 JSON**

`--dump-paths` 扩到 `/telemetry/` 后，落下了那条请求的 **1095 B 原文**（`raw/…_req.bin`）：

```json
{"Events":[
 {"EventName":"AntiCheat.Info.BootstrapperResult",
  "Source":"Bootstrapper/1.9.4","BuildTime":"Mar 25 2026 05:12:33",
  "ProductId":"f163733398a54335966be5046900198b",
  "SandboxId":"222a4933c4e2417d8d5da019a4e41c9e",
  "DeploymentId":"8511004301af474cad0c5a65679fe02e",
  "OSName":"Windows","OSVer":"10.0 x86-64 (Build 26320)",
  "LaunchResult":301},
 {"EventName":"AntiCheat.Info.BootstrapperInitPerf", …},
 {"EventName":"AntiCheat.Info.BootstrapperGameWindowVisibilityCheckDuration", …}]}
```

要点：
- **明文 JSON，无加密**；字段全集 `EventName/Source/BuildTime/ProductId/SandboxId/DeploymentId/OSName/OSVer/LaunchResult/DownloadDurationMs/InitDurationMs/TotalDurationMs/WaitDurationMs`；
- **只报启动阶段**（init/perf/窗口可见性检查/**启动结果码**），**不含任何检测结果或完整性判定**；
- `ProductId` / `DeploymentId` 与静态 strings 挖到的 CDN 路径**完全一致**（交叉验证）；
- **`LaunchResult: 301`** 是现成的"启动结果码"，可作注入前后 A/B 的第一观察点。

### 7.3 ★ 方法更正：T6a 的 `/anticheat/v8` 是**经系统代理**抓到的，不是 hosts

`docs/T6-MITM-SIDECHANNEL-2026-11-21.md` §1 记录的方法是：
> 临时把 HKCU IE 代理（WinHTTP/WinINet 会读）指到 `127.0.0.1:18092`，**并给游戏进程设 `HTTP(S)_PROXY`**（libcurl 系 SDK 读）；跑完立即恢复。

⇒ 第 1、2 轮的"hosts 重定向"是**弯路**：EOS/EAC 不是"不吃代理"，而是"本轮没给代理"。
若要做第 3 轮，正解是**恢复 T6a 那套**（CONNECT 代理 + env），而不是上 WinDivert。
（间接证据：游戏侧组件到 `127.0.0.1:7897` 有 317 次连接 —— 系统代理对游戏内组件是生效的。）

### 7.4 第 3 轮建议顺序（修正后）

1. **首选**：CONNECT 代理 + `HKCU` IE 代理 + 给游戏进程 `HTTP(S)_PROXY`（`setx` + 重启 Steam 让子进程继承）；
2. 备选：WinDivert 按 IP 透明重定向（Secure Boot 下需 WHQL 签名，成本高）；
3. 兜底：只采启动器通道（已证可用）。

## 8. 第 3 轮（00:55–01:00）：**系统代理是正解**，`/anticheat/` 全部拿到

**方案**：`tools/eac_capture_proxy_run.py` —— HKCU IE 代理 → `127.0.0.1:18092`（我们的 **CONNECT** 代理，
非拦截域名链回原上游 `7897`）+ tshark 抓 loopback + 落原始字节；收尾精确还原代理。
（**hosts/透明模式两轮都失败，见 §6/§7 —— 因为游戏进程内的 EOS 走系统代理，不吃 hosts。**）

### 8.1 结果：完胜

```
anticheat 请求 21 / 响应 21，raw_files=22（原始字节全量）
req_len_series = [72, 0, 388, 4904, 728, 8544, 10056, 8000, 0, 33280, 23424, 7976, 4328, 8024, 896, 0, 768, 1120, 0, 1088, 0]
dt_ms_series   = [51000, 3000, 7000, 3000, 7000, 5000, 3000, 31000, 3000, 24000, 4000, 5000, 5000, 6000, 14000, 3000, 14000, 42000, 19000, 41000]
ie_proxy_restored_identical = True
telemetry.app_ids = ['EOSSDK.PhaseRelease.ReleaseBuild', 'f163733398a54335966be5046900198b']
telemetry.event_names = [EOSSDK.HTTP.Complete, EOSSDK.Heartbeat, GameHeartbeat, GameStartup,
                         SDKOverlayComponent.Init, SessionStart, UsageMetric]
```
- v8 会话主通道 12 条，长度 **388 B–33,280 B**，与 T6a 的 388 B–33,472 B 同量级；间隔 3–14 s，偶发 24–42 s；
- v6 服务状态通道 2 条（响应固定 682 B）—— 与 T6a 完全一致；
- 首项 `72` 是采集器的开窗自检（PREFLIGHT），不属游戏流量。

### 8.2 第一条 `/anticheat/v8` 的客户端自报身份（**注入前后的指纹基线**）

```
ts 00:56:33 | req 388 B | resp 1289 B | status 200
user-agent  = EOS-SDK/1.19.1.2-53289219 (Windows/10.0.26100.10002.64bit) Helldivers 2/Developer Build (1.8.47139.0)
x-eos-version = 1.19.1.2-53289219
x-epic-correlation-id = EOS-xD8Yi4_ATkuPQFIXyiQ8xQ-1h8XRI_a5EqrBWdNZgYO3Q
content-type = application/octet-stream | accept-encoding = deflate, gzip
authorization = Bearer <JWT，约 1375 字符，不落档>
```

### 8.3 ★ body 结构：**不是明文 protobuf**（自查更正）

我最初看前 64 B 觉得像 protobuf，**实测否证**：

| 检查 | 结果 |
|---|---|
| 顶层 protobuf wire 解析 | **失败**（`wt4` / `tag0`）|
| zlib / gzip / raw-deflate | **都失败** |
| 熵 | **6.80 bits/byte**（<7.5 ⇒ **不是加密**，是结构化数据）|
| 前 128 B（每 4 B 小端）| `12, 393216, 262152, 6, 4, 1, 16, 524300, 0, 262144, 12, 4, 336, 6, 11, 0, 101, 5, …` 一串**小整数** |
| 之后 | 迅速变成高熵字节（像哈希/签名/压缩块）|

⇒ 更准确的描述是：**自定义封装 = 前段小整数头部/描述符表 + 后段高熵载荷**（不是 protobuf，也不是 TLS 之外再加一层加密的整体密文）。
**下一步**（若要读内容）：从 2 字节/4 字节边界试探字段表，或对高熵段单独尝试已知压缩/签名算法；**不建议暴力破解**。

### 8.4 三轮对照（这轮把前两轮的负结论固化了）

| 轮 | 变量 | `/anticheat/` | 结论 |
|---|---|---|---|
| 1 | hosts + 透明终结 | **0 条** | hosts 对游戏侧 EOS 无效（只对启动器有效）|
| 2 | + 机器根 CA + 掐 DoH | **0 条** | 与本轮变量无关；游戏不吃 hosts |
| **3** | **IE 代理 + CONNECT 终结** | **21 条** | **正解** |
