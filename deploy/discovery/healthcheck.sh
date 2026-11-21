#!/usr/bin/env bash
# 发现层健康检查：本机回环 + 真实域名 SNI（证书是我们自签的，-k 仅用于健康检查）
set -euo pipefail
APP=/opt/hd2-discovery
DOMAIN=${1:-api.live.prod.thehelldiversgame.com}
echo "== /health（回环 443，SNI=$DOMAIN） =="
curl -sk --max-time 5 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/health" | head -c 600
echo
echo "== discovery 探针（用显式账号头，不依赖 IP） =="
"$APP/venv/bin/python" "$APP/tools/discovery_probe.py" --url "https://127.0.0.1:443" --insecure 2>&1 | tail -14
