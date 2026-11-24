"""按玩家分档覆盖层（`save-export` 产物）的回归测试 —— 2026-11-24 新增。

目标：自建服按玩家回 `/api/Progression`、`/api/Progression/inventory`、`/api/Stats/profile/summary`
等端点，并且**解析不到玩家时回落公共抓包数据**（不能让客户端读到空响应）。

身份解析口径（契约档 §9）：请求头 `X-HD2-Account` → 本进程学到的 IP 映射 → 索引里只有一个玩家。
"""

from __future__ import annotations

import json
from pathlib import Path

from mock.handler import MockHandler
from mock.players import PlayerStore

STEAM_A = "76561198968648344"
HD2_A = "11893343864225233967"
STEAM_B = "76561199881579811"
HD2_B = "13887067702231565058"


def _write_player(root: Path, steam_id: str, hd2_id: str, *, progression: dict,
                  inventory: dict | None = None, stats: dict | None = None,
                  customize: dict | None = None, war_stats: dict | None = None,
                  mission_end: dict | None = None, with_index: bool = True) -> None:
    pdir = root / "players" / steam_id
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "progression.json").write_text(json.dumps(progression, ensure_ascii=False), encoding="utf-8")
    if inventory is not None:
        (pdir / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
    if stats is not None:
        (pdir / "stats_profile.json").write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    if customize is not None:
        (pdir / "customize_v2.json").write_text(json.dumps(customize, ensure_ascii=False), encoding="utf-8")
    if war_stats is not None:
        (pdir / "stats_profile_war.json").write_text(json.dumps(war_stats, ensure_ascii=False), encoding="utf-8")
    if mission_end is not None:
        (pdir / "mission_end.json").write_text(json.dumps(mission_end, ensure_ascii=False), encoding="utf-8")
    if with_index:
        idx = root / "index.json"
        players = json.loads(idx.read_text(encoding="utf-8"))["players"] if idx.exists() else []
        players = [p for p in players if p["steamId64"] != steam_id]
        players.append({"steamId64": steam_id, "hd2AccountId": hd2_id, "saveId": f"steam_{steam_id}"})
        idx.write_text(json.dumps({"schema": 1, "players": players}, ensure_ascii=False), encoding="utf-8")


def _handler(synthetic_capture: Path, players_root: Path) -> MockHandler:
    from mock.capture import CaptureStore
    return MockHandler(CaptureStore.load(synthetic_capture), players_dir=players_root)


def test_resolve_by_header_serves_that_player(synthetic_capture: Path, tmp_path: Path):
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210, "marker": "A"})
    _write_player(tmp_path, STEAM_B, HD2_B, progression={"experience": 1, "marker": "B"})
    h = _handler(synthetic_capture, tmp_path)

    resp = h.handle("GET", "/api/Progression", headers={"x-hd2-account": HD2_A})
    assert resp.status == 200 and json.loads(resp.body)["marker"] == "A"
    assert resp.source == f"player:{STEAM_A}"

    # 用 steamId64 也认
    resp = h.handle("GET", "/api/Progression", headers={"X-HD2-Account": STEAM_B})
    assert json.loads(resp.body)["marker"] == "B"
    assert h.stats["player"] == 2


def test_learned_ip_mapping_then_headerless_request(synthetic_capture: Path, tmp_path: Path):
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210})
    _write_player(tmp_path, STEAM_B, HD2_B, progression={"experience": 1})
    h = _handler(synthetic_capture, tmp_path)
    # 先来一条带头的 lobby 请求（真实客户端只在 lobby 端点带身份头）学出 IP 映射
    h.handle("PUT", "/api/lobby", headers={"x-hd2-account": HD2_A}, client_addr="10.0.0.9")
    resp = h.handle("GET", "/api/Progression", client_addr="10.0.0.9")
    assert resp.source == f"player:{STEAM_A}"
    assert json.loads(resp.body)["experience"] == 210


def test_header_wins_over_learned_ip(synthetic_capture: Path, tmp_path: Path):
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210, "marker": "A"})
    _write_player(tmp_path, STEAM_B, HD2_B, progression={"experience": 1, "marker": "B"})
    h = _handler(synthetic_capture, tmp_path)
    h.handle("PUT", "/api/lobby", headers={"x-hd2-account": HD2_A}, client_addr="10.0.0.9")
    resp = h.handle("GET", "/api/Progression", headers={"x-hd2-account": HD2_B}, client_addr="10.0.0.9")
    assert json.loads(resp.body)["marker"] == "B"


def test_unresolvable_falls_back_to_shared_dataset(synthetic_capture: Path, tmp_path: Path):
    """两个玩家、无身份头、无 IP 映射 ⇒ 不能瞎猜，回落公共数据（source 不是 player:*）。"""
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210})
    _write_player(tmp_path, STEAM_B, HD2_B, progression={"experience": 1})
    h = _handler(synthetic_capture, tmp_path)
    resp = h.handle("GET", "/api/Progression")
    assert resp.status == 200
    assert not resp.source.startswith("player:")
    assert json.loads(resp.body).get("experience") == 1      # conftest 的合成抓包


def test_single_player_index_resolves_without_header(synthetic_capture: Path, tmp_path: Path):
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210})
    h = _handler(synthetic_capture, tmp_path)
    resp = h.handle("GET", "/api/Progression")
    assert resp.source == f"player:{STEAM_A}"
    assert json.loads(resp.body)["experience"] == 210


def test_endpoint_mapping_inventory_stats_customize_war_and_mission_end(synthetic_capture: Path, tmp_path: Path):
    _write_player(tmp_path, STEAM_A, HD2_A,
                  progression={"experience": 210},
                  inventory={"items": [{"mixId": 1, "amount": 2}]},
                  stats={"kills": 7},
                  customize={"items": [{"mixId": 9}]},
                  war_stats={"warKills": 3},
                  mission_end={"experience": 936})
    h = _handler(synthetic_capture, tmp_path)
    hdr = {"x-hd2-account": HD2_A}

    inv = h.handle("GET", "/api/Progression/inventory", headers=hdr)
    assert json.loads(inv.body)["items"][0]["mixId"] == 1
    stats = h.handle("GET", "/api/Stats/profile/summary", headers=hdr)
    assert json.loads(stats.body)["kills"] == 7
    war = h.handle("GET", "/api/Stats/profile/801/summary", headers=hdr)
    assert json.loads(war.body)["warKills"] == 3
    cust = h.handle("GET", "/api/Progression/Items/Customize/v2", headers=hdr)
    assert json.loads(cust.body)["items"][0]["mixId"] == 9
    me = h.handle("POST", "/api/Operation/Mission/end", body=b"{}", headers=hdr)
    assert json.loads(me.body)["experience"] == 936


def test_missing_player_file_falls_back_for_that_endpoint_only(synthetic_capture: Path, tmp_path: Path):
    """玩家目录里没有 stats_profile.json ⇒ 该端点回落公共数据，其它端点仍按玩家。"""
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210})
    h = _handler(synthetic_capture, tmp_path)
    hdr = {"x-hd2-account": HD2_A}
    assert h.handle("GET", "/api/Progression", headers=hdr).source == f"player:{STEAM_A}"
    stats = h.handle("GET", "/api/Stats/profile/summary", headers=hdr)
    assert not stats.source.startswith("player:")


def test_health_reports_player_overlay(synthetic_capture: Path, tmp_path: Path):
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210})
    h = _handler(synthetic_capture, tmp_path)
    h.handle("GET", "/api/Progression", headers={"x-hd2-account": HD2_A})
    doc = h.health()
    assert doc["players"]["count"] == 1
    assert doc["players"]["last_source"] == "header:unknown"


def test_store_loads_hd2_id_from_manifest_when_index_lacks_it(tmp_path: Path):
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 1}, with_index=False)
    (tmp_path / "players" / STEAM_A / "manifest.json").write_text(
        json.dumps({"schema": 1, "identity": {"steamId64": STEAM_A, "hd2AccountId": HD2_A}}),
        encoding="utf-8")
    store = PlayerStore(tmp_path)
    assert store.by_hd2[HD2_A] == STEAM_A
    assert store.resolve({"x-hd2-account": HD2_A}) is not None


def test_disabled_overlay_keeps_old_behaviour(synthetic_capture: Path, tmp_path: Path):
    """不传 players_dir ⇒ 行为与之前完全一致（防止给现有离线/发现层实验带回归）。"""
    from mock.capture import CaptureStore
    h = MockHandler(CaptureStore.load(synthetic_capture))
    assert h.players is None
    assert h.health()["players"] is None
    assert not h.handle("GET", "/api/Progression").source.startswith("player:")


# ---- X-HD2-Account-Source 语义（产品引擎 >=0.7.0-alpha.1） -------------------

def test_prebind_unknown_account_does_not_guess(synthetic_capture: Path, tmp_path: Path):
    """prebind 断言命中不到索引：即使索引里只有一个玩家，也不能猜（回落公共数据）。"""
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210})
    h = _handler(synthetic_capture, tmp_path)
    resp = h.handle("GET", "/api/Progression",
                    headers={"x-hd2-account": "99999999999999999999",
                             "x-hd2-account-source": "prebind"})
    assert not resp.source.startswith("player:")
    assert h.players.last_source == "header-unresolved:prebind"


def test_prebind_does_not_learn_ip_mapping(synthetic_capture: Path, tmp_path: Path):
    """prebind 是未校验断言 ⇒ 不写 IP 映射；之后的同 IP 无头请求不应被"顺带"认成该玩家。"""
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210})
    _write_player(tmp_path, STEAM_B, HD2_B, progression={"experience": 1})
    h = _handler(synthetic_capture, tmp_path)
    h.handle("PUT", "/api/lobby", client_addr="10.0.0.7",
             headers={"x-hd2-account": HD2_A, "x-hd2-account-source": "prebind"})
    assert not h.players.describe()["learned_ips"]
    resp = h.handle("GET", "/api/Progression", client_addr="10.0.0.7")
    assert not resp.source.startswith("player:")


def test_getmotd_source_resolves_and_learns_ip(synthetic_capture: Path, tmp_path: Path):
    """getmotd/getmessages 是本局已校验来源 ⇒ 正常解析并写 IP 映射。"""
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210})
    _write_player(tmp_path, STEAM_B, HD2_B, progression={"experience": 1})
    h = _handler(synthetic_capture, tmp_path)
    h.handle("PUT", "/api/lobby", client_addr="10.0.0.8",
             headers={"x-hd2-account": STEAM_A, "x-hd2-account-source": "getmotd"})
    assert h.players.describe()["learned_ips"] == 1
    resp = h.handle("GET", "/api/Progression", client_addr="10.0.0.8")
    assert resp.source == f"player:{STEAM_A}"
    assert h.players.last_source == "ip"


def test_header_without_source_still_resolves_and_learns(synthetic_capture: Path, tmp_path: Path):
    """老客户端/探针只带 X-HD2-Account 不带 Source：保持原有行为（解析 + 学 IP）。"""
    _write_player(tmp_path, STEAM_A, HD2_A, progression={"experience": 210})
    _write_player(tmp_path, STEAM_B, HD2_B, progression={"experience": 1})
    h = _handler(synthetic_capture, tmp_path)
    h.handle("PUT", "/api/lobby", client_addr="10.0.0.9", headers={"x-hd2-account": HD2_A})
    assert h.players.describe()["learned_ips"] == 1
    assert h.handle("GET", "/api/Progression", client_addr="10.0.0.9").source == f"player:{STEAM_A}"
