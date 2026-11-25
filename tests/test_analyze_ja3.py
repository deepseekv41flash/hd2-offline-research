"""`analyze_ja3.py` 的离线测试：JA3 集合稳定性（第 5 观察量，辅助信号不作红判据）。

背景：七轮实测 api.epicgames.dev 的 JA3 集合完全相同（3 个指纹、分布稳定），
所以"集合变化"是高特异性告警；但集合一致**不能**证明"没被感知"。
"""

from __future__ import annotations

import json
from pathlib import Path

import analyze_ja3 as ja

HDR = "time_epoch\tdst_ip\tdst_port\tsni\tja3\tja3_full\ttls_version"
A = "d7268fba5c8ebdc86121861931c04402"
B = "fae0e5d973c96ae1888b99538efa0363"
C = "1ce565f411fbc7412e3c5ba0bcfd62ab"


def _tsv(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    d = tmp_path / "cap"
    d.mkdir(parents=True, exist_ok=True)
    lines = [HDR] + [f"1\t127.0.0.1\t18092\t{sni}\t{j}\tx\t0x0303" for sni, j in rows]
    (d / "ja3-sni.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def test_read_filters_by_sni(tmp_path: Path):
    d = _tsv(tmp_path, [("api.epicgames.dev", A), ("bing.com", B),
                        ("api.epicgames.dev", B)])
    rows = ja.read_ja3(d / "ja3-sni.tsv")
    assert [r["ja3"] for r in rows] == [A, B]


def test_read_missing_file_is_empty(tmp_path: Path):
    assert ja.read_ja3(tmp_path / "nope.tsv") == []


def test_summarize_counts_and_share():
    s = ja.summarize([{"ja3": A}, {"ja3": A}, {"ja3": B}])
    assert s["connections"] == 3 and s["distinct"] == 2
    assert s["ja3"][0] == {"ja3": A, "count": 2, "share": 0.667}


def test_compare_same_set():
    ref = {"ja3": [{"ja3": A}, {"ja3": B}]}
    v = ja.compare_sets(ja.summarize([{"ja3": A}, {"ja3": B}]), ref)
    assert v["code"] == 0 and v["label"] == "ja3-same-set"


def test_compare_new_fingerprint_is_alert():
    """出现参考外指纹 ⇒ 强告警（客户端网络栈被换/重启）。"""
    ref = {"ja3": [{"ja3": A}]}
    v = ja.compare_sets(ja.summarize([{"ja3": A}, {"ja3": C}]), ref)
    assert v["code"] == 1 and v["label"] == "ja3-set-changed"
    assert v["added"] == [C]


def test_compare_missing_fingerprint_is_alert():
    ref = {"ja3": [{"ja3": A}, {"ja3": B}]}
    v = ja.compare_sets(ja.summarize([{"ja3": A}]), ref)
    assert v["code"] == 1 and v["label"] == "ja3-missing-fingerprint"
    assert v["missing"] == [B]


def test_compare_no_data():
    v = ja.compare_sets(ja.summarize([]), {"ja3": [{"ja3": A}]})
    assert v["code"] == 3 and v["label"] == "no-data"


def test_freeze_and_compare_roundtrip(tmp_path: Path):
    d1 = _tsv(tmp_path / "r1", [("api.epicgames.dev", A), ("api.epicgames.dev", B)])
    out = tmp_path / "ref.json"
    assert ja.main(["--freeze", "--runs", str(d1), "--out", str(out)]) == 0
    ref = json.loads(out.read_text(encoding="utf-8"))
    assert {x["ja3"] for x in ref["ja3"]} == {A, B}
    assert ref["per_round"]
    # 同集合 ⇒ 0
    assert ja.main(["--run", str(d1), "--reference", str(out)]) == 0
    # 新指纹 ⇒ 1
    d2 = _tsv(tmp_path / "r2", [("api.epicgames.dev", C)])
    assert ja.main(["--run", str(d2), "--reference", str(out)]) == 1
