#!/usr/bin/env bash
# HD2 stage-2 discovery backend installer (Debian 13 / systemd)
# 用法: sudo ./install.sh /path/to/stage2-discovery-src [--hosts "api.live.prod.thehelldiversgame.com,9749.playfabapi.com"]
set -euo pipefail

SRC=${1:-}; shift || true
HOSTS="api.live.prod.thehelldiversgame.com"
while [ $# -gt 0 ]; do
  case "$1" in
    --hosts) HOSTS="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$SRC" ] || [ ! -d "$SRC/mock" ] || [ ! -d "$SRC/tools" ]; then
  echo "用法: sudo $0 <研究仓目录> [--hosts \"a,b\"]" >&2
  echo "  <研究仓目录> 需包含 mock/ tools/ samples/ profiles/" >&2
  exit 2
fi
if [ "$(id -u)" -ne 0 ]; then echo "需要 root（安装 systemd 服务 / 建用户）" >&2; exit 1; fi

APP=/opt/hd2-discovery
echo "== 1. 用户与目录 =="
id hd2disc >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin hd2disc
mkdir -p "$APP"/{state,logs,state/certs}
echo "== 2. 拷贝代码（不含 .git / state / dist）=="
for sub in mock tools profiles samples; do
  rm -rf "$APP/$sub"
  cp -a "$SRC/$sub" "$APP/$sub"
done
echo "== 3. Python venv + 依赖（只有 cryptography 一个第三方包）=="
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet cryptography
echo "== 4. 生成短有效期证书（默认 2 天）=="
"$APP/venv/bin/python" -m tools.mockctl cert-gen --dir "$APP/state/certs" --hosts "$HOSTS" --days 2
echo "== 5. 安装 systemd 服务 =="
install -m 0644 "$(dirname "$0")/hd2-discovery.service" /etc/systemd/system/hd2-discovery.service
install -m 0755 "$(dirname "$0")/healthcheck.sh" "$APP/healthcheck.sh"
install -m 0755 "$(dirname "$0")/cert-issue.sh" "$APP/cert-issue.sh"
chown -R hd2disc:hd2disc "$APP"
systemctl daemon-reload
systemctl enable --now hd2-discovery
sleep 2
echo "== 6. 自检 =="
"$APP/healthcheck.sh" || true
echo
echo "完成。客户端需要的 CA 证书： $APP/state/certs/ca.cer"
echo "证书指纹："
"$APP/venv/bin/python" -m tools.mockctl cert-status --dir "$APP/state/certs" 2>/dev/null | sed -n '1,12p' || true
