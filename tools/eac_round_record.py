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
| `C(weak)` | 补丁装上了，但**钩子没命中**（槽打错/不在活路径）| 只能说明"一处未被执行的写"不扰动通道，**不能当"启动器被改"的对照** |
| `B0` | 改启动器 + **拿句柄**、不注入 | 与 `C` 比 ⇒ 测"拿句柄"这一步本身 |
| `B0(deg)` | 本想跑 `B0`，但**没拿到句柄**（`pi_ptr` 仍空）| 等价于 `C`，不能当 B0 |
| `B1` | 改启动器 + 拿句柄 + **真注入** | 与 `B0` 比 ⇒ 测"注入本身" |
| `B1(deg)` | 本想跑 `B1`，但**注入没发生** | 等价于 `B0`，不能当 B1 |
| `void` | 轮次作废（游戏没起来/链路没通/被冻死等）| **不进任何臂**，只留痕 |

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
VALID_ARMS = ("A", "C", "B", "B0", "B1", "A(deg)", "C(weak)", "B0(deg)", "B1(deg)", "void")


def _import(name: str):
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    return __import__(name)


def _truthy(v: str | bool | None) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "t")


def resolve_arm(arm: str, hook_installed: bool | None,
                hook_fired: bool | None = None,
                handle_obtained: bool | None = None,
                inject_done: bool | None = None) -> tuple[str, list[str]]:
    """把用户报的臂名规范化并做降级。纯函数。

    C 臂成立需要**两个**条件（2026-11-25 第 1 次实战补的）：
      ① `hook_installed=True` —— 8 字节确实写进去了（回读自检）；
      ② `hook_fired=True`     —— 那个槽**真被调用**（拿到 PROCESS_INFORMATION）。
    只满足 ① 不满足 ② ⇒ 补丁写在一个没被执行的槽上，启动器实际行为不变
    ⇒ 降级为 `C(weak)`：只能说明"一处未被执行的写不扰动通道"，**不能当控制臂**。
    """
    warnings: list[str] = []
    arm = (arm or "").strip()
    if arm not in VALID_ARMS:
        raise ValueError(f"未知 arm: {arm!r}（只接受 {VALID_ARMS}）")
    if arm == "void":
        warnings.append("void 轮次：不进任何臂，仅留痕")
        return "void", warnings
    if arm == "C(weak)":
        warnings.append("C(weak) 臂：补丁未被命中，不得当作『启动器被改』的对照")
        return arm, warnings
    # 四臂阶梯的降级护栏（2026-11-25：B0 = 改启动器+拿句柄、B1 = 再注入）
    if arm == "B0" and handle_obtained is False:
        warnings.append("handle_obtained=False ⇒ 没拿到句柄，B0 退化成 C（记 B0(deg)）")
        return "B0(deg)", warnings
    if arm == "B1" and inject_done is False:
        warnings.append("inject_done=False ⇒ 注入没发生，B1 退化成 B0（记 B1(deg)）")
        return "B1(deg)", warnings
    if arm == "B1" and inject_done is None:
        warnings.append("B1 未声明 --inject-done：无法确认注入真的发生，请补该字段")
    if arm == "C" and hook_installed is False:
        warnings.append("hook_installed=False ⇒ C 臂不成立，降级记为 A(deg)（不得当控制臂用）")
        return "A(deg)", warnings
    if arm == "C" and hook_fired is False:
        warnings.append("hook_fired=False ⇒ 补丁未被命中（槽打错/不在活路径），"
                        "降级记为 C(weak)：不能当『启动器被改』的对照")
        return "C(weak)", warnings
    if arm == "C" and hook_installed is None:
        warnings.append("C 臂未声明 --hook-installed，按 True 处理（请自行确认回读自检已通过）")
    if arm == "C" and hook_fired is None:
        warnings.append("C 臂未声明 --hook-fired：无法排除『装上了但没命中』，请补该字段")
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
        rec["derived"] = derive_windows(summary, times or {})
    return rec


def _parse_hms(v) -> float | None:
    """'01:43:23.456' / '01:43:23' → 当天秒数（用于求差）；不可解析返回 None。"""
    if not v or not isinstance(v, str):
        return None
    core = v.strip().split("T")[-1]
    try:
        hh, mm, rest = core.split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(rest)
    except (ValueError, AttributeError):
        return None


def derive_windows(summary: dict, times: dict) -> dict:
    """从采样时间线派生"启动器脏窗口"，并与 RE 报的 `iat_alive_ms` 交叉核对（纯函数）。

    为什么重要（2026-11-25 更正）：补丁必须赶在启动器调 `CreateProcessW` **之前**装上，
    所以"启动器被改"的窗口 ≈ 启动器出现 → 游戏出现，实测 **~3.5–4 s**（不是早期误传的 100–300 ms）。
    这个窗口是**归因边界**：C 臂若出现异常，只能由这个脏窗口解释（别的东西都没变）。

    注意：**不能用"异常时刻是否落在窗口内"来定位原因** —— EAC 上报通道 +44 s 才建立，
    4 s 察觉 ≠ 4 s 上报，延迟上报才是现实机制。归因只能靠 A/C/B 三臂差。
    """
    first_l = (summary.get("launcher_windows") or [{}])[0].get("first")
    first_g = (summary.get("game_windows") or [{}])[0].get("first")
    out: dict = {"launcher_first_seen": first_l, "game_first_seen": first_g,
                 "note": "脏窗口=启动器出现→游戏出现；异常时刻不落在窗口内是正常的（上报延迟）"}
    a, b = _parse_hms(first_l), _parse_hms(first_g)
    if a is not None and b is not None:
        out["launcher_to_game_s"] = round(b - a, 3)
    # 与 RE 报的补丁存活时长交叉核对（采样 1 s 粒度，允许 ±1.5 s）
    iat = times.get("iat_alive_ms")
    if iat is not None and out.get("launcher_to_game_s") is not None:
        try:
            re_s = float(iat) / 1000.0
        except (TypeError, ValueError):
            re_s = None
        if re_s is not None:
            out["iat_alive_ms_reported"] = float(iat)
            out["iat_alive_ms_crosscheck"] = {
                "reported_s": round(re_s, 3),
                "sampler_s": out["launcher_to_game_s"],
                "delta_s": round(abs(re_s - out["launcher_to_game_s"]), 3),
                "consistent": abs(re_s - out["launcher_to_game_s"]) <= 1.5,
            }
    return out


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
    ap.add_argument("--hook-fired", help="C 臂必填：True/False（钩子是否真被调用/拿到 PI）")
    ap.add_argument("--handle-obtained", help="B0 臂必填：True/False（是否拿到 PROCESS_INFORMATION/句柄）")
    ap.add_argument("--inject-done", help="B1 臂必填：True/False（是否真的执行了注入）")
    ap.add_argument("--launcher-seen")
    ap.add_argument("--launcher-gone")
    ap.add_argument("--game-appeared")
    ap.add_argument("--inject-at-ms", help="RE 的注入完成时刻（相对进程出现，ms）")
    ap.add_argument("--iat-alive-ms", help="RE 报的补丁存活时长（ms，预期 3.5k~4k）")
    ap.add_argument("--re-json", help="RE 那一轮的 json 路径（整份读入 re_fields）")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    hook = None if args.hook_installed is None else _truthy(args.hook_installed)
    fired = None if args.hook_fired is None else _truthy(args.hook_fired)
    handle = None if args.handle_obtained is None else _truthy(args.handle_obtained)
    injected = None if args.inject_done is None else _truthy(args.inject_done)
    try:
        arm, warnings = resolve_arm(args.arm, hook, fired, handle, injected)
    except ValueError as exc:
        print(f"错误：{exc}")
        return 2

    times = {k: v for k, v in {
        "launcher_seen": args.launcher_seen,
        "launcher_gone": args.launcher_gone,
        "game_appeared": args.game_appeared,
        "inject_at_ms": args.inject_at_ms,
        "hook_installed": args.hook_installed,
        "hook_fired": args.hook_fired,
        "handle_obtained": args.handle_obtained,
        "inject_done": args.inject_done,
        "iat_alive_ms": args.iat_alive_ms,
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
