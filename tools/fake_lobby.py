"""造一个"假大厅"做单机加入实验（B6）。

背景：真实客户端点"加入"时会 `POST /api/lobby/<id>/member`，然后拿响应里的
`connectionString` 去 PlayFab 走 Lobby/Party。我们造一个**可控响应**的假大厅，
逐个改变字段，看客户端在哪一步放弃（`DELETE /api/lobby/clear`）或是否还会去 PlayFab。

注意：假大厅在 PlayFab 侧不存在，所以**不可能真的加入成功**；本实验只回答
"客户端因为哪个字段判失败"以及"它退出的行为链"。

用法：
    python tools/fake_lobby.py create --variant baseline
    python tools/fake_lobby.py list
    python tools/fake_lobby.py delete
变体：baseline / no-conn / garbage-conn / empty-players / num-mismatch
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from discovery_conformance import HttpTransport  # noqa: E402

HOST_REAL_ID = "2231864975069446623"
DEFAULT_ACCOUNT = "76561190000000077001"     # 假房主的合成账号
CONN_OK = "cv2:11111111-2222-4333-8444-555555555555.r-00000000|123456|kv1:AAAA"
REAL_BODY = REPO / "state/evidence/e2prep/real-bodies/lobby-put-req.json"


def real_connection_string(lobby_id: str) -> str:
    """按 2026-11-21 实机捕获的形状合成会话描述符：cv2:<lobbyId>|<6 位>|kv1:<32B key>。

    真实样本：`cv2:<uuid>.r-20260323|619689|kv1:<44 字符 base64>`（44 字符 = 32 字节密钥）。
    """
    key = base64.b64encode(os.urandom(32)).decode()
    return f"cv2:{lobby_id}|{uuid.uuid4().int % 900000 + 100000}|kv1:{key}"


def real_template(lobby_id: str, name: str) -> dict | None:
    """用实机捕获的真实 body 当模板（若存在），只替换身份相关字段。"""
    if not REAL_BODY.exists():
        return None
    try:
        body = json.loads(REAL_BODY.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    body["lobbyId"] = lobby_id
    body["connectionString"] = real_connection_string(lobby_id)
    body["privacyMode"] = 0            # 公开，保证客户端能在列表里看到
    body["numLobbyMembers"] = 1
    players = body.get("players") or []
    if players:
        players[0]["memberAccountId"] = HOST_REAL_ID
        players[0]["playerName"] = name
        players[0]["isHost"] = True
        players[0]["hasInfo"] = True
        for extra in players[1:]:
            extra["memberAccountId"] = "0"
            extra["isHost"] = False
            extra["hasInfo"] = False
            extra["playerName"] = ""
    body["memberAccountIds"] = [None, HOST_REAL_ID]
    return body


def build_lobby(lobby_id: str, variant: str, name: str) -> dict:
    players = [
        {"sourcePlatform": 1, "memberAccountId": HOST_REAL_ID, "isHost": True,
         "playerName": name, "playerRank": 77, "experience": 1234567,
         "hasInfo": True, "commendRank": 0},
        {"sourcePlatform": 1, "memberAccountId": "0", "isHost": False, "playerName": "",
         "playerRank": 0, "experience": 0, "hasInfo": False, "commendRank": 0},
        {"sourcePlatform": 1, "memberAccountId": "0", "isHost": False, "playerName": "",
         "playerRank": 0, "experience": 0, "hasInfo": False, "commendRank": 0},
        {"sourcePlatform": 1, "memberAccountId": "0", "isHost": False, "playerName": "",
         "playerRank": 0, "experience": 0, "hasInfo": False, "commendRank": 0},
    ]
    body = {
        "startTime": "1",
        "lobbyId": lobby_id,
        "players": players,
        "connectionString": CONN_OK,
        "operationProgress": 0, "gameModeProgress": 0, "memberAccountIds": [],
        "inLoadout": False, "buildGameHash": "0123456789abcdef", "warId": 801,
        "state": 1, "difficulty": 0, "hostability": 0, "privacyMode": 0,
        "onboardingState": 0, "numLobbyMembers": 1, "sosBeacons": 0, "planetId": 0,
        "territoryId": 189, "race": 0, "crossplayMode": 0, "preferenceGroup": 0,
        "regionIndex": -1,
    }
    if variant == "no-conn":
        body["connectionString"] = ""
    elif variant == "garbage-conn":
        body["connectionString"] = "garbage-connection-string"
    elif variant == "empty-players":
        body["players"] = []
        body["numLobbyMembers"] = 0
    elif variant == "num-mismatch":
        body["numLobbyMembers"] = 4
    elif variant != "baseline":
        raise SystemExit(f"未知变体: {variant}")
    return body


def transport(args) -> HttpTransport:
    return HttpTransport(args.url, host=args.host, connect_to=args.connect_to,
                         insecure=args.insecure)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="假大厅（B6 单机加入实验）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("create", "list", "delete"):
        p = sub.add_parser(name)
        p.add_argument("--url", default="https://api.live.prod.thehelldiversgame.com")
        p.add_argument("--host", default="api.live.prod.thehelldiversgame.com")
        p.add_argument("--connect-to", default="100.67.88.219:443")
        p.add_argument("--insecure", action="store_true", default=True)
        p.add_argument("--account", default=DEFAULT_ACCOUNT)
        p.add_argument("--lobby-id", default="")
        p.add_argument("--variant", default="baseline")
        p.add_argument("--name", default="FAKE-HOST")
        p.add_argument("--host-id", default=HOST_REAL_ID, help="假房主的 20 位 accountId")
        p.add_argument("--conn", default="", help="覆盖 connectionString（例如复制真房主的串）")
    args = ap.parse_args(argv)
    t = transport(args)

    if args.cmd == "list":
        st, body, _ = t.request("GET", "/api/lobby/accounts", account=args.account)
        doc = json.loads(body or b"{}")
        print(f"status={st} lobbies={len(doc.get('lobbies') or [])}")
        for lobby in doc.get("lobbies") or []:
            ids = [p.get("memberAccountId") for p in lobby.get("players") or []
                   if p.get("memberAccountId") and p.get("memberAccountId") != "0"]
            print(f"  {lobby.get('lobbyId')} privacy={lobby.get('privacyMode')} "
                  f"num={lobby.get('numLobbyMembers')} conn={len(lobby.get('connectionString') or '')}B "
                  f"players={ids}")
        return 0

    if args.cmd == "delete":
        lobby_id = args.lobby_id
        if not lobby_id:
            st, body, _ = t.request("GET", "/api/lobby/accounts", account=args.account)
            doc = json.loads(body or b"{}")
            mine = [a for a in doc.get("accountLobbies") or [] if a.get("account") == args.account]
            lobby_id = mine[0].get("lobbyId") if mine else ""
        st, _body, _ = t.request("DELETE", "/api/lobby/clear", b"null", account=args.account)
        print(f"cleared account={args.account} lobby={lobby_id or '(none)'} status={st}")
        return 0

    lobby_id = args.lobby_id or f"{uuid.uuid4()}.r-{uuid.uuid4().hex[:8]}"
    lobby = real_template(lobby_id, args.name) or build_lobby(lobby_id, args.variant, args.name)
    if args.host_id:
        for pl in lobby.get("players") or []:
            if pl.get("isHost"):
                pl["memberAccountId"] = args.host_id
    if args.conn:
        lobby["connectionString"] = args.conn
    if args.variant == "no-conn":
        lobby["connectionString"] = ""
    elif args.variant == "garbage-conn":
        lobby["connectionString"] = "garbage-connection-string"
    elif args.variant == "num-mismatch":
        lobby["numLobbyMembers"] = 4
    elif args.variant == "empty-players":
        lobby["players"] = []
        lobby["numLobbyMembers"] = 0
    body = json.dumps(lobby).encode()
    st, resp, src = t.request("PUT", "/api/lobby", body, account=args.account)
    print(f"variant={args.variant} lobbyId={lobby_id} status={st} source={src}")
    if st == 200 and resp:
        print(resp.decode("utf-8", "replace")[:300])
    return 0 if st == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
