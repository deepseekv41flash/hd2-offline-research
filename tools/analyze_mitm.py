"""T6a：分析 MITM 取证日志（`state/evidence/t6/mitm.jsonl`）。

日志是 `tools/mitm_proxy.py` 产出的 JSONL：每条 = 一个被 TLS 终止的 HTTP 请求/响应，
body 只记 schema（字段名/长度/字符类/hash），不落原值。

本工具回答三个问题：
1. 哪些旁路域名**能被** MITM（出现了请求记录）？哪些**不能**（只有 `tls_handshake_failed`
   ⇒ 自带信任链/证书固定）？
2. 每个域名上**实际发生了什么**（method/path/status/UA/体积）？
3. 响应 JSON 的**字段结构**是什么（键树 + 字符串长度/字符类），用来判定"传的是什么"。

用法：
    python tools/analyze_mitm.py [--log state/evidence/t6/mitm.jsonl] [--json]
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"找不到日志: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def key_tree(shape: dict, depth: int = 0, max_depth: int = 2) -> str:
    """把 schema 压成一行键树（字符串只显示长度与字符类）。"""
    t = shape.get("type")
    if t == "obj":
        keys = shape.get("keys") or []
        if depth >= max_depth or not shape.get("children"):
            return "{" + ", ".join(keys) + "}"
        inner = ", ".join(f"{k}: {key_tree(shape['children'][k], depth + 1, max_depth)}"
                          for k in keys)
        return "{" + inner + "}"
    if t == "arr":
        item = shape.get("item")
        return f"[{shape.get('len')} x {key_tree(item, depth + 1, max_depth)}]" if item else f"[{shape.get('len')}]"
    if t == "str":
        return f"str(len={shape.get('len')}, {shape.get('class')})"
    if t in ("number", "bool"):
        return f"{t}({shape.get('value')})"
    return str(t)


def summarize(recs: list[dict]) -> dict:
    hosts = collections.Counter(r.get("host") for r in recs)
    events = collections.Counter((r.get("host"), r.get("event")) for r in recs if "event" in r)
    reqs = [r for r in recs if "event" not in r]
    mitm_able = sorted({r["host"] for r in reqs if r.get("host")})
    tls_failed = sorted({r["host"] for r in recs if r.get("event") == "tls_handshake_failed"})
    return {"total": len(recs), "hosts": dict(hosts), "events": {f"{h}|{e}": n for (h, e), n in events.items()},
            "requests": reqs, "mitm_able": mitm_able, "tls_failed": tls_failed}


def render(summary: dict) -> str:
    out = []
    out.append(f"总记录 {summary['total']} 条；可 MITM 域名 {len(summary['mitm_able'])} 个；"
               f"握手失败域名 {len(summary['tls_failed'])} 个")
    out.append("")
    out.append("## 1. 能被 MITM 的域名（出现 HTTP 记录 ⇒ 该 SDK 走系统信任链）")
    if not summary["mitm_able"]:
        out.append("  （无）")
    for h in summary["mitm_able"]:
        n = sum(1 for r in summary["requests"] if r.get("host") == h)
        out.append(f"  - {h}  ({n} 条请求)")
    out.append("")
    out.append("## 2. 不能 MITM 的域名（握手失败 ⇒ 自带信任链/证书固定）")
    if not summary["tls_failed"]:
        out.append("  （无失败记录：也可能该域名在本窗口没被连接）")
    for h in summary["tls_failed"]:
        out.append(f"  - {h}")
    out.append("")
    out.append("## 3. 请求明细")
    for r in summary["requests"]:
        out.append(f"  {r.get('host','?'):42s} {str(r.get('method','?')):5s} "
                   f"{str(r.get('path','?'))[:60]:60s} {str(r.get('status','?')):4s} "
                   f"req={r.get('req_len',0):<7} resp={r.get('resp_len',0):<7} "
                   f"ct={str(r.get('resp_ctype'))[:24]:24s} ua={str(r.get('ua'))[:34]}")
    out.append("")
    out.append("## 4. 请求/响应结构（schema，不含原值）")
    for r in summary["requests"]:
        out.append(f"### {r.get('host')} {r.get('method')} {r.get('path')}  -> {r.get('status')}")
        if r.get("req") and r["req"].get("json"):
            out.append("  请求体: " + key_tree(r["req"]["json"]))
        if r.get("resp") and r["resp"].get("json"):
            out.append("  响应体: " + key_tree(r["resp"]["json"]))
        elif r.get("resp"):
            out.append(f"  响应体: bytes={r['resp'].get('bytes')} format={r['resp'].get('format')}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="分析 T6a MITM 取证日志")
    p.add_argument("--log", type=Path, default=REPO / "state/evidence/t6/mitm.jsonl")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    summary = summarize(load(args.log))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
