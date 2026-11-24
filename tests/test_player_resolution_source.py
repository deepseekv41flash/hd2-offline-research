"""「这一次请求靠什么解析的」—— `resolve_ex` / `Response.resolution` / 日志 `via` 的回归测试。

背景（2026-11-24，与产品线对齐后加）：
`PlayerStore.last_source` 是 **sticky** 的（只保留最后一次解析），所以"响应靠身份头还是靠学到的 IP"
不能事后读 `/health` 判定 —— 多线程下还会串。S4（存档回放可见性）就靠这个区分
`header:prebind/getmotd`（身份头真的到了 mock）与 `ip`（只是 IP 兜底）。

这里锁三件事：
1. `resolve_ex()` 与玩家一起**原子返回**解析依据；
2. `Response.resolution` 记录本请求的依据，并进请求日志；
3. 日志渲染成 `[player:<steam> via header:getmotd]`，肉眼可查。
"""

from __future__ import annotations

import json
from pathlib import Path

from mock import server as srv
from mock.capture import CaptureStore
from mock.handler import MockHandler, Response
from mock.players import PlayerStore

STEAM_A = "76561198968648344"
HD2_A = "11893343864225233967"
STEAM_B = "76561199881579811"
HD2_B = "13887067702231565058"


def _store(root: Path, players: list[tuple[str, str]]) -> PlayerStore:
    """造一个最小覆盖层目录并返回 PlayerStore（不落抓包数据）。"""
    (root / "players").mkdir(parents=True, exist_ok=True)
    rows = []
    for steam, hd2 in players:
        pdir = root / "players" / steam
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "progression.json").write_text(json.dumps({"experience": 210}),
                                               encoding="utf-8")
        rows.append({"steamId64": steam, "hd2AccountId": hd2, "saveId": f"steam_{steam}"})
    (root / "index.json").write_text(json.dumps({"schema": 1, "players": rows}),
                                     encoding="utf-8")
    return PlayerStore(root)


def test_resolve_ex_reports_header_source(tmp_path: Path):
    st = _store(tmp_path / "o", [(STEAM_A, HD2_A), (STEAM_B, HD2_B)])
    player, how = st.resolve_ex(headers={"x-hd2-account": HD2_A,
                                         "x-hd2-account-source": "getmotd"})
    assert player is not None and player.steam_id == STEAM_A
    assert how == "header:getmotd"


def test_resolve_ex_prebind_does_not_learn_ip(tmp_path: Path):
    """`prebind` 是未校验断言：照给玩家，但**不写 IP 映射**（否则污染后续无头请求）。"""
    st = _store(tmp_path / "o", [(STEAM_A, HD2_A), (STEAM_B, HD2_B)])
    player, how = st.resolve_ex(headers={"x-hd2-account": HD2_A,
                                         "x-hd2-account-source": "prebind"},
                                client_addr="1.2.3.4")
    assert how == "header:prebind" and player.steam_id == STEAM_A
    assert st.describe()["learned_ips"] == 0
    # 该 IP 没被学到 ⇒ 无头请求不会落到主号（两人索引 ⇒ 回落公共数据）
    assert st.resolve_ex(client_addr="1.2.3.4") == (None, "")


def test_resolve_ex_ip_then_header_overwrites_ip(tmp_path: Path):
    """先带头 → 学 IP；再换人带头 → IP 被改写；无头则拿到**最后带头**的那个人。"""
    st = _store(tmp_path / "o", [(STEAM_A, HD2_A), (STEAM_B, HD2_B)])
    assert st.resolve_ex(headers={"x-hd2-account": HD2_A},
                         client_addr="1.1.1.1")[1] == "header:unknown"
    assert st.resolve_ex(client_addr="1.1.1.1") == (st.players[STEAM_A], "ip")
    st.resolve_ex(headers={"x-hd2-account": HD2_B}, client_addr="1.1.1.1")
    player, how = st.resolve_ex(client_addr="1.1.1.1")
    assert how == "ip" and player.steam_id == STEAM_B


def test_resolve_ex_uncertain_skips_fallbacks(tmp_path: Path):
    st = _store(tmp_path / "o", [(STEAM_A, HD2_A)])
    player, how = st.resolve_ex(headers={"x-hd2-account-source": "uncertain"},
                                client_addr="1.1.1.1")
    assert player is None and how == "uncertain:uncertain"


def test_resolve_ex_unresolved_header_never_guesses(tmp_path: Path):
    """带了身份头但索引里没有 ⇒ 回落公共数据，绝不用"唯一玩家"去猜。"""
    st = _store(tmp_path / "o", [(STEAM_A, HD2_A)])
    player, how = st.resolve_ex(headers={"x-hd2-account": "99999999999999999"})
    assert player is None and how == "header-unresolved:unknown"


def test_resolve_ex_single_player_and_empty(tmp_path: Path):
    one = _store(tmp_path / "one", [(STEAM_A, HD2_A)])
    assert one.resolve_ex()[1] == "single"
    two = _store(tmp_path / "two", [(STEAM_A, HD2_A), (STEAM_B, HD2_B)])
    assert two.resolve_ex() == (None, "")


def test_last_source_still_mirrors_resolve_ex(tmp_path: Path):
    """老接口 `resolve()` 仍维护 `last_source`（/health 兼容）。"""
    st = _store(tmp_path / "o", [(STEAM_A, HD2_A)])
    st.resolve(headers={"x-hd2-account": HD2_A, "x-hd2-account-source": "getmessages"})
    assert st.last_source == "header:getmessages"
    assert st.describe()["last_source"] == "header:getmessages"


def test_response_resolution_and_record(synthetic_capture: Path, tmp_path: Path):
    """Handler 把本请求的解析依据写进 Response.resolution 和请求日志。"""
    root = tmp_path / "o"
    _store(root, [(STEAM_A, HD2_A)])
    h = MockHandler(CaptureStore.load(synthetic_capture), players_dir=root)
    resp = h.handle("GET", "/api/Progression",
                    headers={"x-hd2-account": HD2_A, "x-hd2-account-source": "prebind"},
                    client_addr="9.9.9.9")
    assert resp.source == f"player:{STEAM_A}"
    assert resp.resolution == "header:prebind"
    rec = h.request_log[-1]
    assert rec["source"] == f"player:{STEAM_A}" and rec["resolution"] == "header:prebind"


def test_log_line_renders_via(synthetic_capture: Path, tmp_path: Path):
    """请求日志渲染成 `[player:<steam> via header:getmotd]`（S4 肉眼判据）。"""
    root = tmp_path / "o"
    _store(root, [(STEAM_A, HD2_A)])
    h = MockHandler(CaptureStore.load(synthetic_capture), players_dir=root)
    resp = h.handle("GET", "/api/Progression",
                    headers={"x-hd2-account": HD2_A, "x-hd2-account-source": "getmotd"})
    line = srv._format_request_log("GET", "/api/Progression", b"", resp,
                                   peer_ip="127.0.0.1", eff_ip="127.0.0.1")
    assert f"[player:{STEAM_A} via header:getmotd]" in line


def test_log_line_omits_via_when_no_resolution():
    line = srv._format_request_log(
        "GET", "/api/Progression", b"",
        Response(200, b"{}", "application/json", "exact"),
        peer_ip="127.0.0.1", eff_ip="127.0.0.1")
    assert "[exact]" in line and " via " not in line.split("[", 1)[1]


def test_log_line_keeps_forwarded_ip_note():
    """转发场景（eff_ip≠peer_ip）仍带外层 ` via <peer_ip>`，与内层解析依据不混淆。"""
    line = srv._format_request_log(
        "GET", "/api/Progression", b"",
        Response(200, b"{}", "application/json", f"player:{STEAM_A}", "header:prebind"),
        peer_ip="100.64.0.9", eff_ip="127.0.0.1")
    assert f"[player:{STEAM_A} via header:prebind] via 100.64.0.9" in line
