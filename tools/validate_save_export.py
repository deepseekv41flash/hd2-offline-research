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
import hashlib
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
        "stats_profile_war": "dccbac58_api_Stats_profile_801_summary.json",
        "customize_v2": "eef46741_api_Progression_Items_Customize_v2.json",
    },
    "main": {
        "progression": "f288f166_api_Progression.json",
        "inventory": "a1e6d9db_api_Progression_inventory.json",
        "stats": "dfdad048_api_Stats_profile_summary.json",
        "stats_profile_war": "dfdad048_api_Stats_profile_801_summary.json",
        "customize_v2": "544b4168_api_Progression_Items_Customize_v2.json",
    },
}
# manifest.baselineFiles 里的端点名 → 本地基线目录的 glob（找不到就跳过该端点的交叉核对）
BASELINE_GLOBS = {
    "progression": "*_api_Progression.json",
    "inventory": "*_api_Progression_inventory.json",
    "stats_profile": "*_api_Stats_profile_summary.json",
    "stats_profile_war": "*_api_Stats_profile_801_summary.json",
    "customize_v2": "*_api_Progression_Items_Customize_v2.json",
}
BASELINE_SHA_RECIPE = 'sha256("endpoint:sha256\n" 按端点名排序拼接)'  # 与产品侧发布说明一致


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


def resolve_mission_end_template(path_arg: str | None) -> Path | None:
    """找 Mission/end 参照模板：显式参数 → 仓内 samples/mission-end 里第一个非空模板。"""
    if path_arg:
        return Path(path_arg)
    cands = sorted(Path("samples/mission-end").glob("*.json"))
    for c in cands:
        try:
            if (json.loads(c.read_text(encoding="utf-8")).get("experience") or 0) > 0:
                return c
        except Exception:  # noqa: BLE001
            continue
    return cands[0] if cands else None


def key_order(obj) -> list[str]:
    return list(obj.keys()) if isinstance(obj, dict) else []


SETTLEMENT_REQUIRED = ("schema", "event", "ts", "saveId", "steamId64", "hd2AccountId",
                       "missionId", "experience", "items", "packages", "catalogBuild")
ITEM_SOURCES = {"rewards", "badges", "both", "none"}
EVENT_SOURCES = {"mission_end", "ledger-only"}
ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$")


def check_settlements(path: Path, *, expected: dict | None = None,
                      cursor: dict | None = None, counters: dict | None = None,
                      tag: str = "J") -> list[dict]:
    """校验 `settlements.jsonl`（每玩家一行一局）。返回判据列表（PASS/FAIL）。"""
    results: list[dict] = []

    def add(cid: str, ok: bool, detail: str) -> None:
        results.append({"id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})

    rows, bad_lines = [], []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            doc = json.loads(line)
        except Exception:  # noqa: BLE001
            bad_lines.append(i)
            continue
        if not isinstance(doc, dict):
            bad_lines.append(i)
            continue
        rows.append(doc)
    add(f"{tag}01 每行合法 JSON 对象", not bad_lines,
        f"{len(rows)} 行有效；坏行={bad_lines[:5]}" if bad_lines or not rows else f"{len(rows)} 行全部可解析")

    missing = [k for r in rows for k in SETTLEMENT_REQUIRED if k not in r]
    add(f"{tag}02 必填字段", not missing, f"缺 {sorted(set(missing))[:8]}" if missing else "11 个必填字段齐全")

    bad_ver = [r.get("missionId") for r in rows if r.get("schema") != 1 or r.get("event") != "settlement"]
    add(f"{tag}03 schema/event", not bad_ver,
        f"异常行={bad_ver[:3]}" if bad_ver else "schema=1、event=settlement")

    if expected:
        bad_id = [r.get("missionId") for r in rows
                  if str(r.get("steamId64")) != expected["steamId64"]
                  or str(r.get("hd2AccountId")) != expected["hd2AccountId"]
                  or str(r.get("saveId")) != f"steam_{expected['steamId64']}"]
        add(f"{tag}04 身份一致", not bad_id,
            f"与 manifest 不一致的行={bad_id[:3]}" if bad_id else "saveId/steamId64/hd2AccountId 与 manifest 一致")

    mids = [str(r.get("missionId") or "") for r in rows]
    dup = sorted({m for m in mids if mids.count(m) > 1})
    add(f"{tag}05 missionId 去重", all(mids) and not dup,
        f"空={mids.count('')}、重复={dup[:3]}" if (not all(mids) or dup) else f"{len(mids)} 条 missionId 唯一")

    bad_items = [r.get("missionId") for r in rows
                 if not isinstance(r.get("items"), dict)
                 or any(not isinstance(k, str) or not isinstance(v, int) for k, v in (r.get("items") or {}).items())
                 or not isinstance(r.get("experience"), (int, float))]
    add(f"{tag}06 数值形状", not bad_items,
        f"异常行={bad_items[:3]}" if bad_items else "experience 数值、items 为 {mixId(str): amount(int)}")

    def pkg_bad(r: dict) -> str:
        pkgs = r.get("packages")
        if not isinstance(pkgs, dict):
            return "packages 不是对象"
        for k, v in pkgs.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                return f"{k}: 元素不是对象"
            if not isinstance(v.get("level"), int) or not isinstance(v.get("progress"), int):
                return f"{k}: level/progress 非整数"
            pa = v.get("platformAccountId")
            if pa is not None and expected and str(pa) not in (expected["hd2AccountId"], expected["steamId64"]):
                return f"{k}: platformAccountId={pa} 与玩家身份不符"
        return ""

    bad_pkg = [(r.get("missionId"), pkg_bad(r)) for r in rows if pkg_bad(r)]
    add(f"{tag}07 packages 形状", not bad_pkg,
        f"{bad_pkg[:2]}" if bad_pkg else "packages 为 {id32:{level,progress[,platformAccountId]}} 且身份一致")

    bad_ts = [r.get("missionId") for r in rows if not ISO_TS_RE.match(str(r.get("ts") or ""))]
    add(f"{tag}08 ts 带时区", not bad_ts,
        f"异常行={bad_ts[:3]}" if bad_ts else "ISO 8601 + 显式时区")

    bad_src = [r.get("missionId") for r in rows
               if "itemSource" in r and str(r.get("itemSource")) not in ITEM_SOURCES]
    bad_evsrc = [r.get("missionId") for r in rows
                 if "source" in r and str(r.get("source")) not in EVENT_SOURCES]
    add(f"{tag}09 来源枚举", not bad_src and not bad_evsrc,
        f"itemSource 异常={bad_src[:3]}、source 异常={bad_evsrc[:3]}" if (bad_src or bad_evsrc)
        else f"itemSource ∈ {sorted(ITEM_SOURCES)}；source ∈ {sorted(EVENT_SOURCES)}")

    # J11：每行必须显式带 source（消费端不推断）；manifest.settlements 计数器须与文件一致
    no_src = [r.get("missionId") for r in rows
              if str(r.get("source") or "") not in EVENT_SOURCES]
    detail = f"缺/非法 source 的行={no_src[:3]}" if no_src else "每行都显式带 source"
    if counters is not None and isinstance(counters.get("ledgerOnly"), int):
        ledger_only = sum(1 for r in rows if str(r.get("source")) == "ledger-only")
        ok_cnt = counters["ledgerOnly"] == ledger_only
        detail += f"；manifest.ledgerOnly={counters['ledgerOnly']} vs 实际 {ledger_only}"
        add(f"{tag}11 source 覆盖与计数", not no_src and ok_cnt, detail)
    else:
        add(f"{tag}11 source 覆盖", not no_src, detail)

    if cursor is not None:
        lines_ok = cursor.get("lines") == len(rows)
        last_ok = (not rows) or str(cursor.get("lastMissionId")) == mids[-1]
        add(f"{tag}10 manifest 游标", lines_ok and last_ok,
            f"cursor.lines={cursor.get('lines')} vs 实际 {len(rows)}、"
            f"cursor.lastMissionId={cursor.get('lastMissionId')} vs 末行 {mids[-1] if mids else '-'}")
    return results


def check(export_dir: Path, baseline_dir: Path, shape_ref: Path | None = None,
          mission_end_template: Path | None = None) -> dict:
    results: list[dict] = []

    def add(cid: str, ok: bool, detail: str) -> None:
        results.append({"id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})

    export_dir = Path(export_dir)
    players_dir = export_dir / "players"
    base = resolve_baseline(Path(baseline_dir))
    ref = resolve_baseline(Path(shape_ref)) if shape_ref else None
    me_tpl_path = resolve_mission_end_template(str(mission_end_template) if mission_end_template else None)
    mission_end_tpl = load_json(me_tpl_path) if me_tpl_path and me_tpl_path.exists() else None
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
        # M05/M06/M07：baselineFiles 自描述 + 配方复算 + 与本地基线逐字节核对
        # （alpha.0 时代的导出没有 baselineFiles：缺失时只提示不判失败，保持校验器向后可用）
        bfiles = src.get("baselineFiles")
        if bfiles is None:
            add(f"M05[{sid}] baselineFiles 结构", True,
                "未提供 baselineFiles（旧格式导出；alpha.1 起应提供，建议补上以便独立复算）")
        else:
            ok_bf = isinstance(bfiles, dict) and bool(bfiles) and all(
                isinstance(v, dict) and isinstance(v.get("name"), str)
                and SHA256_RE.match(str(v.get("sha256") or ""))
                for v in bfiles.values())
            bad = ([k for k, v in bfiles.items()
                    if not isinstance(v, dict) or not SHA256_RE.match(str(v.get('sha256') or ''))]
                   if isinstance(bfiles, dict) else [])
            add(f"M05[{sid}] baselineFiles 结构", ok_bf,
                f"{len(bfiles) if isinstance(bfiles, dict) else 0} 个端点；缺/坏={bad[:5]}")
            if ok_bf:
                recipe_text = "".join(f"{k}:{bfiles[k]['sha256']}" + chr(10) for k in sorted(bfiles))
                recomputed = hashlib.sha256(recipe_text.encode()).hexdigest()
                add(f"M06[{sid}] baselineSha256 可复算", recomputed == str(src.get("baselineSha256")),
                    f"复算 {recomputed[:16]}… vs 声明 {str(src.get('baselineSha256'))[:16]}…")
                diffs, checked = [], 0
                for ep, globpat in BASELINE_GLOBS.items():
                    decl = (bfiles.get(ep) or {}).get("sha256")
                    if not decl:
                        continue
                    cands = sorted(Path(baseline_dir).glob(globpat))
                    if not cands:
                        continue
                    local = hashlib.sha256(cands[0].read_bytes()).hexdigest()
                    checked += 1
                    if local != decl:
                        diffs.append(f"{ep}:{cands[0].name}")
                add(f"M07[{sid}] 基线逐文件一致", not diffs,
                    f"baselineId={src.get('baselineId')}；核对 {checked} 个端点，差异={diffs[:5]}")

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
        # ---- 可选端点（alpha.1 起默认导出）：mission_end / customize_v2 / stats_profile_war ----
        me_path = pdir / "mission_end.json"
        if me_path.exists() and mission_end_tpl is not None:
            me = load_json(me_path)
            add(f"F06[{sid}] mission_end 键集/键序", list(me) == list(mission_end_tpl),
                f"导出 {len(me)} 键 vs 模板 {len(mission_end_tpl)} 键；多={[k for k in me if k not in mission_end_tpl][:5]}、"
                f"缺={[k for k in mission_end_tpl if k not in me][:5]}")
            bad_nested = []
            for field in ("multipliers", "badges", "rewards", "lootedItemRewards",
                          "lootedHeavyItemRewards", "progressionPackageUpdates", "levelUpRewards"):
                got, want = elem_keys(me, field), elem_keys(mission_end_tpl, field)
                if got is None and want is None:
                    continue
                if got != want:
                    bad_nested.append(f"{field}: 导出{sorted(got)} vs 模板{sorted(want)}" if got and want
                                      else f"{field}: 导出{sorted(got) if got else '空'} vs 模板{sorted(want) if want else '空'}")
            add(f"F07[{sid}] mission_end 嵌套元素键", not bad_nested, '; '.join(bad_nested) or "7 类数组元素键与模板一致")
            rp = me.get("repeatingPlayer")
            add(f"V05[{sid}] mission_end 语义", isinstance(me.get("experience"), (int, float)) and rp in ([], None),
                f"experience={me.get('experience')!r}、repeatingPlayer={rp!r}（历史实测恒空，不入账）")

        cus_path = pdir / "customize_v2.json"
        if cus_path.exists():
            cus = load_json(cus_path)
            base_cus = sorted(Path(baseline_dir).glob(BASELINE_GLOBS["customize_v2"]))
            if base_cus:
                ref_cus = load_json(base_cus[0])
                add(f"F08[{sid}] customize_v2 键集", set(cus) == set(ref_cus),
                    f"导出 {sorted(cus)[:6]} vs 基线 {sorted(ref_cus)[:6]}")

        war_path = pdir / "stats_profile_war.json"
        if war_path.exists():
            war = load_json(war_path)
            add(f"F09[{sid}] stats_profile_war 键集", set(war) == set(base_s),
                f"导出 {len(war)} 键 vs 基线 stats {len(base_s)} 键")

        # 结算事件流（可选文件；alpha.1+ 计划产出）
        set_path = pdir / "settlements.jsonl"
        if set_path.exists():
            results.extend(check_settlements(
                set_path,
                expected={"steamId64": sid,
                          "hd2AccountId": str((man.get("identity") or {}).get("hd2AccountId") or "")},
                cursor=man.get("settlementsCursor"),
                counters=man.get("settlements"),
                tag=f"J[{sid}]"))

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
    ap.add_argument("--export", default=None, help="导出根目录（含 players/ 与 index.json）")
    ap.add_argument("--baseline", default=None, help="基线抓包目录（空号或主号）")
    ap.add_argument("--shape-ref", default=None,
                    help="参照基线（基线里某些数组为空时，用它学元素形状；通常给另一套基线）")
    ap.add_argument("--mission-end-template", default=None,
                    help="Mission/end 参照模板（默认取 samples/mission-end 里 exp>0 的第一个）")
    ap.add_argument("--settlements-file", default=None,
                    help="只校验一个 settlements.jsonl（可选，配合 --json 便于先验样例）")
    ap.add_argument("--steam-id64", default=None, help="配合 --settlements-file：该玩家 SteamID64")
    ap.add_argument("--hd2-account-id", default=None, help="配合 --settlements-file：该玩家 HD2 账号 id")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    if args.settlements_file:
        expected = ({"steamId64": args.steam_id64, "hd2AccountId": args.hd2_account_id}
                    if args.steam_id64 and args.hd2_account_id else None)
        crit = check_settlements(Path(args.settlements_file), expected=expected, tag="J")
        failed = [c for c in crit if c["status"] == "FAIL"]
        report = {"export": args.settlements_file, "baseline": "-", "players": [],
                  "criteria": crit, "passed": len(crit) - len(failed), "failed": len(failed),
                  "verdict": "PASS" if not failed else "FAIL"}
        print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else format_text(report))
        return 0 if report["verdict"] == "PASS" else 1
    if not args.export or not args.baseline:
        ap.error("需要 --export 与 --baseline（或用 --settlements-file 单独校验事件流）")
    report = check(Path(args.export), Path(args.baseline),
                   Path(args.shape_ref) if args.shape_ref else None,
                   Path(args.mission_end_template) if args.mission_end_template else None)
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else format_text(report))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
