#!/usr/bin/env bash
# 发现层健康检查：本机回环（两种形态都查）+ 服务形态对应的真实入口
#   direct: mock 自己绑 443
#   nginx : mock 在 127.0.0.1:8443，443 由本机 nginx 反代（查 nginx 这一跳）
set -euo pipefail
APP=/opt/hd2-discovery
# shellcheck disable=SC1091
[ -f "$APP/deploy.env" ] && . "$APP/deploy.env"
MODE=${MODE:-direct}
DOMAIN=${1:-${DOMAIN:-api.live.prod.thehelldiversgame.com}}

echo "== 部署形态: $MODE  域名: $DOMAIN =="
echo "== /health（经 443，SNI=$DOMAIN） =="
curl -sk --max-time 5 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/health" | head -c 600 || true
echo
if [ "$MODE" = nginx ]; then
  echo "== mock 直连（回环 ${UPSTREAM_PORT:-8443}，跳过 nginx 便于定位是 mock 还是 nginx 的问题） =="
  curl -sk --max-time 5 "https://127.0.0.1:${UPSTREAM_PORT:-8443}/health" | head -c 300 || true
  echo
  echo "== discovery 探针（经 nginx：Host=域名 + 显式账号头） =="
  "$APP/venv/bin/python" "$APP/tools/discovery_probe.py" --url "https://127.0.0.1:443" --insecure \
    --host-header "$DOMAIN" 2>&1 | tail -14
else
  echo "== discovery 探针（用显式账号头，不依赖 IP） =="
  "$APP/venv/bin/python" "$APP/tools/discovery_probe.py" --url "https://127.0.0.1:443" --insecure 2>&1 | tail -14
fi
