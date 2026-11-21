# 阶段2 发现层部署包（Debian/systemd，两种形态）

把 `mock/`（含多客户端 `DiscoveryLobby`）部署成一台公网发现层后端，供两台客户端 hosts 指向。
只跑 HTTP JSON（登录/大厅/任务回放），**不做游戏数据面中继**（数据面走 Steam SDR / PlayFab 云）。

## 两种形态，先看 443 归谁

| 形态 | 适用 | mock 监听 | 443 归属 |
|---|---|---|---|
| `direct` | 干净机器（443 空闲） | `0.0.0.0:443`（TLS，靠 `CAP_NET_BIND_SERVICE`） | mock |
| `nginx` | 443 已被业务 nginx 占用（如腾讯云 A 机） | `127.0.0.1:8443`（TLS，只回环） | 本机 nginx 按域名反代 |

`nginx` 形态下业务站完全不受影响：只新增一个独立 vhost（`hd2-discovery.conf`，不含 BT 的
WAF/php/伪静态 include），`nginx -t` 通过才 reload；失败时保留原配置不 reload。

## 前置
- Debian 12/13、systemd、python3（≥3.10）、网络可出站（装 pip 包 `cryptography` 用）
- 云安全组/防火墙放行 **TCP 443**（客户端 → 发现层）
- 不需要 UDP 端口（发现层是纯 HTTP）
- `nginx` 形态额外需要：本机 nginx（BT 面板默认 `/www/server/nginx/sbin/nginx`）、
  vhost 目录（默认 `/www/server/panel/vhost/nginx/`）、回环 8443 空闲

## 安装
```bash
# 把研究仓的 mock/ tools/ profiles/ samples/ 传到服务器，然后：
sudo ./install.sh /root/stage2-discovery-src                       # 干净机器
sudo ./install.sh /root/stage2-discovery-src --mode nginx \
     --domain api.live.prod.thehelldiversgame.com                   # 业务机（443 被占）

# 其它参数（一般不用改）：
#   --upstream-port 8443    nginx -> mock 的回环端口
#   --nginx-bin /www/server/nginx/sbin/nginx
#   --nginx-vhost-dir /www/server/panel/vhost/nginx
#   --cert-out-dir /www/server/panel/vhost/cert/hd2-discovery
#   --days 2                证书有效期
```

脚本做的事：形态预检（443/8443/域名是否撞车）→ 建 `hd2disc` 用户 → 拷代码到
`/opt/hd2-discovery` → venv 装 `cryptography` → 签 N 天证书 → 渲染并启动 systemd 服务
→（`nginx` 形态）装 vhost + `nginx -t` + `reload` → 自检 → 写 `/opt/hd2-discovery/deploy.env`。

## 给客户端的东西
| 文件 | 用途 |
|---|---|
| `/opt/hd2-discovery/state/certs/ca.cer` | 测试根证书（客户端**用户级**安装即可，无需管理员） |
| 服务器公网 IP | 客户端 hosts：`<IP> api.live.prod.thehelldiversgame.com` |

客户端接入脚本见仓库 `kit/stage2/`（hosts 路线 + 代理路线 + 一键回滚 + 自检）。

## 运维
```bash
systemctl status hd2-discovery
journalctl -u hd2-discovery -n 100 --no-pager
/opt/hd2-discovery/healthcheck.sh          # /health + mock 直连（nginx 形态）+ 双客户端探针
/opt/hd2-discovery/cert-issue.sh           # 证书快到期时重签（nginx 形态会同步换证书并 reload）
./uninstall.sh                             # 卸服务（nginx 形态再删 vhost 并 reload）
```

日志：
- mock：`/opt/hd2-discovery/state/logs/mock.log`；服务 stdout：`logs/service.log`
- nginx 形态访问日志：`/www/wwwlogs/hd2-discovery.access.log`
- 请求行里的 `via <IP>` 表示该请求经 nginx 反代、真实客户端 IP 取自 `X-Real-IP`

## 关于账号归属（重要）
`mock/discovery.py` 用「来源 IP」把请求归属到账号。nginx 反代后对端恒为 `127.0.0.1`，
因此 nginx 形态下 profile 打开 `net.trust_proxy_headers`：mock 在**对端是回环**时采信
`X-Real-IP` / `X-Forwarded-For` 第一个地址；直连形态不采信（防伪造）。

## 兼容性
- `mock/` 只用标准库，只有证书生成依赖 `cryptography`；
- 若云主机是 arm64，`pip install cryptography` 用预编译 wheel 即可；
- 客户端侧不需要任何 Python/依赖。
