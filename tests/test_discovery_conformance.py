"""发现层一致性检查：把 2026-11-21 E2 现场结论固化为可重复的检查（进程内跑）。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from discovery_conformance import _build_in_process, run_checks  # noqa: E402


def test_conformance_all_checks_pass_in_process():
    results = run_checks(_build_in_process(), unknown_policy="empty")
    failed = [r for r in results if not r["ok"]]
    assert not failed, "失败项: " + "; ".join(f"{r['check']} ({r['detail']})" for r in failed)
    assert len(results) >= 11


def test_conformance_probe_accounts_are_20_digit_numeric():
    """探针账号必须与真实 accountId 同形（20 位纯数字），否则会测不出真实崩溃风险。"""
    transport = _build_in_process()
    results = run_checks(transport, unknown_policy="empty")
    assert all(r["ok"] for r in results)
    accounts = list(transport.handler.discovery.dump()["accounts"].keys())
    assert accounts
    for acc in accounts:
        assert acc.isdigit() and len(acc) == 20, f"非 20 位纯数字探针账号: {acc!r}"


def test_conformance_with_closed_policy_passes():
    """部署 profile 用 `unknown_lobby=closed`（终态）时，同一套检查也必须全绿。"""
    results = run_checks(_build_in_process(unknown_lobby="closed"), unknown_policy="closed")
    failed = [r for r in results if not r["ok"]]
    assert not failed, "失败项: " + "; ".join(f"{r['check']} ({r['detail']})" for r in failed)


def test_conformance_with_error404_policy_passes():
    """实机 A/B 结论：未知厅回 HTTP 404 时客户端会停止轮询 → 该策略也要全绿。"""
    results = run_checks(_build_in_process(unknown_lobby="error:404"), unknown_policy="error404")
    failed = [r for r in results if not r["ok"]]
    assert not failed, "失败项: " + "; ".join(f"{r['check']} ({r['detail']})" for r in failed)
