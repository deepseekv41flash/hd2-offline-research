# PlayFab 明文抓包 Runbook（阶段2 E3 / B6 / B8 现场用）

> 2026-11-21 建立并完成链路验证；实机抓包待游戏在场。
> 前置结论见 `docs/STAGE2-LOCAL-TESTS-2026-11-21.md` §4。

## 为什么

自建发现层只能看到 `/api/lobby*` 的 HTTP；真正的入厅由游戏内 **PlayFab Multiplayer / Party SDK**
直连 `974a9.playfabapi.com`。要判断「卡在 Receiving Data」的具体原因（隐私拒绝 / 房间不存在 /
Party 网络不可用 / 认证过期），必须拿到那几条 HTTPS 的请求体与返回码。

## 一次性准备（已完成）

1. 生成自签 CA（含 CRL）并把 CA 装进当前用户根证书（Schannel 信任）：
   ```bash
   python tools/playfab_mitm.py --install-ca      # 生成 + certutil -user -addstore Root
   ```
   验证：`Get-ChildItem Cert:\CurrentUser\Root | ? {$_.Subject -match "PlayFab Capture"}`

## 每次抓包流程

```bash
# 0) 关掉可能占用 18080/18082 的旧实例（任务管理器或 taskkill）
# 1) 起 MITM（默认 inspect=974a9.playfabapi.com；其余 host 经 Clash 隧道）
python tools/playfab_mitm.py --port 18080 --inspect 974a9.playfabapi.com \
    --upstream 127.0.0.1:7897 --upstream-mode direct --crl-port 18082

# 2) 系统代理指向 MITM（自动备份原值；bypass 自动加 127.*/localhost/中继 IP）
powershell -NoProfile -ExecutionPolicy Bypass -File tools/system_proxy.ps1 -Action Set -Proxy 127.0.0.1:18080

# 3) 启动游戏、复现加入流程；期间所有 PlayFab HTTPS 明文落在 state/mitm/capture-*.jsonl

# 4) 结束：恢复系统代理
powershell -NoProfile -ExecutionPolicy Bypass -File tools/system_proxy.ps1 -Action Restore
```

## 抓到了怎么读

`state/mitm/capture-*.jsonl` 每行一条记录：

- `kind=request`：`host/method/path/req_headers/req_body`
- `kind=response`：`status`（如 `HTTP/1.1 401 Unauthorized`）/`resp_headers`/`resp_body`
- `kind=error`：上游连接/TLS 异常（附 `trace` 末尾 6 行）

重点看四类 PlayFab 调用（按 path 关键字过滤）：

| path 片段 | 说明 | 关注点 |
|---|---|---|
| `/Client/LoginWith…` | PlayFab 客户端登录 | 是否 200、EntityToken/TitlePlayerAccount 是否有效 |
| `/Lobby/…` | PlayFab Lobby（PFLobby） | 错误码：`lobby not joinable`（隐私/满员）/不存在 |
| `/Party/…` | PlayFab Party 网络创建/加入 | 错误码 + 返回的 relay 端口/区域 |
| `/Client/GetTitleData` 等 | 常规 API | 用于确认整体认证是否可用 |

已知错误码参考：`0x89236227` = lobby not joinable（HellForge 文档记录）。

## 限制与注意

- **只看得到 TCP/HTTPS**；Party 数据面走 UDP，本工具抓不到（要用 pktmon/admin 或按 port 动态中继）。
- MITM 会替换 PlayFab 的服务端证书，客户端必须信任自签 CA；**PlayFab SDK 是否有证书固定未验证**——
  若游戏在 PlayFab 请求上直接报错/超时，先恢复系统代理再排查。
- 抓包文件含会话票据，**只留在 gitignored 的 `state/` 下**，不要提交、不要外发；用完可删。
- 恢复代理后建议重启一次游戏，避免 SDK 复用旧连接。

## 与其它实验的配合

- **B6（假大厅变体）**：`tools/fake_lobby.py create --variant …` 造厅 → 游戏内加入 → 同时看
  `state/mitm/capture-*.jsonl`（是否发起 PlayFab 调用）与 A 机 mock 日志（是否 `DELETE /api/lobby/clear`）。
- **B8（终态响应）**：`systemctl restart hd2-discovery`（A 机，已开 `unknown_lobby=closed`）→
  看客户端是否自动退出旧厅；同时段 PlayFab 明文可解释它为什么此前不肯退出。
