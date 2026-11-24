#!/usr/bin/env python3
"""EAC 轮次记录卡：把一轮采集的**身份 + 时间轴 + 关键计数 + 链路判定**固化成一份 JSON。

## 为什么需要（2026-11-25 三臂设计定下来之后）

第 5 轮起要同时维护三条臂，而且每条臂都要跟不同的对象比：

| 臂 | 含义 | 跟谁比 |
|---|---|---|
| `A` | 无注入、启动器未改 | 冻结成基线（唯一锚）|
| `C` | 改启动器 IAT、无注入 | 跟 `A` 比（看"改启动器"本身有没有信号）|
| `B` | 改启动器 IAT + 真注入 | 跟 `A` 和 `C` 比 |
| `A(deg)` | 本想跑 `C`，但 **hook 没装上** ⇒ 退化成 `A` | 只能当 `A` 的补样，**不能当控制臂** |

三臂 + 两两对比 + RE 的毫秒级时刻，靠嘴记一定会乱。所以每轮采完跑这条命令，
把该轮的"身份证"落成 `state/evidence/rounds/<date>-<arm>.json`，之后所有报告都引它。

## 用法

    python tools/eac_round_record.py --arm C \
        --run D:/mod/tmp/eac-p6-control --liveness D:/mod/tmp/eac-p6-control/eac-watch.jsonl \
        --launcher-seen 01:35:47.100 --game-appeared 01:35:49.675 \
        --re-json D:/mod/work/hd2-eacoff-mode/state/xxx.json \
        --out state/evidence/rounds/2026-11-25-C.json

`--arm` 只接受 `A` / `C` / `B` / `A(deg)`；`--arm C` 时必须给 `--hook-installed`（True/False），
若为 False 会自动改记成 `A(deg)` 并打警告 —— 这是"没装上钩子的 C 轮不许冒充控制臂"的护栏。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
VALID_ARMS = ("A", "C", "B", "A(deg)")


def _import(name: str):
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    return __import__(name)


def _truthy(v: str | bool | None) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "t")


def resolve_arm(arm: str, hook_installed: bool | None) -> tuple[str, list[str]]:
    """把用户报的臂名规范化，并在"钩子没装上"时降级成 A(deg)。纯函数。"""
    warnings: list[str] = []
    arm = (arm or "").strip()
    if arm not in VALID_ARMS:
        raise ValueError(f"未知 arm: {arm!r}（只接受 {VALID_ARMS}）")
    if arm == "C" and hook_installed is False:
        warnings.append("hook_installed=False ⇒ C 臂不成立，降级记为 A(deg)（不得当控制臂用）")
        return "A(deg)", warnings
    if arm == "C" and hook_installed is None:
        warnings.append("C 臂未声明 --hook-installed，按 True 处理（请自行确认回读自检已通过）")
    return arm, warnings


def build_record(arm: str, run_dir: Path, liveness_jsonl: Path | None = None,
                 times: dict | None = None, re_fields: dict | None = None,
                 note: str = "") -> dict:
    """组装一条轮次记录（纯函数；计数与判定都去调已有工具，不重实现）。"""
    cb = _import("compare_baseline_vs_run")
    wl = _import("eac_watch_liveness")

    run = cb.load_run(Path(run_dir))
    rec = {
        "schema": 1,
        "arm": arm,
        "run_dir": str(run_dir),
        "counts": {
            "v8": len(run["v8"]),
            "v6": len(run["v6"]),
            "telemetry": len(run["telemetry"]),
        },
        "v8": {
            "req_len_series": [m["req_len"] for m in run["v8"]],
            "dt_ms_series": [m.get("dt_ms") for m in run["v8"]],
            "ua_set": sorted({m["ua"] for m in run["v8"] if m.get("ua")}),
        },
        "times": dict(times or {}),
        "re_fields": dict(re_fields or {}),
        "note": note,
    }
    if liveness_jsonl:
        summary = wl.summarize(wl.read_jsonl(Path(liveness_jsonl)))
        rec["liveness"] = {
            "summary": summary,
            "verdict": wl.verdict(summary, len(run["v8"])),
        }
        rec["times"].setdefault("game_appeared_from_sampler",
                                (summary.get("game_windows") or [{}])[0].get("first"))
    return rec


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="EAC 轮次记录卡")
    ap.add_argument("--arm", required=True, help="A / C / B / A(deg)")
    ap.add_argument("--run", required=True, help="capture 目录")
    ap.add_argument("--liveness", help="eac-watch.jsonl")
    ap.add_argument("--hook-installed", help="C 臂必填：True/False（回读自检结果）")
    ap.add_argument("--launcher-seen")
    ap.add_argument("--launcher-gone")
    ap.add_argument("--game-appeared")
    ap.add_argument("--inject-at-ms", help="RE 的注入完成时刻（相对进程出现，ms）")
    ap.add_argument("--re-json", help="RE 那一轮的 json 路径（整份读入 re_fields）")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    hook = None if args.hook_installed is None else _truthy(args.hook_installed)
    try:
        arm, warnings = resolve_arm(args.arm, hook)
    except ValueError as exc:
        print(f"错误：{exc}")
        return 2

    times = {k: v for k, v in {
        "launcher_seen": args.launcher_seen,
        "launcher_gone": args.launcher_gone,
        "game_appeared": args.game_appeared,
        "inject_at_ms": args.inject_at_ms,
    }.items() if v}
    re_fields = {}
    if args.re_json:
        try:
            re_fields = json.loads(Path(args.re_json).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"警告：读不了 --re-json（{type(exc).__name__}），re_fields 留空")

    rec = build_record(arm, Path(args.run),
                       Path(args.liveness) if args.liveness else None,
                       times, re_fields, args.note)
    rec["warnings"] = warnings

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

    c = rec["counts"]
    print(f"arm={rec['arm']}  v8={c['v8']}  v6={c['v6']}  telemetry={c['telemetry']}  -> {out}")
    if "liveness" in rec:
        v = rec["liveness"]["verdict"]
        print(f"链路：{v['label']}（code {v['code']}）{v['detail']}")
    for w in warnings:
        print(f"⚠ {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
