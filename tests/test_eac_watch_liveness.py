"""`eac_watch_liveness.py` 的离线测试：第 5 轮"链路存活"反证控制。

为什么要锁：v8 = 0 条会被误读成"未被感知"，实际可能是 EAC 被注入弄死/代理没生效。
这台判定就是那条分界线（0=链路活 / 2=链路断或缺流量 / 3=无游戏窗口）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import eac_watch_liveness as wl

UA = ("EOS-SDK/1.19.1.2-53289219 (Windows/10.0.26100.10002.64bit) "
      "Helldivers 2/Developer Build (1.8.47139.0)")
NOW = datetime(2026, 11, 25, 1, 30, 0, 123000)


# ---------------------------------------------------------------- 假 psutil
class _Raddr:
    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port


class _Conn:
    def __init__(self, status="ESTABLISHED", raddr=None):
        self.status = status
        self.raddr = raddr


class _InfoProc:
    def __init__(self, info: dict):
        self.info = info


class _Svc:
    def __init__(self, status: str):
        self._status = status

    def status(self) -> str:
        return self._status


class _ConnProc:
    def __init__(self, conns: list, boom: bool = False):
        self._conns = conns
        self._boom = boom

    def net_connections(self, kind="tcp"):
        if self._boom:
            raise PermissionError("denied")
        return self._conns


class FakePs:
    """最小 psutil 替身：进程表 + 每 pid 连接 + 服务状态。"""

    def __init__(self, procs: list[dict], conns: dict[int, list],
                 service="running", svc_boom: bool = False):
        self._procs = procs
        self._conns = conns
        self._service = service
        self._svc_boom = svc_boom

    def process_iter(self, attrs):
        for p in self._procs:
            yield _InfoProc(p)

    def Process(self, pid: int):
        return _ConnProc(self._conns.get(pid, []))

    def win_service_get(self, name: str):
        if self._svc_boom:
            raise ValueError("boom")
        return _Svc(self._service)


def _procs(game=(), launcher=(), eac=()) -> list[dict]:
    out = []
    for pid in game:
        out.append({"pid": pid, "name": "helldivers2.exe", "create_time": 1.0})
    for pid in launcher:
        out.append({"pid": pid, "name": "start_protected_game.exe", "create_time": 1.0})
    for pid in eac:
        out.append({"pid": pid, "name": "EasyAntiCheat_EOS.exe", "create_time": 1.0})
    return out


# ---------------------------------------------------------------- sample
def test_sample_records_game_eac_and_conns():
    ps = FakePs(
        procs=_procs(game=[39020], launcher=[2548], eac=[43284]),
        conns={39020: [_Conn(raddr=_Raddr("127.0.0.1", 18092)),
                       _Conn(raddr=_Raddr("127.0.0.1", 18092)),
                       _Conn(status="TIME_WAIT", raddr=None)]},
    )
    s = wl.sample(ps, now=NOW)
    assert s["t"].startswith("2026-11-25T01:30:00")
    assert [g["pid"] for g in s["game"]] == [39020]
    assert [p["pid"] for p in s["launcher"]] == [2548]
    assert s["eac_pid"] == [43284]
    assert s["eac_service"] == "running"
    assert s["conns"]["total"] == 3
    assert s["conns"]["established"] == 2
    assert s["conns"]["remote443"] == 0          # MITM 模式远端是 18092，不是 443
    assert s["conns"]["remote_ips"] == ["127.0.0.1"]


def test_sample_tolerates_unreadable_connections():
    """读不到某进程连接时不能炸，只是计数为 0。"""
    class BadPs(FakePs):
        def Process(self, pid):
            return _ConnProc([], boom=True)

    bad = BadPs(procs=_procs(game=[1], eac=[2]), conns={})
    s = wl.sample(bad, now=NOW)
    assert s["conns"]["established"] == 0
    assert s["eac_pid"] == [2]


def test_sample_service_unknown_is_not_fatal():
    ps = FakePs(procs=_procs(game=[1]), conns={}, svc_boom=True)
    s = wl.sample(ps, now=NOW)
    assert s["eac_service"].startswith("unknown:")


# ---------------------------------------------------------------- summarize
def _s(t: str, game=(), eac=(), conns=0, svc="running") -> dict:
    return {
        "t": t,
        "game": [{"pid": p, "name": "helldivers2.exe", "create": 1.0} for p in game],
        "launcher": [],
        "eac_pid": list(eac),
        "eac_service": svc,
        "conns": {"total": conns, "established": conns, "remote443": 0,
                  "remote_ips": ["127.0.0.1"] if conns else []},
    }


def test_summarize_splits_windows_and_ratio():
    samples = [
        _s("01:29:00"),                                    # 游戏没起
        _s("01:29:05", game=[100], eac=[200], conns=2),
        _s("01:29:10", game=[100], eac=[200], conns=5),
        _s("01:29:15", game=[100], conns=4),               # EAC 掉了（注入杀了？）
        _s("01:29:20", game=[300], eac=[400], conns=1),    # 换了 pid ⇒ 新窗口
    ]
    sm = wl.summarize(samples)
    assert sm["samples"] == 5
    assert sm["game_samples"] == 4
    assert [w["pid"] for w in sm["game_windows"]] == [100, 300]
    assert sm["game_windows"][0] == {"pid": 100, "first": "01:29:05",
                                     "last": "01:29:15", "samples": 3}
    assert sm["eac_alive_samples"] == 3
    assert sm["eac_alive_ratio"] == 0.75
    assert sm["conns_peak"] == 5
    assert sm["service_states"] == {"running": 4}


def test_summarize_empty():
    sm = wl.summarize([])
    assert sm["samples"] == 0 and sm["game_windows"] == []
    assert sm["eac_alive_ratio"] == 0.0 and sm["conns_peak"] == 0


# ---------------------------------------------------------------- verdict
def test_verdict_no_game_window():
    v = wl.verdict(wl.summarize([_s("01:29:00")]), None)
    assert (v["code"], v["label"]) == (3, "no-game-window")


def test_verdict_link_dead_when_eac_missing():
    sm = wl.summarize([_s("01:29:05", game=[100], conns=3),
                       _s("01:29:10", game=[100], conns=3)])
    v = wl.verdict(sm, v8_count=14)
    assert (v["code"], v["label"]) == (2, "link-dead-eac-missing")


def test_verdict_link_dead_when_no_network():
    sm = wl.summarize([_s("01:29:05", game=[100], eac=[200], conns=0)])
    v = wl.verdict(sm, v8_count=14)
    assert (v["code"], v["label"]) == (2, "link-dead-no-network")


def test_verdict_traffic_missing_is_inconclusive():
    sm = wl.summarize([_s("01:29:05", game=[100], eac=[200], conns=3)])
    v = wl.verdict(sm, v8_count=1, min_v8=3)
    assert (v["code"], v["label"]) == (2, "link-alive-but-traffic-missing")


def test_verdict_link_alive():
    sm = wl.summarize([_s("01:29:05", game=[100], eac=[200], conns=3),
                       _s("01:29:10", game=[100], eac=[200], conns=4)])
    v = wl.verdict(sm, v8_count=14, min_v8=3)
    assert (v["code"], v["label"]) == (0, "link-alive")
    assert "v8 14 条" in v["detail"]


def test_verdict_without_run_is_still_alive():
    sm = wl.summarize([_s("01:29:05", game=[100], eac=[200], conns=3)])
    v = wl.verdict(sm, v8_count=None)
    assert (v["code"], v["label"]) == (0, "link-alive-unchecked-traffic")


# ---------------------------------------------------------------- v8 计数口径
def test_command_v8_count_excludes_preflight(tmp_path: Path):
    cap = tmp_path / "cap"
    cap.mkdir()

    def row(t, ln, hdrs):
        return {"t": t, "host": "api.epicgames.dev", "method": "POST",
                "path": "/anticheat/v8/games/client/message/", "req_len": ln,
                "status": 200, "resp_len": 1289, "req_headers": hdrs}

    rows = [
        row("01:20:40", 72, {}),                                    # 开窗自检，必须排除
        row("01:22:30", 388, {"user-agent": UA, "x-eos-version": "1"}),
        row("01:22:36", 4904, {"user-agent": UA, "x-eos-version": "1"}),
        row("01:22:41", 728, {"user-agent": UA, "x-eos-version": "1"}),
    ]
    (cap / "mitm.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    assert wl.command_v8_count(cap) == 3


def test_read_jsonl_skips_broken_lines(tmp_path: Path):
    p = tmp_path / "w.jsonl"
    p.write_text('{"t":"a"}\nnot json\n\n{"t":"b"}\n', encoding="utf-8")
    got = wl.read_jsonl(p)
    assert [x["t"] for x in got] == ["a", "b"]
    assert wl.read_jsonl(tmp_path / "missing.jsonl") == []


# ---------------------------------------------------------------- 启动看门狗（早终止）
def test_launch_verdict_waiting_before_grace():
    assert wl.launch_verdict(100.0, None, 110.0, grace_s=30) is None


def test_launch_verdict_flags_frozen_launcher():
    """第 1 次实战的真实故障：启动器冻死、游戏永不出现 ⇒ 30 s 就必须报警。"""
    v = wl.launch_verdict(100.0, None, 131.0, grace_s=30)
    assert v["code"] == 2 and v["label"] == "suspect-launcher-frozen"
    assert "31" in v["detail"]


def test_launch_verdict_passes_when_game_appears():
    v = wl.launch_verdict(100.0, 104.0, 200.0, grace_s=30)
    assert v["code"] == 0 and v["label"] == "game-appeared"


def test_launch_verdict_nothing_yet():
    assert wl.launch_verdict(None, None, 999.0, grace_s=30) is None


def test_watchdog_end_to_end_writes_alert(tmp_path: Path, monkeypatch):
    """真跑采集循环：启动器出现后 grace 秒没有游戏 ⇒ 写报警文件并（可选）提前退出。"""
    alert = tmp_path / "alert.json"
    t = {"now": 1_000.0}

    monkeypatch.setattr(wl, "now_iso", lambda now=None: "2026-11-25T01:45:%05.2f" % (t["now"] % 60))
    monkeypatch.setitem(wl.__dict__, "_load_psutil", lambda: FakePs(procs=_procs(launcher=[21260]),
                                                                   conns={}))

    import time as _t
    monkeypatch.setattr(_t, "time", lambda: t["now"])
    monkeypatch.setattr(_t, "sleep", lambda s: t.__setitem__("now", t["now"] + s))

    def fake_sample(ps=None, now=None):
        return {"t": wl.now_iso(), "game": [], "launcher": [{"pid": 21260}],
                "eac_pid": [], "eac_service": "stopped",
                "conns": {"total": 0, "established": 0, "remote443": 0, "remote_ips": []}}
    monkeypatch.setattr(wl, "sample", fake_sample)

    out = tmp_path / "w.jsonl"
    code = wl.main(["--out", str(out), "--duration", "60", "--interval", "5",
                    "--alert-file", str(alert), "--grace", "10", "--abort-on-freeze"])
    assert code == 0
    assert alert.exists()
    data = json.loads(alert.read_text(encoding="utf-8"))
    assert data["label"] == "suspect-launcher-frozen"


def test_summarize_records_launcher_windows():
    """启动器窗口也要进摘要：它是"启动器被改"的脏窗口边界（1 s 采样派生）。"""
    samples = [
        _s("01:43:20"),
        {"t": "01:43:23", "game": [], "launcher": [{"pid": 21260}], "eac_pid": [],
         "eac_service": "stopped", "conns": {"total": 0, "established": 0,
                                             "remote443": 0, "remote_ips": []}},
        {"t": "01:43:24", "game": [], "launcher": [{"pid": 21260}], "eac_pid": [],
         "eac_service": "running", "conns": {"total": 0, "established": 0,
                                             "remote443": 0, "remote_ips": []}},
        _s("01:43:27", game=[13332], eac=[19868], conns=1),
    ]
    sm = wl.summarize(samples)
    assert len(sm["launcher_windows"]) == 1
    assert sm["launcher_windows"][0]["first"] == "01:43:23"
    assert sm["launcher_windows"][0]["last"] == "01:43:24"
    assert sm["launcher_windows"][0]["samples"] == 2
    assert sm["game_windows"][0]["first"] == "01:43:27"
