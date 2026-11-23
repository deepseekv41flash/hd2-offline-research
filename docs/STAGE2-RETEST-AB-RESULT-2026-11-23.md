# 阶段2 A+B 实机复测结果：兜底路径已验证（受控复现）— 2026-11-23

> **一句话**：A+B 的兜底路径（等待 1.5 s + 单未知候选绑定）**在真实客户端上验证通过**：
> `keys_bind_hit +1`、`keys_waited +1`、`keys_miss +0`、`join +1`、双人厅体 **1278 B**，判定 10/10 全绿。
> **但这是「受控复现」**：自然复现被证明**结构性不可行**（客户端上报窗口 10 s < 人可点击时间 ~17 s，中间隔着进船过场动画）。
> 同时修正了判定工具里一条**错判据**（C05 用 `lobby_get` 冒充"查过 Keys"）。

## 1. 最终判定（受控复现轮）

```
python tools/stage2_retest.py judge \
  --before state/evidence/retest-20261123/health-before4.json \
  --after  state/evidence/retest-20261123/health-after-final.json \
  --log-file state/evidence/retest-20261123/mock-watch.log
```

| 判据 | 结果 | 数值 |
|---|---|---|
| V01 增量可信 | ✓ | accounts 2→2、login 0→2 |
| **C01 兜底被触发** | **✓** | `keys_bind_hit Δ1`、`keys_waited Δ1`、`keys_hit Δ1`、`keys_miss Δ0` |
| C02 keys_miss 未增 | ✓ | Δ0 |
| C03 join_rejected 未增 | ✓ | Δ0（全程 0）|
| C04 双方都登录 | ✓ | login Δ2 |
| C05 双方上报身份 + Keys 有成功解析 | ✓ | put Δ2、keys_hit Δ1、keys_bind_hit Δ1（判据已修正，见 §5）|
| C06 加入被接受 | ✓ | join Δ1 |
| C07 留痕 | ✓ | bindings=2、keys_known=2 |
| L01 双人厅成员体 | ✓ | ≥1200 B 共 15 次，**最大 1278 B** |
| L02 成员被拒 | ✓ | `lobby_member:rejected` 0 次 |

**判定：✓✓ 通过（exit 0）**。证据快照：`health-before4.json`（19:45:10，全 0）、
`health-after-final.json`（19:50:22，含兜底计数）。

## 2. 四轮实机实录（19:13–19:50）

| # | 时间 | 方向 | 结果 | 关键数据 |
|---|---|---|---|---|
| 0 | 19:13–19:22 | 志愿者→我（房主方向） | **不可行** | 志愿者客户端的好友列表只查 8 个 account id，**不含我**（`lobby_accounts` 请求体实证）⇒ 他根本看不见我的船 |
| 1 | 19:29–19:33 | 镜像：我→他 | 加入成功、**未复现缝隙** | `keys_hit Δ2`、`keys_bind_hit 0`；我的客户端在进船时（19:33:17）就 PUT 了，他查询公钥在 19:33:32 |
| 2 | 19:36–19:39 | 镜像重试（拼手速） | 加入成功、**仍未复现** | PUT 19:39:40 → 键查询 19:39:47；**窗口只有 10 s，而可点击时间 ≈17 s**（过场动画）|
| 3 | 19:45–19:50 | 镜像 + **受控延迟 45 s** | **✓✓ 兜底触发、PASS** | `keys_waited +1`、`keys_bind_hit +1`、`keys_miss 0`、`join 1`、member 1278 B |

### 2.1 自然复现为什么不可行（实测，不是推测）

两轮镜像实测的时序完全一致：

```
登录 → +6 s  客户端建好自己的大厅（member 首帧 251 B）
      → +16 s 客户端自动 PUT /api/lobby 上报自己的大厅   ← 窗口关闭（A 机从此知道我的账号）
      → +17~25 s 进船过场动画结束，人才能点「加入」      ← 永远晚于窗口
```

- 第 1 轮：PUT 19:33:17 → 点击 ~19:33:32（晚 15 s）
- 第 2 轮：PUT 19:39:40 → 点击 ~19:39:47（晚 7 s，用户已"能点就点"）
- ⇒ **镜像方向下，加入方必须先过完过场动画才能操作，而客户端在加载期就完成了上报**，
  缝隙天然被堵死。房主方向（志愿者加入我）才是天然会撞上缝隙的方向，
  但今天志愿者客户端不持有我的 account id（轮 0），该方向今天不可用。

## 3. 受控复现方法（可复跑）

在 A 机 mock 上加一个**默认关闭**的测试闸门（`mock/handler.py`）：
环境变量 `MOCK_LOBBY_PUT_DELAY_MS` > 0 时，`PUT /api/lobby` 的处理先 sleep 对应毫秒数。
用途：把"上报慢"的窗口人为放大到人可操作的范围，从而在真实客户端上触发兜底路径。

```bash
# 打开（A 机）
printf '[Service]\nEnvironment=MOCK_LOBBY_PUT_DELAY_MS=45000\n' \
  > /etc/systemd/system/hd2-discovery.service.d/zz-test-lever.conf
systemctl daemon-reload && systemctl restart hd2-discovery
# 关闭（本轮已执行）
rm -f /etc/systemd/system/hd2-discovery.service.d/zz-test-lever.conf
systemctl daemon-reload && systemctl restart hd2-discovery   # 已还原 handler.py 到部署版本
```

**⚠ 踩坑记录（重要）**：`/health` 的 discovery 统计是**内存态**，`systemctl restart` 会清零。
本轮关闸门时重启了服务，导致关闸后的快照里 `keys_bind_hit/keys_waited` 全归零。
**判定必须用重启前的快照**（`health-after-final.json` = 重启前 19:50:22 拍的）。
以后跑这轮流程：**先拍 after 快照，再关闸门/重启**。

### 3.1 受控延迟的副作用（已记录，不影响结论）

- 45 s 延迟超过客户端 PUT 超时 ⇒ 客户端断开重试（日志 `!! PUT /api/lobby 客户端断开` 2 次），
  身份最终由重试补齐（19:52 起 `identities=2`）。
- 身份补齐前，双人厅体是 **1199 B**（两人但我的 profile 缺失）；补齐后变 **1277/1278 B**。
  ⇒ 厅体字节数对"成员资料是否已知"敏感，判据仍以 ≥1200 B 为准（1199 会被判为非双人证据，偏保守但安全）。

## 4. 本轮同时确认的正面结论

1. **加入链路本身健康**：三轮实机加入全部成功，`join` 计数递增，`lobby_member:rejected` 全程 0。
2. **双人厅体在真实客户端稳定复现**：1236 / 1237 / 1277 / 1278 B 共 15 帧。
3. **A+B 的兜底不制造回归**：`keys_miss` 零增长、`join_rejected` 零增长、无身份冲突（`conflict` 0）。
4. **公钥链路双向可达**：两个方向各产生一次 114/115 B 的 Keys 响应。

## 5. 判定工具修正（C05）

**原实现**：`C05 = put Δ≥1 and get Δ≥1`，其中 `get` 是 `GET /api/lobby/<id>`（大厅详情拉取），
却被当成"双方都查过 Keys"的代理。实测：镜像方向**没有任何一方**调 `GET /api/lobby/<id>`
（两轮均为 `get Δ0`），于是正常加入会被判 FAIL —— 这是**判据 bug，不是功能问题**。

**修正后**：`C05 = put Δ≥2（双方各上报一次身份）and (keys_hit + keys_bind_hit) Δ≥1`，
`lobby_get` 只作为参考信息打印、不作判据。配套 3 条回归测试
（`tests/test_stage2_retest.py`：无 lobby_get 也能过 / 只有一方 PUT 仍 fail / Keys 从未解析仍 fail）。

## 6. 遗留与建议

| 项 | 状态 | 建议 |
|---|---|---|
| 自然复现缝隙（房主方向） | ⚠ 未完成 | 唯一自然可撞的方向，但要求**志愿者客户端持有我的 account id**。下次让他先进我的船一次（同会话内即持有），再重启测 |
| 3 人以上候选 ≥2 | 按设计退回 miss | 已知限制，设计档 §6.3 |
| C05 判据 | ✓ 已修正 | 已加回归测试 |
| 受控闸门 | 代码保留、环境变量默认关闭 | 只在 A 机实验环境使用，禁止带进产品 |

## 7. 证据与路径

- 判定输入：`state/evidence/retest-20261123/health-before4.json`、`health-after-final.json`
- 全量请求日志：`state/evidence/retest-20261123/mock-watch.log`（A 机 `state/logs/mock.log` 的本地跟随，323 KB）
- A 机状态留存：`retest-20261123-state-round{1,2,3,4}.json`（各轮重置前的 `discovery-state.json`）
- 工具：`tools/stage2_retest.py`（check/snapshot/judge）、`tools/e2_watch.py`、`mock/handler.py`（闸门）
- 作业单：`docs/STAGE2-RETEST-AB-2026-11-23.md`；设计档：`docs/STAGE2-DESIGN-KEY-TIMING-GAP-2026-11-22.md`

## 8. 环境还原（复测后，2026-11-23 20:05–20:10）

| 项 | 动作 |
|---|---|
| 本机中继 / CRL / 日志跟随 | 全部停止；443 与 18081 均无监听 |
| 本机信任存储 | 已卸掉 A 机研究 CA `23ED103B…`（`CurrentUser\Root`）；产品 CA `B00398DB…` 全程未动 |
| hosts | **未改动**（中继正好绑同一 IP:443，原行可直接复用）|
| A 机 mock 代码 | 已还原为部署版本 `sha256 6d86d00b…`（临时延迟闸门撤除、systemd drop-in 删除）|
| A 机发现层状态 | 已重置干净（`accounts/lobbies/bindings` 全空；healthcheck 探针清掉了）|
| 通道交接 | 已通知 MITM 线「通道已还」并给还原清单，对方核对一致后已重启 alpha.2 引擎 |
| **A 机证书重签** | 20:07 重签（新 CA + 新叶子，有效期至 **2026-11-25 20:07 本地**）：
  CA 指纹 `83:0DE2:87:41:37:0F:29:5C:6A:C6:C6:98:9F:E5:9B:D5:7B:FD:E6`，
  `ca.cer` sha256 `3637f23a…bf92b`、`ca.crl` sha256 `257db2a9…5d70e9`（本地 `state/certs-a/` 已更新）。
  **明天志愿者只需替换这 2 个文件**（12 文件包其余不变）|
| 代码提交 | `44e944b`（明文 mock）、`a7b0039`（复测结果 + C05 修正 + 延迟闸门）、`835cbba`（backlog/还原清单）；已 push 到 `origin/master` |
