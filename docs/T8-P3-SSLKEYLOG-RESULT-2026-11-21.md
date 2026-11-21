# T8-P3 结果：SSLKEYLOGFILE 对游戏无效（2026-11-21）

> 承接 T8 计划 P3（10 分钟试探）。三态：`✓✓ 实机` / `⚠ 未证` / `✗ 否证`。

## 0. 结论

**`SSLKEYLOGFILE` 这条路对 HD2 的数据面取证无效（✗ 否证）**：
- 游戏运行期间 keylog 文件 **新增 0 行**（70 → 70，全部是此前 Steam 重启时 Chromium/steamwebhelper 写的）；
- 用该 keylog 对同窗口抓包解密：内层协议（http/http2）帧数 **4 → 4（新增 0）**；
- ⇒ 游戏的 TLS 栈（Schannel / EOS 静态 OpenSSL / Steam 自有）都不写 keylog；数据面明文**只能靠进程内 hook（T8-P2）**。

## 1. 方法

| 项 | 值 |
|---|---|
| 取 key 的方式 | 带 `SSLKEYLOGFILE=<state/evidence/t8/sslkeylog.txt>` **重启 Steam**（Steam 启动的游戏继承其环境变量） |
| 抓包 | `dumpcap -i 9 -i 11 -f "udp or tcp port 443" -a duration:100`（自然结束，避免强杀丢包） |
| 游戏 | EAC-on 正常启动（官方 API → mock），到主界面停留 |
| 判据 | ①keylog 行数在游戏期间是否增加 ②tshark 带 keylog 时能否解出内层协议 |

## 2. 实测数据

| 指标 | 值 |
|---|---|
| keylog 行数 | 70 → **70**（游戏期间 +0）；内容为 TLS1.3 `CLIENT/SERVER_HANDSHAKE_TRAFFIC_SECRET`（Chromium 风格） |
| 抓包 | 10,443 包 / 7.1 MB（以太网 9,127 + 回环 1,316） |
| TLS records | 4,448 |
| 内层协议帧（无 keylog → 带 keylog） | 4 → **4**（新增 0 ⇒ 没有可解会话） |
| SNI 采样 | 官方 API（→mock）、`974a9.playfabapi.com`、`api.epicgames.dev`、`datarouter`、`clientconfig.api.wwsga.me` 等，与 T4/T6a 一致 |
| 高端口 UDP | 本窗口出现 53393 / 58745 / 62201（T4 里是 45368）⇒ 数据面端口是**临时分配**的，不是固定端口 |

## 3. 对计划的影响

* P3 关闭：不再尝试环境变量路线；
* 数据面明文只剩 **P2（进程内 hook：Steam `SendMessageToConnection` / EOS `EOS_P2P_SendPacket`）**，
  且必须等阶段2 启动 + 双机时才有意义（单人时数据面基本只有心跳）；
* 顺带确认：DTLS/UDP 端口不固定，做 MITM/伪造中继要按 PlayFab `Party/RequestParty` 返回的端口动态处理。

## 4. 现场

Steam 已恢复为**不带 SSLKEYLOGFILE** 的正常启动（重启一次）；研究 CA 已卸；mock 已停。
（期间 Steam 自动更新了 `Steamworks Common Redistributables`（228980）—— 强杀重启触发的常规更新，已完成，HD2 文件未动。）
