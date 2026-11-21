#!/usr/bin/env bash
# 重启演练（部署门禁）：验证「后端重启不会让客户端粘在旧厅」的两条路径
#
#   Phase 1 干净重启（systemctl restart → SIGTERM → 优雅退出）
#     → 大厅必须**原样保留**（live，成员还在），客户端无感
#   Phase 2 非干净重启（SIGKILL）
#     → 大厅必须转成**终态**（closed / numLobbyMembers=0），客户端拿到明确答案
#       而不是 200 {}（空对象会让客户端以为"还在加载"）
#
# 用法：bash deploy/discovery/restart_drill.sh [域名]
#   默认直连回环 127.0.0.1:${UPSTREAM_PORT:-8443}（nginx 形态下 mock 的端口）
set -uo pipefail
APP=/opt/hd2-discovery
# shellcheck disable=SC1091
[ -f "$APP/deploy.env" ] && . "$APP/deploy.env"
DOMAIN=${1:-${DOMAIN:-api.live.prod.thehelldiversgame.com}}
BASE="https://127.0.0.1:${UPSTREAM_PORT:-8443}"
ACC="76561190000000099011"
LID="dddddddd-0000-4000-8000-000000000001.r-00000099"
HOST_ID="2231864975069446623"
FAIL=0

say() { echo "[drill] $*"; }
fail() { echo "[drill] FAIL: $*"; FAIL=1; }

health_ok() {
  for _i in $(seq 1 30); do
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 2 "$BASE/health" || true)
    [ "$code" = "200" ] && return 0
    sleep 0.5
  done
  return 1
}

create_lobby() {
  curl -sk --max-time 5 -X PUT "$BASE/api/lobby" \
    -H 'Content-Type: application/json' -H "X-HD2-Account: $ACC" \
    -d "{\"lobbyId\":\"$LID\",\"players\":[{\"memberAccountId\":\"$HOST_ID\",\"isHost\":true,\"hasInfo\":true,\"playerName\":\"DRILL\"}],\"numLobbyMembers\":1}" \
    -o /dev/null -w '%{http_code}'
}

get_lobby() {
  curl -sk --max-time 5 "$BASE/api/lobby/$LID" -H "X-HD2-Account: $ACC"
}

cleanup() {
  curl -sk --max-time 5 -X DELETE "$BASE/api/lobby/clear" -H "X-HD2-Account: $ACC" -o /dev/null || true
}

say "target=$BASE account=$ACC"
if ! health_ok; then
  say "服务未就绪，演练中止"
  exit 2
fi
cleanup

say "== Phase 1: 干净重启 =="
code=$(create_lobby)
[ "$code" = "200" ] || fail "建房返回 $code"
systemctl restart hd2-discovery
health_ok || fail "重启后健康检查超时"
body=$(get_lobby)
if printf '%s' "$body" | grep -q "$HOST_ID"; then
  say "Phase 1 OK：重启后大厅原样保留（成员仍在）"
else
  fail "Phase 1 失败：重启后大厅丢失或变终态 -> ${body:0:160}"
fi

say "== Phase 2: 非干净重启（SIGKILL） =="
cleanup
code=$(create_lobby)
[ "$code" = "200" ] || fail "建房返回 $code"
main_pid=$(systemctl show -p MainPID --value hd2-discovery)
[ -n "$main_pid" ] && [ "$main_pid" != "0" ] || fail "拿不到 MainPID"
systemctl kill -s KILL hd2-discovery || fail "SIGKILL 失败"
sleep 0.5
systemctl start hd2-discovery
health_ok || fail "非干净重启后健康检查超时"
body=$(get_lobby)
if printf '%s' "$body" | grep -q '"numLobbyMembers":0'; then
  say "Phase 2 OK：非干净重启后旧厅返回终态（numLobbyMembers=0）"
else
  fail "Phase 2 失败：旧厅未变终态 -> ${body:0:160}"
fi
cleanup

if [ "$FAIL" -ne 0 ]; then
  say "演练结果：FAIL"
  exit 1
fi
say "演练结果：PASS（干净重启保状态 + 非干净重启给终态）"
