"""`check_s4_replay.py` 的回归测试（2026-11-24 加）。

锁死 S4 的三条机器判据与两种失效路径：
  * S4-1 sha256 全等（硬）/ 仅字节数相等（无 digest 时退让）/ 两者都不等（合并或回落，SKIP）
  * S4-2 `via header:*` 必须出现、`via ip` 必须不出现
  * S4-3 引擎 0 条 SAVE
日志样例取自真实 mock 输出格式（含 `via` 与 `sha:`）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import check_s4_replay as s4

STEAM = "76561198968648344"
HD2 = "11893343864225233967"

PROG = b'{"experience":210,"selectedProgressionPackage":1}'
INV = b'{"items":[{"mixId":3608481516,"amount":1000}],"limitSettings":{}}'
STATS = b'{"stats":{"totalEnemyKills":7}}'


def _export(tmp_path: Path) -> Path:
    root = tmp_path / "export"
    pdir = root / "players" / STEAM
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "progression.json").write_bytes(PROG)
    (pdir / "inventory.json").write_bytes(INV)
    (pdir / "stats_profile.json").write_bytes(STATS)
    return root


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _line(ts, method, path, out_bytes, *, source=None, resolution=None, digest=None):
    bracket = ""
    if source:
        parts = [source]
        if resolution:
            parts.append(f"via {resolution}")
        if digest:
            parts.append(f"sha:{digest[:12]}")
        bracket = " [" + " ".join(parts) + "]"
    return "%s %-6s %s 0B -> 200 %dB%s" % (ts, method, path, out_bytes, bracket)


def _good_log() -> str:
    return "\n".join([
        _line("21:00:00.001", "GET", "/api/Pes/GetMotd", 136),
        _line("21:00:00.100", "GET", "/api/Progression", len(PROG),
              source=f"player:{STEAM}", resolution="header:prebind", digest=_sha(PROG)),
        _line("21:00:05.200", "GET", "/api/Progression/inventory", len(INV),
              source=f"player:{STEAM}", resolution="header:getmotd", digest=_sha(INV)),
        _line("21:00:05.300", "GET", "/api/Stats/profile/summary", len(STATS),
              source=f"player:{STEAM}", resolution="header:getmotd", digest=_sha(STATS)),
    ]) + "\n"


def test_parse_bracket_with_via_and_sha():
    rows = s4.parse_mock_log(_good_log())
    prog = [r for r in rows if r["path"] == "/api/Progression"][0]
    assert prog["source"] == f"player:{STEAM}"
    assert prog["resolution"] == "header:prebind"
    assert prog["digest"] == _sha(PROG)[:12]


def test_parse_line_without_bracket():
    rows = s4.parse_mock_log("21:00:00.001 GET    /health 0B -> 200 1963B\n")
    assert rows[0]["source"] == "" and rows[0]["resolution"] == "" and rows[0]["digest"] == ""


def test_pass_when_sha_matches_everything(tmp_path: Path):
    res = s4.evaluate(_good_log(), "", _export(tmp_path), STEAM)
    assert res["verdict"] == "PASS", res["checks"]
    assert res["checks"][0]["id"] == "S4-1" and res["checks"][0]["ok"]


def test_size_only_match_is_soft_pass(tmp_path: Path):
    """mock 没记 digest（旧版本）时退回字节数相等 —— 通过但标 info，提示不是硬证明。"""
    log = "\n".join([
        _line("21:00:00.100", "GET", "/api/Progression", len(PROG),
              source=f"player:{STEAM}", resolution="header:getmotd"),
        _line("21:00:05.200", "GET", "/api/Progression/inventory", len(INV),
              source=f"player:{STEAM}", resolution="header:getmotd"),
        _line("21:00:05.300", "GET", "/api/Stats/profile/summary", len(STATS),
              source=f"player:{STEAM}", resolution="header:getmotd"),
    ]) + "\n"
    res = s4.evaluate(log, "", _export(tmp_path), STEAM)
    c = res["checks"][0]
    assert res["verdict"] == "PASS" and c["level"] == "info", c


def test_content_mismatch_is_skip_with_caveat(tmp_path: Path):
    """字节/sha 都不等 ⇒ SKIP（可能是"只抬不降"合并，也可能是回落 baseline），不硬判 FAIL。"""
    wrong = b'{"experience":999}'
    log = _good_log().replace(_line("21:00:00.100", "GET", "/api/Progression", len(PROG),
                                    source=f"player:{STEAM}",
                                    resolution="header:prebind", digest=_sha(PROG)),
                              _line("21:00:00.100", "GET", "/api/Progression", len(wrong),
                                    source=f"player:{STEAM}",
                                    resolution="header:prebind", digest=_sha(wrong)))
    res = s4.evaluate(log, "", _export(tmp_path), STEAM)
    assert res["verdict"] == "SKIP"
    assert "只抬不降" in res["checks"][0]["detail"]


def test_ip_fallback_fails_s4_2(tmp_path: Path):
    log = _good_log() + _line("21:00:09.000", "GET", "/api/Progression", len(PROG),
                              source=f"player:{STEAM}", resolution="ip") + "\n"
    res = s4.evaluate(log, "", _export(tmp_path), STEAM)
    assert res["verdict"] == "FAIL" and "S4-2" in res["failed"]


def test_prebind_only_passes_with_identity_info(tmp_path: Path):
    """真实的正常流程：首屏 5 条玩家档读全是 `prebind`，GetMotd 晚 3 秒才到。

    ⇒ 只有 prebind **不是**异常（2026-11-24 实机 + 产品线双方确认）；
    S4-2 的硬判据是"所有玩家档请求靠身份头、无 ip/single/空"，身份升级只作 info。
    """
    log = chr(10).join([
        _line("21:00:00.100", "GET", "/api/Progression", len(PROG),
              source=f"player:{STEAM}", resolution="header:prebind", digest=_sha(PROG)),
        _line("21:00:05.200", "GET", "/api/Progression/inventory", len(INV),
              source=f"player:{STEAM}", resolution="header:prebind", digest=_sha(INV)),
        _line("21:00:05.300", "GET", "/api/Stats/profile/summary", len(STATS),
              source=f"player:{STEAM}", resolution="header:prebind", digest=_sha(STATS)),
    ]) + chr(10)
    res = s4.evaluate(log, "", _export(tmp_path), STEAM)
    assert res["verdict"] == "PASS", res["checks"]
    info = [c for c in res["checks"] if c["id"] == "info-identity"]
    assert info and "未观察到身份升级证据" in info[0]["detail"]


def test_health_upgrade_shows_in_identity_info(tmp_path: Path):
    """提供 /health 时，身份升级为 getmotd 只体现在 info（首屏读档在升级之前属正常）。"""
    log = chr(10).join([
        _line("21:00:00.100", "GET", "/api/Progression", len(PROG),
              source=f"player:{STEAM}", resolution="header:prebind", digest=_sha(PROG)),
        _line("21:00:05.200", "GET", "/api/Progression/inventory", len(INV),
              source=f"player:{STEAM}", resolution="header:prebind", digest=_sha(INV)),
        _line("21:00:05.300", "GET", "/api/Stats/profile/summary", len(STATS),
              source=f"player:{STEAM}", resolution="header:prebind", digest=_sha(STATS)),
    ]) + chr(10)
    health = {"players": {"last_header_source": "getmotd", "last_source": "header:getmotd",
                          "learned_ips": 1}}
    res = s4.evaluate(log, "", _export(tmp_path), STEAM, health=health)
    assert res["verdict"] == "PASS"
    info = [c for c in res["checks"] if c["id"] == "info-identity"][0]
    assert "getmotd" in info["detail"] and "正常时序" in info["detail"]


def test_single_and_empty_resolution_fail_s4_2(tmp_path: Path):
    """`single`（索引里只有一个玩家蒙的）/空（回落 baseline）也算身份路径失败。"""
    log = chr(10).join([
        _line("21:00:00.100", "GET", "/api/Progression", len(PROG),
              source=f"player:{STEAM}", resolution="single", digest=_sha(PROG)),
        _line("21:00:05.200", "GET", "/api/Progression/inventory", len(INV),
              source=f"player:{STEAM}", resolution="header:prebind", digest=_sha(INV)),
        _line("21:00:05.300", "GET", "/api/Stats/profile/summary", len(STATS),
              source=f"player:{STEAM}", resolution="header:prebind", digest=_sha(STATS)),
    ]) + chr(10)
    res = s4.evaluate(log, "", _export(tmp_path), STEAM)
    assert res["verdict"] == "FAIL" and "S4-2" in res["failed"]
    assert "single" in next(c for c in res["checks"] if c["id"] == "S4-2")["detail"]


def test_missing_endpoint_fails(tmp_path: Path):
    log = _good_log().replace("/api/Stats/profile/summary", "/api/Other")
    res = s4.evaluate(log, "", _export(tmp_path), STEAM)
    assert res["verdict"] == "FAIL" and "S4-1" in res["failed"]


def test_engine_save_lines_fail(tmp_path: Path):
    eng = "21:00:03 SAVE REWRITE steam 一处\n21:00:04 SAVE INV 补 1 条\n"
    res = s4.evaluate(_good_log(), eng, _export(tmp_path), STEAM)
    assert res["verdict"] == "FAIL" and "S4-3" in res["failed"]
    assert "2 条" in next(c for c in res["checks"] if c["id"] == "S4-3")["detail"]


def test_cli_exit_codes(tmp_path: Path, capsys):
    root = _export(tmp_path)
    mock_log = tmp_path / "mock.log"
    mock_log.write_text(_good_log(), encoding="utf-8")
    engine_log = tmp_path / "proxy.log"
    engine_log.write_text("21:00:00 engine 启动\n", encoding="utf-8")

    rc = s4.main(["--mock-log", str(mock_log), "--engine-log", str(engine_log),
                  "--export", str(root), "--steam", STEAM, "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "PASS"

    engine_log.write_text("21:00:03 SAVE REWRITE x\n", encoding="utf-8")
    rc2 = s4.main(["--mock-log", str(mock_log), "--engine-log", str(engine_log),
                   "--export", str(root), "--steam", STEAM])
    assert rc2 == 1
    assert "结论：FAIL" in capsys.readouterr().out
