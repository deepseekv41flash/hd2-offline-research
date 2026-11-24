"""`save_switch_verdict.py` 的回归测试（2026-11-24 加）。

覆盖 S2 六条判据的通过与失败形态，以及日志文案的两种真实变体
（`身份切换：A（已识别）→ B` / `身份确认：A（local/未识别）已合并进 B`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import save_state_snapshot as sss
import save_switch_verdict as svv

STEAM_A = "76561198968648344"
HD2_A = "11893343864225233967"
STEAM_B = "76561199881579811"
HD2_B = "13887067702231565058"

GOOD_LOG = """\
20:52:34 存档：首屏预绑定 steam_76561198968648344（身份待 GetMotd 校验）
20:53:10 ⚠ 身份切换：steam_76561198968648344（已识别）→ steam_76561199881579811；**不合并**（避免跨玩家混档）。本局写入=无，回滚=none
20:54:02 存档：首屏预绑定 steam_76561199881579811（身份待 GetMotd 校验）
20:54:40 ⚠ 身份切换：steam_76561199881579811（已识别）→ steam_76561198968648344；**不合并**（避免跨玩家混档）。本局写入=无，回滚=none
"""


def _ledger(steam: str, hd2: str, *, experience=210, inventory=None, achievements=None,
            source="getmotd") -> dict:
    return {
        "schema": 2,
        "save_id": f"steam_{steam}",
        "updated_at": "2026-11-24T20:52:00",
        "identity": {"steam_id": steam, "hd2_account_id": hd2, "source": source},
        "earned": {
            "experience": experience,
            "inventory_amounts": inventory if inventory is not None else {"3608481516": 910, "897894480": 1},
            "achievements": achievements if achievements is not None else {"a": 1, "b": 1, "c": 1, "d": 1},
            "stats": {"totalEnemyKills": 0},
        },
    }


def _write_state(tmp_path: Path, ledgers: dict[str, dict], *, last_steam: str | None = None,
                 orphans: list[str] | None = None, log: str = "") -> Path:
    state = tmp_path / "state"
    saves = state / "runtime" / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    for steam, doc in ledgers.items():
        (saves / f"steam_{steam}.json").write_text(json.dumps(doc), encoding="utf-8")
    if last_steam:
        (saves / "_last.json").write_text(json.dumps({
            "last_save_id": f"steam_{last_steam}", "last_steam_id": last_steam,
            "last_hd2_account_id": None, "updated_at": "2026-11-24T20:54:40"}),
            encoding="utf-8")
    for name in orphans or []:
        d = saves / "_orphan"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("{}", encoding="utf-8")
    (state / "logs").mkdir(parents=True, exist_ok=True)
    (state / "logs" / "proxy.log").write_text(log, encoding="utf-8")
    return state


def _summarize(tmp_path: Path, doc: dict) -> dict:
    """用产品口径的摘要函数造 before 账本条目（与真实快照同构）。"""
    p = tmp_path / "one.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return sss._summarize_ledger(p)



def test_evaluate_pass_when_switch_is_clean(tmp_path: Path):
    state = _write_state(tmp_path, {STEAM_A: _ledger(STEAM_A, HD2_A),
                                    STEAM_B: _ledger(STEAM_B, HD2_B, experience=1)},
                         last_steam=STEAM_A, log=GOOD_LOG)
    after = svv.capture(state)
    before = {"saves": {f"steam_{STEAM_A}": after["saves"][f"steam_{STEAM_A}"]}}
    res = svv.evaluate(before, after, GOOD_LOG, [], STEAM_A, STEAM_B)
    assert res["verdict"] == "PASS", res
    ids = {c["id"]: c for c in res["checks"]}
    assert ids["S2-2"]["ok"] and ids["S2-3"]["ok"] and ids["S2-4"]["ok"]
    assert ids["S2-5"]["ok"] and ids["S2-6"]["ok"]


def test_alt_ledger_missing_fails_s2_2(tmp_path: Path):
    state = _write_state(tmp_path, {STEAM_A: _ledger(STEAM_A, HD2_A)},
                         last_steam=STEAM_A, log=GOOD_LOG)
    after = svv.capture(state)
    before = {"saves": {f"steam_{STEAM_A}": after["saves"][f"steam_{STEAM_A}"]}}
    res = svv.evaluate(before, after, GOOD_LOG, [], STEAM_A, STEAM_B)
    assert res["verdict"] == "FAIL"
    assert "S2-2" in res["failed"]


def test_cross_contamination_detected(tmp_path: Path):
    """主号账本被别人的进度改写（典型混档）→ S2-3 必须失败。"""
    before_doc = _ledger(STEAM_A, HD2_A)
    dirty = _ledger(STEAM_A, HD2_A, experience=999,
                    inventory={"3608481516": 1000, "897894480": 11})
    state = _write_state(tmp_path, {STEAM_A: dirty,
                                    STEAM_B: _ledger(STEAM_B, HD2_B, experience=1)},
                         last_steam=STEAM_A, log=GOOD_LOG)
    after = svv.capture(state)
    before = {"saves": {f"steam_{STEAM_A}":
                        _summarize(tmp_path, before_doc)}}
    res = svv.evaluate(before, after, GOOD_LOG, [], STEAM_A, STEAM_B)
    assert "S2-3" in res["failed"]
    detail = next(c for c in res["checks"] if c["id"] == "S2-3")["detail"]
    assert "experience" in detail


def test_identity_change_beyond_source_detected(tmp_path: Path):
    """主号账本的 identity.steam_id 被改成副号 → S2-1/S2-3 至少一项失败。"""
    b = _ledger(STEAM_A, HD2_A)
    swapped = _ledger(STEAM_A, HD2_A)
    swapped["identity"] = {"steam_id": STEAM_B, "hd2_account_id": HD2_B, "source": "getmotd"}
    state = _write_state(tmp_path, {STEAM_A: swapped,
                                    STEAM_B: _ledger(STEAM_B, HD2_B, experience=1)},
                         last_steam=STEAM_A, log=GOOD_LOG)
    after = svv.capture(state)
    before = {"saves": {f"steam_{STEAM_A}": _summarize(tmp_path, b)}}
    res = svv.evaluate(before, after, GOOD_LOG, [], STEAM_A, STEAM_B)
    assert res["verdict"] == "FAIL"


def test_source_change_is_tolerated(tmp_path: Path):
    """identity.source 从 prebind 变 getmotd 是正常校验推进，不算污染。"""
    b = _ledger(STEAM_A, HD2_A, source="prebind")
    state = _write_state(tmp_path, {STEAM_A: _ledger(STEAM_A, HD2_A, source="getmotd"),
                                    STEAM_B: _ledger(STEAM_B, HD2_B, experience=1)},
                         last_steam=STEAM_A, log=GOOD_LOG)
    after = svv.capture(state)
    before = {"saves": {f"steam_{STEAM_A}": _summarize(tmp_path, b)}}
    res = svv.evaluate(before, after, GOOD_LOG, [], STEAM_A, STEAM_B)
    ids = {c["id"]: c for c in res["checks"]}
    assert ids["S2-3"]["ok"], ids["S2-3"]["detail"]


def test_rollback_and_orphan_fail_s2_5(tmp_path: Path):
    log = GOOD_LOG + "20:53:11 ⚠ 会话回滚：steam_76561198968648344 已整档恢复到会话基准（mode=snapshot）\n"
    state = _write_state(tmp_path, {STEAM_A: _ledger(STEAM_A, HD2_A),
                                    STEAM_B: _ledger(STEAM_B, HD2_B, experience=1)},
                         last_steam=STEAM_A, orphans=["20261124-205311-steam_x-identity-switch.json"],
                         log=log)
    after = svv.capture(state)
    before = {"saves": {f"steam_{STEAM_A}": after["saves"][f"steam_{STEAM_A}"]}}
    res = svv.evaluate(before, after, log, ["20261124-205311-steam_x-identity-switch.json"],
                       STEAM_A, STEAM_B)
    assert "S2-5" in res["failed"]
    detail = next(c for c in res["checks"] if c["id"] == "S2-5")["detail"]
    assert "会话回滚" in detail and "_orphan" in detail


def test_merge_variant_log_accepted(tmp_path: Path):
    """副号档首次是 local/未识别时产品走「已合并」而非「身份切换」。"""
    log = ("身份确认：steam_76561198968648344（local/未识别）已合并进 steam_76561199881579811 并归档\n"
           "身份切换：steam_76561199881579811（已识别）→ steam_76561198968648344；**不合并**。本局写入=无\n")
    state = _write_state(tmp_path, {STEAM_A: _ledger(STEAM_A, HD2_A),
                                    STEAM_B: _ledger(STEAM_B, HD2_B, experience=1)},
                         last_steam=STEAM_A, log=log)
    after = svv.capture(state)
    before = {"saves": {f"steam_{STEAM_A}": after["saves"][f"steam_{STEAM_A}"]}}
    res = svv.evaluate(before, after, log, [], STEAM_A, STEAM_B)
    assert next(c for c in res["checks"] if c["id"] == "S2-4")["ok"], res["events"]


def test_single_trip_log_fails_s2_4(tmp_path: Path):
    log = ("⚠ 身份切换：steam_76561198968648344（已识别）→ steam_76561199881579811；**不合并**。本局写入=无\n")
    state = _write_state(tmp_path, {STEAM_A: _ledger(STEAM_A, HD2_A),
                                    STEAM_B: _ledger(STEAM_B, HD2_B, experience=1)},
                         last_steam=STEAM_A, log=log)
    after = svv.capture(state)
    before = {"saves": {f"steam_{STEAM_A}": after["saves"][f"steam_{STEAM_A}"]}}
    res = svv.evaluate(before, after, log, [], STEAM_A, STEAM_B)
    assert "S2-4" in res["failed"]


def test_parse_log_extracts_prebinds_in_order():
    ev = svv.parse_log(GOOD_LOG)
    assert ev["prebinds"] == [f"steam_{STEAM_A}", f"steam_{STEAM_B}"]
    assert (f"steam_{STEAM_A}", f"steam_{STEAM_B}") in ev["switches"]
    assert ev["rollbacks"] == []


def test_cli_json_and_exit_codes(tmp_path: Path, capsys):
    state = _write_state(tmp_path, {STEAM_A: _ledger(STEAM_A, HD2_A),
                                    STEAM_B: _ledger(STEAM_B, HD2_B, experience=1)},
                         last_steam=STEAM_A, log=GOOD_LOG)
    before = svv.capture(state)
    before_path = tmp_path / "before.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")

    rc = svv.main(["--state-dir", str(state), "--before", str(before_path),
                   "--main", STEAM_A, "--alt", STEAM_B, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["verdict"] == "PASS"

    # 删掉副号账本 → 必须非零退出，便于脚本/CI 直接卡住
    (state / "runtime" / "saves" / f"steam_{STEAM_B}.json").unlink()
    rc2 = svv.main(["--state-dir", str(state), "--before", str(before_path),
                    "--main", STEAM_A, "--alt", STEAM_B])
    assert rc2 == 1
    assert "结论：FAIL" in capsys.readouterr().out


def test_switch_regex_tolerates_legacy_wording():
    """产品 `saves.py:797` 的「旧档 X（已识别）→ 新档 Y」变体也要认得（防将来合并两条路径）。"""
    text = ("身份切换：旧档 steam_76561198968648344（已识别）→ 新档 "
            "steam_61199881579811；不合并（避免跨玩家混档）\n")
    ev = svv.parse_log(text)
    assert ev["switches"] == [("steam_76561198968648344", "steam_61199881579811")]
