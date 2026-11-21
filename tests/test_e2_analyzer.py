"""E2 抓包判定：classify() 的纯函数用例（不依赖 tshark）。"""

from __future__ import annotations

from tools.analyze_e2_capture import classify


def test_sdr_only():
    r = classify({"sdr_stun_packets": 456, "sdr_stun_bytes": 30000})
    assert r["verdict"] == "steam-sdr"
    assert r["confidence"] == "high"
    assert r["metrics"]["sdr_stun_packets"] == 456


def test_party_only_with_qos_note():
    r = classify({"dtls_records": 2380, "qos_packets": 120})
    assert r["verdict"] == "playfab-party"
    assert any("QoS" in note for note in r["notes"])


def test_dual_track():
    r = classify({"sdr_stun_packets": 10, "dtls_records": 5})
    assert r["verdict"] == "steam-sdr+playfab-party"
    assert any("双轨" in note for note in r["notes"])


def test_unknown_warns():
    r = classify({})
    assert r["verdict"] == "unknown"
    assert r["confidence"] == "low"
    assert any("既无" in note for note in r["notes"])
