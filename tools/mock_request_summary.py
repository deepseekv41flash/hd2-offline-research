#!/usr/bin/env python3
"""mock 请求日志汇总：给「引擎 ↔ mock」联调做逐端点对账。

用途（2026-11-24 与 MITM 线做存档层 mock 验收时加）：对方引擎经 `upstreamTest` 把请求
转发到我们的明文 mock，验收后要回答「它到底请求了哪些端点、各几次、看到多大响应」。
本脚本只读 `mock.log`，输出：

  1. 按 (方法, 路径) 聚合：次数、请求/响应字节、首次/末次时间、命中来源集合；
  2. 写操作（PUT/POST）单独成表 —— 存档层的入账/上报都在这类端点上；
  3. `--json` 输出机器可读结果，便于贴进验收档。

日志行格式（mock/server.py `_Logger`）：
  11:39:46.390 GET    /api/Progression 0B -> 200 1500B [exact] via 127.0.0.1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LINE_RE = re.compile(
    r"^(?P<ts>\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<method>[A-Z]+)\s+"
    r"(?P<path>\S+)\s+"
    r"(?P<req>\d+)B\s+->\s+(?P<status>\d{3})\s+"
    r"(?P<resp>\d+)B"
    r"(?:\s+\[(?P<source>[^\]]+)\])?"
)
# 静态资源/健康检查不进对账主表（避免把探针流量算成验收流量）
NOISE_PATHS = {"/health", "/favicon.ico"}


def normalize(path: str) -> str:
    """把动态段（uuid/纯数字/长 base64 段）换成占位符，便于按模板聚合。"""
    raw = path.split("?", 1)[0]
    parts = []
    for seg in raw.split("/"):
        if not seg:
            parts.append(seg)
        elif re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\.r-[0-9a-f]+)?", seg):
            parts.append("{uuid}")
        elif re.fullmatch(r"\d{6,}", seg):
            parts.append("{id}")
        else:
            parts.append(seg)
    return "/".join(parts)


def parse_log(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        rows.append({
            "ts": m.group("ts"),
            "method": m.group("method"),
            "path": m.group("path"),
            "template": normalize(m.group("path")),
            "req_bytes": int(m.group("req")),
            "status": int(m.group("status")),
            "resp_bytes": int(m.group("resp")),
            "source": (m.group("source") or "").split(":")[0] or "-",
        })
    return rows


def filter_window(rows: list[dict], *, since: str = "", until: str = "") -> list[dict]:
    """按时间窗过滤（`HH:MM:SS` 前缀比较即可，日志时间戳零填充定宽）。

    用途：一次联调里日志文件会混着探针/自检流量（例：验收前的 curl 自测），
    只统计验收窗口内的真实流量才能和对方引擎日志逐条对账。
    """
    out = []
    for r in rows:
        if since and r["ts"] < since:
            continue
        if until and r["ts"] > until:
            continue
        out.append(r)
    return out


def summarize(rows: list[dict], *, include_noise: bool = False) -> dict:
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        if not include_noise and r["path"].split("?", 1)[0] in NOISE_PATHS:
            continue
        key = (r["method"], r["template"])
        slot = agg.setdefault(key, {"method": r["method"], "path": r["template"],
                                    "count": 0, "req_bytes": 0, "resp_bytes": 0,
                                    "first": r["ts"], "last": r["ts"], "sources": set(),
                                    "statuses": set()})
        slot["count"] += 1
        slot["req_bytes"] += r["req_bytes"]
        slot["resp_bytes"] += r["resp_bytes"]
        slot["last"] = r["ts"]
        slot["sources"].add(r["source"])
        slot["statuses"].add(r["status"])

    items = []
    for slot in agg.values():
        slot = dict(slot)
        slot["sources"] = sorted(slot["sources"])
        slot["statuses"] = sorted(slot["statuses"])
        items.append(slot)
    items.sort(key=lambda s: (-s["count"], s["method"], s["path"]))
    writes = [s for s in items if s["method"] in ("PUT", "POST", "DELETE", "PATCH")]
    return {"total_lines": len(rows), "endpoints": items, "writes": writes,
            "unique_endpoints": len(items)}


def format_text(summary: dict) -> str:
    out = [f"== mock 请求汇总（{summary['total_lines']} 行，{summary['unique_endpoints']} 个端点模板） =="]
    out.append(f"{'方法':<6} {'次数':>4}  {'请求B':>8} {'响应B':>9}  {'首→末':<19} 端点")
    for s in summary["endpoints"]:
        span = f"{s['first'][:8]}→{s['last'][:8]}"
        out.append(f"{s['method']:<6} {s['count']:>4}  {s['req_bytes']:>8} {s['resp_bytes']:>9}  {span:<19} {s['path']}  [{','.join(s['sources'])}]")
    if summary["writes"]:
        out.append("")
        out.append("== 写操作（存档层入账/上报关注） ==")
        for s in summary["writes"]:
            out.append(f"{s['method']:<6} {s['count']:>4}  {s['path']}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="mock 请求日志汇总（只读）")
    ap.add_argument("log", help="mock.log 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON（便于贴进验收档）")
    ap.add_argument("--include-noise", action="store_true", help="把 /health 等探针流量也算进来")
    ap.add_argument("--since", default="", help="只统计该时刻之后（含），格式 HH:MM:SS")
    ap.add_argument("--until", default="", help="只统计该时刻之前（含），格式 HH:MM:SS")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    path = Path(args.log)
    if not path.exists():
        sys.exit(f"日志不存在: {path}")
    rows = parse_log(path.read_text(encoding="utf-8", errors="replace"))
    rows = filter_window(rows, since=args.since, until=args.until)
    summary = summarize(rows, include_noise=args.include_noise)
    summary["window"] = {"since": args.since, "until": args.until}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print(format_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
