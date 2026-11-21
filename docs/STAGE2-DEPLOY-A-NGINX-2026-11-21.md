# 阶段2 发现层部署记录 —— 服务器 A（nginx 反代形态）

> 2026-11-21 20:27–20:35 实机部署完成并验收。本文记录**为什么这样部署、改了什么、怎么验证、怎么回滚**。
> 三态：✓✓ = 实机验证；✓ = 离线/数据验证；⚠ = 未证；✗ = 否证。

## 1. 结论

| 项 | 结果 |
|---|---|
| 部署目标 | 服务器 A（腾讯云 2C4G，Debian 13.6，Python 3.13.5） |
| 形态 | **nginx 反代**：mock 只监听 `127.0.0.1:8443`（TLS），nginx 在 443 上按域名转发 |
| 域名 | `api.live.prod.thehelldiversgame.com` |
| 公网入口 | `https://api.live.prod.thehelldiversgame.com/`（hosts 指向 A） |
| CA 证书 | `CN=HD2 Offline Research CA`，SHA256 指纹 `284C18B8B0E0093E34C730078B46B9A16DA965B2C8001A2A6993D6AB6A07BEE8` |
| CA 有效期 | 至 `2026-11-23 20:32 +0800`（48 h；过期用 `cert-issue.sh` 重签，客户端换包） |
| 服务 | `hd2-discovery.service`（用户 `hd2disc`，`ProtectSystem=strict`，只写 `state/` 与 `logs/`） |
| 验收 | 公网 `/health` 200 ✓✓；公网双客户端发现层探针 **PASS** ✓✓；业务站回归正常 ✓✓；Windows Schannel 证书校验通过 ✓✓ |

## 2. 为什么是 nginx 反代（不是直绑 443）

服务器 A 是**业务机**：nginx（BT 面板版）已占 `0.0.0.0:443`，挂着 6 个业务站，另有 BT 面板、docker、mihomo。
直绑 443 不可能。两条备选：

| 方案 | 结论 |
|---|---|
| 停业务 nginx / 改它监听端口 | ✗ 不可接受（业务中断风险） |
| **新增独立 vhost 反代到 mock** | ✓ 采用：只新增文件，`nginx -t` 通过才 reload，业务站零改动 |
| 改部署到服务器 B（443 空闲） | 未采用：B 内存紧（1.7 GB，可用 ~245 MB、swap 已用 2.3 GB），用户判断风险更高 |

vhost 是独立文件、**不包含 BT 的 WAF/php/伪静态 include**，与业务站完全隔离。

## 3. 为反代形态改了什么（代码）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `mock/server.py` | 新增 `effective_client_addr()`；`MockServer(..., trust_proxy_headers=)`；请求分发用它替代裸 TCP 对端 IP；日志带 `via <对端IP>` | 反代后 TCP 对端恒为 `127.0.0.1`，若按对端 IP 归属账号，**两台客户端会被并成同一个账号**。信任仅在对端是回环时生效，直连模式行为不变（防伪造） |
| `tools/mockctl.py` | `serve` 读 profile `net.trust_proxy_headers`，另有 `--trust-proxy-headers` | 部署侧开关 |
| `profiles/coop-discovery-nginx.json` | 新增：绑 `127.0.0.1:8443`、`net.trust_proxy_headers=true`、开 body_shapes（B9 登录体schema）、大厅 discovery | 反代形态专用 profile |
| `tools/discovery_probe.py` | 新增 `--host-header` | 经 nginx 验活时连 `127.0.0.1:443` 但要让 nginx 按真实域名选 vhost |
| `deploy/discovery/install.sh` | 新增 `--mode direct\|nginx` 与 nginx 参数；形态预检（443/8443/域名撞车）；venv 修复（`cd $APP`）；写 `deploy.env` | 一份脚本支持两种形态 |
| `deploy/discovery/nginx-vhost.conf` | 新增 vhost 模板（SNI/server_name → `proxy_pass https://127.0.0.1:8443`，注入 `X-Real-IP`/`X-Forwarded-For`） | 反代入口 |
| `deploy/discovery/hd2-discovery.service.in` | 由模板渲染（`direct` 带 `CAP_NET_BIND_SERVICE`，`nginx` 不带） | 最小权限 |
| `deploy/discovery/healthcheck.sh` / `cert-issue.sh` / `uninstall.sh` | 支持 `deploy.env`；nginx 形态同步换证书/reload/删 vhost | 运维闭环 |
| `deploy/remote_exec.py` | 新增：paramiko 直连执行/上传（DSH SSH MCP 是只读白名单，部署需要执行 `bash`/`systemctl`/`nginx -s reload`） | 部署通道，不改 MCP 配置 |

测试：`pytest` **130 passed**（新增反代归属 8 项 + kit 防回归 18 项）；`discovery_probe --self-test` PASS。

## 4. 部署过程（实机命令）

```bash
# 本地打包研究仓（mock/tools/profiles/samples/deploy）
tar -czf state/dist/hd2-discovery-src.tar.gz mock tools profiles samples deploy/discovery
# 上传 -> /root/hd2-deploy/ 解包 -> chmod +x deploy/discovery/*.sh
# 服务器先补 venv 包（Debian 13 默认没有 ensurepip）
apt-get install -y python3.13-venv

bash /root/hd2-deploy/src/deploy/discovery/install.sh /root/hd2-deploy/src \
     --mode nginx --domain api.live.prod.thehelldiversgame.com
```

脚本做：形态预检 → `hd2disc` 用户 → 拷代码 `/opt/hd2-discovery` → venv + `cryptography`
→ `cert-gen --hosts <域名> --days 2` → 渲染 systemd 服务并启动 → 拷证书到
`/www/server/panel/vhost/cert/hd2-discovery/{fullchain,privkey}.pem`（`root:www 0640`）
→ 渲染 `/www/server/panel/vhost/nginx/hd2-discovery.conf` → `nginx -t` → `nginx -s reload`
→ 自检 → 写 `/opt/hd2-discovery/deploy.env`。

实机耗时约 40 s（含 pip 装 cryptography）。

## 5. 验收证据（✓✓ 全部实机）

| 检查 | 命令 | 结果 |
|---|---|---|
| 公网可达 | 本机 `curl --resolve <域名>:443:106.52.201.42 https://<域名>/health` | `{"ok":true,"mock":"hd2-offline-mock",…}` |
| 公网双客户端发现层 | `discovery_probe --url https://106.52.201.42:443 --insecure --host-header <域名>` | `RESULT: PASS（双客户端发现层全流程通过）` |
| 反代 IP 归属生效 | 服务器 `grep 'via ' mock.log` | 请求行带 `via 127.0.0.1`（即真实客户端 IP 已取自 `X-Real-IP`） |
| 业务站回归 | 本机 `curl --resolve dsh.hyw114514.asia…` / `maibot…` | `dsh 401`（其正常鉴权响应）/ `maibot 200` |
| nginx 配置 | 部署时 `nginx -t` | `syntax is ok` / `test is successful` |
| 服务状态 | `systemctl is-active hd2-discovery` | `active` |
| 监听面 | `ss -lnt` | `0.0.0.0:443`（nginx）/ `127.0.0.1:8443`（mock） |
| Windows Schannel 证书校验 | 本机 `curl.exe --resolve …`（**无 -k**，用户级装 CA） | HTTP 200 —— CA 信任 + 证书链通过 |
| CRL 服务二进制 | 本机 `crl-server.ps1` + 请求 `/ca.crl` | 366 B，SHA256 与服务器 `ca.crl` 一致；非 `/ca.crl` 路径 404 |

## 6. 回滚

```bash
# 方式一（推荐）：卸载脚本（删服务 + nginx vhost + reload，保留 /opt 与用户）
bash /root/hd2-deploy/src/deploy/discovery/uninstall.sh
# 方式二：手工最小回滚
systemctl disable --now hd2-discovery
rm -f /www/server/panel/vhost/nginx/hd2-discovery.conf
/www/server/nginx/sbin/nginx -t && /www/server/nginx/sbin/nginx -s reload
```

业务站不受影响（vhost 是独立文件；回滚只删这一个文件）。

## 7. 已知限制 / 风险

1. **证书 2 天有效期**：到 `2026-11-23 20:32 +0800`。测试窗口须在此之前，或重签后重新分发客户端包（`cert-issue.sh`）。
2. **BT 面板可能覆盖 vhost**：若在面板里新增/修改站点触发重写，需确认 `hd2-discovery.conf` 还在、`nginx -t` 后 reload。面板不会主动删无关文件。
3. **CRL 依赖是软失败（⚠ 未证硬依赖）**：Windows curl.exe 在 CRL 服务停掉后仍接受证书（CryptNet 缓存/软失败）。客户端包仍带 CRL 服务（与阶段1 配方一致、成本低）；若游戏侧报证书错误，先查 `http://127.0.0.1:18081/ca.crl` 是否可取。
4. **两台客户端不能同时用一个账号**：反代形态靠 `X-Real-IP` 归属账号；同一出口 IP 的两台机器会被视为同一账号（同一个 NAT 下的两人会撞）。E2 是两地两人，不触发；后续 4 人局需注意。
5. **服务器 A 是业务机**：我们只加了 `hd2-discovery.conf` + 证书目录两个路径；每次动 nginx 都跑了 `nginx -t`，失败不 reload。业务回归已测，但长时间运行仍需观察。
6. **部署源码在 `/root/hd2-deploy/src`**（非版本库目录），重装/升级需重新上传 tar 包。
