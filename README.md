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
> **阶段1 定稿摘要（一页看完）**：[docs/STAGE1-FINAL-2026-11-21.md](docs/STAGE1-FINAL-2026-11-21.md)
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
  compare_shapes.py   B9：两份登录体 schema 对照 + 字节对账 + 差异归因
  tcp_passthrough.py  透明 TCP 转发（不改 hosts 临时连真服务器做对照）
  mitm_proxy.py       T6a 旁路取证 MITM（CONNECT 代理 + 选择性 TLS 终止 + 只记 schema）
  analyze_mitm.py     T6a 分析：哪些域名可 MITM / 实际请求 / 响应 schema
  t7_probe.py         T7 必要性差分探针（blackhole / fake-200 逐组断开，自动判定是否阻断）
  t7b_probe.py        T7b 整局验收探针（PlayFab/Sony 黑洞下打一局，自动判定进图/结算并复原代理）
  steam_appinfo.py    T8b 只读解析 Steam appinfo 分片（magic 0x0756442a），导出任一游戏的官方启动项
  discovery_probe.py  阶段2 发现层双客户端探针（--self-test 离线自测 / --url 远端验活）
  analyze_e2_capture.py  阶段2 E2 抓包判定（Steam SDR vs PlayFab Party，--self-test 可离线验证）
tests/      pytest 104 项（含真实抓包矩阵、TLS 端到端、脱敏门禁、B9 schema 采集/对照、TCP 转发）
profiles/   offline-mock.json（与产品 profile 无关的独立配置）
docs/       阶段1 定稿摘要(STAGE1-FINAL) + 结项档 + A2 主机依赖 + B2/B3 大厅/登录 + T4 出网清单 + T4b 无 PSN + B9 登录体 schema + T6 MITM 取证 + T7 必要性差分 + T8 计划（数据面取证/EAC-off）+ T8 结果（EAC-off ✗ 否证）+ T8-P3（SSLKEYLOGFILE ✗ 对游戏无效）+ T8b（Broken Arrow 关 EAC 机制对照）+ T8c（打补丁跳反作弊的成本评估：加固/加密壳）+ T8d（社区项目 HellForge：免反作弊=固定旧 build；阶段2 替身方案参考） + **阶段2 设计（STAGE2-DESIGN：方案对比/架构/里程碑/判定实验）** + 志愿者环境侦察清单（VOLUNTEER-RECON） + 侦察结果判定（VOLUNTEER-RECON-RESULT：条件通过，差管理员权限） + 预检包（PREFLIGHT-KIT：志愿者以管理员重测改机能力） + 判定实验 E1/E3/E4 结果（PlayFab 存活 / SDK 原版一致 / B10 未证） + 发现层 API 契约与 M1 实现（STAGE2-DISCOVERY-API-CONTRACT）
docs/ 之外：deploy/discovery/（发现层 Debian/systemd 部署包）、kit/stage2/（客户端双路线测试包 + 测试卡）
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
| **未绑定 PSN 的整局单人任务（T3b）**：账号 B 进图 → 5.5 分钟局内 → 撤离结算 → 回船 | **✓✓ 实机（49 请求 / 0 非 200 / 0 fallback）** |
| **旁路连接到底传什么（T6a/MITM）**：PlayFab=大厅+Party 中继分配+QoS 列表；EOS=身份/配置/EAC 反作弊/遥测；Sony CRS=崩溃上报 | **✓✓ 实机**（79 条记录、0 握手失败，见 [T6](docs/T6-MITM-SIDECHANNEL-2026-11-21.md)） |
| 除 API 主机外的主机依赖（A2） | **✓✓ 运行时抓包完成**：无第二条游戏自有主机；旁路=PlayFab/EOS+EAC/Steam/Sony PC（见 [T4 文档](docs/T4-RUNTIME-EGRESS-2026-11-21.md)） |
| 出网审计的盲区 | T4 已补：DNS/SNI/UDP 五元组（UDP 3075=PlayFab QoS、3478/3479=Steam SDR、高端口=EOS） |
| PSN 是否必需 | **非必需**：未绑定 PSN 的 Steam 号可跳过绑定页进船，官方 API 仍是纯本地接管；Sony 侧域名两账号都出现（见 [T4b](docs/T4b-NOPSN-CONTROL-2026-11-21.md)） |
| **阶段1 必要性（T7，3 组×2 种处置 + 两轮整局）**：PlayFab/Sony 断开或伪 `200 {}` 都不阻断；**PlayFab 黑洞整局通过**（27.1s→120.2s→352.3s），**PlayFab+Sony 组合黑洞整局也通过**（阶段1 部署形态，24.0s→104.2s→340.3s，59 请求/0 错/0 fallback）；**EOS/EAC 两种处置都被拦**（EAC 需真实协议交互）⇒ 阶段1 只需接管官方 API + 保 EAC 网络；阶段2 必须保留 PlayFab | **✓✓ 实机**（6 轮差分 + T7b 整局 + 用户目视确认，见 [T7](docs/T7-STAGE1-NECESSITY-2026-11-21.md)） |
| 登录体 schema（B9）：437 B（绑定，9 字段）vs 346 B（未绑，6 字段），91 B = 3 个可选字段（票据长度恒定 116） | **✓✓ 实机**（见 [B9](docs/B9-LOGIN-BODY-SCHEMA-2026-11-21.md)）；"真登录过就会带"的缓存假设 **✗ 否证**，PSN 绑定关系 **✓ 支持** |
| 尚未在 mock 里还原的端点 | `Progression/inventory/consume`、`Progression/Achievements`（T3 新发现，已登记 200 `{}`；数值还原需补抓） |
