"""发现层生命周期：终态（tombstone）、跨重启持久化、干净/非干净重启、handler 终态策略。

背景（2026-11-21 E2）：后端一重启，客户端仍轮询旧厅，我们回 `200 {}`，客户端无法
判断"厅已不存在"，只能无限等 → 必须重开游戏。修复方向见
`docs/STAGE2-DESIGN-STALE-LOBBY-2026-11-21.md`；本文件锁住行为。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mock.capture import CaptureStore
from mock.discovery import DiscoveryLobby
from mock.handler import MockHandler

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
LOBBY_ID = "11111111-2222-4333-8444-555555555555.r-00000001"
HOST_REAL_ID = "2231864975069446623"


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _lobby(lobby_id: str = LOBBY_ID, host_id: str = HOST_REAL_ID) -> dict:
    return {
        "lobbyId": lobby_id,
        "players": [
            {"memberAccountId": host_id, "isHost": True, "hasInfo": True, "playerName": "HOST"},
            {"memberAccountId": "0", "isHost": False, "hasInfo": False},
        ],
        "memberAccountIds": [],
        "connectionString": "cv2:00000000-0000-4000-8000-000000000000.r-00000000|1|kv1:AA",
        "numLobbyMembers": 1,
    }


# ---------------------------------------------------------------- 终态
def test_unknown_lobby_resolves_unknown():
    svc = DiscoveryLobby()
    res = svc.resolve("no-such-lobby.r-00000000")
    assert res["kind"] == "unknown"


def test_clear_writes_tombstone_and_resolves_closed():
    svc = DiscoveryLobby()
    acc = svc.account_for(client_addr="10.0.0.1")
    svc.put(acc, _lobby())
    svc.clear(acc)
    assert svc.get(LOBBY_ID) is None
    res = svc.resolve(LOBBY_ID)
    assert res["kind"] == "closed" and res["reason"] == "left"
    assert svc.stats()["tombstones"] == 1


def test_expiry_writes_tombstone():
    clock = FakeClock()
    svc = DiscoveryLobby(ttl_seconds=60, clock=clock)
    acc = svc.account_for(client_addr="10.0.0.1")
    svc.put(acc, _lobby())
    clock.advance(61)
    res = svc.resolve(LOBBY_ID)
    assert res["kind"] == "closed" and res["reason"] == "expired"


def test_tombstone_expires():
    clock = FakeClock()
    svc = DiscoveryLobby(ttl_seconds=60, tombstone_ttl_seconds=30, clock=clock)
    acc = svc.account_for(client_addr="10.0.0.1")
    svc.put(acc, _lobby())
    svc.clear(acc)
    clock.advance(31)
    assert svc.resolve(LOBBY_ID)["kind"] == "unknown"


# ---------------------------------------------------------------- 持久化
def test_clean_restart_keeps_lobby_and_members(tmp_path: Path):
    state = tmp_path / "state" / "discovery-state.json"
    clock = FakeClock()
    svc1 = DiscoveryLobby(clock=clock, persist_path=state)
    acc_host = svc1.account_for(client_addr="10.0.0.1")
    acc_join = svc1.account_for(client_addr="10.0.0.2")
    svc1.put(acc_host, _lobby())
    svc1.join(acc_join, LOBBY_ID, None)
    svc1.close()
    assert state.exists() and (tmp_path / "state" / "discovery-state.json.clean").exists()

    svc2 = DiscoveryLobby(clock=clock, persist_path=state)
    res = svc2.resolve(LOBBY_ID)
    assert res["kind"] == "live"
    assert acc_join in json.dumps(res["lobby"])
    assert svc2.stats()["generation"] == svc1.stats()["generation"]


def test_unclean_restart_closes_lobbies_with_server_reset(tmp_path: Path):
    state = tmp_path / "state" / "discovery-state.json"
    clock = FakeClock()
    svc1 = DiscoveryLobby(clock=clock, persist_path=state)
    acc = svc1.account_for(client_addr="10.0.0.1")
    svc1.put(acc, _lobby())
    # 模拟崩溃：不调用 close()，因此没有 .clean 标记
    svc2 = DiscoveryLobby(clock=clock, persist_path=state)
    res = svc2.resolve(LOBBY_ID)
    assert res["kind"] == "closed" and res["reason"] == "server_reset"
    assert svc2.stats()["generation"] == 1


# ---------------------------------------------------------------- handler 策略
def _handler(capture: Path, **lobby_cfg) -> MockHandler:
    store = CaptureStore.load(capture)
    cfg = {"mode": "discovery", "echo_put": True}
    cfg.update(lobby_cfg)
    return MockHandler(store, profile={"lobby": cfg}, now_fn=lambda: NOW)


def test_handler_closed_policy_returns_terminal_body(synthetic_capture: Path):
    h = _handler(synthetic_capture, unknown_lobby="closed")
    r = h.handle("GET", "/api/lobby/no-such-lobby", b"", client_addr="10.0.0.7")
    body = json.loads(r.body)
    assert r.status == 200
    assert r.source.startswith("discovery:lobby_get:closed")
    assert body["numLobbyMembers"] == 0 and body["players"] == []


def test_handler_closed_policy_after_leave(synthetic_capture: Path):
    h = _handler(synthetic_capture, unknown_lobby="closed")
    acc = h.discovery.account_for(client_addr="10.0.0.1")
    h.handle("PUT", "/api/lobby", json.dumps(_lobby()).encode(), client_addr="10.0.0.1")
    h.handle("DELETE", "/api/lobby/clear", b"", client_addr="10.0.0.1")
    r = h.handle("GET", f"/api/lobby/{LOBBY_ID}", b"", client_addr="10.0.0.1")
    assert r.source.startswith("discovery:lobby_get:closed:left")
    assert acc  # 账号仍存在
