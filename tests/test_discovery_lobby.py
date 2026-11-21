"""阶段2 发现层（多客户端大厅）：离线双客户端自测，不启动游戏。

覆盖：账号归属（IP/显式头）、大厅创建与跨客户端可见、加入（空槽填充 + 幂等 + 容量）、
按 id 查询（含 uuid 前缀）、GET 续期、TTL 过期、并发同 id 冲突、清理与成员移除、
以及 MockHandler 在 `lobby.mode=discovery` 下的路由接线。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mock.capture import CaptureStore
from mock.discovery import DiscoveryLobby
from mock.handler import MockHandler

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
LOBBY_ID = "11111111-2222-4333-8444-555555555555.r-00000001"


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _lobby(**patch) -> dict:
    body = {
        "lobbyId": LOBBY_ID,
        "maxMembers": 4,
        "players": [
            {"slot": 0, "memberAccountId": "", "isHost": False, "hasInfo": False},
            {"slot": 1, "memberAccountId": "", "isHost": False, "hasInfo": False},
        ],
        "memberAccountIds": [],
        "connectionString": "cv2:00000000-0000-4000-8000-000000000000.r-00000000|123456|kv1:AAAA",
    }
    body.update(patch)
    return body


def _service(ttl: float = 60, max_members: int = 4) -> tuple[DiscoveryLobby, FakeClock]:
    clock = FakeClock()
    return DiscoveryLobby(ttl_seconds=ttl, max_members=max_members, clock=clock), clock


# ---------------------------------------------------------------- 账号
def test_login_returns_session_uuid_and_attributes_by_ip():
    svc, _ = _service()
    out = svc.login(client_addr="10.0.0.1", body={"instanceId": "inst-a"})
    uuid.UUID(out["sessionId"])
    acc_a = svc.account_for(client_addr="10.0.0.1")
    assert acc_a
    # 同一 IP 稳定归属；不同 IP 得到不同账号
    assert svc.account_for(client_addr="10.0.0.1") == acc_a
    acc_b = svc.account_for(client_addr="10.0.0.2")
    assert acc_b and acc_b != acc_a


def test_explicit_account_header_wins():
    svc, _ = _service()
    acc = svc.account_for(client_addr="10.0.0.9", account_hint="probe-1")
    assert acc == "probe-1"


# ---------------------------------------------------------------- 创建/可见
def test_two_clients_see_each_other_lobby():
    svc, _ = _service()
    acc_a = svc.account_for(client_addr="10.0.0.1")
    saved = svc.put(acc_a, _lobby(players=[{"slot": 0, "memberAccountId": acc_a, "isHost": True, "hasInfo": True}]))
    assert saved is not None and saved["lobbyId"] == LOBBY_ID
    acc_b = svc.account_for(client_addr="10.0.0.2")
    view = svc.accounts(acc_b)
    assert [lobby["lobbyId"] for lobby in view["lobbies"]] == [LOBBY_ID]
    assert {"account": acc_a, "lobbyId": LOBBY_ID} in view["accountLobbies"]


def test_join_fills_empty_slot_and_is_idempotent():
    svc, _ = _service()
    acc_a = svc.account_for(client_addr="10.0.0.1")
    acc_b = svc.account_for(client_addr="10.0.0.2")
    svc.put(acc_a, _lobby(players=[{"slot": 0, "memberAccountId": acc_a, "isHost": True, "hasInfo": True}]))

    joined = svc.join(acc_b, LOBBY_ID, {"memberAccountId": acc_b})
    assert joined is not None
    slots = [p["memberAccountId"] for p in joined["players"] if p.get("memberAccountId")]
    assert acc_b in slots and acc_a in slots
    assert len(joined["players"]) == 2          # 填了空槽，没有追加第三条

    again = svc.join(acc_b, LOBBY_ID, {"memberAccountId": acc_b})
    assert [p["memberAccountId"] for p in again["players"] if p.get("memberAccountId")].count(acc_b) == 1
    assert svc.stats()["join"] == 1             # 第二次没有计数为成功加入


def test_join_rejected_when_full():
    svc, _ = _service(max_members=2)
    acc_a = svc.account_for(client_addr="10.0.0.1")
    acc_b = svc.account_for(client_addr="10.0.0.2")
    acc_c = svc.account_for(client_addr="10.0.0.3")
    svc.put(acc_a, _lobby(players=[{"slot": 0, "memberAccountId": acc_a, "isHost": True, "hasInfo": True}]))
    assert svc.join(acc_b, LOBBY_ID, None) is not None
    assert svc.join(acc_c, LOBBY_ID, None) is None
    assert svc.stats()["join_rejected"] == 1


# ---------------------------------------------------------------- 查询/续期
def test_get_by_uuid_prefix():
    svc, _ = _service()
    acc = svc.account_for(client_addr="10.0.0.1")
    svc.put(acc, _lobby())
    short = LOBBY_ID.split(".")[0]
    assert svc.get(short) is not None
    assert svc.get(LOBBY_ID) is not None
    assert svc.get("does-not-exist") is None


def test_get_refreshes_ttl_but_silence_expires():
    svc, clock = _service(ttl=60)
    acc = svc.account_for(client_addr="10.0.0.1")
    svc.put(acc, _lobby())
    clock.advance(40)
    assert svc.get(LOBBY_ID) is not None        # GET 续期
    clock.advance(40)                           # 距上次活动 40s < ttl
    assert svc.get(LOBBY_ID) is not None
    clock.advance(61)                           # 静默超过 TTL
    assert svc.get(LOBBY_ID) is None
    assert svc.stats()["expired"] >= 1


def test_expired_lobby_disappears_from_accounts():
    svc, clock = _service(ttl=30)
    acc_a = svc.account_for(client_addr="10.0.0.1")
    svc.put(acc_a, _lobby())
    acc_b = svc.account_for(client_addr="10.0.0.2")
    clock.advance(31)
    view = svc.accounts(acc_b)
    assert view["lobbies"] == []
    assert all(item["lobbyId"] == "" for item in view["accountLobbies"])


# ---------------------------------------------------------------- 冲突/清理
def test_same_lobby_id_from_other_account_is_rejected():
    svc, _ = _service()
    acc_a = svc.account_for(client_addr="10.0.0.1")
    acc_b = svc.account_for(client_addr="10.0.0.2")
    assert svc.put(acc_a, _lobby()) is not None
    assert svc.put(acc_b, _lobby()) is None
    assert svc.stats()["conflict"] == 1
    # 原大厅归属不变
    assert svc.get(LOBBY_ID) is not None


def test_clear_removes_owned_lobby_and_membership():
    svc, _ = _service()
    acc_a = svc.account_for(client_addr="10.0.0.1")
    acc_b = svc.account_for(client_addr="10.0.0.2")
    svc.put(acc_a, _lobby(players=[{"slot": 0, "memberAccountId": acc_a, "isHost": True, "hasInfo": True}]))
    svc.join(acc_b, LOBBY_ID, None)
    svc.clear(acc_a)
    assert svc.get(LOBBY_ID) is None
    svc.clear(acc_a)                             # 幂等
    assert svc.stats()["leave"] == 2


# ---------------------------------------------------------------- handler 接线
def _handler(capture: Path) -> MockHandler:
    store = CaptureStore.load(capture)
    return MockHandler(store, profile={"lobby": {"mode": "discovery", "echo_put": True}},
                       now_fn=lambda: NOW)


def test_handler_routes_discovery_by_client_address(synthetic_capture: Path):
    h = _handler(synthetic_capture)

    login_a = h.handle("POST", "/api/Account/Login", b'{"instanceId":"A"}', client_addr="10.0.0.1")
    assert login_a.source == "discovery:account_login"
    uuid.UUID(login_a.json()["sessionId"])

    body = json.dumps(_lobby()).encode()
    put_a = h.handle("PUT", "/api/lobby", body, client_addr="10.0.0.1")
    assert put_a.status == 200 and put_a.source == "discovery:lobby_put"
    assert put_a.json()["lobbyId"] == LOBBY_ID      # echo_put=True

    acc_a = h.discovery.account_for(client_addr="10.0.0.1")
    acc_b = h.discovery.account_for(client_addr="10.0.0.2")
    assert acc_a != acc_b

    accounts_b = h.handle("GET", "/api/lobby/accounts", b"", client_addr="10.0.0.2")
    assert accounts_b.source == "discovery:lobby_accounts"
    assert [lobby["lobbyId"] for lobby in accounts_b.json()["lobbies"]] == [LOBBY_ID]

    joined = h.handle("POST", f"/api/lobby/{LOBBY_ID}/member", json.dumps({"memberAccountId": acc_b}).encode(),
                      client_addr="10.0.0.2")
    assert joined.source == "discovery:lobby_member"
    assert acc_b in json.dumps(joined.json())

    got = h.handle("GET", f"/api/lobby/{LOBBY_ID}", b"", client_addr="10.0.0.2")
    assert got.source == "discovery:lobby_get"
    assert json.loads(got.body)["lobbyId"] == LOBBY_ID

    cleared = h.handle("DELETE", "/api/lobby/clear", b"", client_addr="10.0.0.1")
    assert cleared.source == "discovery:lobby_clear"
    assert h.handle("GET", f"/api/lobby/{LOBBY_ID}", b"", client_addr="10.0.0.2").source.endswith(":unknown")


def test_handler_unknown_lobby_returns_empty_object(synthetic_capture: Path):
    h = _handler(synthetic_capture)
    r = h.handle("GET", "/api/lobby/no-such-lobby", b"", client_addr="10.0.0.7")
    assert r.status == 200 and r.json() == {}
