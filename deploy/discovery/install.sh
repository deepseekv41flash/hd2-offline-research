#!/usr/bin/env bash
# HD2 stage-2 discovery backend installer
#
# 两种形态：
#   direct —— mock 直接绑 0.0.0.0:443（需要 443 空闲 + CAP_NET_BIND_SERVICE）
#   nginx  —— mock 只绑 127.0.0.1:8443，由本机 nginx 按域名在 443 上反代
#             （443 已被业务 nginx 占用时用这个；业务站完全不受影响）
#
# 用法:
#   sudo ./install.sh <研究仓目录> [--mode direct|nginx] [--domain D]
#        [--upstream-port 8443] [--nginx-bin PATH] [--nginx-vhost-dir DIR]
#        [--cert-out-dir DIR] [--days 2]
#
# 安装脚本做的事：形态预检 → 建 hd2disc 用户 → 拷代码 → venv 装 cryptography
#   → 签 N 天多 SAN 证书 → 渲染并启动 systemd 服务 →（nginx 形态）装 vhost + 测试 + reload
#   → 自检。任何一步失败都会打印原因并退出，不会在 nginx 配置测试失败时 reload。
set -euo pipefail

SRC=${1:-}; shift || true

MODE=direct
DOMAIN=api.live.prod.thehelldiversgame.com
UPSTREAM_PORT=8443
NGINX_BIN=/www/server/nginx/sbin/nginx
NGINX_VHOST_DIR=/www/server/panel/vhost/nginx
CERT_OUT_DIR=/www/server/panel/vhost/cert/hd2-discovery
DAYS=2

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --hosts) DOMAIN="${2%%,*}"; shift 2 ;;          # 兼容旧参数
    --upstream-port) UPSTREAM_PORT="$2"; shift 2 ;;
    --nginx-bin) NGINX_BIN="$2"; shift 2 ;;
    --nginx-vhost-dir) NGINX_VHOST_DIR="$2"; shift 2 ;;
    --cert-out-dir) CERT_OUT_DIR="$2"; shift 2 ;;
    --days) DAYS="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

case "$MODE" in direct|nginx) ;; *) echo "--mode 只能是 direct 或 nginx" >&2; exit 2 ;; esac
if [ -z "$SRC" ] || [ ! -d "$SRC/mock" ] || [ ! -d "$SRC/tools" ] || [ ! -d "$SRC/profiles" ] || [ ! -d "$SRC/samples" ]; then
  echo "用法: sudo $0 <研究仓目录> [--mode direct|nginx] [--domain D]" >&2
  echo "  <研究仓目录> 需包含 mock/ tools/ profiles/ samples/" >&2
  exit 2
fi
if [ "$(id -u)" -ne 0 ]; then echo "需要 root（装 systemd 服务 / 改 nginx）" >&2; exit 1; fi
command -v python3 >/dev/null 2>&1 || { echo "缺 python3" >&2; exit 1; }

APP=/opt/hd2-discovery
HERE=$(cd "$(dirname "$0")" && pwd)

echo "== 0. 形态预检（$MODE）=="
if [ "$MODE" = direct ]; then
  if ss -lnt 2>/dev/null | grep -q ':443 '; then
    echo "443 已被占用，业务机请改用 --mode nginx："; ss -lnt 2>/dev/null | grep ':443 ' | head -3 >&2
    exit 3
  fi
else
  [ -x "$NGINX_BIN" ] || { echo "nginx 不可执行: $NGINX_BIN（用 --nginx-bin 指定）" >&2; exit 3; }
  [ -d "$NGINX_VHOST_DIR" ] || { echo "vhost 目录不存在: $NGINX_VHOST_DIR" >&2; exit 3; }
  if ss -lnt 2>/dev/null | grep -q ":$UPSTREAM_PORT "; then
    echo "回环端口 $UPSTREAM_PORT 已被占用："; ss -lnt 2>/dev/null | grep ":$UPSTREAM_PORT " >&2
    exit 3
  fi
  if grep -rls --exclude='hd2-discovery.conf' "server_name[^;]*$DOMAIN" "$NGINX_VHOST_DIR" 2>/dev/null | grep -q .; then
    echo "nginx 里已有 $DOMAIN 的 server_name，先人工确认再装" >&2
    exit 3
  fi
fi

echo "== 1. 用户与目录 =="
id hd2disc >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin hd2disc
mkdir -p "$APP"/{state,logs,state/certs}

echo "== 2. 拷贝代码（不含 .git / state / dist）=="
for sub in mock tools profiles samples; do
  rm -rf "$APP/$sub"
  cp -a "$SRC/$sub" "$APP/$sub"
done
find "$APP" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "== 3. Python venv + 依赖（只有 cryptography 一个第三方包）=="
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet cryptography

echo "== 4. 生成短有效期证书（$DAYS 天，SAN=$DOMAIN）=="
( cd "$APP" && "$APP/venv/bin/python" -m tools.mockctl cert-gen --dir "$APP/state/certs" --hosts "$DOMAIN" --days "$DAYS" )

PROFILE=profiles/coop-discovery.json
SERVE_ARGS="--profile $PROFILE"
CAPS_AMBIENT="AmbientCapabilities=CAP_NET_BIND_SERVICE"
CAPS_BOUNDING="CapabilityBoundingSet=CAP_NET_BIND_SERVICE"
if [ "$MODE" = nginx ]; then
  PROFILE=profiles/coop-discovery-nginx.json
  SERVE_ARGS="--profile $PROFILE --no-crl"
  CAPS_AMBIENT=""
  CAPS_BOUNDING=""
fi

echo "== 5. 安装 systemd 服务（$PROFILE）=="
sed -e "s|@MODE@|$MODE|g" \
    -e "s|@PYTHON@|$APP/venv/bin/python|g" \
    -e "s|@SERVE_ARGS@|$SERVE_ARGS|g" \
    -e "s|@CAPS_AMBIENT@|$CAPS_AMBIENT|g" \
    -e "s|@CAPS_BOUNDING@|$CAPS_BOUNDING|g" \
    "$HERE/hd2-discovery.service.in" > /etc/systemd/system/hd2-discovery.service
install -m 0755 "$HERE/healthcheck.sh" "$APP/healthcheck.sh"
install -m 0755 "$HERE/cert-issue.sh" "$APP/cert-issue.sh"
cat > "$APP/deploy.env" <<ENV
MODE=$MODE
DOMAIN=$DOMAIN
UPSTREAM_PORT=$UPSTREAM_PORT
NGINX_BIN=$NGINX_BIN
NGINX_VHOST_DIR=$NGINX_VHOST_DIR
CERT_OUT_DIR=$CERT_OUT_DIR
ENV
chown -R hd2disc:hd2disc "$APP"
systemctl daemon-reload
systemctl enable --now hd2-discovery
sleep 2

if [ "$MODE" = nginx ]; then
  echo "== 6. 装 nginx vhost（$DOMAIN -> 127.0.0.1:$UPSTREAM_PORT） =="
  install -d -m 0755 "$CERT_OUT_DIR"
  install -m 0640 -o root -g www "$APP/state/certs/server.chain.pem" "$CERT_OUT_DIR/fullchain.pem"
  install -m 0640 -o root -g www "$APP/state/certs/server.key" "$CERT_OUT_DIR/privkey.pem"
  sed -e "s|__DOMAIN__|$DOMAIN|g" \
      -e "s|__CERT__|$CERT_OUT_DIR/fullchain.pem|g" \
      -e "s|__KEY__|$CERT_OUT_DIR/privkey.pem|g" \
      -e "s|__UPSTREAM_PORT__|$UPSTREAM_PORT|g" \
      -e "s|__ACCESS_LOG__|/www/wwwlogs/hd2-discovery.access.log|g" \
      -e "s|__ERROR_LOG__|/www/wwwlogs/hd2-discovery.error.log|g" \
      "$HERE/nginx-vhost.conf" > "$NGINX_VHOST_DIR/hd2-discovery.conf"
  echo "-- nginx -t --"
  if ! "$NGINX_BIN" -t; then
    echo "nginx 配置测试失败：已保留业务配置、未 reload。删掉 $NGINX_VHOST_DIR/hd2-discovery.conf 后重试。" >&2
    exit 4
  fi
  "$NGINX_BIN" -s reload
  sleep 1
fi

echo "== 7. 自检 =="
"$APP/healthcheck.sh" "$DOMAIN" || true

echo
echo "完成（mode=$MODE）。客户端需要的 CA 证书： $APP/state/certs/ca.cer"
echo "证书指纹："
( cd "$APP" && "$APP/venv/bin/python" -m tools.mockctl cert-status --dir "$APP/state/certs" 2>/dev/null | sed -n '1,12p' ) || true
