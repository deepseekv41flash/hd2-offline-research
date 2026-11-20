"""有状态端点：登录 / 大厅 / 作战 / 任务链。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from mock.state import MockState


def test_login_issues_uuid():
    st = MockState()
    a = st.login()["sessionId"]
    b = st.login()["sessionId"]
    assert a != b
    uuid.UUID(a)
    assert len(st.session_ids) == 2


def test_operation_create_echoes_client_id():
    st = MockState(next_operation_id=1283)
    body = {"operations": [{
        "operationSettingsId": 1, "jointOperationId": -1, "type": 0, "id": 1284,
        "seed": 42, "race": 2, "warId": 801, "placementNodeIndex": 3,
        "campaignId": 51715, "planetIndex": 268, "difficulty": 1,
        "missions": [{"type": 1, "state": 1, "index": 0}],
    }]}
    resp = st.operation_create(body)
    assert resp == {"operations": [{"id": 1284, "rewards": [], "backupRewards": []}]}
    assert st.next_operation_id == 1285
    listed = st.operation_list()
    assert listed["nextOperationId"] == 1285
    assert [o["id"] for o in listed["operations"]] == [1284]
    op = listed["operations"][0]
    assert op["seed"] == 42 and op["planetIndex"] == 268 and op["isActive"] is True
    assert op["missions"][0]["missionId"] == "00000000-0000-0000-0000-000000000000"


def test_mission_start_and_end_chain():
    st = MockState()
    st.operation_create({"operations": [{"id": 7, "missions": [{"type": 1, "index": 0}]}]})
    mid = st.mission_start({"operationId": 7, "missionIndex": 0, "participants": ["1"]})["missionId"]
    uuid.UUID(mid)
    template = {
        "operationId": 999, "missionIndex": 0, "missionId": "x",
        "campaignWon": True, "othersImpactPlanet": 5, "othersImpactCommunityTarget": 6,
        "othersImpactPlanetEvent": 1880999,
        "progressionPackageUpdates": [{"platformAccountId": "LEAK", "id32": 1}],
        "rewards": [{"mixId": 1, "amount": 2}],
    }
    out = st.mission_end({"operationId": 7, "missionIndex": 0, "missionId": mid}, template)
    assert out["operationId"] == 7
    assert out["missionId"] == mid
    assert out["rewards"] == [{"mixId": 1, "amount": 2}]
    assert out["progressionPackageUpdates"][0]["platformAccountId"] == st.account_id
    # 离线默认不假装社区战果（模板里 campaignWon/impact/othersImpact 是抓包当时别人的成绩）
    assert out["othersImpactPlanet"] == 0
    assert out["othersImpactPlanetEvent"] == 0
    assert out["campaignWon"] is False
    # 作战里 mission 标记完成，全部完成后 isActive=False
    op = st.operation_list()["operations"][0]
    assert op["missions"][0]["missionId"] == mid
    assert op["missions"][0]["state"] == 3
    assert op["isActive"] is False


def test_mission_end_without_template_gives_skeleton():
    st = MockState()
    out = st.mission_end({"operationId": 1, "missionIndex": 2}, None)
    assert out["operationId"] == 1 and out["missionIndex"] == 2
    assert out["rewards"] == [] and out["campaignWon"] is False
    uuid.UUID(out["missionId"])


def test_abandon_removes_operation():
    st = MockState()
    st.operation_create({"operations": [{"id": 5, "missions": []}]})
    st.operation_abandon({"operationId": 5})
    assert st.operation_list()["operations"] == []


def test_lobby_lifecycle_and_connection_string_forwarding():
    st = MockState(account_id="100")
    cs = "cv2:11111111-2222-4333-8444-555555555555.r-00000000|000000|kv1:AAAA"
    body = {
        "lobbyId": "11111111-2222-4333-8444-555555555555.r-00000000",
        "connectionString": cs,
        "players": [{"memberAccountId": "100", "isHost": True, "hasInfo": True},
                    {"memberAccountId": "0", "isHost": False, "hasInfo": False}],
        "numLobbyMembers": 1,
    }
    lobby = st.lobby_put(body)
    assert lobby["connectionString"] == cs
    got = st.lobby_get(body["lobbyId"])
    assert got is not None and got["connectionString"] == cs
    # 用 uuid 前缀也能查到
    assert st.lobby_get("11111111-2222-4333-8444-555555555555") is not None

    joined = st.lobby_member(body["lobbyId"], {"memberAccountId": "200"})
    assert joined["numLobbyMembers"] == 2
    accounts = st.lobby_accounts()
    assert any(l["connectionString"] == cs for l in accounts["lobbies"])
    assert any(a["account"] == "100" for a in accounts["accountLobbies"])

    st.lobby_clear(account_id="100")
    assert st.lobbies == {}


def test_customization_merge():
    st = MockState()
    template = {"items": [{"mixId": 42, "modes": [0, 0, 0, 0], "activeIndex": 0}]}
    out = st.set_customization({"mixId": 42, "modes": [1, 0, 0, 0], "activeIndex": 1}, template)
    assert out["items"][0]["modes"] == [1, 0, 0, 0]
    assert out["items"][0]["activeIndex"] == 1
    assert st.customization["mixId"] == 42


def test_set_max_rewards_recorded():
    st = MockState()
    st.set_max_rewards({"operationId": 7, "maxRewards": 4})
    assert st.max_rewards[7] == 4


def test_missing_body_tolerant():
    st = MockState()
    assert st.operation_create(None) == {"operations": []}
    st.operation_abandon(None)
    mid = st.mission_start(None)["missionId"]
    uuid.UUID(mid)
    assert st.mission_end(None, None)["rewards"] == []
