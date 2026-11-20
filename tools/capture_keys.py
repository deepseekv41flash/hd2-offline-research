#!/usr/bin/env python3
"""抓包 JSON 形状枚举：每个端点输出唯一 JSON 路径（含类型与样例值）。

用途：回放/自建信令要重造响应时，先知道客户端读了哪些字段、哪些字段随时间变化。

用法:
    python tools/capture_keys.py <capture_dir> [--endpoint SUBSTR] [--max-sample 60] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from analyze_capture import parse_index


def type_name(v) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, dict):
        return "obj"
    if isinstance(v, list):
        return "arr"
    if v is None:
        return "null"
    return type(v).__name__


def sample(v):
    if isinstance(v, str):
        if len(v) > 40:
            return v[:16] + f"…({len(v)})"
        return v
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return f"<{type_name(v)}>"


def walk(node, prefix, acc: dict, depth=0):
    if depth > 8:
        return
    if isinstance(node, dict):
        if not node and prefix:
            acc.setdefault(prefix, {"types": set(), "samples": []})["types"].add("obj{}")
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                walk(v, p, acc, depth + 1)
            else:
                slot = acc.setdefault(p, {"types": set(), "samples": []})
                slot["types"].add(type_name(v))
                if len(slot["samples"]) < 3:
                    slot["samples"].append(sample(v))
    elif isinstance(node, list):
        p = f"{prefix}[]"
        if not node:
            acc.setdefault(p, {"types": set(), "samples": []})["types"].add("arr[]")
            return
        # 数组元素形状取并集（前 50 个足够）
        for v in node[:50]:
            if isinstance(v, (dict, list)):
                walk(v, p, acc, depth + 1)
            else:
                slot = acc.setdefault(p, {"types": set(), "samples": []})
                slot["types"].add(type_name(v))
                if len(slot["samples"]) < 3:
                    slot["samples"].append(sample(v))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture_dir", type=Path)
    ap.add_argument("--endpoint", default="")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    entries = parse_index(args.capture_dir)
    out: dict = {}
    for e in entries:
        if args.endpoint and args.endpoint.lower() not in e["path"].lower():
            continue
        fp = args.capture_dir / e["file"]
        if not fp.exists():
            continue
        try:
            doc = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            continue
        acc: dict = {}
        walk(doc, "", acc)
        base = e["path"].split("?")[0]
        merged = out.setdefault(base, {})
        for p, slot in acc.items():
            slot2 = merged.setdefault(p, {"types": set(), "samples": []})
            slot2["types"] |= slot["types"]
            for s in slot["samples"]:
                if len(slot2["samples"]) < 3 and s not in slot2["samples"]:
                    slot2["samples"].append(s)

    for base in sorted(out):
        print(f"\n## {base}")
        for p in sorted(out[base]):
            slot = out[base][p]
            types = "/".join(sorted(slot["types"]))
            print(f"  {p}  [{types}]  {slot['samples']}")

    if args.json:
        ser = {b: {p: {"types": sorted(s["types"]), "samples": s["samples"]}
                   for p, s in paths.items()} for b, paths in out.items()}
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(ser, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
