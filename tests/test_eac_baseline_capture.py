"""`eac_baseline_capture.py` 的离线测试：索引构建 / 指纹序列 / host 统计。

不碰注册表、不起进程、不需要游戏 —— 只锁"把 MITM 日志变成上报指纹"这一段纯逻辑，
因为将来注入前后做差分就靠它。
"""

from __future__ import annotations

import json
from pathlib import Path

import eac_baseline_capture as eac

AC = "api.epicgames.dev"
GAME = "api.live.prod.thehelldiversgame.com"


def _lines(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "mitm.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return p


def _ac_req(t: str, req_len: int, raw: dict | None = None) -> dict:
    return {"t": t, "host": AC, "method": "POST",
            "path": "/anticheat/v8/games/client/message/",
            "req_len": req_len, "status": 200, "resp_len": 500,
            "raw": ([raw] if raw else [])}


def test_build_index_keeps_anticheat_only(tmp_path: Path):
    p = _lines(tmp_path, [
        _ac_req("10:00:00.000", 1000, {"direction": "req", "file": "a.bin",
                                      "sha256": "deadbeef", "truncated": False}),
        {"t": "10:00:01.000", "host": GAME, "method": "GET", "path": "/api/Progression",
         "req_len": 0, "status": 200, "resp_len": 2468},
        {"t": "10:00:02.000", "host": AC, "method": "GET", "path": "/sdk/v1/default",
         "req_len": 0, "status": 200, "resp_len": 7900},
    ])
    rows = eac.build_index(p)
    assert rows, "anticheat 条目不应为空"
    assert all(r["path"].startswith("/anticheat/") for r in rows)
    req = [r for r in rows if r["direction"] == "req"][0]
    assert req["len"] == 1000 and req["file"] == "a.bin" and req["sha256"] == "deadbeef"


def test_interval_series_computed(tmp_path: Path):
    """dt_ms 就是"节奏指纹"：注入前后对比的是这条序列。"""
    p = _lines(tmp_path, [
        _ac_req("10:00:00.000", 388),
        _ac_req("10:00:12.000", 1200),
        _ac_req("10:00:12.500", 800),
    ])
    rows = eac.build_index(p)
    reqs = [r for r in rows if r["direction"] == "req"]
    assert [r["dt_ms"] for r in reqs] == [None, 12000, 500]


def test_summarize_reports_series_and_totals(tmp_path: Path):
    p = _lines(tmp_path, [
        _ac_req("10:00:00.000", 388),
        _ac_req("10:00:12.000", 1200),
    ])
    rows = eac.build_index(p)
    meta = eac.summarize(rows, started="a", ended="b", game_pid=4242)
    assert meta["anticheat_requests"] == 2
    assert meta["req_len_series"] == [388, 1200]
    assert meta["req_bytes_total"] == 1588
    assert meta["game_pid"] == 4242
    assert meta["dt_ms_min"] == 12000 and meta["dt_ms_max"] == 12000


def test_count_hosts_includes_control_hosts(tmp_path: Path):
    p = _lines(tmp_path, [
        _ac_req("10:00:00.000", 100),
        {"t": "10:00:01.000", "host": GAME, "method": "GET", "path": "/x",
         "req_len": 0, "resp_len": 10},
        {"t": "10:00:02.000", "host": "modules-cdn.eac-prod.on.epicgames.com",
         "method": "GET", "path": "/mod.bin", "req_len": 0, "resp_len": 9999},
    ])
    hosts = eac.count_hosts(p)
    assert hosts[GAME]["requests"] == 1
    assert hosts["modules-cdn.eac-prod.on.epicgames.com"]["resp_bytes"] == 9999


def test_missing_log_is_empty_not_crash(tmp_path: Path):
    empty = tmp_path / "nope.jsonl"
    assert eac.build_index(empty) == []
    assert eac.count_hosts(empty) == {}


def test_ts_to_ms_parses_both_formats():
    assert eac._ts_to_ms("10:00:01.500") == 36001500
    assert eac._ts_to_ms(None) == 0
    assert eac._ts_to_ms("garbage") == 0


def test_telemetry_index_extracts_app_id_and_session(tmp_path: Path):
    """`/telemetry/...&AppID=EOSSDK.AntiCheatExt.Launcher` 是启动器上报（与 /anticheat/ 不同路径）。"""
    p = tmp_path / "mitm.jsonl"
    p.write_text(json.dumps({
        "t": "2026-11-25T00:36:43", "host": AC, "method": "POST",
        "path": "/telemetry/data/datarouter/api/v1/public/data?AppVersion=1.0.0"
                "&UploadType=sdkevents&AppID=EOSSDK.AntiCheatExt.Launcher&SessionId=fa38ae34",
        "req_len": 0, "status": 200, "resp_len": 666,
        "raw": [{"direction": "req", "file": "t1.bin", "sha256": "abc", "truncated": False}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    rows = eac.build_telemetry_index(p)
    assert len(rows) == 1
    r = rows[0]
    assert r["app_id"] == "EOSSDK.AntiCheatExt.Launcher"
    assert r["upload_type"] == "sdkevents"
    assert r["session_id"] == "fa38ae34"
    assert r["resp_len"] == 666 and r["file"] == "t1.bin"


def test_anticheat_index_ignores_telemetry(tmp_path: Path):
    """两条路径必须分开统计：telemetry 不能污染 req_len_series（否则指纹不可比）。"""
    p = _lines(tmp_path, [
        _ac_req("10:00:00.000", 100),
        {"t": "10:00:01.000", "host": AC, "method": "POST",
         "path": "/telemetry/data/datarouter/api/v1/public/data?AppID=X",
         "req_len": 0, "status": 200, "resp_len": 666},
    ])
    assert len(eac.build_index(p)) == 2          # req + resp 两条（只来自 anticheat）
    assert len(eac.build_telemetry_index(p)) == 1
