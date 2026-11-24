#!/usr/bin/env python3
"""校验 `save-export` 产物（`<root>/players/<steamId64>/*.json`）是否符合自建服契约。

契约见 `docs/SELFHOST-SAVE-CONTRACT-2026-11-24.md`。本工具只读，用来在**发版前**机器化验证：
  1. 目录/文件/索引齐全，manifest 身份与 index、目录名三者一致；
  2. **shape 保真**：三个响应体的键集与键序 = 基线的深拷贝（不是按语义重建）；
  3. **合并语义**：库存款只抬不降、不丢行；统计逐 key 单调不减；
  4. `counts` 自检字段与正文一致（服务端启动自检就靠这四项）；
  5. manifest 的 hash/时间/engine 字段格式可用。

基线用研究仓现成的两套抓包目录：
  * 空号基线：`state/dist/save-fixtures-fresh-raw`
  * 主号基线：`samples/store-season`

    python tools/validate_save_export.py \
        --export D:/mod/tmp/save-export-sample \
        --baseline state/dist/save-fixtures-fresh-raw \
        --shape-ref samples/store-season        # 空号基线里 achievements 为空，用主号基线学元素形状

退出码：0 全过；1 有 FAIL。`--json` 输出机器可读报告。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED = ("manifest.json", "progression.json", "inventory.json", "stats_profile.json")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# 研究仓两套基线的固定文件名（抓包目录里是按 sha8 命名的）
BASELINE_FILES = {
    "fresh": {
        "progression": "78db2b64_api_Progression.json",
        "inventory": "85bebe5a_api_Progression_inventory.json",
        "stats": "dccbac58_api_Stats_profile_summary.json",
    },
    "main": {
        "progression": "f288f166_api_Progression.json",
        "inventory": "a1e6d9db_api_Progression_inventory.json",
        "stats": "dfdad048_api_Stats_profile_summary.json",
    },
}


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_baseline(directory: Path) -> dict:
    """在基线目录里找三种响应体文件：先按已知文件名，再按 `*api_Progression.json` 等后缀兜底。"""
    d = Path(directory)
    picked: dict[str, Path] = {}
    for key, name in {**BASELINE_FILES["fresh"], **BASELINE_FILES["main"]}.items():
        cand = d / name
        if cand.exists():
            picked[key] = cand
    patterns = {
        "progression": "*_api_Progression.json",
        "inventory": "*_api_Progression_inventory.json",
        "stats": "*_api_Stats_profile_summary.json",
    }
    for key, pat in patterns.items():
        if key in picked:
            continue
        cands = sorted(p for p in d.glob(pat))
        if cands:
            picked[key] = cands[0]
    missing = [k for k in patterns if k not in picked]
    if missing:
        raise FileNotFoundError(f"基线目录缺少: {missing}（{d}）")
    return picked


def key_order(obj) -> list[str]:
    return list(obj.keys()) if isinstance(obj, dict) else []


def check(export_dir: Path, baseline_dir: Path, shape_ref: Path | None = None) -> dict:
    results: list[dict] = []

    def add(cid: str, ok: bool, detail: str) -> None:
        results.append({"id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})

    export_dir = Path(export_dir)
    players_dir = export_dir / "players"
    base = resolve_baseline(Path(baseline_dir))
    ref = resolve_baseline(Path(shape_ref)) if shape_ref else None
    base_p = load_json(base["progression"])
    base_i = load_json(base["inventory"])
    base_s = load_json(base["stats"])

    # ---- 目录结构 ----
    add("S01 目录结构", players_dir.is_dir() and (export_dir / "index.json").exists(),
        f"players/ 存在={players_dir.is_dir()}、index.json 存在={(export_dir / 'index.json').exists()}")

    index_path = export_dir / "index.json"
    index = load_json(index_path) if index_path.exists() else {}
    idx_players = {str(p.get("steamId64")): p for p in (index.get("players") or [])}
    dirs = sorted(p.name for p in players_dir.iterdir() if p.is_dir()) if players_dir.is_dir() else []
    add("S02 index 与目录一致", bool(dirs) and set(dirs) == set(idx_players),
        f"目录 {len(dirs)} 个、index {len(idx_players)} 个；差集={sorted(set(dirs) ^ set(idx_players))[:5]}")

    for sid in dirs:
        pdir = players_dir / sid
        missing = [f for f in REQUIRED if not (pdir / f).exists()]
        add(f"S03[{sid}] 文件齐全", not missing, f"缺 {missing}" if missing else "manifest/progression/inventory/stats_profile 齐全")
        if missing:
            continue
        man = load_json(pdir / "manifest.json")
        prog = load_json(pdir / "progression.json")
        inv = load_json(pdir / "inventory.json")
        stats = load_json(pdir / "stats_profile.json")

        # ---- manifest 身份 ----
        ident = man.get("identity") or {}
        idx = idx_players.get(sid) or {}
        ok_id = (ident.get("steamId64") == sid
                 and str(idx.get("steamId64")) == sid
                 and (not idx.get("hd2AccountId") or ident.get("hd2AccountId") == idx.get("hd2AccountId"))
                 and man.get("saveId") == f"steam_{sid}")
        add(f"M01[{sid}] 身份一致", ok_id,
            f"manifest={ident.get('steamId64')}/{ident.get('hd2AccountId')}、index={idx.get('hd2AccountId')}、saveId={man.get('saveId')}")
        add(f"M02[{sid}] schema/engine/时间", man.get("schema") == 1 and bool(man.get("generatedAt"))
            and bool((man.get("engine") or {}).get("version")),
            f"schema={man.get('schema')}、generatedAt={man.get('generatedAt')}、engine={(man.get('engine') or {}).get('version')}")
        src = man.get("source") or {}
        hashes_ok = all(SHA256_RE.match(str(src.get(k) or "")) for k in ("baselineSha256", "ledgerSha256"))
        add(f"M03[{sid}] 源 hash 格式", hashes_ok,
            f"baselineSha256={str(src.get('baselineSha256'))[:12]}…、ledgerSha256={str(src.get('ledgerSha256'))[:12]}…")
        add(f"M04[{sid}] catalogBuild 类型", "catalogBuild" in man and (man["catalogBuild"] is None or isinstance(man["catalogBuild"], str)),
            f"catalogBuild={man.get('catalogBuild')!r}")

        # ---- shape 保真 ----
        add(f"F01[{sid}] progression 键集/键序", key_order(prog) == key_order(base_p),
            f"导出 {len(key_order(prog))} 键 vs 基线 {len(key_order(base_p))} 键；多={sorted(set(prog) - set(base_p))[:5]}、缺={sorted(set(base_p) - set(prog))[:5]}")
        add(f"F02[{sid}] inventory 键集/键序", key_order(inv) == key_order(base_i),
            f"导出 {key_order(inv)} vs 基线 {key_order(base_i)}")
        add(f"F03[{sid}] stats 键集", set(stats) == set(base_s),
            f"导出 {len(stats)} 键 vs 基线 {len(base_s)} 键；多={sorted(set(stats) - set(base_s))[:5]}、缺={sorted(set(base_s) - set(stats))[:5]}")

        # 数组元素形状：以「基线里有元素的那个」为准（都空时用 shape-ref）
        def elem_keys(doc: dict, field: str) -> set[str] | None:
            val = doc.get(field)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return set(val[0])
            return None

        for field in ("achievements", "assignments", "packageProgress", "purchasedSeasonPassItems"):
            got = elem_keys(prog, field)
            want = elem_keys(base_p, field) or (elem_keys(load_json(ref["progression"]), field) if ref else None)
            if got is None:
                add(f"F04[{sid}:{field}] 元素形状", True, "导出为空数组（无需比对元素键）")
            elif want is None:
                add(f"F04[{sid}:{field}] 元素形状", True, f"导出 {sorted(got)}（无参照，跳过严格比对）")
            else:
                add(f"F04[{sid}:{field}] 元素形状", got == want,
                    f"导出 {sorted(got)} vs 参照 {sorted(want)}")

        item_keys_ok = (not inv.get("items")) or (isinstance(inv["items"][0], dict)
                                                  and set(inv["items"][0]) == set((base_i.get("items") or [{}])[0]))
        add(f"F05[{sid}] inventory 行元素键", item_keys_ok,
            f"导出 {(inv.get('items') or [{}])[0] and sorted(inv['items'][0])} vs 基线 {sorted((base_i.get('items') or [{}])[0])}")

        # ---- 合并语义：库存款只抬不降、不丢行 ----
        bmap = {str(x.get("mixId")): x.get("amount") for x in (base_i.get("items") or [])}
        emap = {str(x.get("mixId")): x.get("amount") for x in (inv.get("items") or [])}
        lost = [k for k in bmap if k not in emap]
        lowered = [(k, bmap[k], emap[k]) for k in bmap if k in emap and (emap[k] or 0) < (bmap[k] or 0)]
        add(f"V01[{sid}] 库存不丢行", not lost, f"丢失 {len(lost)} 行 {lost[:5]}")
        add(f"V02[{sid}] 库存只抬不降", not lowered, f"被降 {len(lowered)} 行 {lowered[:5]}")

        # ---- 统计单调不减（max 语义）----
        bad_stats = [(k, base_s.get(k), stats.get(k)) for k in base_s
                     if isinstance(base_s.get(k), (int, float)) and isinstance(stats.get(k), (int, float))
                     and stats[k] < base_s[k]]
        add(f"V03[{sid}] 统计单调不减", not bad_stats, f"退化的 key {len(bad_stats)} 个 {bad_stats[:3]}")

        # ---- counts 自检 ----
        counts = man.get("counts") or {}
        ach = prog.get("achievements") or []
        checks = {
            "experience": counts.get("experience") == prog.get("experience"),
            "inventoryRows": counts.get("inventoryRows") == len(inv.get("items") or []),
            "statsNonZero": counts.get("statsNonZero") == sum(1 for v in stats.values() if v),
        }
        # achievements 计数为建议字段：有就查，没有不判 fail
        if "achievements" in counts:
            checks["achievements"] = counts.get("achievements") == len(ach)
        add(f"V04[{sid}] counts 自检", all(checks.values()),
            f"{counts} | 逐个对={checks} | 实际 exp={prog.get('experience')} 行={len(inv.get('items') or [])} "
            f"非零={sum(1 for v in stats.values() if v)} 成就={len(ach)}")

    failed = [r for r in results if r["status"] == "FAIL"]
    return {"export": str(export_dir), "baseline": str(baseline_dir),
            "players": dirs, "criteria": results,
            "passed": len(results) - len(failed), "failed": len(failed),
            "verdict": "PASS" if not failed else "FAIL"}


def format_text(report: dict) -> str:
    out = [f"== save-export 校验（PASS {report['passed']} / FAIL {report['failed']}）==",
           f"   export={report['export']}", f"   baseline={report['baseline']}",
           f"   players={report['players']}"]
    for r in report["criteria"]:
        mark = "✓" if r["status"] == "PASS" else "✗"
        out.append(f"  [{mark}] {r['id']}  {r['detail']}")
    out.append(f"  结论：{'✓✓ 通过' if report['verdict'] == 'PASS' else '✗ 未通过'}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验 save-export 产物是否符合自建服契约（只读）")
    ap.add_argument("--export", required=True, help="导出根目录（含 players/ 与 index.json）")
    ap.add_argument("--baseline", required=True, help="基线抓包目录（空号或主号）")
    ap.add_argument("--shape-ref", default=None,
                    help="参照基线（基线里某些数组为空时，用它学元素形状；通常给另一套基线）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    report = check(Path(args.export), Path(args.baseline),
                   Path(args.shape_ref) if args.shape_ref else None)
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else format_text(report))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
