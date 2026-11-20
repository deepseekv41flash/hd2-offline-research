# B9 — 登录体真实 schema 对照（437 B vs 346 B）`✓✓`

> 日期：2026-11-21 02:03–02:21 ｜ 环境：离线 mock（`100.67.169.17:443`）+ 研究 CA + hosts 接管；
> 含一次**真服务器对照实验**（02:17–02:21，用透明 TCP 转发，详见 §4.3）
> 采集纪律：只记 **字段名 / 长度 / 字符类 / sha8**，**不落任何原值**；产物在 `state/evidence/b9/`（gitignore）
> 对账工具：`tools/compare_shapes.py`；存证 `state/evidence/b9/compare.txt`

## 0. 结论（一句话）

**437 B 与 346 B 的 91 B 差异，来自账号 A 多带 3 个可选账号态字段（`isSuspended` / `isBanned` / `betaToolkitAccountId`）；
登录票据 `token` 两个账号都是 116 字符、其余字段长度完全相同——差异与票据长度无关。**
B3 侦察里「437 B 定长 ⇒ 单票据定长」的推断**被推翻**（见 §4）。

**这三个字段与"是否连过真服务器"无关**：让账号 B 真登录官方服务器一次后再抓，body 仍是 346 B
（`delta = 0`，见 §4.3）⇒ 「历史账号态缓存」假设 **✗ 否证**，「PSN 绑定关系」假设 **✓ 支持（强，非绝对）**。

## 1. 方法：schema 采集开关

`mock/bodycapture.py` 新增 `BodyShapeCapture`，mock 以 `--capture-bodies` 启动后：

* 只对 `body_shapes.paths`（默认 `/api/Account/Login`）生效；
* 字符串只记 `len` + 字符类（`hex`/`base64`/`uuid`/`digits`/`ascii`/`text`）+ `sha256[:8]`；
* 每次请求落一个 `state/evidence/b9/<ts>-<sha8>.json` + `index.jsonl` 一行；
* 未启用时零开销、零落盘。

**接线探针**（先证明开关真的生效，再采集真数据）：

```
curl.exe --noproxy "*" -sk -X POST https://100.67.169.17:443/api/Account/Login \
  -H "Content-Type: application/json" -d '{"probe":"b9-wiring-check"}'
```

→ 立即产生 `27B / format=json / keys=["probe"]` 记录，且原值 `b9-wiring-check` 不在文件里（只有 `len=15 / class=ascii / sha8`）。
探针记录随后清空，目录只留真实登录。

## 2. 实测结果

### 2.1 账号 B（未绑定 PSN）— `346 B`，6 字段，2 次独立样本一致

| 字段 | 类型 / 长度 | 02:03:09 | 02:06:44 |
|---|---|---|---|
| `buildIdentifier` | str 61 ascii | sha8 `16fa4932` | sha8 `16fa4932`（不变） |
| `publicKey` | str 44 base64 | sha8 `9db0a677` | sha8 `2b442252`（每次新） |
| `platform` | number `3` | — | — |
| `instanceId` | str 6 digits | sha8 `4d874b74` | sha8 `4d874b74`（不变） |
| `token` | str **116** ascii | sha8 `a1758584` | sha8 `74bc758e`（每次新） |
| `clientVersion` | number `1007203` | — | — |

> 两次样本**所有字段长度完全相同**（346 B 恒定）⇒ 346 B 是账号 B 的稳定值，不是偶发。

### 2.2 账号 A（已绑定 PSN）— `437 B`，9 字段

| 字段 | 类型 / 长度 |
|---|---|
| `isSuspended` | **bool false**（B 无此字段） |
| `publicKey` | str 44 base64（sha8 `a7360c3f`） |
| `platform` | number `3` |
| `isBanned` | **bool false**（B 无此字段） |
| `instanceId` | str 6 digits（sha8 `4d874b74`，与 B 相同） |
| `token` | str **116** ascii（sha8 `5cbd0f14`） |
| `betaToolkitAccountId` | **str 19 digits**（sha8 `dc52388f`；B 无此字段） |
| `buildIdentifier` | str 61 ascii（sha8 `16fa4932`，与 B 相同） |
| `clientVersion` | number `1007203` |

字段出现顺序（原始顺序）：
`A = isSuspended, publicKey, platform, isBanned, instanceId, token, betaToolkitAccountId, buildIdentifier, clientVersion`
`B = buildIdentifier, publicKey, platform, instanceId, token, clientVersion`

> 注意：**共同字段的相对顺序在两份里并不一致**（`buildIdentifier` 在 B 里排第 1、在 A 里排第 8），
> 既不是字母序也不是固定 schema 序 ⇒ 字段是按运行时来源**追加**进 body 的，**不能用字段顺序推断来源**。

## 3. 差异归因：91 B 精确分解 `✓✓`

`tools/compare_shapes.py` 先做**字节对账**：仅用 schema（字段名 + 每个值的长度）按观测到的序列化风格重建 body 长度。

该风格由本轮实测确定：**键值间两个空格、逗号后一个空格、结尾一个换行**（即 `json.dumps(..., separators=(', ', ' : ')) + "\n"`）：

```
[字节对账] 按 schema 重建: A=437 B=346 -> MATCH
[共同且一致的字段] 6: publicKey, platform, instanceId, token, buildIdentifier, clientVersion
[只出现在 A 的字段]
  + isSuspended: bool(False)
  + isBanned: bool(False)
  + betaToolkitAccountId: str(len=19, class=digits)
[差异归因]
  新增字段 JSON 本体 = 82 B
  新增字段分隔符风格 = 9 B
  合计 = 91 B (= delta 91 ? YES)
```

* **82 B** = 3 个新增字段自身的 JSON 成本（键名 39 + 引号/冒号/逗号 12 + 值 31）；
* **9 B** = 3 个新增字段各带 3 B 分隔符风格开销（`" : "` 多两格、`", "` 多一格）；
* **0 B** = 既有 6 个字段**一个都没改长度**（对账工具直接给出 `common_diff = []`）。

⇒ 直接否证三种猜想：①Steam 票据长度不同；②`publicKey` 长度不同；③`buildIdentifier`/`instanceId` 随账号变。
`token` 恒为 116 字符、字符类 `ascii`（不是 hex、也不是纯 base64）——票据类字段与账号绑定状态无关。

## 4. 三字段语义与来源判定

### 4.1 已确认的来源约束 `✓✓`

1. **不是本次登录响应回读**：登录响应样本只有 `{"sessionId":"00000000-0000-4000-8000-000000000000"}`（52 B），
   不含这三个字段；客户端没有可回读的载体。
2. **不是运行期从别的端点取**：账号 A 会话的 mock 日志显示，登录 POST 是**本进程第一个 POST**，
   在此之前只有一个不需要登录的公开端点：

   ```
   02:08:04.430 GET    /api/Configuration/GameClient 0B -> 200 17409B [exact]
   02:08:04.465   SHAPE POST   /api/Account/Login 437B sha=0f521117 format=json
   02:08:04.465 POST   /api/Account/Login 437B -> 200 52B [state:account_login]
   ```

   ⇒ 三个值在**任何账号相关网络交互之前**就已存在，只能来自**本地账号态**。
3. **本机明文存档里没有**：以下与账号强相关的本地文件既不含这三个字段名，也不含 19 位数字值的 hash 命中
   （扫描方式：文件内 19 位数字 → sha256 前 4 字节 == `dc52388f`）：

   | 文件 | 大小 | 字段名命中 | 值 hash 命中 |
   |---|---|---|---|
   | `%APPDATA%\Arrowhead\Helldivers2\saves\<A>::user_settings.config`（文件名含账号派生 hash，已遮蔽） | 1472 B | 0 | 0 |
   | `%APPDATA%\Arrowhead\Helldivers2\saves\<B>::user_settings.config` | 1489 B | 0 | 0 |
   | `E:\Steam\userdata\<A>\553850\remote\testament_new.sav` | 9179 B | 0 | 0 |
   | `E:\Steam\userdata\<B>\553850\remote\testament_new.sav` | 3041 B | 0 | 0 |

   ⇒ 若由本地承载，则承载者只能是**加密/二进制容器**（`.sav` 未解密，不能排除），不在明文配置里。

### 4.2 两个竞争归因（判别实验已做，见 §4.3）

| 假设 | 内容 | 支持 | 反对 | 状态 |
|---|---|---|---|---|
| **H1 账号绑定态** | 账号在 PSN / HD2 后端有真实账号记录并绑定 PSN 时，客户端从本地 **Sony testament（PSN PC SDK）** 缓存读出 `betaToolkitAccountId`（19 位数字，与 PSN account id 形态一致）及两个布尔标志；未绑定账号无此缓存 ⇒ 字段整体省略 | ①唯一区别就是"是否绑 PSN"；②`testament_new.sav` 正是按账号分文件；③19 位纯数字形态与 PSN account id 相容；④§4.3 实验：真登录一次不会带来这三个字段 | 无直接证据（`.sav` 加密，取不出值）；未做"给 B 绑 PSN"的正向对照 | **✓ 支持（强，非绝对）** |
| **H2 历史账号态缓存** | 客户端缓存"上次由真服务器下发的账号对象"（挂起/封禁标志 + 账号 id），登录时回传；账号只要连过一次真服务器就会有 | 语义上更自然（服务端权威数据被客户端回传）；账号 B 最初是全新账号、从未连过真服务器 | **§4.3 实验直接否证最简形式**：B 真登录官方服务器一次后，下次启动**仍是 346 B** | **✗ 否证** |

### 4.3 判别实验（2026-11-21 02:17–02:21，已完成）：让账号 B 真登录官方服务器一次

**不改 hosts**（写 hosts 需要管理员，本会话非提权）。改用 `tools/tcp_passthrough.py`：停掉 mock，
在同一 `100.67.169.17:443` 上做**透明 TCP 转发**到真服务器 `172.200.240.121:443`。
转发器不终止 TLS、不落明文，只统计字节数。

**先证明转发的确实是真的官方服务器**（不是 mock）：

```
subject: {'commonName': 'api.live.prod.thehelldiversgame.com'}
issuer : {'countryName': 'US', 'organizationName': "Let's Encrypt", 'commonName': 'YR2'}
notAfter: Dec 27 02:26:34 2026 GMT
```

（研究 CA 是自签 + 48 h；这是 **Let's Encrypt** 签发的真证书 ⇒ TLS 端到端到官方服务器。）

**会话统计**（`state/evidence/b9/real-passthrough.log`）：

| 项 | 值 |
|---|---|
| 连接数 | 12 |
| 上行合计 | 37,564 B |
| 下行合计 | 232,843 B（单连接最大 94,299 B —— 与 mock 里 `warinfo` 83,609 B 量级吻合，说明真登录成功并拉到真实战况） |
| 游戏 | 真登录后正常到主界面，**优雅退出**（WM_CLOSE，4 s 内退出，保证缓存能落盘） |
| 真登录后本地落盘 | `<B>::user_settings.config`（2:18:14）、`testament_new.sav` 2,901 B（2:20:25） |

**然后切回 mock，再启动账号 B，抓第 4 份登录体**（`02:21:06`）：

```
A = B after real login  (346 B, 6 fields)
B = B before real login (346 B, 6 fields)
delta = A - B = 0 B
[字节对账] 按 schema 重建: A=346 B=346 -> MATCH
[共同且一致的字段] 6: buildIdentifier, publicKey, platform, instanceId, token, clientVersion
```

⇒ **H2 的最简形式被否证**：账号在官方服务器上有了记录（真登录成功）之后，客户端**依然**只发 6 个字段。
⇒ **H1 获得支持**：差异只能来自账号侧的**绑定关系**（PSN 关联），而不是"有没有连过真服务器"。

**这次实验的边界（诚实标注）**：

* 未做"给账号 B 绑定 PSN"的正向对照（那要动用户的 PSN 账号，超出本轮授权）——所以 H1 记 **✓（强支持）** 而非 ✓✓；
* 只覆盖"登录到主界面 + 停留约 2 分钟"；若缓存需要更特殊条件（例如完整打一局、或首次 21:9 教学流程）才落盘，本实验未覆盖；
* 但"普通真登录一次"这一最简形式已被直接排除，这足以判定差异**不是**由"连过官方服务器"引起的。

**实验的现场复原**：hosts 行全程未改动（`100.67.169.17 api.live.prod.thehelldiversgame.com # hd2proxy`），
转发器已停止，mock 已恢复监听 443 + 18081 并继续正常服务（02:21 会话 39 请求 / 0 非 200 / 0 fallback）。

### 4.3 为什么这条结论对离线 mock 有价值 `✓✓`

* mock 的登录处理**不校验 body**（只看路径），所以 346 B / 437 B **两种登录体都返回 200 + 52 B `sessionId`**，
  两个账号都进到主界面 —— 这是 T4b（账号 B）与 T2/T3（账号 A）都已实测的。
* ⇒ **离线接管不需要"伪装这三个字段"**：既不需要伪造 `betaToolkitAccountId`，也不需要伪造两个布尔。
* ⇒ 反过来，若将来要做"更真实的登录响应/账号态回放"，才需要关心它们（属于 B 线/产品化议题，不是本线阻塞项）。

## 5. 三态判定

| 断言 | 状态 | 依据 |
|---|---|---|
| 437 B 与 346 B 的差异只在 3 个可选字段，既有字段长度全同 | **✓✓ 实机** | §2 + §3 字节对账 MATCH |
| `token` 与账号绑定状态无关（恒 116 字符 ascii） | **✓✓ 实机** | 两次 B 样本 + A 样本 |
| 91 B = 82 B（字段本体）+ 9 B（分隔符风格） | **✓✓ 实机** | `tools/compare_shapes.py` 对账 + 归因 sum_check |
| 三字段的值来自本地账号态而非本次网络回读 | **✓✓ 实机** | §4.1 时序证据（登录是本进程首个 POST）+ 登录响应仅 `sessionId` |
| **"真登录官方服务器一次"会让三字段出现**（H2 最简形式） | **✗ 否证** | §4.3 实验：B 真登录成功（232 KB 真实数据）后仍 346 B，`delta = 0` |
| 三字段与账号的 **PSN 绑定关系**相关（H1） | **✓ 支持（强，非绝对）** | §4.3 排除"连过真服务器"；三字段仅在绑定账号出现；19 位数字形态与 PSN account id 相容。未做"给 B 绑 PSN"正向对照 |
| 离线 mock 无需伪装这三个字段 | **✓✓ 实机** | 两种 body 均 200，两账号均进主界面 |
| 本地明文配置/存档承载这三字段 | **✗ 否证** | §4.1 三、文件名/值 hash 均 0 命中（加密容器仍可能，故整体来源仍 ⚠） |

## 6. 复现

```powershell
cd D:\mod\work\hd2-offline-research
# 1) 起 mock 并开 schema 采集（监听 100.67.169.17:443 + 127.0.0.1:18081）
python tools\mockctl.py serve --capture-bodies
# 2) 启动游戏（Steam 当前账号决定抓到哪一份 schema）→ 登录
Start-Process "steam://rungameid/553850"
# 3) 看结果（每个请求一个 json + index.jsonl）
Get-ChildItem state\evidence\b9
# 4) 对照两份 schema（自动字节对账 + 91 B 归因）
python tools\compare_shapes.py state\evidence\b9\<A>.json state\evidence\b9\<B>.json `
  --label-a "account A (PSN-bound)" --label-b "account B (unbound)"
```

单元测试：`python -m pytest tests/test_bodycapture.py tests/test_compare_shapes.py`
（`test_compare_shapes.py` 用合成 body 锁定"预测长度 = 真实字节数"与 91 B 归因，防回归。）

## 7. 顺带得到的两条可用事实

1. **客户端 body 序列化风格**：`", "` + `" : "` + 结尾换行（紧凑 JSON 会是 328 B / 410 B，实测 346 B / 437 B）。
   将来若 mock 需要**构造**登录体或其它请求体来骗过客户端校验，按此风格生成才与真实字节数一致。
2. **`instanceId` 是机器级安装 id**：账号 A、账号 B、四次会话全部 sha8 `4d874b74` ⇒ 与账号无关，
   不能当账号标识使用（会话内稳定、跨账号相同）。

## 8. 附带产出：透明 TCP 转发工具（判别实验用）

`tools/tcp_passthrough.py`：把本机某 `IP:port` 上的 TCP 流原样转给真实后端，**不终止 TLS、不落明文**，
只统计"连接数/上行字节/下行字节"。用途：在不改 hosts（无需管理员）的前提下，临时让客户端连真服务器做对照实验。

```powershell
# 停 mock 后，用同一地址/端口转发到官方 API（TLS 端到端到真服务器）
python tools\tcp_passthrough.py --listen 100.67.169.17:443 --upstream 172.200.240.121:443 `
  --log state\evidence\b9\real-passthrough.log
```

判据：客户端侧拿到的证书应是公共 CA 签发给 `api.live.prod.thehelldiversgame.com`（本轮实测 Let's Encrypt YR2）；
若拿到的是研究 CA 自签证书，说明 mock 还在跑、转发没生效。

单测：`python -m pytest tests/test_tcp_passthrough.py -q`（2 项：字节原样往返 + 上游不可达时干净关闭）。
