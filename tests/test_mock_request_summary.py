"""mock 请求日志汇总脚本的回归测试（2026-11-24 与 MITM 线做 mock 验收时新增）。"""

from __future__ import annotations

from pathlib import Path

import mock_request_summary as mrs

SAMPLE = """\
11:39:46.351 GET    /api/Progression 0B -> 200 1500B [exact] via 127.0.0.1
11:39:46.390 GET    /api/Progression/inventory 0B -> 200 3267B [exact] via 127.0.0.1
11:39:46.429 GET    /api/Stats/profile/summary 0B -> 200 644B [exact] via 127.0.0.1
11:39:46.463 POST   /api/Operation/Mission/end 2B -> 200 1614B [state:mission_end] via 127.0.0.1
11:39:46.500 GET    /health 0B -> 200 1742B [health] via 127.0.0.1
11:39:46.600 POST   /api/lobby/6f1c6d1e-1111-2222-3333-444455556666.r-20260323/member 4B -> 200 1236B [discovery:lobby_member] via 127.0.0.1
11:39:46.700 POST   /api/lobby/6f1c6d1e-1111-2222-3333-444455556666.r-20260323/member 4B -> 200 1236B [discovery:lobby_member] via 127.0.0.1
not-a-log-line
"""


def test_parse_log_extracts_fields():
    rows = mrs.parse_log(SAMPLE)
    assert len(rows) == 7          # 非法行被跳过
    first = rows[0]
    assert first["method"] == "GET"
    assert first["path"] == "/api/Progression"
    assert first["resp_bytes"] == 1500
    assert first["source"] == "exact"
    assert rows[3]["source"] == "state"    # state:mission_end → 取冒号前


def test_normalize_collapses_uuid_and_long_ids():
    assert mrs.normalize("/api/lobby/6f1c6d1e-1111-2222-3333-444455556666.r-20260323/member") == \
        "/api/lobby/{uuid}/member"
    assert mrs.normalize("/api/Account/Keys/76561198968648344") == "/api/Account/Keys/{id}"
    assert mrs.normalize("/api/Progression?x=1") == "/api/Progression"


def test_summarize_groups_and_skips_noise_by_default():
    rows = mrs.parse_log(SAMPLE)
    summary = mrs.summarize(rows)
    paths = {s["path"] for s in summary["endpoints"]}
    assert "/health" not in paths                     # 探针流量默认不计
    member = next(s for s in summary["endpoints"] if s["path"] == "/api/lobby/{uuid}/member")
    assert member["count"] == 2                       # 同类请求被聚合成一条
    assert summary["unique_endpoints"] == len(summary["endpoints"])


def test_summarize_include_noise_and_writes():
    rows = mrs.parse_log(SAMPLE)
    summary = mrs.summarize(rows, include_noise=True)
    assert any(s["path"] == "/health" for s in summary["endpoints"])
    writes = {(s["method"], s["path"]) for s in summary["writes"]}
    assert ("POST", "/api/Operation/Mission/end") in writes
    assert ("POST", "/api/lobby/{uuid}/member") in writes
    assert ("GET", "/api/Progression") not in writes


def test_format_text_has_headers_and_rows():
    summary = mrs.summarize(mrs.parse_log(SAMPLE))
    text = mrs.format_text(summary)
    assert "mock 请求汇总" in text
    assert "/api/Progression" in text
    assert "写操作" in text


def test_cli_reads_file_and_outputs_json(tmp_path: Path, capsys):
    log = tmp_path / "mock.log"
    log.write_text(SAMPLE, encoding="utf-8")
    rc = mrs.main([str(log), "--json"])
    assert rc == 0
    import json as _json
    data = _json.loads(capsys.readouterr().out)
    assert data["total_lines"] == 7
