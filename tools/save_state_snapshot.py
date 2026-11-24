#!/usr/bin/env python3
"""存档机制单人测试：引擎隔离状态的「快照 / 对比」工具（2026-11-24 加）。

为什么需要：存档测试（S1/S2/S5/S6/S7）判据是"账本发生了什么"，靠肉眼看 JSON 不可靠。
本工具只读引擎的隔离状态目录（`<state>/runtime/saves/`），一键抽成紧凑快照，再对比前后差：

    python tools/save_state_snapshot.py snapshot --state-dir <隔离状态> --out before.json
    python tools/save_state_snapshot.py snapshot --state-dir <隔离状态> --out after.json
    python tools/save_state_snapshot.py diff --before before.json --after after.json

覆盖的判据（对应 `docs/SAVE-TEST-SINGLE-PERSON-2026-11-24.md`）：
  * 换号不混档：identity.steam_id / hd2_account_id 是否按账号切换，是否新建了别人的账本文件；
  * 存档闭环：experience / achievements / package_progress / inventory / stats 的增量；
  * 跨会话：`_last.json`、`_session-start/*.json`（会话基准是否重建）、`updated_at`；
  * 异常回滚：`.bak` 是否存在、sha 是否变化；
  * 结算事件流：`settlements.jsonl` 行数与末条 missionId；
  * 购买/消耗：`purchases`、`purchased_season_pass_items`、`inventory_amounts` 的变化。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

TOP_MIXES = 8          # inventory_amounts 只记前 N 大（避免快照过大）
EARNED_SCALARS = ("experience", "onboarding_flags", "onboarding_state", "ship_name", "location")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _summarize_ledger(path: Path) -> dict:
    """把一个玩家账本压成判据需要的摘要（不复制整份内容）。"""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"file": path.name, "error": repr(exc), "sha256": sha256_file(path)}
    earned = doc.get("earned") or {}
    ident = doc.get("identity") or {}
    amounts = earned.get("inventory_amounts") or {}
    stats = earned.get("stats") or {}
    missions = earned.get("missions") or {}
    purchases = earned.get("purchases") or {}
    recent = (missions.get("recent_ids") if isinstance(missions, dict) else []) or []
    # 真实引擎的 purchases/missions 是 {count, recent_ids/recent_keys, last} 结构；
    # 也兼容旧式 list（单测用），避免 KeyError。
    purchases_count = (purchases.get("count") if isinstance(purchases, dict)
                       else len(purchases))
    purchases_last = (purchases.get("last") if isinstance(purchases, dict)
                      else (purchases[-1] if purchases else None))
    missions_count = (missions.get("count") if isinstance(missions, dict) and missions.get("count") is not None
                      else len(recent))
    season_items = earned.get("purchased_season_pass_items") or []
    season_items_count = len(season_items) if isinstance(season_items, (list, dict)) else 0
    top = sorted(((str(k), v) for k, v in amounts.items()), key=lambda kv: -abs(int(kv[1] or 0)))[:TOP_MIXES]
    return {
        "file": path.name,
        "sha256": sha256_file(path),
        "save_id": doc.get("save_id"),
        "updated_at": doc.get("updated_at"),
        "catalog_build": doc.get("catalog_build"),
        "identity": {
            "steam_id": ident.get("steam_id"),
            "hd2_account_id": ident.get("hd2_account_id"),
            "source": ident.get("source"),
        },
        "earned": {
            **{k: earned.get(k) for k in EARNED_SCALARS},
            "achievements_count": len(earned.get("achievements") or {}),
            "achievements": earned.get("achievements") or {},
            "package_progress": earned.get("package_progress") or {},
            "inventory_rows": len(amounts),
            "inventory_top": dict(top),
            "inventory_amount_sum": sum(int(v or 0) for v in amounts.values()),
            "stats_nonzero": sum(1 for v in stats.values() if v),
            "stats_total_enemy_kills": stats.get("totalEnemyKills"),
            "purchased_season_pass_items": season_items_count,
            "missions_count": missions_count,
            "missions_last": missions.get("last") if isinstance(missions, dict) else None,
            "purchases_count": purchases_count,
            "purchases_last": purchases_last,
        },
    }


def capture(state_dir: Path | str) -> dict:
    """读取隔离状态目录，输出可对比的快照。"""
    state = Path(state_dir)
    saves = state / "runtime" / "saves"
    out: dict = {"state_dir": str(state), "exists": saves.is_dir(), "saves": {}, "extras": {}}
    if not saves.is_dir():
        return out

    for path in sorted(saves.glob("*.json")):
        if path.name.startswith("_") or path.name.startswith("local."):
            continue
        out["saves"][path.stem] = _summarize_ledger(path)
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.exists():
            out["saves"][path.stem]["bak"] = {"exists": True, "sha256": sha256_file(bak)}
        else:
            out["saves"][path.stem]["bak"] = {"exists": False}

    # 预绑定 / 会话基准 / 事件流
    last = saves / "_last.json"
    if last.exists():
        try:
            out["extras"]["_last"] = json.loads(last.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            out["extras"]["_last"] = {"error": "parse"}
    sess = {}
    for path in sorted((saves / "_session-start").glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            sess[path.stem] = {"error": "parse"}
            continue
        sess[path.stem] = {k: doc.get(k) for k in
                           ("schema", "save_id", "run_id", "started_at", "existed", "sha")}
        doc_body = doc.get("doc")
        sess[path.stem]["has_doc"] = doc_body is not None
        sess[path.stem]["doc_keys"] = sorted(doc_body) if isinstance(doc_body, dict) else None
    out["extras"]["session_start"] = sess

    settlements = {}
    for path in sorted(saves.glob("*/settlements.jsonl")):
        lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        mids = []
        for ln in lines:
            try:
                mids.append(str(json.loads(ln).get("missionId")))
            except Exception:  # noqa: BLE001
                mids.append("<bad>")
        settlements[path.relative_to(saves).as_posix()] = {
            "lines": len(lines), "mission_ids": mids,
            "sha256": sha256_file(path),
        }
    out["extras"]["settlements"] = settlements
    return out


def _diff_dict(before: dict, after: dict, prefix: str, changes: list[str]) -> None:
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b != a:
            changes.append(f"{prefix}{key}: {json.dumps(b, ensure_ascii=False)[:120]} → {json.dumps(a, ensure_ascii=False)[:120]}")


def diff(before: dict, after: dict) -> dict:
    """对比两份快照，输出人类可读的变化清单（判据直接看这里）。"""
    changes: list[str] = []
    b_saves, a_saves = before.get("saves", {}), after.get("saves", {})
    for sid in sorted(set(a_saves) - set(b_saves)):
        changes.append(f"[新账本] {sid}")
    for sid in sorted(set(b_saves) - set(a_saves)):
        changes.append(f"[账本消失] {sid}")
    for sid in sorted(set(b_saves) & set(a_saves)):
        b, a = b_saves[sid], a_saves[sid]
        for field in ("save_id", "updated_at", "catalog_build"):
            if b.get(field) != a.get(field):
                changes.append(f"{sid}.{field}: {b.get(field)} → {a.get(field)}")
        if b.get("identity") != a.get("identity"):
            changes.append(f"{sid}.identity: {json.dumps(b.get('identity'), ensure_ascii=False)} → "
                           f"{json.dumps(a.get('identity'), ensure_ascii=False)}")
        be, ae = b.get("earned") or {}, a.get("earned") or {}
        for field in ("experience", "achievements_count", "inventory_rows", "inventory_amount_sum",
                      "stats_nonzero", "stats_total_enemy_kills", "purchased_season_pass_items",
                      "missions_count", "purchases_count"):
            if be.get(field) != ae.get(field):
                changes.append(f"{sid}.earned.{field}: {be.get(field)} → {ae.get(field)}")
        if be.get("achievements") != ae.get("achievements"):
            changes.append(f"{sid}.achievements: {json.dumps(be.get('achievements'), ensure_ascii=False)} → "
                           f"{json.dumps(ae.get('achievements'), ensure_ascii=False)}")
        if be.get("package_progress") != ae.get("package_progress"):
            changes.append(f"{sid}.package_progress: {json.dumps(be.get('package_progress'), ensure_ascii=False)} → "
                           f"{json.dumps(ae.get('package_progress'), ensure_ascii=False)}")
        if be.get("inventory_top") != ae.get("inventory_top"):
            changes.append(f"{sid}.inventory_top: {json.dumps(be.get('inventory_top'), ensure_ascii=False)} → "
                           f"{json.dumps(ae.get('inventory_top'), ensure_ascii=False)}")
        if be.get("missions_last") != ae.get("missions_last"):
            changes.append(f"{sid}.missions_last: {json.dumps(be.get('missions_last'), ensure_ascii=False)[:160]} → "
                           f"{json.dumps(ae.get('missions_last'), ensure_ascii=False)[:160]}")
        if be.get("purchases_last") != ae.get("purchases_last"):
            changes.append(f"{sid}.purchases_last: {json.dumps(be.get('purchases_last'), ensure_ascii=False)[:160]} → "
                           f"{json.dumps(ae.get('purchases_last'), ensure_ascii=False)[:160]}")
        if (b.get("bak") or {}).get("sha256") != (a.get("bak") or {}).get("sha256"):
            changes.append(f"{sid}.bak: {json.dumps(b.get('bak'), ensure_ascii=False)} → "
                           f"{json.dumps(a.get('bak'), ensure_ascii=False)}")
        if b.get("sha256") != a.get("sha256"):
            changes.append(f"{sid}.file_sha: {str(b.get('sha256'))[:12]}… → {str(a.get('sha256'))[:12]}…")

    _diff_dict(before.get("extras", {}).get("_last", {}),
               after.get("extras", {}).get("_last", {}), "_last.", changes)
    _diff_dict(before.get("extras", {}).get("session_start", {}),
               after.get("extras", {}).get("session_start", {}), "_session-start.", changes)
    _diff_dict(before.get("extras", {}).get("settlements", {}),
               after.get("extras", {}).get("settlements", {}), "settlements.", changes)
    return {"changes": changes, "changed": bool(changes)}


def format_text(result: dict) -> str:
    if not result["changed"]:
        return "== 存档状态对比：**无变化** =="
    return "== 存档状态对比（%d 处变化）==\n" % len(result["changes"]) + "\n".join(
        f"  · {c}" for c in result["changes"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="引擎隔离状态的快照/对比（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    snap = sub.add_parser("snapshot")
    snap.add_argument("--state-dir", required=True, help="隔离状态目录（含 runtime/saves/）")
    snap.add_argument("--out", required=True)

    dif = sub.add_parser("diff")
    dif.add_argument("--before", required=True)
    dif.add_argument("--after", required=True)

    ap.add_argument("--json", action="store_true", help="diff 输入/输出均为 JSON 报告")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    if args.cmd == "snapshot":
        snap_doc = capture(Path(args.state_dir))
        Path(args.out).write_text(json.dumps(snap_doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"快照已写 {args.out}：账本 {len(snap_doc['saves'])} 个、存在={snap_doc['exists']}")
        return 0

    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    result = diff(before, after)
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else format_text(result))
    return 0 if not result["changed"] else 0  # 对比本身总是成功；是否有变化由调用方判断


if __name__ == "__main__":
    raise SystemExit(main())
