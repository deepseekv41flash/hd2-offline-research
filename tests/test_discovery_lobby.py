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
from mock import discovery
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
    # 真实契约：不在厅里的条目没有 lobbyId 键（不是空串）
    assert all("lobbyId" not in item for item in view["accountLobbies"])


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


# ---------------------------------------------------------------- 账号 ID 形状
def test_account_id_is_20_digit_numeric():
    """账号 ID 必须是 20 位纯数字：客户端把玩家 accountId 按 64 位整数解析，
    非数字（早期 "acct-000001"）会让 game.dll 崩溃（2026-11-21 21:46 实测取证）。"""
    svc = DiscoveryLobby()
    account = svc.login(client_addr="10.0.0.1")
    aid = svc.account_for(client_addr="10.0.0.1")
    assert aid is not None
    assert aid.isdigit() and len(aid) == 20, f"非 20 位纯数字: {aid!r}"
    assert aid.startswith("7656119")


# ---------------------------------------------------------------- 真实 accountId 学习（E2 现场修复）
REAL_HOST_ID = "2231864975069446623"      # 房主客户端自己的真实 accountId（非本服务合成）
REAL_JOINER_ID = "13887067702231565058"   # 加入方客户端自己的真实 accountId


def _host_lobby(real_id: str, name: str) -> dict:
    return _lobby(
        players=[
            {"slot": 0, "memberAccountId": real_id, "isHost": True, "hasInfo": True,
             "playerName": name, "playerRank": 199, "experience": 2759938, "commendRank": 0},
            {"slot": 1, "memberAccountId": "", "isHost": False, "hasInfo": False},
        ],
        numLobbyMembers=1,
    )


def test_join_uses_identity_learned_from_own_lobby_put():
    """`/member` 请求体是 null（E2 实测）：加入方身份要取它自己 PUT 大厅时暴露的真实 accountId。

    否则返回 body 里写的是合成 `7656119…`，加入方客户端看到「厅里不是我」→ 每 ~90 s 重试。
    """
    svc, _ = _service()
    acc_host = svc.account_for(client_addr="10.0.0.1")
    acc_join = svc.account_for(client_addr="10.0.0.2")
    svc.put(acc_host, _host_lobby(REAL_HOST_ID, "Fanna_"))
    svc.put(acc_join, _lobby(
        lobbyId="22222222-3333-4444-8555-666666666666.r-00000002",
        players=[{"slot": 0, "memberAccountId": REAL_JOINER_ID, "isHost": True,
                  "hasInfo": True, "playerName": "CAS MONSTER", "playerRank": 42}],
        numLobbyMembers=1,
    ))

    joined = svc.join(acc_join, LOBBY_ID, None)
    assert joined is not None
    ids = [p["memberAccountId"] for p in joined["players"] if p.get("memberAccountId")]
    assert REAL_JOINER_ID in ids, f"加入方真实 ID 未写入: {ids}"
    assert acc_join not in ids, f"合成的 7656119… 不应出现在厅里: {ids}"
    assert joined["numLobbyMembers"] == 2
    slot = next(p for p in joined["players"] if p.get("memberAccountId") == REAL_JOINER_ID)
    assert slot["playerName"] == "CAS MONSTER" and slot["hasInfo"] is True


def test_join_falls_back_to_synthetic_id_when_no_identity_learned():
    """加入方从未建过厅（服务端没学到真实 ID）时，退回合成账号 ID —— 不崩、不丢成员。"""
    svc, _ = _service()
    acc_host = svc.account_for(client_addr="10.0.0.1")
    acc_join = svc.account_for(client_addr="10.0.0.2")
    svc.put(acc_host, _host_lobby(REAL_HOST_ID, "Fanna_"))

    joined = svc.join(acc_join, LOBBY_ID, None)
    assert joined is not None
    ids = [p["memberAccountId"] for p in joined["players"] if p.get("memberAccountId")]
    assert acc_join in ids and REAL_HOST_ID in ids
    assert joined["numLobbyMembers"] == 2


def test_owner_reput_does_not_wipe_joined_members():
    """owner 客户端周期重发自己那份（还没看到新成员）的 lobby body 时，已加入成员不能被抹掉。

    2026-11-21 E2 实测：join 成功后 owner 的下一次 PUT 把 players 覆盖回「只有自己」，
    加入方永远看不到自己在厅里 → 卡加载 + 每 90 s 重试。
    """
    svc, _ = _service()
    acc_host = svc.account_for(client_addr="10.0.0.1")
    acc_join = svc.account_for(client_addr="10.0.0.2")
    svc.put(acc_host, _host_lobby(REAL_HOST_ID, "Fanna_"))
    svc.join(acc_join, LOBBY_ID, None)

    svc.put(acc_host, _host_lobby(REAL_HOST_ID, "Fanna_"))     # owner 用旧 body 重发
    got = svc.get(LOBBY_ID)
    ids = [p["memberAccountId"] for p in got["players"] if p.get("memberAccountId")]
    assert acc_join in ids, f"owner 重发 PUT 抹掉了成员: {ids}"
    assert got["numLobbyMembers"] == 2


# ---------------------------------------------------------------- 真实契约（2026-11-22 抓包）
FIXTURES = Path(__file__).parent / "fixtures"
# 真实服务端在客户端上传体之外**补**的 6 个字段（其余必需数字字段客户端自己会上传）
SERVER_ADDED_LOBBY_FIELDS = {"completedObjectives", "numSessionMembers", "operationSeed",
                             "operationType", "planetType", "transitionTarget"}


def _golden_lobby() -> dict:
    """2026-11-21 全通基线抓到的真实「在飞船里的好友厅」（已脱敏，见 fixtures 注释）。"""
    return json.loads((FIXTURES / "real_lobby_in_ship.json").read_text(encoding="utf-8"))


def test_lobby_view_reproduces_real_official_body():
    """真实后端只做这几件事：补 6 个字段 + 丢 memberAccountIds + 补人数。逐字段对照真身。"""
    golden = _golden_lobby()
    upload = {k: v for k, v in golden.items() if k not in SERVER_ADDED_LOBBY_FIELDS}
    upload["memberAccountIds"] = ["22318649750694466001", "22318649750694466002"]
    view = discovery._lobby_view(upload, max_members=4)
    assert view == golden


def test_lobby_view_fills_required_numbers_and_pads_players():
    """缺任一必需数字字段会让整份 /lobby/accounts 解析失败（好友全灰）；players 固定 maxMembers 槽。"""
    view = discovery._lobby_view({"lobbyId": LOBBY_ID})
    for name in discovery.REQUIRED_LOBBY_NUMBERS:
        assert name in view, name
        assert view[name] == 0, name
    assert "memberAccountIds" not in view
    assert len(view["players"]) == 4
    assert view["numLobbyMembers"] == 0
    assert all(p["memberAccountId"] == "0" and p["hasInfo"] is False for p in view["players"])


def test_accounts_echoes_requested_ids_in_order():
    """`accountLobbies` 严格按请求 ids 顺序等长回显；不在厅里的条目没有 lobbyId 键。"""
    svc, _ = _service()
    acc_host = svc.account_for(client_addr="10.0.0.1")
    svc.put(acc_host, _host_lobby(REAL_HOST_ID, "Fanna_"))
    unknown = "19999999999999999999"

    view = svc.accounts(acc_host, ids={"ids": [REAL_HOST_ID, unknown]})
    assert [e["account"] for e in view["accountLobbies"]] == [REAL_HOST_ID, unknown]
    assert view["accountLobbies"][0] == {"account": REAL_HOST_ID, "lobbyId": LOBBY_ID}
    assert view["accountLobbies"][1] == {"account": unknown}      # 没有 lobbyId 键
    assert [l["lobbyId"] for l in view["lobbies"]] == [LOBBY_ID]


def test_accounts_maps_joined_member_and_dedups_bodies():
    """加入方按**真实 accountId** 出现在映射里；两个 id 指向同一厅时 body 只回一份。"""
    svc, _ = _service()
    acc_host = svc.account_for(client_addr="10.0.0.1")
    acc_join = svc.account_for(client_addr="10.0.0.2")
    svc.put(acc_host, _host_lobby(REAL_HOST_ID, "Fanna_"))
    svc.put(acc_join, _lobby(lobbyId="22222222-3333-4444-8555-666666666666.r-00000002",
                            players=[{"slot": 0, "memberAccountId": REAL_JOINER_ID,
                                      "isHost": True, "hasInfo": True, "playerName": "J"}],
                            numLobbyMembers=1))
    svc.join(acc_join, LOBBY_ID, None)

    view = svc.accounts(acc_host, ids={"ids": [REAL_HOST_ID, REAL_JOINER_ID]})
    assert [e.get("lobbyId") for e in view["accountLobbies"]] == [LOBBY_ID, LOBBY_ID]
    assert len(view["lobbies"]) == 1                              # 去重
    assert view["lobbies"][0]["numLobbyMembers"] == 2             # 真实成员数
    assert "memberAccountIds" not in view["lobbies"][0]


def test_login_public_key_is_served_by_account_keys():
    """加入门槛：每台游戏在 Login 自报公钥，`/Account/Keys` 按真实 accountId 回它。"""
    svc, _ = _service()
    key = "Q2hhbmdlTWUxMjM0NTY3ODkwYWJjZGVmZ2hpamts"          # 44 个可打印字符，非十六进制
    svc.login(client_addr="10.0.0.1", body={"instanceId": "A", "publicKey": key})
    acc_host = svc.account_for(client_addr="10.0.0.1")
    svc.put(acc_host, _host_lobby(REAL_HOST_ID, "Fanna_"))        # 学会真实 accountId

    assert svc.account_keys([REAL_HOST_ID]) == {
        "accountKeys": [{"platformAccountId": REAL_HOST_ID, "key": key}]}
    assert svc.account_keys(["19999999999999999999"]) == {"accountKeys": []}
    assert svc.account_keys([acc_host])["accountKeys"][0]["key"] == key


def test_login_ignores_unprintable_public_key():
    svc, _ = _service()
    svc.login(client_addr="10.0.0.1", body={"publicKey": "bad key\n"})
    acc = svc.account_for(client_addr="10.0.0.1")
    assert svc.account_keys([acc]) == {"accountKeys": []}


def test_handler_login_honours_account_header(synthetic_capture: Path):
    """带 `X-HD2-Account` 的登录必须落在**同一个**账号上。

    2026-11-22 修：`account_login` 分支原来没把账号头传进 `login()`，探针登录会建出另一个账号，
    公钥挂错人 → 对端 `/Account/Keys` 拿不到 → 加入方永不被接纳。真实客户端走 IP 归属，
    但两台机器经同一中继/同 NAT 时必须靠显式账号头区分。
    """
    h = _handler(synthetic_capture)
    header = {"x-hd2-account": "76561190000000000042"}
    key = "probe-key-0123456789abcdefghijklmnop"
    r = h.handle("POST", "/api/Account/Login",
                 json.dumps({"instanceId": "P", "publicKey": key}).encode(),
                 headers=header, client_addr="10.0.0.9")
    assert r.source == "discovery:account_login"
    acc = h.discovery.account_for(account_hint=header["x-hd2-account"])
    assert acc == header["x-hd2-account"]
    served = h.discovery.account_keys([header["x-hd2-account"]])
    assert served["accountKeys"] == [{"platformAccountId": header["x-hd2-account"], "key": key}]
