"""`eac_round_record.py` 的离线测试：三臂身份 + 钩子没装上时的降级护栏。

为什么要锁：C 臂的唯一价值是"启动器真的被改过"；如果 hook 没装上却仍记成 C，
就会把 A 当控制臂用，B 的异常会被错误归因 —— 这个降级必须由工具强制。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import eac_round_record as rr

UA = ("EOS-SDK/1.19.1.2-53289219 (Windows/10.0.26100.10002.64bit) "
      "Helldivers 2/Developer Build (1.8.47139.0)")


def test_resolve_arm_c_with_hook_ok():
    arm, warns = rr.resolve_arm("C", True)
    assert arm == "C" and warns == []


def test_resolve_arm_c_without_hook_downgrades_to_a_deg():
    arm, warns = rr.resolve_arm("C", False)
    assert arm == "A(deg)"
    assert any("不得当控制臂" in w for w in warns)


def test_resolve_arm_c_with_unknown_hook_warns():
    arm, warns = rr.resolve_arm("C", None)
    assert arm == "C"
    assert any("hook-installed" in w for w in warns)


def test_resolve_arm_rejects_unknown():
    with pytest.raises(ValueError):
        rr.resolve_arm("D", True)


@pytest.fixture()
def _cap(tmp_path: Path) -> Path:
    d = tmp_path / "cap"
    d.mkdir()
    rows = [{"t": "01:22:30", "host": "api.epicgames.dev", "method": "POST",
             "path": "/anticheat/v8/games/client/message/", "req_len": 388,
             "status": 200, "resp_len": 1289, "ua": UA,
             "req_headers": {"user-agent": UA, "x-eos-version": "1"}},
            {"t": "01:22:36", "host": "api.epicgames.dev", "method": "POST",
             "path": "/anticheat/v8/games/client/message/", "req_len": 4904,
             "status": 200, "resp_len": 1289, "ua": UA,
             "req_headers": {"user-agent": UA, "x-eos-version": "1"}}]
    (d / "mitm.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return d


def test_build_record_counts_and_series(_cap: Path):
    rec = rr.build_record("C", _cap, times={"game_appeared": "01:21:35"})
    assert rec["counts"] == {"v8": 2, "v6": 0, "telemetry": 0}
    assert rec["v8"]["req_len_series"] == [388, 4904]
    assert rec["v8"]["ua_set"] == [UA]
    assert rec["times"]["game_appeared"] == "01:21:35"
    assert "liveness" not in rec


def test_build_record_with_liveness(_cap: Path, tmp_path: Path):
    live = tmp_path / "w.jsonl"
    samples = [
        {"t": "01:21:40", "game": [{"pid": 1}], "launcher": [], "eac_pid": [2],
         "eac_service": "running",
         "conns": {"total": 3, "established": 3, "remote443": 0,
                   "remote_ips": ["127.0.0.1"]}},
        {"t": "01:21:45", "game": [{"pid": 1}], "launcher": [], "eac_pid": [2],
         "eac_service": "running",
         "conns": {"total": 4, "established": 4, "remote443": 0,
                   "remote_ips": ["127.0.0.1"]}},
    ]
    live.write_text("\n".join(json.dumps(s) for s in samples) + "\n", encoding="utf-8")
    rec = rr.build_record("C", _cap, live)
    # 本轮只有 2 条 v8 < 默认阈值 3 ⇒ 链路活但流量不足（code 2），这是**预期**行为
    assert rec["liveness"]["verdict"]["label"] == "link-alive-but-traffic-missing"
    assert rec["liveness"]["verdict"]["code"] == 2
    assert rec["liveness"]["summary"]["conns_peak"] == 4
    assert rec["times"]["game_appeared_from_sampler"] == "01:21:40"


def test_main_writes_record_and_reports_downgrade(_cap: Path, tmp_path: Path):
    out = tmp_path / "rec.json"
    code = rr.main(["--arm", "C", "--hook-installed", "false", "--run", str(_cap),
                    "--out", str(out)])
    assert code == 0
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["arm"] == "A(deg)"
    assert any("不得当控制臂" in w for w in rec["warnings"])


def test_main_rejects_bad_arm(_cap: Path, tmp_path: Path):
    assert rr.main(["--arm", "Z", "--run", str(_cap), "--out", str(tmp_path / "x.json")]) == 2


def test_void_arm_is_accepted_and_flagged():
    """作废轮次（游戏没起来等）只留痕、不进任何臂。"""
    arm, warns = rr.resolve_arm("void", None)
    assert arm == "void"
    assert any("不进任何臂" in w for w in warns)
