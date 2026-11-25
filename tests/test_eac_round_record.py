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
    arm, warns = rr.resolve_arm("C", True, True)
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


# ---------------------------------------------------------------- 启动器脏窗口（1 s 采样派生）
def test_parse_hms():
    assert rr._parse_hms("01:43:23.456") == 1 * 3600 + 43 * 60 + 23.456
    assert rr._parse_hms("2026-11-25T01:43:23") == 1 * 3600 + 43 * 60 + 23
    assert rr._parse_hms("") is None and rr._parse_hms(None) is None
    assert rr._parse_hms("garbage") is None


def test_derive_windows_computes_launcher_to_game():
    """脏窗口 = 启动器出现 → 游戏出现（实测 ~3.5–4 s），1 s 采样足够复算。"""
    summary = {"launcher_windows": [{"pid": 21260, "first": "01:43:23.000",
                                     "last": "01:43:27.000", "samples": 5}],
               "game_windows": [{"pid": 13332, "first": "01:43:27.000",
                                 "last": "01:45:00.000", "samples": 90}]}
    d = rr.derive_windows(summary, {})
    assert d["launcher_first_seen"] == "01:43:23.000"
    assert d["game_first_seen"] == "01:43:27.000"
    assert d["launcher_to_game_s"] == 4.0


def test_derive_windows_crosschecks_reported_iat():
    summary = {"launcher_windows": [{"pid": 1, "first": "01:43:23.000", "last": "x", "samples": 5}],
               "game_windows": [{"pid": 2, "first": "01:43:27.200", "last": "y", "samples": 90}]}
    d = rr.derive_windows(summary, {"iat_alive_ms": "3800"})
    cc = d["iat_alive_ms_crosscheck"]
    assert cc["reported_s"] == 3.8 and cc["sampler_s"] == 4.2
    assert cc["consistent"] is True          # 差 0.4 s < 1.5 s 容差


def test_derive_windows_flags_inconsistent_reported_iat():
    summary = {"launcher_windows": [{"pid": 1, "first": "01:43:23.000", "last": "x", "samples": 5}],
               "game_windows": [{"pid": 2, "first": "01:43:27.000", "last": "y", "samples": 90}]}
    d = rr.derive_windows(summary, {"iat_alive_ms": "150"})   # 早期误传的 100~300 ms
    assert d["iat_alive_ms_crosscheck"]["consistent"] is False


def test_derive_windows_without_launcher():
    d = rr.derive_windows({"game_windows": [{"pid": 1, "first": "01:43:27.000",
                                             "last": "y", "samples": 9}]}, {})
    assert "launcher_to_game_s" not in d


# ---------------------------------------------------------------- C 臂成立需要"钩子真命中"
def test_c_arm_requires_hook_installed():
    arm, warns = rr.resolve_arm("C", False, True)
    assert arm == "A(deg)"


def test_c_arm_requires_hook_fired():
    """装上了但没命中 ⇒ 补丁在没被执行的槽上，启动器行为不变 ⇒ 降级 C(weak)。"""
    arm, warns = rr.resolve_arm("C", True, False)
    assert arm == "C(weak)"
    assert any("不能当" in w for w in warns)


def test_c_arm_valid_only_when_installed_and_fired():
    arm, warns = rr.resolve_arm("C", True, True)
    assert arm == "C" and warns == []


def test_c_arm_without_fired_field_warns():
    arm, warns = rr.resolve_arm("C", True, None)
    assert arm == "C"
    assert any("hook-fired" in w for w in warns)


def test_c_weak_arm_accepted_directly():
    arm, warns = rr.resolve_arm("C(weak)", None, None)
    assert arm == "C(weak)"
    assert any("不得当作" in w for w in warns)


# ---------------------------------------------------------------- 四臂阶梯：B0 / B1 的降级护栏
def test_b0_requires_handle_obtained():
    """B0 = 改启动器 + 拿句柄（不注入）。拿不到句柄 ⇒ 等价于 C，记 B0(deg)。"""
    arm, warns = rr.resolve_arm("B0", True, True, False)
    assert arm == "B0(deg)"
    assert any("退化成 C" in w for w in warns)


def test_b0_valid_with_handle():
    arm, warns = rr.resolve_arm("B0", True, True, True)
    assert arm == "B0" and warns == []


def test_b1_requires_injection():
    """B1 = 再注入。注入没发生 ⇒ 等价于 B0，记 B1(deg)。"""
    arm, warns = rr.resolve_arm("B1", True, True, True, False)
    assert arm == "B1(deg)"
    assert any("退化成 B0" in w for w in warns)


def test_b1_valid_with_injection():
    arm, warns = rr.resolve_arm("B1", True, True, True, True)
    assert arm == "B1" and warns == []


def test_b1_without_inject_field_warns():
    arm, warns = rr.resolve_arm("B1", True, True, True, None)
    assert arm == "B1"
    assert any("inject-done" in w for w in warns)


def test_four_arm_ladder_is_ordered():
    """四臂阶梯：A(未改) → C(改启动器) → B0(+拿句柄) → B1(+注入)，每级只加一个变量。"""
    ladder = ["A", "C", "B0", "B1"]
    for a in ladder:
        assert a in rr.VALID_ARMS


def test_m2_kernel_arm_accepted_with_scope_warning():
    """内核探查轮：不属于 A/C/B 阶梯，只回答"这一轮四通道有没有变化"。"""
    arm, warns = rr.resolve_arm("M2-kernel", None, None)
    assert arm == "M2-kernel"
    assert any("不属于 A/C/B 阶梯" in w for w in warns)
    assert any("内核组件本地" in w for w in warns)
