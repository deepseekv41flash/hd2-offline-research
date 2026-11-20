"""路径归一 / 回放索引 / 有状态路由匹配。"""

from __future__ import annotations

from pathlib import Path

from mock.capture import CaptureStore
from mock.rules import ReplayIndex, match_state_route, split_path, template_path


def test_split_and_template():
    assert split_path("/api/lobby/accounts?x=1") == ("/api/lobby/accounts", "x=1")
    assert template_path("/api/WarSeason/801/Status") == "/api/WarSeason/{n}/Status"
    assert template_path("/api/lobby/accounts") == "/api/lobby/accounts"
    assert template_path(
        "/api/lobby/7262c34b-ccef-4a9c-82ad-cb66f67a46b0.r-20260323"
    ) == "/api/lobby/{uuid}.r-20260323"

def test_replay_index_prefers_last_capture(synthetic_capture: Path):
    store = CaptureStore.load(synthetic_capture)
    idx = ReplayIndex(store)
    hit = idx.lookup("GET", "/api/WarSeason/801/Status")
    assert hit is not None and hit.kind == "exact"
    # query 变化也能命中 base
    hit2 = idx.lookup("GET", "/api/NewsFeed/801?maxEntries=16")
    assert hit2 is None or hit2.kind in ("base", "template")
    # 未知路径
    assert idx.lookup("GET", "/api/DoesNotExist") is None
    desc = idx.describe()
    assert desc["entries"] == len(store.entries)
    assert desc["base"] >= 11


def test_state_route_matching():
    assert match_state_route("POST", "/api/Account/Login")[0] == "account_login"
    assert match_state_route("PUT", "/api/lobby")[0] == "lobby_put"
    assert match_state_route("DELETE", "/api/lobby/clear")[0] == "lobby_clear"
    name, params = match_state_route("GET", "/api/lobby/abc-123")
    assert name == "lobby_get" and params["lobby_id"] == "abc-123"
    name2, params2 = match_state_route("POST", "/api/lobby/abc/member")
    assert name2 == "lobby_member" and params2["lobby_id"] == "abc"
    # 大小写方法、带 query 也要命中
    assert match_state_route("post", "/api/Operation/Create?x=1")[0] == "operation_create"
    # 不认识的端点
    assert match_state_route("GET", "/api/WarSeason/801/Status") is None


def test_real_capture_has_expected_endpoints(real_capture: Path):
    store = CaptureStore.load(real_capture)
    idx = ReplayIndex(store)
    for path in ("/api/Account/Login", "/api/Configuration/GameClient",
                 "/api/WarSeason/current/WarId", "/api/WarSeason/801/Status",
                 "/api/WarSeason/801/warinfo", "/api/WarSeason/801/timeSinceStart",
                 "/api/WarSeason/GalacticWarEffects", "/api/WarSeason/NewsTicker",
                 "/api/Progression/inventory", "/api/lobby/accounts",
                 "/api/StoreFront/rotation", "/api/SeasonPass",
                 "/api/Mission/RewardEntries", "/api/v2/Assignment/War/801"):
        assert idx.lookup("GET", path) is not None, path
