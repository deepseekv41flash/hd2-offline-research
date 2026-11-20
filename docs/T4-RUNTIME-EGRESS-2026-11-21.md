# T4：运行时出网清单（正式抓包，2026-11-21）

> 目标：关掉 A2/E1「除官方 API 主机外，游戏还连哪些主机」的**运行时**空白（静态侦察见
> [A2-HOST-DEPENDENCIES-2026-11-21.md](A2-HOST-DEPENDENCIES-2026-11-21.md)）。
> 结论一句话：**官方游戏 API 只连本机 mock；没有第二条 `*.thehelldiversgame.com`；
> 其余全部是 PlayFab / EOS+EAC / Steam（边界外）/ Sony PC 旁路 + 系统噪声。**

三态：`✓✓ 实机抓包` / `✓ 多源一致` / `⚠ 未证` / `✗ 否证`。

---

## 0. 方法（非管理员即可，无需 UAC）

```powershell
& 'C:\Program Files\Wireshark\dumpcap.exe' -i 9 -i 11 `
  -f "udp or tcp port 443 or tcp port 7897" -a duration:180 `
  -w D:\mod\tmp\t4\acp-20261121-0144.pcapng
```

| 项 | 值 |
|---|---|
| 接口 | `9` = 以太网（真实外连）；`11` = Npcap Loopback（本机代理隧道） |
| BPF | `udp or tcp port 443 or tcp port 7897`（覆盖 DNS/全部 UDP/HTTPS/本机代理 CONNECT） |
| 时间窗 | **2026-11-21T01:43:48.9 → 01:46:48.5（+0800）**，整 180 s |
| 期间动作 | **重启游戏一次**（覆盖 EAC 引导 → 登录 → 主界面轮询） |
| 文件 | `D:\mod\tmp\t4\acp-20261121-0144.pcapng`（12.4 MB，本地、不入库） |
| 解析 | `tshark -r`（读文件不需要管理员）；DNS/SNI/证书 SAN/五元组 |
| 归属判定 | DNS 应答 + TLS SNI + 证书 SAN + 进程 netstat 关联 + 公开资料（多源一致才升 `✓✓`） |

---

## 1. 游戏自有 API：只有一条，且到本机（✓✓）

| 观测 | 值 |
|---|---|
| DNS 查询 `api.live.prod.thehelldiversgame.com` | **0 次**（hosts 生效时不会发 DNS——见 §5.1） |
| TLS SNI 出现 | **仅 1 处：`100.67.169.17:443`（本机 mock）** |
| 其它 `*.thehelldiversgame.com` | **无** ⇒ **E1 答案：不存在第二条游戏自有主机（✗ 否证存在）** |
| 本机代理 CONNECT 里是否有 API 域名 | **无** ⇒ 官方 API 没有走代理，直连 mock |

---

## 2. 第三方旁路（运行时确认，游戏进程确实在连）

| 域 | 目的 | 归属证据 | 类别 |
|---|---|---|---|
| `974a9.playfabapi.com` | `2603:1061:14:184::1`（Azure IPv6）、`150.171.110.x` | B2B3 侦察已证 titleId；本次 SNI + DNS 直证 | **PlayFab 前端**（B8：标题在线） |
| UDP `3075` → `mpsqosprod.<region>.cloudapp.azure.com` | Azure 多区（4.x/13.70/20.x/40.83/52.228/130.33/172.x/135.236/48.217） | DNS 里有 16 个区名；3075 是 PlayFab 多人 QoS 探测端口 | **PlayFab Multiplayer QoS** |
| `api.epicgames.dev` | `104.18.124.108` / `104.18.125.108`（Cloudflare） | SNI + 证书；EOS SDK 前端 | **EOS** |
| `datarouter.ol.epicgames.com` | AWS 多 IP（`100.24.162.52`、`34.232.129.252` 等） | SNI + 证书 | **EOS 遥测（datarouter）** |
| `modules-cdn.eac-prod.on.epicgames.com` | `54.192.35.5/.23/.36/.47`（CloudFront） | SNI + 证书；EAC 模块下发 | **EAC（EOS 版）** |
| `p2p-hkg1.discovery.steamserver.net` | `103.28.54.162–187` | SNI + 证书；Steam P2P 发现（香港） | **Steam 边界外** |
| `shared.steamstatic.com` / `steamcommunity.com` | `146.75.115.52` / `74.86.151.162` | SNI + 证书 | **Steam 边界外** |
| `testament.api.wwsga.me` | `32.200.4.29`（AWS us-east-1） | 证书 SAN=`*.api.wwsga.me`（Amazon RSA）；同后缀兄弟游戏子域 `dgpc.api.wwsga.me`（Days Gone）、`mlb25.api.wwsga.me`（MLB The Show）见公开抓包资料 | **Sony PC（PSPC/PSN）侧遥测** |
| `clientconfig.api.wwsga.me` / `crs.api.wwsga.me` | 经本机代理 | 同上；`crs` = 崩溃上报通道（与 `crs-handler.exe` 对应） | **Sony PC 配置/崩溃上报** |
| `pss-cloud.net` | `54.244.195.76`（AWS us-west-2） | 证书 SAN=`*.pss-cloud.net`（Amazon RSA） | **Sony PC 服务（⚠ 精确用途未证）** |

> 这些**都不是官方游戏后端**；离线单机不依赖它们（T3 已证整局任务只连 mock）。

## 3. UDP 目的地址（netstat 看不到的部分）

| 端口 | 目的 | 判定 |
|---|---|---|
| `3075` | Azure 多区（对应 `mpsqosprod.*`） | **PlayFab Multiplayer QoS 探测** |
| `3478/3479` | 中国 ISP IPv4（`1.14.45.154`、`111.206.174.3`、`114.132.152.243`、`118.89.176.235`、`150.158.118.221`）+ IPv6 `2402:4e00:*` | **Steam Datagram Relay 的 STUN/中继探测** ⇒ **B7 附证：SDR 中继确实在使用** |
| 高端口 `63313/63314/45368` | 中国 IPv4/IPv6 | **EOS 中继/P2P** |
| `100.67.169.17:4028/52497/63316` | 本机 IP 自身 | 本地自连/探测（mock 无 UDP 参与，⚠ 未细分） |

## 4. 本机代理隧道（`127.0.0.1:7897`，Loopback 接口抓到 CONNECT）

游戏相关：`974a9.playfabapi.com`、`api.epicgames.dev`、`crs.api.wwsga.me`、`shared.steamstatic.com`、`steamcommunity.com`。
**没有 `api.live.prod.thehelldiversgame.com`** —— 与 §1 一致：官方 API 直连本机，不走代理。

抓包是**整机口径**，同窗口还有大量系统其它应用的 CONNECT（deepseek、vscode、microsoft、bing、chatgpt、nvidia 等），
不属于游戏流量。

## 5. 对 A2 / E1–E8 的更新

### 5.1 为什么 DNS 里没有 API 域名是「正常」而不是「漏抓」
游戏连 API 前会解析域名；`hosts` 命中时 **Windows 解析器直接返回 hosts 条目、不发 DNS 查询**。
所以抓包里没有该域名的 DNS、却有指向 `100.67.169.17` 的 SNI——这正是「hosts 接管生效」的证据。

| 项 | 结论 | 三态 |
|---|---|---|
| **E1** 是否存在第二个游戏自有主机 | **不存在**（DNS/SNI 全量 55 个域名里除 API 主机外无同后缀） | ✗ 否证（即"没有"） |
| **E2** PlayFab 何时连 / crossplay | 启动阶段即连 `974a9.playfabapi.com` + 16 区 QoS | ✓✓ |
| **E3** Party/P2P 中继端点形态 | 走 UDP 高端口 + PlayFab QoS；具体中继地址随会话动态下发 | ⚠ 未证 |
| **E4** EOS 为谁服务 | `api.epicgames.dev` + `datarouter` + UDP 中继；与 EAC 同域族 | ✓ 部分 |
| **E5** 断网是否阻塞进图 | 未做（需断网实验；本次只证明正常联网时这些旁路不阻塞） | ⚠ 未证 |
| **E6** `network_logs\*.ndp` | T2/T3 后仍无该目录（release 不产出） | ✓ 否证 |
| **E7** EAC-on 额外通道 | 本次就是 **EAC-on** 抓包：EAC 模块 CDN + `EasyAntiCheat_EOS` 进程均覆盖 | ✓✓ |
| **E8** Sony/PSN 通道 | 出现 `wwsga.me` / `pss-cloud.net`（Sony PC 服务），是否为 PSN 关联专用未证 | ⚠ |

## 6. 局限

1. **整机抓包、无进程归属**：靠「IP↔进程 netstat 关联 + 域名语义 + 证书」判定，不是 pcap 级进程标注；
2. **未覆盖任务期**：本窗口是"重启→登录→主界面"；任务期域名级清单可再抓一次（T3 已证任务期官方 API 只连 mock）；
3. **UDP 只到五元组**，未解析载荷/协议握手；
4. HTTPS 内容不可见（只有 SNI 与证书）；
5. 部分系统噪声域（`ecs.office.com`、`catalog.gamepass.com`、`clientconfig.queniuqe.com` 等）未逐一归属——它们与游戏进程无关联证据。

## 7. 复现命令

```powershell
# 抓（非管理员）
& 'C:\Program Files\Wireshark\dumpcap.exe' -i 9 -i 11 -f "udp or tcp port 443 or tcp port 7897" `
  -a duration:180 -w D:\mod\tmp\t4\acp.pcapng

# DNS 应答（游戏相关后缀）
& 'C:\Program Files\Wireshark\tshark.exe' -r D:\mod\tmp\t4\acp.pcapng `
  -Y "dns.flags.response==1" -T fields -e dns.qry.name -e dns.a -e dns.aaaa |
  Sort-Object -Unique | Select-String 'helldivers|playfab|epicgames|steam|wwsga|pss-cloud|mpsqos'

# TLS SNI + 目的地址
& 'C:\Program Files\Wireshark\tshark.exe' -r D:\mod\tmp\t4\acp.pcapng `
  -Y "tls.handshake.extensions_server_name" -T fields -e ip.dst -e ipv6.dst -e tcp.dstport `
  -e tls.handshake.extensions_server_name | Sort-Object -Unique

# 本机代理 CONNECT 目标
& 'C:\Program Files\Wireshark\tshark.exe' -r D:\mod\tmp\t4\acp.pcapng `
  -Y "tcp.dstport==7897 && http.request" -T fields -e http.request.method -e http.request.uri |
  Sort-Object -Unique

# UDP 目的地址
& 'C:\Program Files\Wireshark\tshark.exe' -r D:\mod\tmp\t4\acp.pcapng `
  -Y "udp && !dns" -T fields -e ip.dst -e ipv6.dst -e udp.dstport | Sort-Object -Unique
```
