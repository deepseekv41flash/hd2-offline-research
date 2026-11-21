# 阶段2 发现层部署包（Debian/systemd）

把 `mock/`（含多客户端 `DiscoveryLobby`）部署成一台公网发现层后端，供两台客户端 hosts 指向。
只跑 HTTP JSON（大厅/登录/任务回放），**不做游戏数据面中继**（数据面走 Steam SDR / PlayFab 云）。

## 前置
- Debian 12/13、systemd、python3（≥3.10）、网络可出站（装 pip 包 `cryptography` 用）
- 云控制台/防火墙放行 **TCP 443**（客户端 → 发现层）
- 不需要 UDP 端口（发现层是纯 HTTP）

## 安装
```bash
# 在服务器上（把研究仓的 mock/tools/profiles/samples 传上去）
sudo ./install.sh /root/stage2-discovery-src --hosts "api.live.prod.thehelldiversgame.com"
```
安装脚本做的事：建 `hd2disc` 用户 → 拷贝代码到 `/opt/hd2-discovery` → venv 装 `cryptography`
→ 签 2 天有效期的多 SAN 证书 → 装 systemd 服务（`CAP_NET_BIND_SERVICE` 绑 443）→ 启动 → 跑一次自检。

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
/opt/hd2-discovery/healthcheck.sh                 # /health + 双客户端探针
/opt/hd2-discovery/cert-issue.sh                  # 证书快到期时重签（默认 2 天）
```
证书/CRL：CRL 服务绑在 `127.0.0.1:18081`（仅本机，供引擎自检）；客户端不直接访问 CRL。

## 兼容性
- `mock/` 只用标准库，只有证书生成依赖 `cryptography`；
- 若云主机是 arm64，`pip install cryptography` 用预编译 wheel 即可。
