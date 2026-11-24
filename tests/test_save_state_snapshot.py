"""`save_state_snapshot.py` 的回归测试（2026-11-24 加）。

用最小合成隔离状态目录覆盖判据：换号（identity 变化）、结算增量、新账本、
.bak、_last/_session-start、settlements.jsonl。
"""

from __future__ import annotations

import json
from pathlib import Path

import save_state_snapshot as sss

STEAM_A = "76561198968648344"
HD2_A = "11893343864225233967"
STEAM_B = "76561199881579811"
HD2_B = "13887067702231565058"


def _ledger(steam: str, hd2: str, *, experience=210, missions=1, purchases=0,
            inventory=None, source="getmotd") -> dict:
    return {
        "schema": 2,
        "save_id": f"steam_{steam}",
        "created_at": "2026-11-24T13:00:00",
        "updated_at": "2026-11-24T13:10:00",
        "catalog_build": "93e7575a/3021/1007203",
        "identity": {"steam_id": steam, "hd2_account_id": hd2, "source": source,
                     "identified_at": "2026-11-24T13:00:05"},
        "earned": {
            "experience": experience,
            "onboarding_flags": 0, "onboarding_state": 0, "ship_name": None, "location": None,
            "achievements": {"6": 1680},
            "package_progress": {"277818845": {"level": 5, "progress": 459}},
            "purchased_season_pass_items": [],
            "inventory_amounts": inventory or {"897894480": 1, "3608481516": 910},
            "inventory_ids": [],
            "stats": {"totalEnemyKills": 356105, "deaths": 0},
            "season_pass": {},
            "assignments": [],
            "missions": {"recent_ids": [f"m-{i}" for i in range(missions)],
                          "last": {"missionId": f"m-{missions - 1}", "experience": experience}},
            "purchases": [{"at": "2026-11-24T13:05:00", "kind": "buy"}] * purchases,
        },
    }


def _state(tmp_path: Path, *, saves: dict | None = None, last: dict | None = None,
           session: dict | None = None, settlements: dict | None = None) -> Path:
    base = tmp_path / "state" / "runtime" / "saves"
    (base / "_session-start").mkdir(parents=True, exist_ok=True)
    for steam, doc in (saves or {}).items():
        (base / f"steam_{steam}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    if last is not None:
        (base / "_last.json").write_text(json.dumps(last, ensure_ascii=False), encoding="utf-8")
    if session is not None:
        (base / "_session-start" / "local.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    for rel, lines in (settlements or {}).items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    return tmp_path / "state"


def test_capture_summarizes_ledger(tmp_path: Path):
    st = _state(tmp_path, saves={STEAM_A: _ledger(STEAM_A, HD2_A)},
                last={"last_save_id": f"steam_{STEAM_A}", "last_steam_id": STEAM_A},
                session={"schema": 1, "save_id": "local", "run_id": "r1", "existed": False, "doc": None})
    snap = sss.capture(st)
    assert snap["exists"] is True
    s = snap["saves"][f"steam_{STEAM_A}"]
    assert s["identity"]["steam_id"] == STEAM_A
    assert s["earned"]["experience"] == 210
    assert s["earned"]["inventory_rows"] == 2
    assert s["earned"]["missions_count"] == 1
    assert s["bak"]["exists"] is False
    assert snap["extras"]["session_start"]["local"]["has_doc"] is False
    assert snap["extras"]["_last"]["last_steam_id"] == STEAM_A


def test_capture_counts_settlements(tmp_path: Path):
    st = _state(tmp_path, saves={STEAM_A: _ledger(STEAM_A, HD2_A)},
                settlements={f"steam_{STEAM_A}/settlements.jsonl": [
                    {"missionId": "m-1", "source": "mission_end"},
                    {"missionId": "m-2", "source": "ledger-only"}]})
    snap = sss.capture(st)
    key = f"steam_{STEAM_A}/settlements.jsonl"
    assert snap["extras"]["settlements"][key]["lines"] == 2
    assert snap["extras"]["settlements"][key]["mission_ids"] == ["m-1", "m-2"]


def test_diff_detects_settlement_increment(tmp_path: Path):
    before = sss.capture(_state(tmp_path / "b", saves={STEAM_A: _ledger(STEAM_A, HD2_A)}))
    after = sss.capture(_state(tmp_path / "a", saves={
        STEAM_A: _ledger(STEAM_A, HD2_A, experience=1146, missions=2,
                         inventory={"897894480": 8, "3608481516": 5344})}))
    result = sss.diff(before, after)
    assert result["changed"] is True
    joined = "\n".join(result["changes"])
    assert "earned.experience: 210 → 1146" in joined
    assert "inventory_amount_sum" in joined
    assert "missions_count: 1 → 2" in joined


def test_diff_detects_account_switch_without_mixing(tmp_path: Path):
    """换号：新账号出现、旧账号保留原值（不混档）。"""
    before = sss.capture(_state(tmp_path / "b", saves={STEAM_A: _ledger(STEAM_A, HD2_A)}))
    after = sss.capture(_state(tmp_path / "a", saves={
        STEAM_A: _ledger(STEAM_A, HD2_A),
        STEAM_B: _ledger(STEAM_B, HD2_B, experience=1, missions=0)}))
    result = sss.diff(before, after)
    joined = "\n".join(result["changes"])
    assert f"[新账本] steam_{STEAM_B}" in joined
    # A 的数值不应被 B 覆盖
    a_after = after["saves"][f"steam_{STEAM_A}"]["earned"]
    assert a_after["experience"] == 210


def test_diff_detects_bak_and_session_rebuild(tmp_path: Path):
    before = sss.capture(_state(tmp_path / "b", saves={STEAM_A: _ledger(STEAM_A, HD2_A)}))
    st = _state(tmp_path / "a", saves={STEAM_A: _ledger(STEAM_A, HD2_A)},
                session={"schema": 1, "save_id": "local", "run_id": "r2", "existed": True, "doc": {"x": 1}})
    (st / "runtime" / "saves" / f"steam_{STEAM_A}.json.bak").write_text(
        (st / "runtime" / "saves" / f"steam_{STEAM_A}.json").read_text(encoding="utf-8"), encoding="utf-8")
    after = sss.capture(st)
    joined = "\n".join(sss.diff(before, after)["changes"])
    assert ".bak" in joined
    assert "_session-start.local" in joined


def test_diff_identical_reports_no_change(tmp_path: Path):
    st = _state(tmp_path, saves={STEAM_A: _ledger(STEAM_A, HD2_A)})
    a = sss.capture(st)
    b = sss.capture(st)
    result = sss.diff(a, b)
    assert result["changed"] is False
    assert "无变化" in sss.format_text(result)


def test_cli_snapshot_and_diff(tmp_path: Path, capsys):
    st = _state(tmp_path, saves={STEAM_A: _ledger(STEAM_A, HD2_A)})
    out = tmp_path / "snap.json"
    assert sss.main(["snapshot", "--state-dir", str(st), "--out", str(out)]) == 0
    assert out.exists()
    before, after = tmp_path / "b.json", tmp_path / "a.json"
    before.write_text(json.dumps(sss.capture(st), ensure_ascii=False), encoding="utf-8")
    st2 = _state(tmp_path / "x", saves={STEAM_A: _ledger(STEAM_A, HD2_A, experience=1146)})
    after.write_text(json.dumps(sss.capture(st2), ensure_ascii=False), encoding="utf-8")
    assert sss.main(["diff", "--before", str(before), "--after", str(after)]) == 0
    assert "earned.experience" in capsys.readouterr().out


def test_capture_missing_state_dir_is_graceful(tmp_path: Path):
    snap = sss.capture(tmp_path / "nope")
    assert snap["exists"] is False and snap["saves"] == {}


def test_capture_handles_real_engine_shapes(tmp_path: Path):
    """真实引擎的 purchases/missions 是 {count,recent_ids,last} 结构（不是 list）。"""
    doc = _ledger(STEAM_A, HD2_A)
    doc["earned"]["purchases"] = {"count": 3, "recent_keys": ["k1", "k2", "k3"],
                                  "last": {"at": "2026-11-24T13:05:00", "kind": "buy"}}
    doc["earned"]["missions"] = {"count": 7,
                                 "recent_ids": [f"m-{i}" for i in range(7)],
                                 "last": {"at": "2026-11-24T13:10:00", "experience": 936}}
    st = _state(tmp_path, saves={STEAM_A: doc})
    snap = sss.capture(st)
    earned = snap["saves"][f"steam_{STEAM_A}"]["earned"]
    assert earned["purchases_count"] == 3
    assert earned["missions_count"] == 7
    assert earned["purchases_last"]["kind"] == "buy"
