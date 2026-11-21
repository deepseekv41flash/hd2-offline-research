#!/usr/bin/env bash
# 重新签发发现层证书（有效期短；客户端需重新安装新 CA 或沿用同一 CA）
set -euo pipefail
APP=/opt/hd2-discovery
HOSTS=${1:-api.live.prod.thehelldiversgame.com}
DAYS=${2:-2}
"$APP/venv/bin/python" -m tools.mockctl cert-gen --dir "$APP/state/certs" --hosts "$HOSTS" --days "$DAYS"
chown -R hd2disc:hd2disc "$APP/state/certs"
systemctl restart hd2-discovery
sleep 1
"$APP/healthcheck.sh"
echo "新 CA: $APP/state/certs/ca.cer（客户端需装这张）"
