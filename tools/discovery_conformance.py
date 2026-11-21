"""发现层一致性检查（把 2026-11-21 E2 现场结论固化为可重复的检查项）。

两种模式：
  * **in-process（默认）**：用 `MockHandler` 直接跑，不需要网络、不需要 443、不需要证书；
  * **--url**：对**已部署**的后端跑（E2 前置检查 / 部署后回归）。带 SNI 覆盖选项，
    以便经本机中继（100.67.88.219:443）访问 A 机 nginx。

检查项（每项一行 PASS/FAIL，失败会带关键值）：
  C01 登录返回 sessionId
  C02 空账号大厅列表干净
  C03 A 建房后 B 能看见（大厅列表）
  C04 **加入方身份**：`POST /member` 请求体为 `null` 时，写入的是加入方**真实 accountId**
      （从它自己 PUT 的大厅里学来），不是服务端合成的 `7656119…`
  C05 **owner 旧 body 重发 PUT 不抹成员**（2026-11-21 22:50 修复点）
  C06 加入后 GET 能看到自己，`numLobbyMembers` 正确
  C07 重复加入幂等（不再新增成员、人数不变）
  C08 他人用同一 lobbyId PUT 被拒（不覆盖）
  C09 离开（`DELETE /api/lobby/clear`）后成员移除、厅回收
  C10 未知厅响应语义（`--unknown-policy empty|closed`，默认 empty=当前契约）
  C11 大厅列表 `accountLobbies` 索引与实际厅一致

用法：
    python tools/discovery_conformance.py                          # 进程内
    python tools/discovery_conformance.py --json-out state/conf.json
    python tools/discovery_conformance.py --url https://127.0.0.1:8443 --insecure     # 在 A 机上
    python tools/discovery_conformance.py --url https://100.67.88.219:443 \\
        --host api.live.prod.thehelldiversgame.com --insecure --unknown-policy empty   # 经中继
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mock.capture import CaptureStore  # noqa: E402
from mock.discovery import SERVER_DEFAULT_NUMBERS  # noqa: E402
from mock.handler import MockHandler  # noqa: E402

ACCOUNT_PREFIX = "76561190"          # 探针账号前缀（20 位纯数字，与真实同形）
REAL_HOST_ID = "2231864975069446623"
REAL_JOIN_ID = "13887067702231565058"


# --------------------------------------------------------------------- 传输层
class HandlerTransport:
    """进程内：直接调 MockHandler（client_addr 按 IP 区分，便于测 IP 归属）。"""

    def __init__(self, handler: MockHandler):
        self.handler = handler

    def request(self, method: str, path: str, body: bytes = b"",
                account: str = "", client_addr: str = "") -> tuple[int, bytes, str]:
        headers: dict[str, str] = {}
        if account:
            headers["x-hd2-account"] = account
        resp = self.handler.handle(method, path, body, headers, client_addr=client_addr)
        return resp.status, resp.body or b"", resp.source or ""


class HttpTransport:
    """真实 HTTP(S)：支持 `--connect-to` 与 SNI 覆盖（经中继访问 A 机 nginx）。"""

    def __init__(self, url: str, *, host: str = "", connect_to: str = "",
                 insecure: bool = False, timeout: float = 8.0):
        assert url.startswith(("http://", "https://")), url
        self.secure = url.startswith("https://")
        rest = url.split("://", 1)[1]
        def_host, _, def_port = rest.partition("/")
        self.def_path = "/" + def_port if def_port else "/"
        self.hostname = def_host.split(":")[0]
        self.port = int(def_host.split(":")[1]) if ":" in def_host else (443 if self.secure else 80)
        if host:
            self.hostname = host
        self.connect_host, self.connect_port = self.hostname, self.port
        if connect_to:
            ch, _, cp = connect_to.partition(":")
            self.connect_host = ch
            self.connect_port = int(cp or self.port)
        self.insecure = insecure
        self.timeout = timeout

    def request(self, method: str, path: str, body: bytes = b"",
                account: str = "", client_addr: str = "") -> tuple[int, bytes, str]:
        headers = {"Host": self.hostname, "Content-Length": str(len(body)),
                   "Connection": "close"}
        if body:
            headers["Content-Type"] = "application/json"
        if account:
            headers["X-HD2-Account"] = account
        head = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        raw = f"{method} {path} HTTP/1.1\r\n{head}\r\n".encode() + body
        sock = socket.create_connection((self.connect_host, self.connect_port), timeout=self.timeout)
        try:
            if self.secure:
                ctx = ssl.create_default_context()
                if self.insecure:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=self.hostname)
            sock.sendall(raw)
            chunks: list[bytes] = []
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                chunks.append(part)
        finally:
            try:
                sock.close()
            except OSError:
                pass
        data = b"".join(chunks)
        head_bytes, _, body_bytes = data.partition(b"\r\n\r\n")
        status_line = head_bytes.split(b"\r\n", 1)[0].decode("latin-1")
        status = int(status_line.split()[1]) if len(status_line.split()) >= 2 else 0
        source = ""
        for line in head_bytes.split(b"\r\n")[1:]:
            if line.lower().startswith(b"x-mock-source:"):
                source = line.split(b":", 1)[1].decode("latin-1").strip()
        # chunked？
        if b"Transfer-Encoding: chunked" in head_bytes:
            body_bytes = _dechunk(body_bytes)
        return status, body_bytes, source


def _dechunk(data: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while pos < len(data):
        nl = data.find(b"\r\n", pos)
        if nl < 0:
            break
        try:
            size = int(data[pos:nl].split(b";")[0].strip(), 16)
        except ValueError:
            break
        if size == 0:
            break
        start = nl + 2
        out += data[start:start + size]
        pos = start + size + 2
    return bytes(out)


# --------------------------------------------------------------------- 场景
def _login_body(instance: str) -> bytes:
    return json.dumps({"instanceId": instance}).encode()


def _lobby_body(lobby_id: str, host_real_id: str, name: str, *, privacy: int = 0) -> bytes:
    return json.dumps({
        "startTime": "0",
        "lobbyId": lobby_id,
        "players": [
            {"sourcePlatform": 1, "memberAccountId": host_real_id, "isHost": True,
             "playerName": name, "playerRank": 199, "experience": 2759938,
             "hasInfo": True, "commendRank": 0},
            {"sourcePlatform": 1, "memberAccountId": "0", "isHost": False, "playerName": "",
             "playerRank": 0, "experience": 0, "hasInfo": False, "commendRank": 0},
            {"sourcePlatform": 1, "memberAccountId": "0", "isHost": False, "playerName": "",
             "playerRank": 0, "experience": 0, "hasInfo": False, "commendRank": 0},
            {"sourcePlatform": 1, "memberAccountId": "0", "isHost": False, "playerName": "",
             "playerRank": 0, "experience": 0, "hasInfo": False, "commendRank": 0},
        ],
        "connectionString": "cv2:00000000-0000-4000-8000-000000000000.r-00000000|123456|kv1:AAAA",
        "operationProgress": 0, "gameModeProgress": 0, "memberAccountIds": [],
        "inLoadout": False, "buildGameHash": "0123456789abcdef", "warId": 801,
        "state": 1, "difficulty": 0, "hostability": 0, "privacyMode": privacy,
        "onboardingState": 0, "numLobbyMembers": 1, "sosBeacons": 0, "planetId": 0,
        "territoryId": 189, "race": 0, "crossplayMode": 0, "preferenceGroup": 0,
        "regionIndex": -1,
    }).encode()


class _Ctx:
    def __init__(self, transport, tag: str):
        self.t = transport
        self.tag = tag
        # 20 位纯数字（与真实 accountId 同形；客户端按 64 位整数解析）
        self.a = f"{ACCOUNT_PREFIX}{tag}000001"
        self.b = f"{ACCOUNT_PREFIX}{tag}000002"
        self.c = f"{ACCOUNT_PREFIX}{tag}000003"
        self.lobby_a = f"{uuid.uuid4()}.r-{uuid.uuid4().hex[:8]}"
        self.lobby_b = f"{uuid.uuid4()}.r-{uuid.uuid4().hex[:8]}"

    def req(self, method, path, body=b"", account=""):
        return self.t.request(method, path, body, account=account)


def _json_body(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _members(doc: dict) -> list[str]:
    out = []
    for p in doc.get("players") or []:
        mid = str(p.get("memberAccountId") or "")
        if mid and mid not in ("0",) and mid not in out:
            out.append(mid)
    return out


# --------------------------------------------------------------------- 检查项
def run_checks(transport, *, unknown_policy: str = "empty") -> list[dict]:
    results: list[dict] = []
    tag = f"{int(uuid.uuid4().hex[:8], 16) % 1_000_000:06d}"
    ctx = _Ctx(transport, tag)

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        # C01 登录
        st, body, src = ctx.req("POST", "/api/Account/Login", _login_body("conf-a"), account=ctx.a)
        doc = _json_body(body) or {}
        sid = str(doc.get("sessionId") or "")
        ok = st == 200 and src.endswith("account_login") and len(sid) >= 8
        add("C01 login sessionId", ok, f"status={st} source={src} sessionId={sid[:8]}")

        # C02 空列表（新建账号）
        st, body, src = ctx.req("GET", "/api/lobby/accounts", account=ctx.c)
        doc = _json_body(body) or {}
        mine = [x for x in (doc.get("accountLobbies") or []) if x.get("account") == ctx.c]
        # 真实契约：不在厅里的 id 条目**完全没有 lobbyId 键**（不是空字符串）
        ok = st == 200 and all("lobbyId" not in x for x in mine)
        add("C02 新账号无厅", ok, f"status={st} accountLobbies={doc.get('accountLobbies')}")

        # C03 A 建房 → B 可见
        st_a, body_a, src_a = ctx.req("PUT", "/api/lobby",
                                      _lobby_body(ctx.lobby_a, REAL_HOST_ID, "CONF-HOST"),
                                      account=ctx.a)
        st_b, body_b, _ = ctx.req("GET", "/api/lobby/accounts", account=ctx.b)
        seen = [l.get("lobbyId") for l in (_json_body(body_b) or {}).get("lobbies") or []]
        ok = st_a == 200 and ctx.lobby_a in seen
        add("C03 建房后对端可见", ok, f"put={st_a} 可见={ctx.lobby_a in seen} 厅数={len(seen)}")

        # B 先建自己的厅 → 服务端学到 B 的真实 accountId
        st_bb, _, _ = ctx.req("PUT", "/api/lobby",
                              _lobby_body(ctx.lobby_b, REAL_JOIN_ID, "CONF-JOINER"),
                              account=ctx.b)

        # C04 加入方身份：请求体 null，写入真实 ID
        st_j, body_j, src_j = ctx.req("POST", f"/api/lobby/{ctx.lobby_a}/member", b"null", account=ctx.b)
        joined = _json_body(body_j) or {}
        ids = _members(joined)
        ok = (st_j == 200 and REAL_JOIN_ID in ids
              and not any(i.startswith("7656119") for i in ids if i != REAL_HOST_ID))
        add("C04 加入方写入真实 accountId", ok, f"status={st_j} 成员={ids}")

        # C05 owner 用旧 body 重发 PUT 不抹成员
        ctx.req("PUT", "/api/lobby", _lobby_body(ctx.lobby_a, REAL_HOST_ID, "CONF-HOST"), account=ctx.a)
        st_g, body_g, _ = ctx.req("GET", f"/api/lobby/{ctx.lobby_a}", account=ctx.a)
        doc_g = _json_body(body_g) or {}
        ids_g = _members(doc_g)
        ok = st_g == 200 and REAL_JOIN_ID in ids_g
        add("C05 owner 重发不抹成员", ok, f"成员={ids_g}")

        # C06 人数正确
        num = doc_g.get("numLobbyMembers")
        ok = num == len(ids_g) == 2
        add("C06 numLobbyMembers 正确", ok, f"num={num} 实际={len(ids_g)}")

        # C07 重复加入幂等
        st_j2, body_j2, _ = ctx.req("POST", f"/api/lobby/{ctx.lobby_a}/member", b"null", account=ctx.b)
        ids2 = _members(_json_body(body_j2) or {})
        ok = st_j2 == 200 and ids2.count(REAL_JOIN_ID) == 1 and len(ids2) == 2
        add("C07 重复加入幂等", ok, f"status={st_j2} 成员={ids2}")

        # C08 他人用同一 lobbyId PUT 被拒（不覆盖）
        hijack = _lobby_body(ctx.lobby_a, REAL_JOIN_ID, "HIJACK", privacy=0)
        st_h, body_h, src_h = ctx.req("PUT", "/api/lobby", hijack, account=ctx.c)
        doc_h = _json_body(body_h) or {}
        ok = st_h == 200 and (doc_h.get("lobbyId") != ctx.lobby_a) and ("conflict" in src_h or doc_h == {})
        add("C08 抢注他人厅被拒", ok, f"status={st_h} source={src_h} body={str(doc_h)[:60]}")

        # C09 clear 后成员移除、厅回收
        ctx.req("DELETE", "/api/lobby/clear", b"null", account=ctx.a)
        st_after, body_after, _ = ctx.req("GET", f"/api/lobby/{ctx.lobby_a}", account=ctx.b)
        doc_after = _json_body(body_after) or {}
        ok = (st_after == 200 and (doc_after == {} or doc_after.get("numLobbyMembers") in (0, None))
              or st_after == 404)
        add("C09 clear 后厅回收", ok, f"status={st_after} body={str(doc_after)[:80]}")

        # C10 未知厅语义
        unknown_id = f"{uuid.uuid4()}.r-{uuid.uuid4().hex[:8]}"
        st_u, body_u, src_u = ctx.req("GET", f"/api/lobby/{unknown_id}", account=ctx.c)
        doc_u = _json_body(body_u) or {}
        if unknown_policy == "empty":
            ok = st_u == 200 and doc_u == {}
        elif unknown_policy == "error404":
            ok = st_u == 404
        else:
            ok = st_u == 200 and doc_u and doc_u.get("numLobbyMembers") in (0,) and not _members(doc_u)
        add(f"C10 未知厅语义({unknown_policy})", ok,
            f"status={st_u} source={src_u} body={str(doc_u)[:80]}")

        # C11 accountLobbies 索引一致
        st_l, body_l, _ = ctx.req("GET", "/api/lobby/accounts", account=ctx.b)
        doc_l = _json_body(body_l) or {}
        lobby_ids = {l.get("lobbyId") for l in doc_l.get("lobbies") or []}
        index_ids = {a.get("lobbyId") for a in doc_l.get("accountLobbies") or [] if a.get("lobbyId")}
        ok = index_ids.issubset(lobby_ids)
        add("C11 accountLobbies 索引一致", ok,
            f"index={sorted(index_ids)} lobbies={sorted(lobby_ids)}")

        # C12 `accountLobbies` 按请求 ids **同序等长回显**；不在厅里的条目没有 lobbyId 键
        unknown_id = "19999999999999999999"
        st_i, body_i, _ = ctx.req("POST", "/api/lobby/accounts",
                                  json.dumps({"ids": [REAL_JOIN_ID, unknown_id]}).encode(),
                                  account=ctx.b)
        doc_i = _json_body(body_i) or {}
        entries = doc_i.get("accountLobbies") or []
        echoed = [x.get("account") for x in entries]
        first = entries[0] if entries else {}
        second = entries[1] if len(entries) > 1 else {}
        ok = (st_i == 200 and echoed == [REAL_JOIN_ID, unknown_id]
              and first.get("lobbyId") == ctx.lobby_b and "lobbyId" not in second)
        add("C12 ids 同序回显", ok,
            f"status={st_i} 回显={echoed} 首条厅={first.get('lobbyId')}")

        # C13 厅 body 满足真实解析器要求：20 个必需数字字段齐全、players 定长槽、人数真实
        lobbies_i = doc_i.get("lobbies") or []
        lob = lobbies_i[0] if lobbies_i else {}
        missing = [n for n in SERVER_DEFAULT_NUMBERS if n not in lob]
        ok = bool(lob) and not missing and "memberAccountIds" not in lob \
            and len(lob.get("players") or []) == 4 and lob.get("numLobbyMembers") == 1
        add("C13 厅 body 真实形状", ok,
            f"缺字段={missing} 槽数={len(lob.get('players') or [])} 人数={lob.get('numLobbyMembers')}")

        # C14 加入门槛：Login 自报的公钥能按**真实 accountId** 取回（缺它加入方永不被接纳）
        public_key = "conf-public-key-0123456789abcdefghijkl"
        ctx.req("POST", "/api/Account/Login",
                json.dumps({"instanceId": "conf-keys", "publicKey": public_key}).encode(),
                account=ctx.c)
        real_c = "22318649750694466996"
        ctx.req("PUT", "/api/lobby",
                _lobby_body(f"{uuid.uuid4()}.r-{uuid.uuid4().hex[:8]}", real_c, "CONF-KEYS"),
                account=ctx.c)
        st_k, body_k, src_k = ctx.req("GET", f"/api/Account/Keys?id={real_c}", account=ctx.a)
        keys = (_json_body(body_k) or {}).get("accountKeys") or []
        ok = (st_k == 200 and len(keys) == 1
              and keys[0].get("platformAccountId") == real_c
              and keys[0].get("key") == public_key)
        add("C14 公钥按 accountId 可取", ok,
            f"status={st_k} source={src_k} keys={keys}")
    finally:
        for acc in (ctx.a, ctx.b, ctx.c):
            try:
                ctx.req("DELETE", "/api/lobby/clear", b"null", account=acc)
            except Exception:  # noqa: BLE001
                pass
    return results


# --------------------------------------------------------------------- CLI
def _build_in_process(unknown_lobby: str = "empty") -> HandlerTransport:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="conf-capture-"))
    (tmp / "synthetic_Account_Login.json").write_text(
        json.dumps({"sessionId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}), encoding="utf-8")
    (tmp / "index.txt").write_text(
        "20260101T000000Z\t/api/Account/Login\t52\t0\tjson\n", encoding="utf-8")
    store = CaptureStore.load(tmp)
    handler = MockHandler(
        store,
        profile={"lobby": {"mode": "discovery", "echo_put": True, "max_members": 4,
                           "unknown_lobby": unknown_lobby}},
        now_fn=time.time,
    )
    return HandlerTransport(handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="发现层一致性检查")
    ap.add_argument("--url", default="", help="对已部署后端跑，例如 https://127.0.0.1:8443")
    ap.add_argument("--host", default="", help="SNI/Host 覆盖（经中继时用 api.live.prod…）")
    ap.add_argument("--connect-to", default="", help="实际连接地址 host:port")
    ap.add_argument("--insecure", action="store_true", help="跳过 TLS 校验")
    ap.add_argument("--unknown-policy", choices=("empty", "closed", "error404"), default="empty",
                    help="未知厅响应契约：empty={}；closed=200 关闭态；error404=HTTP 404（实机验证最佳）")
    ap.add_argument("--json-out", default="", help="把结果写到 JSON 文件")
    args = ap.parse_args(argv)

    if args.url:
        transport = HttpTransport(args.url, host=args.host, connect_to=args.connect_to,
                                  insecure=args.insecure)
        mode = f"live {args.url}"
    else:
        transport = _build_in_process()
        mode = "in-process"

    print(f"[conformance] mode={mode} unknown_policy={args.unknown_policy} "
          f"time={time.strftime('%Y-%m-%d %H:%M:%S')}")
    results = run_checks(transport, unknown_policy=args.unknown_policy)
    passed = sum(1 for r in results if r["ok"])
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['check']}  {r['detail']}")
    print(f"[conformance] {passed}/{len(results)} passed")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"mode": mode, "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "passed": passed, "total": len(results),
                                   "results": results}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"[conformance] 结果已写入 {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
