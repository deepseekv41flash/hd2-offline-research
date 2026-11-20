#!/usr/bin/env python3
"""抓包结构分析：定位「时间字段」与「敏感字段」，为 capture→replay 设计取证。

用法:
    python tools/analyze_capture.py <capture_dir> [--json out.json] [--quiet]

输入目录需含产品 capture 的 `index.txt`：
    <ISO8601Z>\t<path>\t<size>\t<sha256>\t<kind>

输出:
  * 每个响应的顶层键、时间类字段（键名含 time/date/expire/start/end/ts 的叶子）、
  * 敏感字段命中（connectionString / playerName / sessionId / accountId / token / 票据），
  * 时间参照值（Status.time、timeSinceStart.secondsSinceStart）。

只读：绝不写抓包目录。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TIME_KEY_PAT = re.compile(
    r"(time|date|expire|expiry|start|end|deadline|duration|tick|stamp|ts$|^ts)", re.I
)
# 值本身看起来像「时间」的键名（用于人工复核）
SENSITIVE_KEY_PAT = re.compile(
    r"(connectionstring|playername|memberaccountid|sessionid|token|ticket|secret|password|"
    r"authorization|steamid|accountid|bearer)",
    re.I,
)

MAX_LIST_SAMPLE = 3


def parse_index(capture_dir: Path) -> list[dict]:
    """解析 index.txt → 响应条目（按出现顺序，保留抓包时序）。"""
    idx = capture_dir / "index.txt"
    if not idx.exists():
        raise SystemExit(f"缺少 index.txt: {idx}")
    entries: list[dict] = []
    for line in idx.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        stamp, path, size, sha, kind = parts[0], parts[1], parts[2], parts[3], parts[4]
        if path.startswith("REQ "):
            continue  # 请求体条目，本工具只看响应
        slug = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")[-60:]
        fname = f"{sha[:8]}_{slug}.json"
        entries.append(
            {
                "stamp": stamp,
                "path": path,
                "size": int(size),
                "sha256": sha,
                "kind": kind,
                "file": fname,
            }
        )
    return entries


def walk_time_fields(node, prefix="", out=None, depth=0):
    """递归收集键名像时间的叶子。"""
    if out is None:
        out = []
    if depth > 6:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                walk_time_fields(v, p, out, depth + 1)
            elif TIME_KEY_PAT.search(k):
                out.append((p, v))
    elif isinstance(node, list):
        for i, v in enumerate(node[:MAX_LIST_SAMPLE]):
            walk_time_fields(v, f"{prefix}[{i}]", out, depth + 1)
    return out


def walk_sensitive(node, prefix="", out=None, depth=0):
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                walk_sensitive(v, p, out, depth + 1)
            elif SENSITIVE_KEY_PAT.search(k):
                shown = v
                if isinstance(v, str) and len(v) > 24:
                    shown = v[:12] + f"…({len(v)} chars)"
                out.append((p, shown))
    elif isinstance(node, list):
        for i, v in enumerate(node[:MAX_LIST_SAMPLE]):
            walk_sensitive(v, f"{prefix}[{i}]", out, depth + 1)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture_dir", type=Path)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    entries = parse_index(args.capture_dir)
    report: dict = {"dir": str(args.capture_dir), "entries": [], "time_ref": {}, "sensitive": []}

    for e in entries:
        fp = args.capture_dir / e["file"]
        rec = dict(e)
        if not fp.exists():
            rec["error"] = "missing file"
            report["entries"].append(rec)
            continue
        raw = fp.read_text(encoding="utf-8", errors="replace")
        rec["bytes"] = len(raw.encode("utf-8"))
        try:
            doc = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"not json: {exc}"
            report["entries"].append(rec)
            continue
        rec["top_keys"] = list(doc.keys()) if isinstance(doc, dict) else f"<{type(doc).__name__}>"
        tfields = walk_time_fields(doc)
        rec["time_fields"] = [(p, v) for p, v in tfields if not isinstance(v, (dict, list))]
        sens = walk_sensitive(doc)
        rec["sensitive"] = sens
        if sens:
            report["sensitive"].append({"path": e["path"], "hits": sens})
        report["entries"].append(rec)

    for e in report["entries"]:
        p = e["path"]
        fp = args.capture_dir / e["file"]
        try:
            doc = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            continue
        if p.endswith("/Status") and isinstance(doc, dict) and "time" in doc:
            report["time_ref"]["status_time"] = doc["time"]
            report["time_ref"]["status_warId"] = doc.get("warId")
        if p.endswith("/timeSinceStart") and isinstance(doc, dict):
            report["time_ref"]["time_since_start"] = doc.get("secondsSinceStart")
        if p.endswith("/warinfo") and isinstance(doc, dict):
            report["time_ref"]["warinfo_start"] = doc.get("startDate")
            report["time_ref"]["warinfo_end"] = doc.get("endDate")
    if entries:
        report["time_ref"]["capture_first_stamp"] = entries[0]["stamp"]
        report["time_ref"]["capture_last_stamp"] = entries[-1]["stamp"]

    if not args.quiet:
        print(f"抓包目录: {args.capture_dir}")
        print(f"响应条目: {len(entries)}  首个时间戳: {report['time_ref'].get('capture_first_stamp')}")
        print("\n## 时间参照")
        for k, v in report["time_ref"].items():
            print(f"  {k} = {v}")
        print("\n## 每端点时间字段（去重后）")
        seen: dict[str, set] = {}
        for e in report["entries"]:
            base = e["path"].split("?")[0]
            for p, v in e.get("time_fields", []):
                seen.setdefault(base, set()).add((p, str(v)))
        for base in sorted(seen):
            print(f"\n  {base}")
            for p, v in sorted(seen[base])[:12]:
                print(f"    {p} = {v}")
        print("\n## 敏感字段命中")
        if not report["sensitive"]:
            print("  （无）")
        for s in report["sensitive"]:
            print(f"\n  {s['path']}")
            for p, v in s["hits"][:8]:
                print(f"    {p} = {v}")
        errs = [e for e in report["entries"] if e.get("error")]
        if errs:
            print(f"\n## 解析失败 {len(errs)} 条")
            for e in errs:
                print(f"  {e['path']}: {e['error']}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
