#!/usr/bin/env bash
# 卸掉发现层：停服务 + 删 unit；nginx 形态下再删 vhost 并 reload nginx（先 nginx -t）。
# 保留 /opt/hd2-discovery 与 hd2disc 用户，按需手动删除。
set -euo pipefail
APP=/opt/hd2-discovery
# shellcheck disable=SC1091
[ -f "$APP/deploy.env" ] && . "$APP/deploy.env"
MODE=${MODE:-direct}

systemctl disable --now hd2-discovery 2>/dev/null || true
rm -f /etc/systemd/system/hd2-discovery.service
systemctl daemon-reload

if [ "$MODE" = nginx ]; then
  VHOST="${NGINX_VHOST_DIR:-/www/server/panel/vhost/nginx}/hd2-discovery.conf"
  if [ -f "$VHOST" ]; then
    rm -f "$VHOST"
    if [ -x "${NGINX_BIN:-/www/server/nginx/sbin/nginx}" ] && "${NGINX_BIN:-/www/server/nginx/sbin/nginx}" -t; then
      "${NGINX_BIN:-/www/server/nginx/sbin/nginx}" -s reload
      echo "已删除 nginx vhost 并 reload"
    else
      echo "警告：nginx -t 未通过，未 reload；请先人工检查 nginx 配置" >&2
    fi
  fi
fi
echo "服务已停止并移除；/opt/hd2-discovery 与 hd2disc 用户保留（按需手动删除）。"
