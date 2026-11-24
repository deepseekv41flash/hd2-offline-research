#!/usr/bin/env python3
"""EAC v8 上报基线：**冻结**与**对比**（离线，纯读文件）。

背景（`docs/EAC-NET-ATTRIBUTION-2026-11-25.md` §8）：`/anticheat/v8/games/client/message/`
的载荷是不可读的高熵块（熵 ≈ 7.99），**内容层无法判"注入了没有"**。
唯一可观测的信号被钉成四条：

| 信号 | 红（判定"被感知"）| 黄（需复测）| 绿 |
|---|---|---|---|
| `User-Agent` | 出现任何变化（含 `Developer Build` 消失）| — | 完全一致 |
| 新增周期 | 出现基线没有的固定周期 | 周期抖动范围扩大但无新周期 | 与基线同分布 |
| 体积分布 | 出现基线最大之外的**新量级**，或大包条数翻倍 | 中位数偏移 >30% | 同量级 |
| `correlation-id` | 只作关联键，不作判据 | — | — |

退出码：**0**=绿/黄 · **1**=红 · **3**=无结论（本轮 0 条 v8，先看链路存活）。

用法：
    # 1) 冻结基线（用一轮干净 capture 目录）
    python tools/compare_baseline_vs_run.py --freeze --run D:/mod/tmp/eac-p3 \
        --out state/evidence/baseline-v8.json
    # 2) 任一轮与基线对比
    python tools/compare_baseline_vs_run.py --baseline state/evidence/baseline-v8.json \
        --run D:/mod/tmp/eac-p4
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

V8_PATH_RE = re.compile(r"/anticheat/v8/")
V6_PATH_RE = re.compile(r"/anticheat/v6/")
# 采集器自己的开窗自检（72 B 的 PREFLIGHT），永远排除
PREFLIGHT_LEN = 72
BIG_MSG_BYTES = 8192          # "大包"阈值（基线里 8 KB 以上的消息条数用于翻倍判据）
PERIOD_MIN_S = 2.0            # 小于 2 s 的间隔不当作"周期"（心跳不会这么快）


# ---------------------------------------------------------------- 读取一轮 capture
def _is_preflight(rec: dict) -> bool:
    """识别采集器的开窗自检（必须排除，否则它的长间隔会污染抖动统计）。

    判据用**数据特征**而不是文件名（落盘名里不一定有 PREFLIGHT 字样 —— 2026-11-25 第 4 轮
    就因此漏判：自检 72 B 进了统计，制造出 110 s 的假间隔，把"抖动范围"判成黄）：
      长度 == 72 且 **没有** `user-agent` 且 **没有** `x-eos-version`。
    真实的 EAC 请求这两个头必有其一。
    """
    if int(rec.get("req_len") or 0) != PREFLIGHT_LEN:
        return False
    hd = {str(k).lower(): v for k, v in (rec.get("req_headers") or {}).items()}
    return not hd.get("user-agent") and not hd.get("x-eos-version")


def load_run(capture_dir: Path) -> dict:
    """从一轮 capture 目录抽 v8/v6 的逐条观测（含 UA 与 correlation-id）。"""
    cap = Path(capture_dir)
    jsonl = cap / "mitm.jsonl"
    v8: list[dict] = []
    v6: list[dict] = []
    tele = []
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            path = str(rec.get("path") or "")
            hd = rec.get("req_headers") or {}
            if V8_PATH_RE.search(path):
                if _is_preflight(rec):
                    continue
                v8.append({
                    "ts": rec.get("t"), "req_len": int(rec.get("req_len") or 0),
                    "resp_len": int(rec.get("resp_len") or 0),
                    "ua": rec.get("ua") or hd.get("user-agent"),
                    "x_eos_version": hd.get("x-eos-version"),
                    "correlation_id": (hd.get("x-epic-correlation-id")
                                       or hd.get("x-epic-correlationid")),
                })
            elif V6_PATH_RE.search(path):
                v6.append({"ts": rec.get("t"),
                           "resp_len": int(rec.get("resp_len") or 0)})
            elif "/telemetry/" in path:
                tele.append({"ts": rec.get("t"),
                             "app_id": _query_val(path, "AppID"),
                             "correlation_id": (hd.get("x-epic-correlation-id")
                                                or hd.get("x-epic-correlationid"))})
    # 相对间隔
    prev = None
    for m in v8:
        ms = _ts_ms(m["ts"])
        m["dt_ms"] = None if prev is None else (ms - prev) if ms and prev else None
        if ms:
            prev = ms
    return {"capture_dir": str(cap), "v8": v8, "v6": v6, "telemetry": tele}


def _query_val(path: str, key: str) -> str | None:
    if "?" not in path:
        return None
    for part in path.split("?", 1)[1].split("&"):
        if part.startswith(key + "="):
            return part.split("=", 1)[1]
    return None


def _ts_ms(ts) -> int:
    if not ts:
        return 0
    s = str(ts)
    try:
        if "T" in s:
            import time as _t
            return int(_t.mktime(_t.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)
        p = s.split(":")
        if len(p) == 3:
            return int(((int(p[0]) * 60 + int(p[1])) * 60 + float(p[2])) * 1000)
    except Exception:  # noqa: BLE001
        return 0
    return 0


# ---------------------------------------------------------------- 基线与对比
def freeze(run: dict) -> dict:
    """把一轮干净的 v8 序列冻结成基线（字段按 EAC-off 线要求）。"""
    items = [{"ts": m["ts"], "req_len": m["req_len"], "resp_len": m["resp_len"],
              "dt_ms": m["dt_ms"], "ua": m["ua"], "x_eos_version": m["x_eos_version"]}
             for m in run["v8"]]
    lens = [m["req_len"] for m in run["v8"]]
    dts = [m["dt_ms"] for m in run["v8"] if m["dt_ms"] is not None]
    return {
        "schema": 1,
        "source_capture": run["capture_dir"],
        "frozen_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(items),
        "ua_set": sorted({m["ua"] for m in items if m["ua"]}),
        "x_eos_version_set": sorted({m["x_eos_version"] for m in items if m["x_eos_version"]}),
        "req_len_max": max(lens) if lens else 0,
        "req_len_median": statistics.median(lens) if lens else 0,
        "big_msg_count": sum(1 for x in lens if x >= BIG_MSG_BYTES),
        "dt_ms_median": statistics.median(dts) if dts else None,
        "dt_ms_range": [min(dts), max(dts)] if dts else None,
        "periods_s": dominant_periods(dts),
        "items": items,
    }


def dominant_periods(dts_ms: list[int], min_s: float = PERIOD_MIN_S,
                     top: int = 6, min_hits: int = 3) -> list[float]:
    """粗粒度"周期"提取：间隔按 1 s 分桶，**出现 ≥3 次**的桶才算候选周期。

    为什么从 ≥2 提到 ≥3：2026-11-25 第 4 轮（**无注入的干净复现**）用 ≥2 时
    基线 {3,5,7} vs 本轮 {4,5,6,7} 被判"新增周期=红" —— 纯抖动导致的假阳性。
    判据必须先在干净轮上稳定，才能用于判注入。
    """
    buckets: dict[int, int] = {}
    for d in dts_ms:
        s = round(d / 1000.0)
        if s >= min_s:
            buckets[s] = buckets.get(s, 0) + 1
    cand = sorted(((s, c) for s, c in buckets.items() if c >= min_hits), key=lambda kv: -kv[1])
    return [float(s) for s, _ in cand[:top]]


def compare(baseline: dict, run: dict) -> dict:
    """按四条信号出判定（红/黄/绿）+ 差值表。

    **0 条 v8 是"无结论"而不是"红"**（2026-11-25 第 5 轮前置补的）：
    0 条可能只是链路断了（EAC 被杀 / 代理没生效 / 窗口没覆盖到），把它判成"红"
    = 把"没采到"误报成"被感知"。链路是否成立交给 `tools/eac_watch_liveness.py --verdict`。
    """
    base_items = baseline.get("items") or []
    cur = run["v8"]
    if not cur:
        base_lens0 = [int(i["req_len"]) for i in base_items]
        return {
            "overall": "无结论",
            "rows": [{"signal": s, "verdict": "N/A", "detail": "本轮 0 条 v8，无法对比"}
                     for s in ("User-Agent", "新增周期", "体积分布")] + [
                {"signal": "correlation-id", "verdict": "N/A",
                 "detail": "v8 0 条 / telemetry 带 id %d 条"
                           % len([t for t in run["telemetry"] if t.get("correlation_id")])}],
            "baseline": {"count": len(base_items),
                         "req_len_max": max(base_lens0) if base_lens0 else 0,
                         "big_msg_count": sum(1 for x in base_lens0 if x >= BIG_MSG_BYTES),
                         "req_len_median": statistics.median(base_lens0) if base_lens0 else 0,
                         "periods_s": sorted(baseline.get("periods_s") or []),
                         "ua": sorted(baseline.get("ua_set") or [])},
            "run": {"count": 0, "req_len_max": 0, "big_msg_count": 0, "req_len_median": 0,
                    "periods_s": [], "ua": [],
                    "v6_msgs": len(run["v6"]), "telemetry_msgs": len(run["telemetry"])},
            "note": "本轮 0 条 v8：先跑 tools/eac_watch_liveness.py --verdict 判链路存活；"
                    "链路断（EAC 被杀 / 代理没生效 / 窗口没覆盖）时只能判『无结论』，"
                    "不得写成『被感知』。",
        }
    base_lens = [int(i["req_len"]) for i in base_items]
    cur_lens = [m["req_len"] for m in cur]
    base_dts = [int(i["dt_ms"]) for i in base_items if i.get("dt_ms") is not None]
    cur_dts = [m["dt_ms"] for m in cur if m["dt_ms"] is not None]

    rows = []

    # ① User-Agent：任何变化即红
    base_ua = set(baseline.get("ua_set") or [])
    cur_ua = {m["ua"] for m in cur if m["ua"]}
    if not cur:
        rows.append({"signal": "User-Agent", "verdict": "N/A", "detail": "本轮无 v8 消息"})
    elif cur_ua == base_ua:
        rows.append({"signal": "User-Agent", "verdict": "绿", "detail": "与基线完全一致"})
    else:
        rows.append({"signal": "User-Agent", "verdict": "红",
                     "detail": "变化：基线 %s → 本轮 %s" % (sorted(base_ua), sorted(cur_ua))})

    # ② 新增周期
    base_periods = set(baseline.get("periods_s") or [])
    cur_periods = set(dominant_periods(cur_dts))
    new_periods = sorted(cur_periods - base_periods)
    # 只有"**明显更快**的新周期"才算被追问（如 12 s → 3 s）。
    # 抖动（基线 5 s、本轮 4 s 也在桶里）**不算** —— 第 4 轮无注入的干净复现已证：
    # 用"高频新周期"判会把纯抖动误报成红。
    base_min = min(base_periods) if base_periods else None
    faster = [x for x in new_periods if base_min and x < base_min * 0.7]
    if faster:
        rows.append({"signal": "新增周期", "verdict": "红",
                     "detail": "出现明显更快的新周期：%s s（基线 %s s，本轮 %s s）"
                               % (faster, sorted(base_periods), sorted(cur_periods))})
    else:
        base_rng = baseline.get("dt_ms_range") or [0, 0]
        wider = False
        if cur_dts and base_rng:
            wider = (min(cur_dts) < base_rng[0] * 0.6) or (max(cur_dts) > base_rng[1] * 1.6)
        rows.append({"signal": "新增周期", "verdict": "黄" if wider else "绿",
                     "detail": ("抖动范围扩大但无新周期（基线 %s → 本轮 %s）"
                                % (base_rng, [min(cur_dts), max(cur_dts)] if cur_dts else None))
                               if wider else "与基线同分布"})

    # ③ 体积分布
    base_max = int(baseline.get("req_len_max") or 0)
    base_big = int(baseline.get("big_msg_count") or 0)
    cur_big = sum(1 for x in cur_lens if x >= BIG_MSG_BYTES)
    cur_max = max(cur_lens) if cur_lens else 0
    base_med = float(baseline.get("req_len_median") or 0)
    cur_med = statistics.median(cur_lens) if cur_lens else 0
    new_magnitude = cur_max > max(base_max * 1.5, base_max + 8192)
    doubled = base_big > 0 and cur_big >= base_big * 2
    shifted = base_med > 0 and abs(cur_med - base_med) / base_med > 0.30
    if new_magnitude or doubled:
        rows.append({"signal": "体积分布", "verdict": "红",
                     "detail": "新量级或大包翻倍：max %d→%d、大包 %d→%d"
                               % (base_max, cur_max, base_big, cur_big)})
    elif shifted:
        rows.append({"signal": "体积分布", "verdict": "黄",
                     "detail": "中位数偏移 %.0f%%（%s → %s）"
                               % (abs(cur_med - base_med) / base_med * 100, base_med, cur_med)})
    else:
        rows.append({"signal": "体积分布", "verdict": "绿",
                     "detail": "同量级（max %d vs %d，中位 %s vs %s）"
                               % (base_max, cur_max, base_med, cur_med)})

    # ④ correlation-id：只作关联键
    v8_ids = [m["correlation_id"] for m in cur if m.get("correlation_id")]
    tele_ids = [t["correlation_id"] for t in run["telemetry"] if t.get("correlation_id")]
    rows.append({"signal": "correlation-id", "verdict": "关联",
                 "detail": "v8 带 id %d 条 / telemetry 带 id %d 条；交集 %d"
                           % (len(v8_ids), len(tele_ids), len(set(v8_ids) & set(tele_ids)))})

    reds = [r for r in rows if r["verdict"] == "红"]
    yellows = [r for r in rows if r["verdict"] == "黄"]
    overall = "红" if reds else ("黄" if yellows else "绿")
    return {
        "overall": overall, "rows": rows,
        "baseline": {"count": len(base_items), "req_len_max": base_max,
                     "big_msg_count": base_big, "req_len_median": base_med,
                     "periods_s": sorted(base_periods), "ua": sorted(base_ua)},
        "run": {"count": len(cur), "req_len_max": cur_max, "big_msg_count": cur_big,
                "req_len_median": cur_med, "periods_s": sorted(cur_periods),
                "ua": sorted(cur_ua), "v6_msgs": len(run["v6"]),
                "telemetry_msgs": len(run["telemetry"])},
    }


def process_pid_hint(capture_dir: Path) -> dict | None:
    """若 capture 目录里记了 game_pid（meta.json），带上便于对时间轴。"""
    meta = Path(capture_dir) / "meta.json"
    if not meta.exists():
        return None
    try:
        d = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return {"game_pid": d.get("game_pid"), "started_at": d.get("started_at"),
            "ended_at": d.get("ended_at"),
            "ie_proxy_restored_identical": d.get("ie_proxy_restored_identical")}


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK：中文/⚠ 会 UnicodeEncodeError，统一切 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="EAC v8 基线冻结/对比")
    ap.add_argument("--run", required=True, help="一轮 capture 目录（含 mitm.jsonl）")
    ap.add_argument("--freeze", action="store_true", help="冻结成基线")
    ap.add_argument("--out", help="--freeze 时的输出路径")
    ap.add_argument("--baseline", help="对比时的基线 JSON")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    run = load_run(Path(args.run))
    if args.freeze:
        # 护栏：0 条 v8 的轮次不能当锚（否则锚是空的，之后每轮都跟"空"比）
        if not run["v8"]:
            print("拒绝对 0 条 v8 的轮次执行 --freeze（锚不能是空的）；"
                  "先确认链路存活，再重新采集。")
            return 2
        base = freeze(run)
        out = Path(args.out) if args.out else Path(args.run) / "baseline-v8.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"frozen": str(out), "count": base["count"],
                          "req_len_max": base["req_len_max"],
                          "big_msg_count": base["big_msg_count"],
                          "periods_s": base["periods_s"],
                          "ua_set": base["ua_set"]}, ensure_ascii=False, indent=1))
        return 0
    if not args.baseline:
        print("需要 --baseline（或用 --freeze 冻结）")
        return 2
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    result = compare(baseline, run)
    result["run_meta"] = process_pid_hint(Path(args.run))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 3 if result["overall"] == "无结论" else 0
    print("== EAC v8 基线对比：总体 **%s** ==" % result["overall"])
    for r in result["rows"]:
        print("  [%s] %-16s %s" % (r["verdict"], r["signal"], r["detail"]))
    if result.get("note"):
        print(chr(10) + "  " + chr(0x26a0) + " " + result["note"])
    b, c = result["baseline"], result["run"]
    print("\n  基线: %d 条, max=%d, 大包=%d, 中位=%s, 周期=%s"
          % (b["count"], b["req_len_max"], b["big_msg_count"], b["req_len_median"], b["periods_s"]))
    print("  本轮: %d 条, max=%d, 大包=%d, 中位=%s, 周期=%s（v6 %d 条 / telemetry %d 条）"
          % (c["count"], c["req_len_max"], c["big_msg_count"], c["req_len_median"],
             c["periods_s"], c["v6_msgs"], c["telemetry_msgs"]))
    if result["overall"] == "无结论":
        return 3
    return 0 if result["overall"] != "红" else 1


if __name__ == "__main__":
    raise SystemExit(main())
