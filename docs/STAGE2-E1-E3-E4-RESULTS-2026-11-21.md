# 阶段2 判定实验 E1 / E3 / E4 结果（2026-11-21）

> 性质：**只读**实验（DNS/HTTPS 探测 + 下载官方公开 SDK 包比对哈希 + 静态字符串检索）。
> 不启动游戏、不改游戏文件、不改 hosts/证书。三态：`✓✓ 实机` / `✓ 离线+数据` / `⚠ 未证` / `✗ 否证`。

## 0. 一句话结论

| 实验 | 结论 |
|---|---|
| **E1** PlayFab 标题 `974A9` 是否存活 | **✓ 在线**（DNS 指向 Azure Front Door；API 返回结构化 PlayFab 错误 `NotAuthenticated 1074`） |
| **E3** 当前 build 的 Party/Lobby DLL 是否为公开 SDK 原版 | **✓✓ 逐字节一致**（Party 1.10.16 NuGet 包 + PlayFabMultiplayer 1.8.7 GitHub release 的 DLL SHA256 与游戏内完全相同） |
| **E4** B10「runtime 里关 PlayFab」开关是否存在 | **⚠ 未证**（只找到一条提示字符串，位于 `network.cpp`；配置/数据表里没有任何开关项） |

## 1. E1：PlayFab 标题存活探测

```
$ nslookup 974a9.playfabapi.com
名称:    mr-b01.tm-azurefd.net
Addresses: 2603:1061:14:145::1, 150.171.110.133
Aliases: 974a9.playfabapi.com → mainp-alpha-byagfybsgkckb8aj.b01.azurefd.net

$ curl -X POST https://974a9.playfabapi.com/Client/GetTitleData -H 'Content-Type: application/json' -d '{}'
{"code":401,"status":"Unauthorized","error":"NotAuthenticated","errorCode":1074,
 "errorMessage":"This API method does not allow anonymous callers."}   [HTTP 401, 0.58 s]

$ curl https://974a9.playfabapi.com/            [HTTP 403, 0.86 s]
```

判读：

- 返回的是 **PlayFab 自己的结构化错误体**（`code/status/error/errorCode/errorMessage`），不是 CDN/网络的通用错误
  ⇒ 标题 `974A9` 的 API 前端**在线且可路由**；
- `errorCode 1074 = NotAuthenticated`（"anonymous callers not allowed"）说明该 API 方法要求登录——
  这正是我们预期：**标题活着，只是我们没带票据**；
- **未证**的部分：标题是否仍接受 **Steam 实体登录**（需要真实 Steam 票据；B9 已证登录体里带 116 字符 `token`）。
  这一条会在 E2 双机实测时自然得到答案（客机入厅成功 = 接受）。

对方案A 的意义：**B8 的"标题存活"这一半已解除风险**；剩下"接受 Steam 实体登录"随 E2 验证。

## 2. E3：Party / PlayFab Multiplayer DLL 是否为公开 SDK 原版

### 2.1 游戏内文件的版本与哈希

| 文件 | 大小 | FileVersion | ProductVersion | SHA256 |
|---|---|---|---|---|
| `bin\PartyWin.dll` | 4,031,520 | 1.10.2601.09002 | **1.10.16** | `DE34667C2F7084…4E7995E0` |
| `bin\PlayFabMultiplayerWin.dll` | 1,878,048 | 1.8.2602.25003 | **1.8.7** | `95BC5F958E519F…0B8CED17` |

### 2.2 官方公开包的比对（自己下载、自己算哈希）

| SDK | 来源 | 包哈希 | 包内 DLL 大小 / SHA256 | 与游戏内文件 |
|---|---|---|---|---|
| PlayFab Party 1.10.16 | NuGet `Microsoft.PlayFab.PlayFabParty.Cpp.Windows` 1.10.16（nupkg 2,629,792 B） | `3C8A5170…828D08` | `build/native/lib/x64/release/PartyWin.dll` 4,031,520 / `DE34667C2F7084013D9C03CD5D1CB5DC3321BC36BB616A845E35534E4E7995E0` | **完全相同 ✓✓** |
| PlayFab Multiplayer 1.8.7 | GitHub `PlayFab/PlayFabMultiplayer` release `v1.8.7` 的 `Microsoft.PlayFab.PlayFabMultiplayer.Cpp.Windows.zip`（1,230,893 B） | `F1331238…C8CC297` | `bin/PlayFabMultiplayerWin.dll` 1,878,048 / `95BC5F958E519F612B479DE4227F3BDFFABA9B2B8DE987EB3F7991960B8CED17` | **完全相同 ✓✓** |

> 交叉印证：HellForge 的构建脚本 `scripts/Build-Engine.ps1` 里 pin 的正是这两个包与这两个哈希
> （`DE34667C…` / `95BC5F95…`），与我们独立下载计算的结果一致。

### 2.3 对方案B（HellForge 式替身）的意义

- **API 面不再是未知量**：替身直接用 Microsoft 公开头文件（Party 1.10.16 / Multiplayer 1.8.7）实现即可，
  不需要逆线协议、不需要猜签名；
- **替换方式可行**：当前 build 只是把这两个公开 SDK DLL 原样放进 `bin\`，替换同名文件即可（HellForge 对旧 build 已验证同法）；
- **但不能因此认为方案B 成本降到很低**：当前 build 多一层 **EOSSDK**（不可替，EAC 依赖，T8/T8c），
  且替身需实现 179 个导入 + 自建 Lobby/Party 后端与传输（HellForge ≈1.2 万行），每次游戏更新还要跟版本。
- 结论：方案B 从"未知风险"降级为"已知成本"——**保留为远期备选，仍不在本阶段投入**。

## 3. E4：B10「disabled playfab in runtime」开关检索

### 3.1 已检索范围与结果

| 对象 | 结果 |
|---|---|
| `data\settings.ini` 的 `playfab` 段 | 只有 `title_ids = { LIVE "974A9"; DEV/CERT/BIO/TECH "BCDDE"; BETA "7C75A" }` —— **没有任何 enable/disable 键** |
| `bin\helldivers2.exe` | 唯一相关串是 `0x01725AF0`：`You must login to playfab before making a game session! (ignore this if you have disabled playfab in runtime)`，位于 `D:\Work\…\stingray\runtime\engine\network\network.cpp` |
| 开关类候选串正则（`disable_playfab` / `no_playfab` / `playfab.enabled` / `force_playfab` / `set_playfab` …） | **0 命中** |
| `network.*` 绑名/指标串 | 16 个，**全是监控指标**（`network.transport.*`、`network.game.*`、`network.playfab_transport.*`），**没有开关绑定** |
| `data\game\dl_library.dl_typelib` + 全部 `*.dl_bin`（约 50 MB 数据表） | `playfab` **0 命中** |
| `data\game\game.dll`（49 MB） | 4 条符号串：`playfab`、`playfab_user`、`playfab_platform_update_mt`、`Playfab (%s) [Platform (%s)]`（像 C++/脚本符号，非开关） |
| `bin\runtime.dll`（64.9 MB，保护器重打包） | `playfab` **0 命中**（字符串被处理/加密） |

### 3.2 判读

- **不存在明文配置开关**：settings.ini 的数据表、Lua 数据表、引擎绑定名里都没有"关 PlayFab"的键。
- 那条提示字符串的措辞是「**if you have disabled playfab in runtime**」——指向**运行时/脚本层**（Lua 或引擎内部状态），
  且该文案在 `network.cpp`（创建 game session 的检查点）；要定位到具体机制只能：
  ① 反编译 `network.cpp` 对应逻辑（当前 build 代码段加密，见 [T8c](T8c-EACOFF-PATCH-COST-2026-11-21.md)，静态不可行）；
  ② 实验性试参数（改 settings.ini / 注入 Lua / 改命令行），属**改游戏文件或注入**，需要用户授权且越出只读边界。
- 因此 **B10 判定维持 `⚠ 未证`**；方案A 的风险控制应改为：
  **持续监控 PlayFab 标题存活（E1 已有可复现探测），若标题关停再启动 B10 的动态调查或方案B 评估。**

## 4. 对阶段2 路线的影响

| 影响 | 说明 |
|---|---|
| 方案A（保留 PlayFab 云） | E1 解除"标题已死"的风险；剩余风险=标题未来关停、以及"是否接受 Steam 实体登录"（E2 验证） |
| 方案B（替身） | E3 把 API 面从"未知"变为"公开头文件+已知哈希"，成本可估；但仍受 EOS 不可替 + 跟版本约束 |
| 方案C（B10 关 PlayFab） | 静态检索未找到开关 ⇒ 不能作为兜底路线；要从候选里降级为"若将来需要再动态调查" |
| 下一步 | 等志愿者预检结果（管理员 vs 标准用户）→ 部署发现层 → 正式测试包 → E2 |

## 5. 三态汇总

| 断言 | 状态 |
|---|---|
| PlayFab 标题 `974A9` 的 API 前端在线 | ✓（结构化 PlayFab 401，DNS 正常） |
| 标题仍接受 Steam 实体登录 | ⚠ 未证（需真实票据，E2 自然验证） |
| 当前 build 的 PartyWin.dll = 官方 1.10.16 原版 | ✓✓（SHA256 相同） |
| 当前 build 的 PlayFabMultiplayerWin.dll = 官方 1.8.7 原版 | ✓✓（SHA256 相同） |
| 存在明文/配置层的"关 PlayFab"开关 | ✗ 否证（检索不到；仅运行时提示字符串） |

## 6. 证据与复现

- **E1**：任意 HTTP 客户端（须绕过系统代理）。
  ```powershell
  curl.exe -sS --max-time 20 --noproxy "*" -X POST "https://974a9.playfabapi.com/Client/GetTitleData" -H "Content-Type: application/json" -d "{}"
  ```
- **E3**（我方已下载并校验，产物在 gitignored 的 `state/evidence/e3/`）：
  - `https://api.nuget.org/v3-flatcontainer/microsoft.playfab.playfabparty.cpp.windows/1.10.16/microsoft.playfab.playfabparty.cpp.windows.1.10.16.nupkg`
  - `https://github.com/PlayFab/PlayFabMultiplayer/releases/download/v1.8.7/Microsoft.PlayFab.PlayFabMultiplayer.Cpp.Windows.zip`
  - 期望：nupkg `3C8A5170…`、zip `F1331238…`、DLL `DE34667C…` / `95BC5F95…`
- **E4**：字符串检索脚本在 `state/evidence/e4/`（对 exe/dl_bin/game.dll 的只读扫描）。
