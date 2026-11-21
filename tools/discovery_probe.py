"""阶段2 发现层双客户端探针（离线自测 + 远端验活）。

两种模式：
  * `--self-test`：进程内起一个只跑发现层的 MockHandler（随机端口），用两个模拟客户端
    （靠 `X-HD2-Account` 头区分）走完「A 建房 → B 看见 → B 入厅 → A 看到 B → 幂等 → 清理」，
    不需要游戏、不需要 443/管理员。
  * `--url http://主机:端口`：对**已在运行**的后端做同样一轮（E2 现场验活用；
    也用于验证部署的发现层）。

用法：
    python tools/discovery_probe.py --self-test
    python tools/discovery_probe.py --url http://127.0.0.1:443
    python tools/discovery_probe.py --url https://api.example:443 --insecure
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mock import discovery as discovery_mod  # noqa: E402
from mock.capture import CaptureStore  # noqa: E402
from mock.handler import MockHandler, Response  # noqa: E402

LOBBY_ID = "11111111-2222-4333-8444-555555555555.r-00000001"


# ------------------------------------------------------------------ 进程内自测
class _ProbeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _run(self) -> None:
        mock: MockHandler = self.server.mock  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        try:
            resp: Response = mock.handle(self.command, self.path, body, headers,
                                         client_addr=self.client_address[0])
        except Exception as exc:  # noqa: BLE001
            resp = Response(500, json.dumps({"error": str(exc)}).encode(), "application/json", "error")
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        self.send_header("Content-Length", str(len(resp.body or b"")))
        self.send_header("X-Mock-Source", resp.source or "")
        self.end_headers()
        if resp.body:
            self.wfile.write(resp.body)

    def do_GET(self):  # noqa: N802
        self._run()

    def do_PUT(self):  # noqa: N802
        self._run()

    def do_POST(self):  # noqa: N802
        self._run()

    def do_DELETE(self):  # noqa: N802
        self._run()

    def log_message(self, *args):  # 安静
        pass


def _serve_in_process() -> ThreadingHTTPServer:
    store = CaptureStore.load(REPO / "samples" / "store-season")
    mock = MockHandler(store, profile={"lobby": {"mode": "discovery", "echo_put": True, "ttl_seconds": 120}})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    srv.mock = mock  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ------------------------------------------------------------------ HTTP 客户端
class Client:
    def __init__(self, base: str, account: str, *, insecure: bool = False,
                 host_header: str = ""):
        self.base = base.rstrip("/")
        self.account = account
        self.host_header = host_header
        self.ctx = ssl.create_default_context()
        if insecure:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict, str]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("X-HD2-Account", self.account)
        if self.host_header:
            # 反代形态验活：连 127.0.0.1 但让 nginx 按真实域名选 vhost
            req.add_header("Host", self.host_header)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=10, context=self.ctx) as resp:
                raw = resp.read()
                code, src = resp.status, resp.headers.get("X-Mock-Source", "")
        except urllib.error.HTTPError as exc:  # 4xx/5xx 也要能读
            raw, code, src = exc.read(), exc.code, exc.headers.get("X-Mock-Source", "")
        except Exception as exc:  # noqa: BLE001
            return 0, {"error": repr(exc)}, ""
        try:
            doc = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            doc = {"raw": raw[:200].decode("utf-8", "replace")}
        elapsed = (time.monotonic() - started) * 1000
        print(f"      {method:6s} {path:42s} -> {code} [{src}] {elapsed:6.1f} ms")
        return code, doc, src


# ------------------------------------------------------------------ 场景
def run(base: str, *, insecure: bool = False, host_header: str = "") -> int:
    acc_a = "probe-" + uuid.uuid4().hex[:8]
    acc_b = "probe-" + uuid.uuid4().hex[:8]
    a = Client(base, acc_a, insecure=insecure, host_header=host_header)
    b = Client(base, acc_b, insecure=insecure, host_header=host_header)
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("  [PASS] " if ok else "  [FAIL] ") + name + (f"  ({detail})" if detail else ""))
        if not ok:
            failures.append(name)

    print("== 1. 登录（两个客户端各一次）==")
    code, doc, _ = a.call("POST", "/api/Account/Login", {"instanceId": acc_a, "buildIdentifier": "probe"})
    check("A 登录返回 sessionId", code == 200 and bool(doc.get("sessionId")), str(doc)[:80])
    b.call("POST", "/api/Account/Login", {"instanceId": acc_b, "buildIdentifier": "probe"})

    lobby = {
        "lobbyId": LOBBY_ID,
        "maxMembers": 4,
        "players": [{"slot": 0, "memberAccountId": acc_a, "isHost": True, "hasInfo": True},
                    {"slot": 1, "memberAccountId": "", "isHost": False, "hasInfo": False}],
        "memberAccountIds": [acc_a],
        "connectionString": "cv2:00000000-0000-4000-8000-000000000000.r-00000000|123456|kv1:PROBE",
    }

    print("== 2. A 建房 ==")
    code, doc, _ = a.call("PUT", "/api/lobby", lobby)
    check("A PUT /api/lobby 成功", code == 200, str(doc)[:80])

    print("== 3. B 查看大厅列表 ==")
    code, doc, _ = b.call("GET", "/api/lobby/accounts")
    ids = [item.get("lobbyId") for item in (doc.get("lobbies") or [])]
    check("B 能看到 A 的大厅", code == 200 and LOBBY_ID in ids, f"lobbies={ids[:3]}")

    print("== 4. B 加入大厅（两次，验证幂等）==")
    code, doc, _ = b.call("POST", f"/api/lobby/{LOBBY_ID}/member", {"memberAccountId": acc_b})
    body1 = json.dumps(doc, ensure_ascii=False)
    check("B 入厅成功且响应含 B", code == 200 and acc_b in body1, body1[:100])
    b.call("POST", f"/api/lobby/{LOBBY_ID}/member", {"memberAccountId": acc_b})

    print("== 5. A 看到 B，且成员不重复 ==")
    code, doc, _ = a.call("GET", f"/api/lobby/{LOBBY_ID}")
    players = doc.get("players") or []
    members = [p.get("memberAccountId") for p in players if isinstance(p, dict) and p.get("memberAccountId")]
    check("A 看到 B 入厅", code == 200 and acc_b in members, f"members={members}")
    check("重复加入没有产生重复成员", members.count(acc_b) == 1, f"count={members.count(acc_b)}")

    print("== 6. A 清理大厅，B 再查应为终态 ==")
    a.call("DELETE", "/api/lobby/clear")
    code, doc, src = b.call("GET", f"/api/lobby/{LOBBY_ID}")
    # B8 定论（2026-11-21）：未知/已关闭大厅回 HTTP 404 让客户端**立即停止轮询**；
    # 200 关闭态会让它每 15 s 无限重试。两种都算「不可见」，404 是当前部署策略。
    gone = code == 404 or (code == 200 and (not doc or not doc.get("lobbyId")))
    check("清理后大厅不可见", gone, f"code={code} doc={str(doc)[:60]} src={src}")

    print()
    if failures:
        print(f"RESULT: FAIL（{len(failures)} 项）: " + "; ".join(failures))
        return 1
    print("RESULT: PASS（双客户端发现层全流程通过）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="阶段2 发现层双客户端探针")
    ap.add_argument("--self-test", action="store_true", help="进程内起后端并自测（不需要游戏/端口 443）")
    ap.add_argument("--url", default="", help="对已运行的后端探测，如 http://127.0.0.1:443")
    ap.add_argument("--insecure", action="store_true", help="https 时不校验证书（自签测试用）")
    ap.add_argument("--host-header", default="",
                    help="覆盖 Host 头（经 nginx 反代验活时填真实域名，如 api.live.prod.thehelldiversgame.com）")
    args = ap.parse_args(argv)
    if args.self_test:
        srv = _serve_in_process()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        print(f"== 进程内后端 {base} ==")
        try:
            return run(base)
        finally:
            srv.shutdown()
    if args.url:
        print(f"== 远端后端 {args.url} ==")
        return run(args.url, insecure=args.insecure, host_header=args.host_header)
    ap.error("需要 --self-test 或 --url")


if __name__ == "__main__":
    raise SystemExit(main())
