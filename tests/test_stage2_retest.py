"""阶段2 复测工具（tools/stage2_retest.py）：自检/快照/判据的离线单测。

为什么值得单测：判据是**增量**规则，写错方向（`keys_miss` 该「不增」还是「增」）或者
中途状态重置没识别出来，会让实机结论反向。这里把每条规则钉死。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import stage2_retest as rt  # noqa: E402


# ---------------------------------------------------------------- hosts

def test_hosts_mapping_active_line():
    text = "127.0.0.1 localhost\n100.67.88.219 api.live.prod.thehelldiversgame.com # HD2-STAGE2-TUNNEL\n"
    assert rt.hosts_mapping(text) == "100.67.88.219"


def test_hosts_mapping_ignores_commented():
    text = "#100.67.88.219 api.live.prod.thehelldiversgame.com\n127.0.0.1 localhost\n"
    assert rt.hosts_mapping(text) is None


def test_hosts_mapping_case_and_extra_hosts():
    text = "10.0.0.5 other.example API.LIVE.PROD.THEHELLDIVERSGAME.COM\n"
    assert rt.hosts_mapping(text) == "10.0.0.5"


def test_hosts_mapping_returns_wrong_ip_too():
    """指向旧实验 IP 时也要报出来 —— check 靠这个区分「没有」和「指错」。"""
    text = "100.67.169.17 api.live.prod.thehelldiversgame.com # hd2proxy\n"
    assert rt.hosts_mapping(text) == "100.67.169.17"


# ---------------------------------------------------------------- 日志证据

MOCK_LOG = """\
15:34:08.939   SHAPE POST   /api/lobby/aa.r-20260323/member 4B sha=74234e98 format=json
15:34:08.940 POST   /api/lobby/aa.r-20260323/member 4B -> 200 369B [discovery:lobby_member] via 127.0.0.1
15:35:39.034   SHAPE POST   /api/lobby/aa.r-20260323/member 4B sha=74234e98 format=json
15:35:39.034 POST   /api/lobby/aa.r-20260323/member 4B -> 200 1183B [discovery:lobby_member] via 127.0.0.1
15:35:40.010 POST   /api/lobby/aa.r-20260323/member 4B -> 200 1236B [discovery:lobby_member] via 127.0.0.1
15:36:01.000 POST   /api/lobby/aa.r-20260323/member 4B -> 200 1237B [discovery:lobby_member] via 127.0.0.1
"""

# 「理想一轮」的日志里不能有拒绝行：L02 要求 rejected 必须为 0。
MOCK_LOG_REJECTED = MOCK_LOG + (
    "15:36:00.000 POST   /api/lobby/bb.r-20260323/member 4B -> 200 2B "
    "[discovery:lobby_member:rejected] via 127.0.0.1\n")


def test_parse_member_evidence_counts_and_sizes():
    ev = rt.parse_member_evidence(MOCK_LOG_REJECTED)
    assert ev["member_ok"] == 4
    assert ev["member_rejected"] == 1
    assert ev["max_body"] == 1237
    assert ev["real_bodies"] == 3                     # 1183/1236/1237（369 是空壳档，不计）
    assert ev["single_player_bodies"] == 1            # 1183 = 1 人厅体（实测 1158–1184）
    assert ev["two_player_bodies"] == 2               # 1236/1237 = 2 人厅体
    assert ev["two_player_max"] == 1237


def test_parse_member_evidence_empty_log():
    ev = rt.parse_member_evidence("")
    assert ev == {"member_ok": 0, "member_rejected": 0, "max_body": 0,
                  "real_bodies": 0, "single_player_bodies": 0,
                  "two_player_bodies": 0, "two_player_max": 0}


def test_two_player_threshold_is_above_single_player_max():
    """回归：1 人厅体实测到过 1184 B，阈值若 ≤1184 会把单人误判成双人（假 PASS）。"""
    assert rt.TWO_PLAYER_MIN_BODY > 1184
    assert rt.TWO_PLAYER_MIN_BODY <= 1236      # 不能高到把实测的 2 人体 1236 排除
    assert rt.TWO_PLAYER_MIN_BODY > rt.REAL_BODY_MIN


def test_parse_real_e3_round2_log_sizes():
    """用 E3 第二轮的体积直方图（1 人 1158–1184 / 2 人 1236–1237）钉住分类。"""
    log = "".join(
        f"POST   /api/lobby/a/member 4B -> 200 {sz}B [discovery:lobby_member] via 127.0.0.1\n"
        for sz in (344, 369, 1158, 1184, 1236, 1237, 1448))
    ev = rt.parse_member_evidence(log)
    assert ev["two_player_bodies"] == 3        # 1236/1237/1448（任务开始也是 2 人）
    assert ev["single_player_bodies"] == 2     # 1158/1184
    assert ev["real_bodies"] == 5              # 上面 5 个 + 344/369 不计（<1000）


def test_judge_single_player_only_is_warn_not_pass_evidence():
    """只有单人厅体 ⇒ L01 只能 WARN（未证双人），不能当通过证据。"""
    log = ("POST   /api/lobby/aa/member 4B -> 200 1184B "
           "[discovery:lobby_member] via 127.0.0.1\n")
    result = rt.judge_snapshots(BEFORE, AFTER_IDEAL, log_text=log)
    assert _status(result, "L01") == rt.WARN


# ---------------------------------------------------------------- judge

def _health(**counts) -> dict:
    """构造一份只含 discovery 块的 /health（未知键默认 0，便于逐条改）。"""
    stats = {"login": 0, "put": 0, "get": 0, "join": 0, "join_rejected": 0,
             "keys_hit": 0, "keys_bind_hit": 0, "keys_miss": 0, "keys_waited": 0}
    stats.update({k: v for k, v in counts.items() if k in stats})
    disc = {"stats": stats}
    for key in ("accounts", "identities", "keys_known", "bindings"):
        disc[key] = counts.get(key, 0)
    return {"ok": True, "discovery": disc}


BEFORE = _health()

# 理想一轮：对方重启后立刻点加入 → A 等待命中（keys_waited）或 B 绑定命中；
# 双方登录、双方 PUT、双方查 Keys、成员被接受。
AFTER_IDEAL = _health(
    accounts=2, identities=2, keys_known=2, bindings=2,
    login=2, put=2, get=2, join=2, join_rejected=0,
    keys_hit=1, keys_bind_hit=1, keys_waited=1, keys_miss=0,
)


def _status(result: dict, cid: str) -> str:
    return next(c["status"] for c in result["criteria"] if c["id"] == cid)


def test_judge_ideal_round_passes():
    result = rt.judge_snapshots(BEFORE, AFTER_IDEAL, log_text=MOCK_LOG)
    assert result["verdict"] == rt.VERDICT_PASS
    assert result["deltas"]["keys_bind_hit"] == 1
    assert result["deltas"]["join"] == 2
    assert _status(result, "C01") == rt.OK
    assert _status(result, "L01") == rt.OK


def test_judge_without_log_is_incomplete():
    result = rt.judge_snapshots(BEFORE, AFTER_IDEAL)
    assert result["verdict"] == rt.VERDICT_INCOMPLETE
    assert _status(result, "L01") == rt.SKIP


def test_judge_no_gap_reproduced_fails_c01():
    """没复现缝隙（只有 keys_hit）⇒ C01 fail：含义是「未证明」，不是「功能坏了」。"""
    after = _health(accounts=2, identities=2, bindings=1, login=2, put=2, get=2,
                    join=2, keys_hit=1)
    result = rt.judge_snapshots(BEFORE, after, log_text=MOCK_LOG)
    assert _status(result, "C01") == rt.FAIL
    assert result["verdict"] == rt.VERDICT_FAIL


def test_judge_keys_miss_increase_fails():
    after = _health(accounts=2, identities=2, bindings=1, login=2, put=2,
                    get=3, join=2, keys_bind_hit=1, keys_miss=1)
    result = rt.judge_snapshots(BEFORE, after, log_text=MOCK_LOG)
    assert _status(result, "C02") == rt.FAIL
    assert result["verdict"] == rt.VERDICT_FAIL


def test_judge_member_rejected_increase_fails():
    after = _health(accounts=2, identities=2, bindings=1, login=2, put=2,
                    get=2, join=2, join_rejected=1, keys_bind_hit=1)
    result = rt.judge_snapshots(BEFORE, after, log_text=MOCK_LOG_REJECTED)
    assert _status(result, "C03") == rt.FAIL
    assert _status(result, "L02") == rt.FAIL


def test_judge_detects_midtest_state_reset():
    """中途清过状态 ⇒ 增量失真，V01 必须 fail（否则会把「全 0」误读成「没发生」）。"""
    before = _health(accounts=5, identities=3, login=9, bindings=3)
    after = _health(accounts=0, identities=0, login=0)
    result = rt.judge_snapshots(before, after, log_text="")
    assert _status(result, "V01") == rt.FAIL
    assert result["verdict"] == rt.VERDICT_FAIL


def test_judge_single_side_login_warns():
    after = _health(accounts=1, identities=1, bindings=1, login=1, put=1, get=1,
                    join=1, keys_waited=1)
    result = rt.judge_snapshots(BEFORE, after)
    assert _status(result, "C04") == rt.WARN


def test_judge_join_not_accepted_fails_c06():
    after = _health(accounts=2, identities=2, bindings=1, login=2, put=2, get=1,
                    join=0, keys_waited=1)
    result = rt.judge_snapshots(BEFORE, after)
    assert _status(result, "C06") == rt.FAIL


def test_judge_two_player_body_missing_fails_l01():
    log = "POST   /api/lobby/aa/member 4B -> 200 369B [discovery:lobby_member] via 127.0.0.1\n"
    result = rt.judge_snapshots(BEFORE, AFTER_IDEAL, log_text=log)
    assert _status(result, "L01") == rt.FAIL


def test_snapshot_roundtrip_and_summary():
    health = _health(accounts=1, bindings=1, login=1)
    snap = rt.make_snapshot(health, {"not_after": "2026-11-23T12:32:03+00:00",
                                     "remaining_hours": 6.4},
                            endpoint="100.67.88.219:443")
    again = rt.discover_summary(snap)
    assert again["accounts"] == 1 and again["login"] == 1
    assert rt.discover_summary(health) == again  # 裸 /health 也认


# ---------------------------------------------------------------- check（全注入，不碰系统）

def _checks_by_id(checks: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in checks}


def test_run_checks_all_green_with_injections(tmp_path):
    # 用真实签发的 CA 文件（certs.generate 每次新 CA，这里只需能解析）
    from mock import certs

    cert_dir = tmp_path / "certs"
    certs.generate(cert_dir, host=rt.DOMAIN, days=2)
    ca = rt.load_ca_info(cert_dir)
    health = _health()  # 净空基线

    checks = rt.run_checks(
        cert_dir=cert_dir,
        trust_checker=lambda thumb, scope="CurrentUser": thumb == ca["thumbprint_sha1"],
        hosts_reader=lambda: f"{rt.DEFAULT_RELAY_IP} {rt.DOMAIN} # HD2-STAGE2-TUNNEL\n",
        crl_sha_getter=lambda port: rt.sha256_file(cert_dir / "ca.crl"),
        tcp_probe=lambda host, port, timeout=1.5: True,
        health_fetcher=lambda **kw: (health, {
            "not_after": ca["not_after"], "remaining_hours": 6.0}),
    )
    by_id = _checks_by_id(checks)
    assert {c["status"] for c in checks} == {rt.OK}, [
        (c["id"], c["status"], c["detail"]) for c in checks]
    assert by_id["A01"]["status"] == rt.OK


def test_run_checks_reports_each_gap(tmp_path):
    from mock import certs

    cert_dir = tmp_path / "certs"
    certs.generate(cert_dir, host=rt.DOMAIN, days=2)

    def boom(**kw):
        raise OSError("connection refused")

    checks = rt.run_checks(
        cert_dir=cert_dir,
        trust_checker=lambda *a, **k: False,
        hosts_reader=lambda: "127.0.0.1 localhost\n",
        crl_sha_getter=lambda port: (_ for _ in ()).throw(OSError("no listener")),
        tcp_probe=lambda host, port, timeout=1.5: False,
        health_fetcher=boom,
    )
    by_id = _checks_by_id(checks)
    assert by_id["CA02"]["status"] == rt.FAIL           # 信任没装
    assert by_id["HOST01"]["status"] == rt.FAIL         # hosts 没写
    assert by_id["CRL01"]["status"] == rt.FAIL          # CRL 服务没起
    assert by_id["TRL01"]["status"] == rt.FAIL          # 中继没起
    assert by_id["E2E01"]["status"] == rt.FAIL          # 端到端不通
    assert by_id["A01"]["status"] == rt.SKIP            # 读不到 /health 就不判
    assert by_id["CERT01"]["status"] == rt.SKIP         # 同上，证书剩余也无法读
    assert len(checks) == 8                             # 8 项固定，便于照着清单逐项修
    # 每项 fail 都必须给可执行的补救命令（现场靠它救急）
    for cid in ("CA02", "HOST01", "CRL01", "TRL01"):
        assert by_id[cid]["remedy"], cid


def test_run_checks_flags_stale_hosts_ip(tmp_path):
    from mock import certs

    cert_dir = tmp_path / "certs"
    certs.generate(cert_dir, host=rt.DOMAIN, days=2)
    checks = rt.run_checks(
        cert_dir=cert_dir,
        trust_checker=lambda *a, **k: True,
        hosts_reader=lambda: f"100.67.169.17 {rt.DOMAIN} # hd2proxy\n",
        crl_sha_getter=lambda port: rt.sha256_file(cert_dir / "ca.crl"),
        tcp_probe=lambda *a, **k: True,
        health_fetcher=lambda **kw: (_health(), {"not_after": "x", "remaining_hours": 6.0}),
    )
    host_check = _checks_by_id(checks)["HOST01"]
    assert host_check["status"] == rt.FAIL
    assert "100.67.169.17" in host_check["detail"]


def test_run_checks_dirty_a_state_fails(tmp_path):
    from mock import certs

    cert_dir = tmp_path / "certs"
    certs.generate(cert_dir, host=rt.DOMAIN, days=2)
    dirty = _health(accounts=14, identities=9, login=4, bindings=5)
    checks = rt.run_checks(
        cert_dir=cert_dir,
        trust_checker=lambda *a, **k: True,
        hosts_reader=lambda: f"{rt.DEFAULT_RELAY_IP} {rt.DOMAIN}\n",
        crl_sha_getter=lambda port: rt.sha256_file(cert_dir / "ca.crl"),
        tcp_probe=lambda *a, **k: True,
        health_fetcher=lambda **kw: (dirty, {"not_after": "x", "remaining_hours": 6.0}),
    )
    a01 = _checks_by_id(checks)["A01"]
    assert a01["status"] == rt.FAIL
    assert "accounts=14" in a01["detail"]


def test_format_checks_summary_and_fail_count():
    checks = [
        {"id": "CA01", "name": "证书材料", "status": rt.OK, "detail": "ok", "remedy": ""},
        {"id": "CA02", "name": "本机信任", "status": rt.FAIL, "detail": "no", "remedy": "certutil ..."},
    ]
    text = rt.format_checks(checks)
    assert "补救：certutil" in text
    assert "未就绪" in text
    assert "合计 2 项" in text


@pytest.mark.parametrize("verdict,code", [
    (rt.VERDICT_PASS, 0), (rt.VERDICT_FAIL, 1), (rt.VERDICT_INCOMPLETE, 2)])
def test_cli_judge_exit_codes(tmp_path, verdict, code, monkeypatch):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(__import__("json").dumps(BEFORE), encoding="utf-8")
    after.write_text(__import__("json").dumps(AFTER_IDEAL), encoding="utf-8")
    monkeypatch.setattr(rt, "judge_snapshots",
                        lambda *a, **k: {"verdict": verdict,
                                         "criteria": [], "deltas": {},
                                         "before": {}, "after": {}, "log_evidence": None})
    rc = rt.main(["judge", "--before", str(before), "--after", str(after)])
    assert rc == code


# ---- C05 判据修正（2026-11-23 实机受控复现轮发现的问题） --------------------
# 原实现把 `get`（= GET /api/lobby/<id>，大厅详情拉取）当成"查过 Keys"的代理，
# 但查 Keys 的计数是 keys_hit/keys_bind_hit；大厅详情拉取是否发生完全取决于客户端
# 走哪条 UI 路径（镜像方向实测 0 次），拿它当硬判据会把一轮正常加入判成 FAIL。

def test_judge_c05_passes_without_lobby_get_when_keys_resolved():
    """C05 应只看「双方都 PUT」+「Keys 有成功解析」，不要求 lobby_get。"""
    after = _health(accounts=2, identities=2, keys_known=2, bindings=2,
                    login=2, put=2, get=0, join=1, join_rejected=0,
                    keys_hit=1, keys_bind_hit=1, keys_waited=1, keys_miss=0)
    result = rt.judge_snapshots(BEFORE, after, log_text=MOCK_LOG)
    assert _status(result, "C05") == rt.OK


def test_judge_c05_fails_when_only_one_side_reported_identity():
    """PUT 只有 1 次 ⇒ 只有一侧上报了身份，C05 仍应 FAIL。"""
    after = _health(accounts=2, identities=2, keys_known=2, bindings=1,
                    login=2, put=1, get=0, join=1, join_rejected=0,
                    keys_hit=1, keys_bind_hit=0, keys_waited=0, keys_miss=0)
    result = rt.judge_snapshots(BEFORE, after, log_text=MOCK_LOG)
    assert _status(result, "C05") == rt.FAIL


def test_judge_c05_fails_when_keys_never_resolved():
    """双方都 PUT 了但 Keys 一次都没解析成功 ⇒ 公钥链路没走通，C05 FAIL。"""
    after = _health(accounts=2, identities=2, keys_known=2, bindings=2,
                    login=2, put=2, get=2, join=0, join_rejected=0,
                    keys_hit=0, keys_bind_hit=0, keys_waited=0, keys_miss=2)
    result = rt.judge_snapshots(BEFORE, after, log_text=MOCK_LOG)
    assert _status(result, "C05") == rt.FAIL
