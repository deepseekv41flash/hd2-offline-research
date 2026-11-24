# 第 5 轮（注入轮）执行手册 — 2026-11-25

> 与 EAC-off 线（`session-dbc6228a`）共用同一窗口：**RE 只读注入 + 我这边网络采集**。
> 这轮只回答一个问题：**RE 的只读注入动作，在 EAC 上报通道上有没有可观察的变化？**

## 0. 判定链（顺序不可跳）

```
第 0 步  链路存活？          ← 不活 ⇒ 本轮"无结论"，后面的红/绿都不算数
第 1 步  User-Agent          ← 最廉价的突变点（含 "Developer Build"）
第 2 步  新增周期（只认"明显更快"）
第 3 步  体积分布（新量级 / 大包翻倍 / 中位偏移 >30%）
第 4 步  correlation-id      ← 只作关联键，不作判据
```

**红线**：`v8 = 0 条` **不能**直接读成"未被感知"。必须先用 `eac-watch` 判是**链路断**还是**真没上报**。

## 1. 固定握手（★ 顺序反了本轮就作废）

| # | 谁 | 动作 | 回话 |
|---|---|---|---|
| ① | EAC-off 线 | 发「准备」+ 预计下单时刻 | — |
| ② | **我** | 起采集器 + 自检 PASS + 起旁路采样 | **「代理已就位 HH:MM:SS」** |
| ③ | EAC-off 线 | **收到"就位"之后**才下单 | 「下单 HH:MM:SS」 |
| ④ | EAC-off 线 | 轮询到游戏进程 | 「进程出现 HH:MM:SS.mmm」 |
| ⑤ | RE | 注入完成 | 「注入完成 <ms>」+ EAC 是否存活 |
| ⑥ | **我** | 采集结束 → 还原代理 → 对比 → 报告 | 三态表 |

**硬规则**：③ 必须在 ② 之后。
### 1.1 窗口长度（用第 4 轮实测反推）

| 事件 | 相对「进程出现」|
|---|---|
| 首条 v8 | **+55 s** |
| 末条 v8（p4）| +187 s |
| 启动器退出（p4/演练）| +13~15 s（启动器≠游戏，别把它当游戏）|

⇒ `--duration 300` 从「就位」起算，只要**下单不晚于就位后 ~60 s** 就能覆盖全系列；
若对面拖到就位后 2 min 才下单，必须**重启采集器**（或把 duration 加到 420）再加旁路采样。
因为 `api.epicgames.dev` 是**长连接复用**
（第 4 轮实测：6 条隧道承载 29 个请求），**隧道建好之后再挂代理，整段 v8 序列一条也看不到**。

## 2. 执行清单（三个终端）

### T1 — 采集器（先起，等自检 PASS）

```bash
cd /d/mod/work/hd2-offline-research
MSYS_NO_PATHCONV=1 python tools/eac_capture_proxy_run.py \
    --out-dir D:/mod/tmp/eac-p5 --duration 300 --chain-proxy 127.0.0.1:7897
```

判据：日志出现 **`[*] 开窗自检：PASS`**（采集器自检会经代理打一条 72 B 假上报，
`PREFLIGHT`×8）⇒ 才算"代理已就位"，**此时才能回话让对面下单**。
`FAIL` ⇒ **不回话**，先修（采集器会打印 `[!] 自检失败 —— 现在停下来，别浪费游戏窗口`）。

### T2 — 旁路采样（T1 起来后立刻起，覆盖整个窗口）

```bash
cd /d/mod/work/hd2-offline-research
python tools/eac_watch_liveness.py --out D:/mod/tmp/eac-p5/eac-watch.jsonl \
    --duration 330 --interval 5
```

### T3 — 事后对比（T1 自行收尾还原代理之后）

```bash
cd /d/mod/work/hd2-offline-research
python tools/compare_baseline_vs_run.py --baseline state/evidence/baseline-v8.json \
    --run D:/mod/tmp/eac-p5 ; echo "compare exit=$?"

python tools/eac_watch_liveness.py --verdict D:/mod/tmp/eac-p5/eac-watch.jsonl \
    --run D:/mod/tmp/eac-p5 --min-v8 3 ; echo "liveness exit=$?"
```

## 2.5 三臂对比（A / C / B）★ 归因必需

| 臂 | 条件 | 判据来源 | 存放 |
|---|---|---|---|
| **A** | 无注入、启动器未改 | ✅ 已有两轮（15 + 14 条 v8） | `state/evidence/baseline-v8.json`（**唯一锚**）|
| **C** | 改启动器 IAT、**无注入** | 即 RE 冻结版验证轮 | `state/evidence/control-launcher-patched.json`（**永不并入锚**）|
| **B** | 改启动器 IAT + 真注入 | 注入轮 | 只跟 A、C 比，不冻 |

**为什么必须有 C**：注入/补丁在进程出现后 +4~8 ms 就做完并恢复，而 EAC 上报通道 **+44 s** 才建立
⇒ B 出现异常时，单看网络**无法区分**"注入被感知"与"启动器被改被感知"。只有 C 能把这个差值单独测出来。
**若最终没有 C**：B 相对 A 的变化必须写成**合并效应**，不得单独归因。

```bash
# C 轮采完：只对比，**绝不 --freeze 进 baseline-v8.json**（锚不能带噪声）
python tools/compare_baseline_vs_run.py --baseline state/evidence/baseline-v8.json     --run D:/mod/tmp/eac-p6-control ; echo "A vs C exit=$?"

# 允许给 C 单独冻一份"控制臂基线"（它不是锚，只用于 C vs B）
python tools/compare_baseline_vs_run.py --freeze --run D:/mod/tmp/eac-p6-control     --out state/evidence/control-launcher-patched.json

# B 轮：C vs B（比 A vs B 更能说明"注入本身"的效应）
python tools/compare_baseline_vs_run.py --baseline state/evidence/control-launcher-patched.json     --run D:/mod/tmp/eac-p7-inject ; echo "C vs B exit=$?"
```

### 2.6 `compare_baseline_vs_run.py` 退出码（2026-11-25 起）

| 码 | 含义 | 处置 |
|---|---|---|
| **0** | 绿 / 黄 | 正常判读 |
| **1** | 红（某信号命中） | 先核对是不是判据噪声，再下结论 |
| **3** | **无结论**（本轮 0 条 v8） | 先跑 `eac_watch_liveness --verdict`；链路断 ⇒ 不得写"被感知" |

> 0 条 v8 判"红"是**错的**（会把"没采到"误报成"被感知"），已改成"无结论"+退出码 3。

## 2.7 轮次记录卡（**每轮采完必跑**，防三臂混淆）

三臂 + 两个对比 + RE 的毫秒时刻，靠嘴记必乱。每轮采完跑一次，把身份固化到
`state/evidence/rounds/<date>-<arm>.json`：

```bash
python tools/eac_round_record.py --arm C --hook-installed true     --run D:/mod/tmp/eac-p6-control     --liveness D:/mod/tmp/eac-p6-control/eac-watch.jsonl     --launcher-seen 01:35:47.100 --launcher-gone 01:36:02.400     --game-appeared 01:35:49.675 --inject-at-ms 8     --re-json D:/mod/work/hd2-eacoff-mode/state/<RE>.json     --out state/evidence/rounds/2026-11-25-C.json
```

**护栏**：`--arm C` 且 `--hook-installed false` ⇒ 自动降级为 **`A(deg)`** 并打警告。
理由：C 臂的唯一价值是"启动器真的被改过"；钩子没装上却记成 C，就会把 A 当控制臂用，
B 的异常会被**错误归因**。这一条由工具强制，不靠人记。

记录卡里含：`counts`（v8/v6/telemetry）、`req_len` 序列、`dt_ms` 序列、`ua_set`、时间轴、链路 `verdict`。

## 3. 采集前 30 秒自检

- [ ] 端口 `18092` 空闲（`netstat -ano | grep 18092`）
- [ ] 无 `helldivers2.exe` / `start_protected_game.exe` / `EasyAntiCheat_EOS.exe`
- [ ] IE 代理 = `127.0.0.1:7897`（**原值**，采完要还原成它）
- [ ] `state/evidence/baseline-v8.json` 存在且 `count = 15`
- [ ] 不用管理员（系统代理 + CONNECT 终结不需要提权）

## 4. 判读（照着填，不许跳步）

### 4.1 第 0 步：链路存活（`eac_watch_liveness.py --verdict`）

| 退出码 | label | 含义 | 本轮处置 |
|---|---|---|---|
| 0 | `link-alive` | EAC 活着 + 有连接 + v8 ≥ 3 | 继续做 1/2/3/4 步 |
| 2 | `link-dead-eac-missing` | EAC 进程大面积消失 | **无结论**（很可能注入把它弄死了）|
| 2 | `link-dead-no-network` | 游戏全程 0 连接 | **无结论**（代理没生效）|
| 2 | `link-alive-but-traffic-missing` | EAC 活但 v8 < 3 | **无结论**（流量不足）|
| 3 | `no-game-window` | 没看到游戏进程 | **无结论**（窗口没对上）|

### 4.2 时间轴对齐（他们给三行，我复算）

| 事件 | 来源 | 用途 |
|---|---|---|
| 注入完成 `<ms>` | RE json | 判"注入是否早于隧道建立（+44 s）" |
| 进程出现 `HH:MM:SS.mmm` | 他们的轮询 + `CreationDate` | **原点**（我的 `eac-watch` 能独立复算）|
| 首条 v8 | 本轮 `mitm.jsonl` | 应 ≈ 原点 **+55 s**（p4 实测）；偏太多说明连接建立更慢 |

### 4.3 三态标记（报告里统一用）

`✓✓ 实机` = 真机实测；`✓ 离线+数据` = 离线分析 + 已有数据支撑；`⚠ 未证` = 未验证；`✗ 否证` = 已否证。

## 5. 报告模板

```
## 第 5 轮（注入轮）网络侧判读

- 链路：link-alive（EAC 存活 100%、连接峰值 N、v8 N 条）    ← 第 0 步
- 原点：进程出现 HH:MM:SS.mmm；注入完成 +X ms；首条 v8 +NN s
- [绿/黄/红] User-Agent：与基线一致 / 变化（列出差异）
- [绿/黄/红] 新增周期：无 / 出现 <N s（基线最快 X s × 0.7 = Y s）
- [绿/黄/红] 体积：max N vs 33,280；中位 N vs 4,924（偏移 X%）；大包 N vs 4
- [关联] correlation-id：v8 N 条 / telemetry N 条、交集 N
- dt_ms 全列：...

> 限定：载荷不可读（熵 ≈8、非 protobuf、不可解压），只覆盖 v8/v6 两个通道；
> "未发现变化"≠"未上报"，只表示**在这四个可观察信号上没看到差异**。
```

## 6. 异常分支

| 现象 | 先查 | 处置 |
|---|---|---|
| 自检 FAIL | 端口 18092 是否被占、上游 7897 是否通 | 修好再回"就位"；不回话 |
| `anticheat` 0 条 | `eac-watch` 的 EAC 存活 / `netstat` 18092 是否有连接 | EAC 死 ⇒ 无结论；代理没生效 ⇒ 本轮作废 |
| 只有 PREFLIGHT 1 条 | 游戏是否真的走了系统代理 | 大概率游戏没启动到 EOS 初始化阶段，重跑 |
| 采集结束后代理没还原 | `reg query ... //v ProxyServer` | 手动改回 `127.0.0.1:7897` |

## 6.5 中止 / 重置采集窗口（★ 第 1 次实战踩过，务必照做）

### 6.5.1 「就位」后的下单截止时间

完整系列末条 v8 在 **+187 s**（相对进程出现），所以：

```
下单截止 = 就位 + (300 − 187) ≈ 就位 + 110 s
```

超过这个时刻还没下单 ⇒ **主动重置窗口**（重开 300 s），并把新的「就位」时刻重新告知对方。
否则只采到半截序列（首条 v8 有、尾巴没有），分布类判据不可用。

### 6.5.1b 卡死看门狗（**必须开**，第 1 次实战就撞上）

启动器被冻死、游戏永不出现 —— 这轮本该 30 s 止损，实际空等了 300 s。采样器自带看门狗：

```bash
python tools/eac_watch_liveness.py --out D:/mod/tmp/eac-p7/eac-watch.jsonl     --duration 330 --interval 1     --alert-file D:/mod/tmp/eac-p7/FROZEN.json     --grace 30 --abort-on-freeze
```

判据（`launch_verdict`，纯函数已单测）：

| 状态 | 触发条件 | 动作 |
|---|---|---|
| 正常等待 | 启动器未出现 / 未超 grace | 等 |
| **`suspect-launcher-frozen`** | 启动器出现 > `--grace`（默认 30 s）仍无游戏 | **立刻中止窗口**（三杀）+ 报"疑似卡死" |
| `game-appeared` | 游戏进程出现 | 放行，正常采完 |

我这边每 5 s 轮询 `FROZEN.json` 是否存在，一出现就走中止流程。

### 6.5.2 中止采集时必须杀 **三样**（只杀第一个会留孤儿）

采集器是**三层进程**：

| 层 | 进程 | 只杀上层的后果 |
|---|---|---|
| 1 | `eac_capture_proxy_run.py` | — |
| 2 | `mitm_proxy.py`（子进程，**占着 18092**）| 端口不释放，新窗口起不来 |
| 3 | `tshark` + `dumpcap` | 抓着网卡，pcap 写不进去 |

```powershell
# 一次清干净（按命令行匹配，别按名字乱杀）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'eac_capture_proxy_run|eac_watch_liveness|mitm_proxy' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-Process tshark,dumpcap -ErrorAction SilentlyContinue | Stop-Process -Force
# 然后手动把 IE 代理改回 127.0.0.1:7897，并确认 18092 无 LISTENING
```

### 6.5.3 重置窗口要**换新目录**，绝不 `rm -rf` 旧证据

第 1 次实战（01:45）我重置窗口时删了旧目录，**丢掉了第一次窗口的 `eac-watch.jsonl`**
（里面有"启动器出现"的原样时刻）。虽然靠第二个窗口的采样仍能佐证，但这是不该付的代价。

```
D:/mod/tmp/eac-p6-c-1/   # 第 1 次窗口（即使作废也留着）
D:/mod/tmp/eac-p6-c-2/   # 第 2 次窗口
```

## 7. 回滚清单（每次收尾都核对）

- [ ] IE 代理 = `127.0.0.1:7897`（`ProxyEnable=1`）
- [ ] `18092` 已释放、`tshark` 已退出
- [ ] 不动 hosts / 不装根 CA / 不加防火墙规则（第 1~3 轮已证无效，别再碰）
- [ ] 证据落 `D:/mod/tmp/eac-p5`（`mitm.jsonl` / `raw/` / `eac-watch.jsonl` / `meta.json`）
