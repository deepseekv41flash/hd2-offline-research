"""`compare_baseline_vs_run.py` 的离线测试：四条信号的 红/黄/绿 判定。

为什么要锁：将来"注入后被感知没有"全靠这台判定，阈值一改就会各说各话；
而且 preflight（72 B 自检）绝不能混进统计（第 3 轮已踩过）。
"""

from __future__ import annotations

import json
from pathlib import Path

import compare_baseline_vs_run as cb

UA = ("EOS-SDK/1.19.1.2-53289219 (Windows/10.0.26100.10002.64bit) "
      "Helldivers 2/Developer Build (1.8.47139.0)")


def _cap(tmp_path: Path, msgs: list[dict], telemetry: int = 0) -> Path:
    """造一轮 capture 目录：msgs 里每项给 len/ts/ua（可选）。"""
    d = tmp_path / "cap"
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, m in enumerate(msgs):
        lines.append(json.dumps({
            "t": m.get("ts", "10:00:%02d" % (i * 12)), "host": "api.epicgames.dev",
            "method": "POST", "path": "/anticheat/v8/games/client/message/",
            "req_len": m["len"], "status": 200, "resp_len": m.get("resp", 1289),
            "ua": m.get("ua", UA),
            "req_headers": {"user-agent": m.get("ua", UA),
                            "x-eos-version": m.get("eos", "1.19.1.2-53289219"),
                            "x-epic-correlation-id": m.get("cid", "EOS-abc")},
        }, ensure_ascii=False))
    for _ in range(telemetry):
        lines.append(json.dumps({"t": "10:00:30", "host": "api.epicgames.dev",
                                 "method": "POST",
                                 "path": "/telemetry/data/api/v1/public/data?AppID=EOSSDK.PhaseRelease.ReleaseBuild",
                                 "req_len": 100, "status": 200, "resp_len": 2,
                                 "req_headers": {"x-epic-correlation-id": "EOS-abc"}}))
    (d / "mitm.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def _base(msgs: list[dict]) -> dict:
    return cb.freeze(cb.load_run(_cap(Path(__import__("tempfile").mkdtemp()), msgs)))


def test_preflight_excluded_from_count(tmp_path: Path):
    """72 B 且无 UA/无 x-eos-version 的自检必须被排除（第 3/4 轮踩过的坑）。"""
    d = tmp_path / "cap"
    d.mkdir(parents=True)
    rows = [
        {"t": "10:00:00", "host": "api.epicgames.dev", "method": "POST",
         "path": "/anticheat/v8/games/client/message/", "req_len": 72, "resp_len": 919,
         "ua": None, "req_headers": {}},
        {"t": "10:00:05", "host": "api.epicgames.dev", "method": "POST",
         "path": "/anticheat/v8/games/client/message/", "req_len": 388, "resp_len": 1289,
         "ua": UA, "req_headers": {"user-agent": UA, "x-eos-version": "1.19.1.2"}},
        {"t": "10:00:10", "host": "api.epicgames.dev", "method": "POST",
         "path": "/anticheat/v8/games/client/message/", "req_len": 4904, "resp_len": 1409,
         "ua": UA, "req_headers": {"user-agent": UA, "x-eos-version": "1.19.1.2"}},
    ]
    (d / "mitm.jsonl").write_text(("\n").join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert len(cb.load_run(d)["v8"]) == 2


def test_identical_run_is_green(tmp_path: Path):
    msgs = [{"len": 388}, {"len": 5000}, {"len": 9000}]
    base = _base(msgs)
    res = cb.compare(base, cb.load_run(_cap(tmp_path / "b", msgs)))
    assert res["overall"] == "绿", res["rows"]


def test_ua_change_is_red(tmp_path: Path):
    base = _base([{"len": 388}])
    run = cb.load_run(_cap(tmp_path / "x", [{"len": 388, "ua": UA.replace("Developer Build", "Release Build")}]))
    res = cb.compare(base, run)
    assert res["overall"] == "红"
    assert any(r["signal"] == "User-Agent" and r["verdict"] == "红" for r in res["rows"])


def test_new_period_is_red(tmp_path: Path):
    """基线 12 s 一条；本轮出现 3 s 的固定周期 ⇒ 红。"""
    base_msgs = [{"len": 388, "ts": "10:00:%02d" % (i * 12)} for i in range(5)]
    base = _base(base_msgs)
    fast = [{"len": 388, "ts": "10:01:%02d" % (i * 3)} for i in range(8)]
    res = cb.compare(base, cb.load_run(_cap(tmp_path / "y", fast)))
    row = next(r for r in res["rows"] if r["signal"] == "新增周期")
    assert row["verdict"] == "红", row
    assert "3.0" in row["detail"]


def test_volume_new_magnitude_is_red(tmp_path: Path):
    base = _base([{"len": 388}, {"len": 33280}])
    res = cb.compare(base, cb.load_run(_cap(tmp_path / "z", [{"len": 388}, {"len": 90000}])))
    row = next(r for r in res["rows"] if r["signal"] == "体积分布")
    assert row["verdict"] == "红", row


def test_big_msg_doubled_is_red(tmp_path: Path):
    base = _base([{"len": 9000}, {"len": 388}])
    run_msgs = [{"len": 9000}, {"len": 9000}, {"len": 9000}, {"len": 388}]
    res = cb.compare(base, cb.load_run(_cap(tmp_path / "w", run_msgs)))
    assert next(r for r in res["rows"] if r["signal"] == "体积分布")["verdict"] == "红"


def test_volume_median_shift_is_yellow(tmp_path: Path):
    base = _base([{"len": 1000}, {"len": 4000}, {"len": 8000}])
    run = [{"len": 1600}, {"len": 5600}, {"len": 9000}]
    res = cb.compare(base, cb.load_run(_cap(tmp_path / "v", run)))
    assert next(r for r in res["rows"] if r["signal"] == "体积分布")["verdict"] == "黄"


def test_correlation_id_row_is_adjacency_only(tmp_path: Path):
    """correlation-id 只作关联键，不影响总体判定。"""
    base = _base([{"len": 388}])
    res = cb.compare(base, cb.load_run(_cap(tmp_path / "t", [{"len": 388}], telemetry=2)))
    row = next(r for r in res["rows"] if r["signal"] == "correlation-id")
    assert row["verdict"] == "关联" and "交集" in row["detail"]
    assert res["overall"] == "绿"


def test_freeze_writes_expected_fields(tmp_path: Path):
    d = _cap(tmp_path, [{"len": 388}, {"len": 33280}])
    base = cb.freeze(cb.load_run(d))
    for k in ("count", "ua_set", "req_len_max", "big_msg_count", "periods_s", "items"):
        assert k in base
    assert base["req_len_max"] == 33280 and base["count"] == 2
    assert base["items"][0]["ua"].endswith("(1.8.47139.0)")


def test_preflight_excluded_by_headers_not_filename(tmp_path: Path):
    """自检识别要按**头**判（无 UA/无 x-eos-version），不能靠文件名。

    第 4 轮实测：落盘名里没有 PREFLIGHT 字样，靠文件名会漏判，
    自检的 110 s 间隔把"抖动范围"判成黄。
    """
    d = tmp_path / "cap"
    d.mkdir(parents=True)
    lines = [
        # 自检：72 B、无 UA、无 x-eos-version
        json.dumps({"t": "10:00:00", "host": "api.epicgames.dev", "method": "POST",
                    "path": "/anticheat/v8/games/client/message/", "req_len": 72,
                    "resp_len": 919, "ua": None, "req_headers": {}}),
        # 真实消息
        json.dumps({"t": "10:02:00", "host": "api.epicgames.dev", "method": "POST",
                    "path": "/anticheat/v8/games/client/message/", "req_len": 388,
                    "resp_len": 1289, "ua": UA,
                    "req_headers": {"user-agent": UA, "x-eos-version": "1.19.1.2"}}),
    ]
    (d / "mitm.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    run = cb.load_run(d)
    assert len(run["v8"]) == 1, run["v8"]
    assert run["v8"][0]["dt_ms"] is None      # 不应出现 120 s 的假间隔


def test_jitter_does_not_trip_new_period(tmp_path: Path):
    """抖动（基线 5 s，本轮 4 s 也在桶里）不该判红 —— 否则干净轮就误报。"""
    base = {"items": [{"req_len": 388, "dt_ms": 5000} for _ in range(4)],
            "ua_set": [UA], "req_len_max": 1000, "big_msg_count": 0,
            "req_len_median": 388, "periods_s": [5.0], "dt_ms_range": [5000, 5000]}
    msgs = [{"len": 388, "ts": "10:00:%02d" % (i * 4)} for i in range(6)]
    res = cb.compare(base, cb.load_run(_cap(tmp_path / "j", msgs)))
    assert next(r for r in res["rows"] if r["signal"] == "新增周期")["verdict"] != "红"


def test_faster_period_is_red(tmp_path: Path):
    """12 s 变 3 s（明显更快）才是"被追问"的红。"""
    base = {"items": [{"req_len": 388, "dt_ms": 12000} for _ in range(4)],
            "ua_set": [UA], "req_len_max": 1000, "big_msg_count": 0,
            "req_len_median": 388, "periods_s": [12.0], "dt_ms_range": [12000, 12000]}
    msgs = [{"len": 388, "ts": "10:01:%02d" % (i * 3)} for i in range(5)]
    res = cb.compare(base, cb.load_run(_cap(tmp_path / "k", msgs)))
    assert next(r for r in res["rows"] if r["signal"] == "新增周期")["verdict"] == "红"
