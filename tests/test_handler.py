"""MockHandler：回放 + 有状态路由 + 时间平移 + fallback。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mock.capture import CaptureStore
from mock.handler import MockHandler
from replay_client import method_for

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)   # 比抓包时刻晚 1 天


def build(capture: Path, *, now: datetime | None = NOW, profile: dict | None = None) -> MockHandler:
    store = CaptureStore.load(capture)
    return MockHandler(store, profile=profile, now_fn=(lambda: now) if now else None)


def _delta(store: CaptureStore, base_path: str) -> int:
    """该响应自己的抓包时刻到 NOW 的秒数（时间平移以每条响应自己的时刻为准）。"""
    entry = store.find(base_path=base_path)
    assert entry is not None
    return int((NOW - entry.captured_at).total_seconds())


def test_login_is_stateful_and_returns_uuid(synthetic_capture: Path):
    h = build(synthetic_capture)
    r = h.handle("POST", "/api/Account/Login", b"")
    assert r.status == 200
    assert r.source == "state:account_login"
    uuid.UUID(r.json()["sessionId"])


def test_replay_hits_index(synthetic_capture: Path):
    h = build(synthetic_capture)
    r = h.handle("GET", "/api/WarSeason/current/WarId")
    assert r.status == 200 and r.source in ("exact", "base")
    assert r.json()["id"] == 801


def test_status_time_shifted_one_day(synthetic_capture: Path):
    store = CaptureStore.load(synthetic_capture)
    d = _delta(store, "/api/WarSeason/801/Status")
    h = build(synthetic_capture)
    doc = h.handle("GET", "/api/WarSeason/801/Status").json()
    assert doc["time"] == 1_000_000 + d
    assert doc["globalEvents"][0]["expireTime"] == 1_000_500 + d
    assert doc["spaceStations"][0]["currentElectionEndWarTime"] == 1_000_400 + d
    assert doc["planetStatus"][0]["health"] == 1_000_000


def test_second_counter_and_unix_rules(synthetic_capture: Path):
    store = CaptureStore.load(synthetic_capture)
    d = _delta(store, "/api/WarSeason/801/timeSinceStart")
    h = build(synthetic_capture)
    assert h.handle("GET", "/api/WarSeason/801/timeSinceStart").json()["secondsSinceStart"] \
        == 2_900_000 + d
    warinfo = h.handle("GET", "/api/WarSeason/801/warinfo").json()
    assert warinfo["startDate"] == 1_700_000_000          # Unix 绝对时间不平移
    rot = h.handle("GET", "/api/StoreFront/rotation").json()
    assert rot["expireTime"] > int(NOW.timestamp())      # 过期则刷新
    assign = h.handle("GET", "/api/v2/Assignment/War/801").json()
    assign_d = _delta(store, "/api/v2/Assignment/War/801")
    assert assign[0]["startTime"] == 999_000 + assign_d
    assert assign[0]["expiresIn"] == 60_000              # 倒计时保留


def test_timeshift_can_be_disabled(synthetic_capture: Path):
    h = build(synthetic_capture, profile={"timeshift": {"enabled": False}})
    assert h.handle("GET", "/api/WarSeason/801/Status").json()["time"] == 1_000_000


def test_binary_body_passthrough(synthetic_capture: Path):
    h = build(synthetic_capture)
    r = h.handle("GET", "/api/Binary/Blob")
    assert r.status == 200 and r.body == b"\x00\x01\x02"
    assert r.content_type == "application/octet-stream"


def test_unknown_endpoint_falls_back_200(synthetic_capture: Path):
    h = build(synthetic_capture)
    r = h.handle("GET", "/api/Totally/Unknown")
    assert r.status == 200 and r.source == "fallback"
    assert json.loads(r.body) == {}
    assert h.unmatched["/api/Totally/Unknown"] == 1
    assert h.stats["fallback"] == 1


def test_non_api_root(synthetic_capture: Path):
    h = build(synthetic_capture)
    r = h.handle("GET", "/")
    assert r.status == 200 and r.source == "fallback:root"


def test_health_reports_state(synthetic_capture: Path):
    store = CaptureStore.load(synthetic_capture)
    d = _delta(store, "/api/WarSeason/801/Status")
    h = build(synthetic_capture)
    h.handle("POST", "/api/Account/Login", b"")
    h.handle("POST", "/api/Operation/Create",
             json.dumps({"operations": [{"id": 2000, "missions": []}]}).encode())
    doc = h.health()
    assert doc["ok"] is True
    assert doc["index"]["base"] >= 11
    assert doc["stats"]["state"] == 2
    assert doc["state"]["nextOperationId"] == 2001
    assert doc["anchors"]["war_clock"]["value"] == 1_000_000
    assert doc["war_clock_now"] == 1_000_000 + d


def test_lobby_empty_mode_returns_no_recorded_lobbies(synthetic_capture: Path):
    """离线单机 profile（lobby.mode=empty）：不能把录制里的旧大厅抛给客户端。"""
    h = build(synthetic_capture, profile={"lobby": {"mode": "empty"}})
    r = h.handle("GET", "/api/lobby/accounts")
    doc = r.json()
    assert r.source.startswith("state:lobby_accounts:empty")
    assert doc == {"accountLobbies": [], "lobbies": []}
    # 即使本地存了厅，empty 模式仍然只给空列表
    h.handle("PUT", "/api/lobby",
             json.dumps({"lobbyId": "x", "players": [{"memberAccountId": "1", "isHost": True}]}).encode())
    assert h.handle("GET", "/api/lobby/accounts").json()["lobbies"] == []


def test_lobby_state_takes_over_after_put(synthetic_capture: Path):
    h = build(synthetic_capture)
    # replay 模式且本地没有大厅时：按设计退回抓包响应
    before = h.handle("GET", "/api/lobby/accounts")
    assert not before.source.startswith("state")
    lobby = {
        "lobbyId": "11111111-2222-4333-8444-555555555555.r-00000000",
        "connectionString": "cv2:x|0|kv1:y",
        "players": [{"memberAccountId": "100", "isHost": True, "hasInfo": True}],
        "numLobbyMembers": 1,
    }
    h.handle("PUT", "/api/lobby", json.dumps(lobby).encode())
    after = h.handle("GET", "/api/lobby/accounts")
    assert after.source.startswith("state:lobby_accounts")
    assert any(l["lobbyId"] == lobby["lobbyId"] for l in after.json()["lobbies"])
    got = h.handle("GET", "/api/lobby/" + lobby["lobbyId"])
    assert got.json()["connectionString"] == lobby["connectionString"]


def test_operation_and_mission_chain_via_handler(synthetic_capture: Path):
    h = build(synthetic_capture)
    created = h.handle("POST", "/api/Operation/Create", json.dumps({"operations": [{
        "id": 1500, "seed": 1, "warId": 801, "planetIndex": 268, "difficulty": 3,
        "missions": [{"type": 1, "state": 1, "index": 0}],
    }]}).encode()).json()
    assert created["operations"][0]["id"] == 1500
    mid = h.handle("POST", "/api/Operation/Mission/Start",
                   json.dumps({"operationId": 1500, "missionIndex": 0}).encode()).json()["missionId"]
    end = h.handle("POST", "/api/Operation/Mission/end",
                   json.dumps({"operationId": 1500, "missionIndex": 0,
                               "missionId": mid}).encode()).json()
    assert end["operationId"] == 1500 and end["missionId"] == mid
    assert h.handle("POST", "/api/Mission/Session", b"{}").status == 202
    assert h.handle("POST", "/api/Account/ReportPosition", b"{}").status == 200


def test_real_capture_every_endpoint_matched(real_capture: Path):
    store = CaptureStore.load(real_capture)
    h = build(real_capture, now=None)
    missed = []
    for e in store.entries:
        method = method_for(e.base_path)
        r = h.handle(method, e.raw_path)
        if r.source.startswith("fallback"):
            missed.append(f"{method} {e.raw_path}")
        assert r.status in (200, 202, 204), f"{method} {e.raw_path} -> {r.status}"
    assert missed == []
    assert h.unknown_time_fields == set(), sorted(h.unknown_time_fields)[:5]
