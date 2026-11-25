#!/usr/bin/env python3
"""JA3 指纹稳定性检查：给 EAC 采集轮加一条**免费的第 5 观察量**（离线，纯读文件）。

## 为什么值得看（2026-11-25 七轮实测后）

抓包时顺带抽的 `ja3-sni.tsv` 一直没被用上。回头核对七个轮次（A1/A2/A3、C、B0、M2-kernel），
`api.epicgames.dev` 的 JA3 集合是**完全相同**的 3 个指纹，且分布稳定：

```
d7268fba5c8ebdc86121861931c04402   每轮 3~5 次（主通道，对应 anticheat 长连接）
fae0e5d973c96ae1888b99538efa0363   每轮 1 次
1ce565f411fbc7412e3c5ba0bcfd62ab   每轮 1 次
```

⇒ 这条特征**确定性极强**：如果 EAC 客户端流水线被重启/替换、或注入替换了网络栈，
JA3 集合或分布**可能先变**。它比"体积/周期"更结构化，而且**不花额外成本**（已在每次采集里）。

## 定位（重要，别抬高它）

**辅助信号，不作红判据**：
- 集合一致 ≠ "没被感知"（TLS 指纹只反映客户端 TLS 实现，不反映服务端判定）
- 集合变化 ⇒ **强告警**（说明客户端网络栈被换了/重启了），必须单独查

用法：
    # 单轮：列指纹并标注集合是否与参考一致
    python tools/analyze_ja3.py --run D:/mod/tmp/eac-p7-m2-1

    # 冻结参考集合（用已确认一致的若干轮）
    python tools/analyze_ja3.py --freeze --runs D:/mod/tmp/eac-p3 D:/mod/tmp/eac-p7-c-3 \
        --out state/evidence/ja3-reference-epicgames.json

    # 对比（退出码 0=集合一致 / 1=有变化 / 3=无数据）
    python tools/analyze_ja3.py --run D:/mod/tmp/eac-p7-b0-1 \
        --reference state/evidence/ja3-reference-epicgames.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_SNI = "api.epicgames.dev"


def read_ja3(tsv: Path, sni: str = DEFAULT_SNI) -> list[dict]:
    """读 ja3-sni.tsv，只保留指定 SNI 的握手（无该文件 ⇒ 空）。"""
    path = Path(tsv)
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if (row.get("sni") or "").strip() != sni:
                continue
            out.append({"ja3": (row.get("ja3") or "").strip(),
                        "t": row.get("time_epoch", ""),
                        "tls": row.get("tls_version", "")})
    return out


def summarize(rows: list[dict]) -> dict:
    """计数 + 占比（纯函数）。"""
    c = Counter(r["ja3"] for r in rows if r["ja3"])
    total = sum(c.values())
    return {
        "connections": total,
        "distinct": len(c),
        "ja3": [{"ja3": j, "count": n,
                 "share": round(n / total, 3) if total else 0.0}
                for j, n in c.most_common()],
    }


def compare_sets(summary: dict, reference: dict) -> dict:
    """集合对比：一致 / 新增 / 缺失 / 分布异常（纯函数）。"""
    ref = {x["ja3"] for x in (reference.get("ja3") or [])}
    cur = {x["ja3"] for x in (summary.get("ja3") or [])}
    if not cur:
        return {"code": 3, "label": "no-data",
                "detail": "本轮没有该 SNI 的 TLS 握手记录（代理没生效/窗口没覆盖），无法判断"}
    added = sorted(cur - ref)
    missing = sorted(ref - cur)
    if added:
        return {"code": 1, "label": "ja3-set-changed",
                "detail": f"出现参考集合外的 JA3：{added}（客户端网络栈可能被替换/重启）",
                "added": added, "missing": missing}
    if missing:
        return {"code": 1, "label": "ja3-missing-fingerprint",
                "detail": f"参考里的 JA3 本轮缺失：{missing}（某条通道没建立/被替换）",
                "added": added, "missing": missing}
    return {"code": 0, "label": "ja3-same-set",
            "detail": f"JA3 集合与参考完全一致（{len(cur)} 个指纹）"}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="JA3 指纹稳定性检查（辅助信号，不作红判据）")
    ap.add_argument("--run", help="单轮 capture 目录")
    ap.add_argument("--runs", nargs="*", help="--freeze 时的多个 capture 目录")
    ap.add_argument("--freeze", action="store_true", help="把 --runs 的并集冻结成参考")
    ap.add_argument("--reference", help="对比用的参考 JSON")
    ap.add_argument("--sni", default=DEFAULT_SNI)
    ap.add_argument("--out", help="--freeze 输出路径")
    args = ap.parse_args(argv)

    if args.freeze:
        dirs = args.runs or []
        if not dirs:
            ap.error("--freeze 需要 --runs")
        merged: Counter[str] = Counter()
        per_round = {}
        for d in dirs:
            s = summarize(read_ja3(Path(d) / "ja3-sni.tsv", args.sni))
            per_round[Path(d).name] = s
            for x in s["ja3"]:
                merged[x["ja3"]] += x["count"]
        ref = {
            "schema": 1, "sni": args.sni,
            "sources": [Path(d).name for d in dirs],
            "ja3": [{"ja3": j, "count": n} for j, n in merged.most_common()],
            "per_round": per_round,
        }
        out = Path(args.out) if args.out else Path("state/evidence/ja3-reference.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"frozen: {out}  ({len(ref['ja3'])} 个指纹，来自 {len(dirs)} 轮)")
        return 0

    if not args.run:
        ap.error("需要 --run（或 --freeze --runs）")
    summary = summarize(read_ja3(Path(args.run) / "ja3-sni.tsv", args.sni))
    result = {"run": args.run, **summary}
    if args.reference:
        ref = json.loads(Path(args.reference).read_text(encoding="utf-8"))
        v = compare_sets(summary, ref)
        result["verdict"] = v
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return int(result.get("verdict", {}).get("code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
