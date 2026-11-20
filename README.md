# hd2-offline-research — HD2 离线单机 / 自建联机研究线

> 研究日期 **2026-11-21** ｜ 游戏 build `26568081` ｜ warId 801 ｜ 抓包基线 2026-11-18（73 条响应 / 53 条第二份）
>
> 与相邻研究仓的分工：[hd2-network-proxy](https://github.com/deepseekv41flash/hd2-network-proxy) 放**在线网络层改写**
> （本机 MITM 改 `Status` / GWE），[hd2-datamined](https://github.com/deepseekv41flash/hd2-datamined) 放**离线数据表逆向**
> （`.dl_bin` 结构/字段）；**本仓是第三条线 —— 把官方后端在本地"补掉"**：离线单机与自建联机的可行性研究。
>
> **这是研究线，不是产品线。** 产品仓库 `D:\mod\work\hd2-proxy-toolkit`、产物测试区
> `D:\mod\release-test`、用户运行态 `%LOCALAPPDATA%\HD2ProxyStudio` 全部**只读**；
> 本仓所有产物、证书、日志、抓包样本都在自己的目录里（`state/`、`samples/` 均不入库）。
>
> 交接来源：`D:\mod\work\hd2-proxy-toolkit\docs\HANDOFF-OFFLINE-PRIVATE-SERVER-2026-11-20.md` §9 runbook。
> 本轮结论文档：[docs/OFFLINE-PRIVATE-SERVER-RESEARCH-2026-11-21.md](docs/OFFLINE-PRIVATE-SERVER-RESEARCH-2026-11-21.md)。
>
> **发布状态：研究线，暂不发 Release、不打包产品**——等线 A 有实机结论、线 B 方案稳定后，再另立产品化仓/版本线。

## 这是什么

把官方 API（`api.live.prod.thehelldiversgame.com`）在**本地重放/接管**，目标是
① 官方服务器不可达时仍能进主界面 + 打单人任务；② 之后给自建联机做信令层。

当前实现 = **capture → replay 的本地云端**，三件事：

1. **回放**：把产品 capture 目录里的每个响应按路径接住（exact → 去掉 query → 模板 → 200 `{}` fallback）；
2. **时间平移**：抓包里的战争时钟/Unix 时间按「该响应自己的抓包时刻 → 现在」平移，
   避免客户端判定事件过期（两个独立计数器：`Status.time` 与 `timeSinceStart.secondsSinceStart`，不能互替）；
3. **有状态端点**：登录发 sessionId、大厅原样转发 `connectionString`、`Operation/Create` 回显客户端 id、
   `Mission/Start` 分配 missionId、`Mission/end` 用抓包模板回填结算。

## 不是什么

- **不是产品、不发 Release**：没有安装包、没有 `dist/`、没有 tag；成品能力将来另立产品仓
  （版本规则/门禁/`D:\mod\release-test` 产物测试区都归产品线）。
- **不含真实账号 / 玩家 / 大厅数据**：仓库里没有原始抓包，也没有 `connectionString`、`sessionId`、
  账号 ID 的原值；`samples/`、`state/` 全部 gitignore，样本靠 `tools/sample_build.py` 从本机原始抓包
  重建并**自动脱敏 + 原值残留门禁**。
- **不含游戏资产 / 密钥**：不存 `.dl_bin`、bundle、证书私钥；研究线 CA 是运行时本地生成、签完即删私钥。
- **不提供解锁付费内容 / 破坏他人游戏的方法**：目标是「官方不可达时仍能玩自己的单机」与小圈子联机的可行性验证。
- **不做反作弊绕过**：不碰 EAC/GameGuard、不改游戏文件（改文件会被判「未知版本」，见交接档 §6 死路表）。
- **不是官方认可的私服**：ToS 风险由使用者自担（见结项档 §7）。

## 目录

```
mock/       回放与 mock 服务端（纯逻辑 handler + TLS server + 证书）
  capture.py   读产品 capture（index.txt + 响应体）
  rules.py     路径归一 / 匹配索引 / 有状态路由
  timeshift.py 时间字段规则表（war / elapsed / unix_fresh / keep）
  state.py     登录 / 大厅 / 作战 / 任务链内存状态
  handler.py   (method, path, body) → Response，无 socket、可单测
  certs.py     研究线自签 CA + 叶子 + CRL（Schannel 可接受配方）
  server.py    HTTPS 443（LAN IP）+ CRL 18081
  client.py    极简 HTTPS 客户端（连 IP、用域名做 SNI/校验）
tools/      抓包分析 / 样本构建 / 回放验证 / 控制台
  analyze_capture.py  时间字段 + 敏感字段扫描
  capture_keys.py     端点响应结构枚举
  sample_build.py     抓包 → 脱敏样本（含「原值残留」硬门禁）
  replay_client.py    离线验证：handler / scenario / server 三种模式
  mockctl.py          唯一入口：ports / cert-* / serve / health / selftest
tests/      pytest 56 项（含真实抓包矩阵、TLS 端到端、脱敏门禁）
profiles/   offline-mock.json（与产品 profile 无关的独立配置）
docs/       结项档 + A2 主机依赖侦察 + B2/B3 大厅/登录侦察 + T4 运行时出网清单
```

## 5 分钟跑起来（不需要游戏、不需要管理员）

```powershell
cd D:\mod\work\hd2-offline-research

# 1) 从原始抓包重建脱敏样本（原始抓包在 D:\mod\tmp\...，只读）
python tools\sample_build.py `
  --src D:\mod\tmp\capture-store-season-20261118\raw `
  --dst samples\store-season --mission-end-src D:\mod\out\gwe

# 2) 单测（56 项）+ 离线门禁（证书 → CRL → TLS → 两种回放）
python -m pytest tests -q
python tools\mockctl.py selftest

# 3) 本机 dry-run：只绑 127.0.0.1:18443，不抢 443
python tools\mockctl.py serve --dry-run --insecure-ok      # 另开一个窗口
python tools\replay_client.py --src samples\store-season --mode server `
  --url 127.0.0.1:18443 --ca state\certs\ca.pem
```

## 真机前检查清单（会动系统的地方都在这里，全部需用户确认）

1. **443 只能有一个主人**：产品引擎与 mock 二选一（`mockctl.py ports` 查占用）；
2. **证书信任**：每 48h 需要把研究 CA 装进 `CurrentUser\Root`（非管理员、可逆）：
   `python tools\mockctl.py cert-install`；卸载 `cert-uninstall`；
3. **CRL**：mock 会在 `127.0.0.1:18081` 提供 `ca.crl`（**不是** 18080 —— 本机 18080 已被
   用户另一个进程 `gh_connect_proxy.py` 占用）；
4. **hosts**：当前已是 `100.67.169.17 api.live.prod.thehelldiversgame.com # hd2proxy`，
   mock 复用同一 LAN IP，**不需要改 hosts**；但引擎/mock 都没跑时开游戏会 `Error 56`；
5. **实验收尾**：停 mock → 确认 443/18081 空闲 → 需要回产品就启动产品引擎并 `verify`。

## 脱敏纪律

`sample_build.py` 默认脱敏（`connectionString` / `playerName` / `MemberAccountId` /
`sessionId` / `lobby seed` 等），并做**原值残留自检**：样本里只要还能搜到任一原值就
`FAIL` 退出。`samples/`、`state/` 全部 gitignore。

## 已验证 / 未验证（三态）

| 项 | 状态 |
|---|---|
| 73 条真实抓包全部被 mock 接住（fallback=0）、时间字段全部有规则 | ✓ 离线（可复跑） |
| TLS（自签 CA + CDP/CRL + AKI）：Python 客户端 + **Windows Schannel + .NET SslStream** | ✓✓ 实机（2026-11-21） |
| **游戏客户端接受这套 mock（A1）**：`POST /api/Account/Login 437B -> 200 sessionId` → 主界面 | **✓✓ 实机（EAC-on 也通过）** |
| 官方 API 零外连（180s 全程序连接采样：真实 API IP 0 命中，只有本机 mock） | ✓✓ 实机（详见结项档 §2.4.3） |
| 任务链 / 大厅链状态机（含 Create 回显、connectionString 原样转发） | ✓ 离线（scenario 46 项断言） |
| **单人任务链（A3）**：`Operation/Create → Mission/Start → Mission/end → Mission/Session(202) → Abandon` | **✓✓ 实机（4 分钟任务正常撤离结算）** |
| 除 API 主机外的主机依赖（A2） | **✓✓ 运行时抓包完成**：无第二条游戏自有主机；旁路=PlayFab/EOS+EAC/Steam/Sony PC（见 [T4 文档](docs/T4-RUNTIME-EGRESS-2026-11-21.md)） |
| 出网审计的盲区 | T4 已补：DNS/SNI/UDP 五元组（UDP 3075=PlayFab QoS、3478/3479=Steam SDR、高端口=EOS） |
| 尚未在 mock 里还原的端点 | `Progression/inventory/consume`、`Progression/Achievements`（T3 新发现，已登记 200 `{}`；数值还原需补抓） |
