#!/usr/bin/env python3
"""EAC 轮次旁路采样：**链路存活**（只读，不碰网络配置）。

## 为什么需要（2026-11-25 第 5 轮前新增）

注入轮的判读有个前置问题必须先答清楚：`/anticheat/v8` 条数为 0 时，到底是

  ① EAC 没上报（"未被感知"的候选证据），还是
  ② EAC 被注入/关掉/崩了（"链路断了" ⇒ 本轮只能判**无结论**）？

`mitm.jsonl` 自己分不开这两者。所以在采集器旁边并行跑一份只读时间线，每 `--interval`
秒记一个样本：

  - `game[]`      `helldivers2.exe` pid / create_time（"进程出现时刻"原点独立复算）
  - `launcher[]`  `start_protected_game.exe`（RE 的注入目标，确认注入对象在不在）
  - `eac_pid[]`   `EasyAntiCheat_EOS.exe`（客户端 EAC 进程）
  - `eac_service` `EasyAntiCheat_EOS` 服务状态（正常应随游戏 RUNNING）
  - `conns`       游戏进程 TCP 连接：total / established / remote443 / 远端 IP 集合
                  （MITM 模式下远端是 `127.0.0.1:18092`，"有没有网"看 established）

落 jsonl（一行一个样本），结束时打印 summary。

## 用法

    # 采集（与采集器并行，另一个终端；默认 360 s 覆盖 300 s 采集窗口）
    python tools/eac_watch_liveness.py --out D:/mod/tmp/eac-p5/eac-watch.jsonl --duration 360

    # 事后汇总
    python tools/eac_watch_liveness.py --summary D:/mod/tmp/eac-p5/eac-watch.jsonl

    # 判定（退出码 0=链路活 / 2=链路断或 anticheat 流量缺失 / 3=没有游戏窗口）
    python tools/eac_watch_liveness.py --verdict D:/mod/tmp/eac-p5/eac-watch.jsonl \
        --run D:/mod/tmp/eac-p5 --min-v8 3
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

GAME_IMAGE = "helldivers2.exe"
LAUNCHER_IMAGE = "start_protected_game.exe"
EAC_IMAGE = "easyanticheat_eos.exe"
SERVICE_NAME = "EasyAntiCheat_EOS"

EAC_ALIVE_RATIO_MIN = 0.5   # 游戏在跑的时间里，EAC 进程至少一半样本可见
DEFAULT_MIN_V8 = 3          # 少于 3 条 v8 不足以谈可见性（正常轮 12~15 条）


# ---------------------------------------------------------------- 采样
def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _load_psutil():
    try:
        import psutil  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 环境问题
        raise SystemExit("需要 psutil：pip install psutil") from exc
    return psutil


def list_procs(ps) -> list[dict]:
    """当前进程快照：[{pid, name, create}]，name 统一小写。"""
    out: list[dict] = []
    for p in ps.process_iter(["pid", "name", "create_time"]):
        try:
            name = (p.info.get("name") or "").lower()
            out.append({
                "pid": p.info["pid"],
                "name": name,
                "create": p.info.get("create_time"),
            })
        except Exception:  # 进程刚好退出
            continue
    return out


def proc_conns(ps, pids: list[int]) -> dict:
    """游戏进程的 TCP 连接统计（某个进程读不到就跳过，不抛）。"""
    total = est = r443 = 0
    ips: set[str] = set()
    for pid in pids:
        try:
            conns = ps.Process(pid).net_connections(kind="tcp")
        except Exception:
            continue
        for c in conns:
            total += 1
            if c.status == "ESTABLISHED":
                est += 1
                raddr = c.raddr
                if raddr:
                    if getattr(raddr, "port", None) == 443:
                        r443 += 1
                    ips.add(getattr(raddr, "ip", ""))
    return {"total": total, "established": est, "remote443": r443,
            "remote_ips": sorted(x for x in ips if x)}


def service_status(ps) -> str:
    try:
        return ps.win_service_get(SERVICE_NAME).status()
    except Exception as exc:
        return "unknown:" + type(exc).__name__


def sample(ps=None, now: datetime | None = None) -> dict:
    """一个只读样本（ps / now 可注入，便于离线测试）。"""
    ps = ps or _load_psutil()
    procs = list_procs(ps)
    game = [p for p in procs if p["name"] == GAME_IMAGE]
    launcher = [p for p in procs if p["name"] == LAUNCHER_IMAGE]
    eac = [p for p in procs if p["name"] == EAC_IMAGE]
    return {
        "t": now_iso(now),
        "game": game,
        "launcher": launcher,
        "eac_pid": [p["pid"] for p in eac],
        "eac_service": service_status(ps),
        "conns": proc_conns(ps, [g["pid"] for g in game]),
    }


# ---------------------------------------------------------------- 汇总
def summarize(samples: list[dict]) -> dict:
    """把样本序列压成可判读的摘要（纯函数）。"""
    samples = [s for s in samples if isinstance(s, dict)]
    game_samples = [s for s in samples if s.get("game")]

    windows: list[dict] = []
    for s in game_samples:
        pid = s["game"][0]["pid"]
        if windows and windows[-1]["pid"] == pid:
            windows[-1]["last"] = s["t"]
            windows[-1]["samples"] += 1
        else:
            windows.append({"pid": pid, "first": s["t"], "last": s["t"], "samples": 1})

    eac_alive = sum(1 for s in game_samples if s.get("eac_pid"))
    ip_counter: Counter[str] = Counter()
    for s in game_samples:
        ip_counter.update((s.get("conns") or {}).get("remote_ips") or [])

    return {
        "samples": len(samples),
        "t0": samples[0]["t"] if samples else None,
        "t1": samples[-1]["t"] if samples else None,
        "game_windows": windows,
        "game_samples": len(game_samples),
        "eac_alive_samples": eac_alive,
        "eac_alive_ratio": round(eac_alive / len(game_samples), 3) if game_samples else 0.0,
        "service_states": dict(Counter(s.get("eac_service") for s in game_samples)),
        "conns_peak": max(((s.get("conns") or {}).get("established") or 0)
                          for s in game_samples) if game_samples else 0,
        "conns_peak_443": max(((s.get("conns") or {}).get("remote443") or 0)
                              for s in game_samples) if game_samples else 0,
        "remote_ip_top": ip_counter.most_common(5),
    }


def command_v8_count(run_dir: Path) -> int:
    """复用 `compare_baseline_vs_run.load_run` 的口径数 v8（自检已排除）。"""
    tools_dir = Path(__file__).resolve().parent
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import compare_baseline_vs_run as cb  # noqa: PLC0415

    return len(cb.load_run(Path(run_dir))["v8"])


def verdict(summary: dict, v8_count: int | None, min_v8: int = DEFAULT_MIN_V8) -> dict:
    """把摘要翻成三态结论（纯函数）。code: 0=链路活 / 2=链路断或缺流量 / 3=无游戏窗口。"""
    if not summary.get("game_windows"):
        return {"code": 3, "label": "no-game-window",
                "detail": "采样窗口内没看到 helldivers2.exe，本轮无法判读（原点缺失）"}
    if summary["eac_alive_ratio"] < EAC_ALIVE_RATIO_MIN:
        return {"code": 2, "label": "link-dead-eac-missing",
                "detail": f"EAC 进程仅 {summary['eac_alive_ratio']:.0%} 的样本可见"
                          f"（阈值 {EAC_ALIVE_RATIO_MIN:.0%}）⇒ 链路断，不能判'未被感知'"}
    if summary["conns_peak"] <= 0:
        return {"code": 2, "label": "link-dead-no-network",
                "detail": "游戏进程全程没有 TCP 连接 ⇒ 代理/网络可能没生效，不能判'未被感知'"}
    if v8_count is None:
        return {"code": 0, "label": "link-alive-unchecked-traffic",
                "detail": "EAC 存活且游戏有连接；未提供 --run，v8 条数未核"}
    if v8_count < min_v8:
        return {"code": 2, "label": "link-alive-but-traffic-missing",
                "detail": f"v8 仅 {v8_count} 条（< {min_v8}）⇒ 不足以谈可见性，判无结论"}
    return {"code": 0, "label": "link-alive",
            "detail": f"EAC 存活 {summary['eac_alive_ratio']:.0%}、连接峰值 "
                      f"{summary['conns_peak']}、v8 {v8_count} 条 ⇒ 判读成立"}


# ---------------------------------------------------------------- CLI
def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，中文/JSON 会花屏；统一切 UTF-8 输出
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="EAC 轮次链路存活旁路采样（只读）")
    ap.add_argument("--out", help="采集模式：jsonl 输出路径")
    ap.add_argument("--interval", type=float, default=5.0, help="采样间隔秒（默认 5）")
    ap.add_argument("--duration", type=float, default=360.0, help="采集时长秒（默认 360）")
    ap.add_argument("--summary", help="只读模式：对已有 jsonl 摘要")
    ap.add_argument("--verdict", help="只读模式：对已有 jsonl 判定")
    ap.add_argument("--run", help="配合 --verdict：capture 目录（数 v8 条数）")
    ap.add_argument("--min-v8", type=int, default=DEFAULT_MIN_V8, help="v8 最少条数")
    args = ap.parse_args(argv)

    if args.summary or args.verdict:
        src = Path(args.summary or args.verdict)
        summary = summarize(read_jsonl(src))
        v8 = command_v8_count(Path(args.run)) if args.run else None
        if args.verdict:
            v = verdict(summary, v8, args.min_v8)
            print(json.dumps({**summary, "v8_count": v8, "verdict": v},
                             ensure_ascii=False, indent=2))
            return int(v["code"])
        print(json.dumps({**summary, "v8_count": v8}, ensure_ascii=False, indent=2))
        return 0

    if not args.out:
        ap.error("需要 --out（采集）或 --summary/--verdict（只读）")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ps = _load_psutil()
    n = max(1, int(round(args.duration / max(args.interval, 0.2))))
    import time as _time  # noqa: PLC0415

    with out.open("w", encoding="utf-8") as fh:
        for i in range(n):
            s = sample(ps)
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
            fh.flush()
            g = s["game"][0]["pid"] if s["game"] else "-"
            print(f"[{i + 1}/{n}] {s['t']} game={g} eac={'Y' if s['eac_pid'] else 'N'} "
                  f"svc={s['eac_service']} conns={s['conns']['established']}", flush=True)
            if i < n - 1:
                _time.sleep(args.interval)

    summary = summarize(read_jsonl(out))
    print("\n=== summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
