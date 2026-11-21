#!/usr/bin/env bash
# 重新签发发现层证书（有效期短；客户端需重新安装新 CA 或沿用同一 CA）
# nginx 形态下同时把新证书装到 nginx 证书目录并 reload nginx（先 nginx -t，失败不 reload）。
set -euo pipefail
APP=/opt/hd2-discovery
# shellcheck disable=SC1091
[ -f "$APP/deploy.env" ] && . "$APP/deploy.env"
MODE=${MODE:-direct}
HOSTS=${1:-${DOMAIN:-api.live.prod.thehelldiversgame.com}}
DAYS=${2:-2}

( cd "$APP" && "$APP/venv/bin/python" -m tools.mockctl cert-gen --dir "$APP/state/certs" --hosts "$HOSTS" --days "$DAYS" )
chown -R hd2disc:hd2disc "$APP/state/certs"

if [ "$MODE" = nginx ]; then
  install -d -m 0755 "${CERT_OUT_DIR:-/www/server/panel/vhost/cert/hd2-discovery}"
  install -m 0640 -o root -g www "$APP/state/certs/server.chain.pem" "${CERT_OUT_DIR}/fullchain.pem"
  install -m 0640 -o root -g www "$APP/state/certs/server.key" "${CERT_OUT_DIR}/privkey.pem"
  "$NGINX_BIN" -t && "$NGINX_BIN" -s reload
fi

systemctl restart hd2-discovery
sleep 1
"$APP/healthcheck.sh"
echo "新 CA: $APP/state/certs/ca.cer（客户端需装这张）"
