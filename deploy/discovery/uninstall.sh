#!/usr/bin/env bash
set -euo pipefail
systemctl disable --now hd2-discovery 2>/dev/null || true
rm -f /etc/systemd/system/hd2-discovery.service
systemctl daemon-reload
echo "服务已停止并移除；/opt/hd2-discovery 与 hd2disc 用户保留（按需手动删除）。"
