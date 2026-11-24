"""`save-export` 产物校验器的回归测试（2026-11-24 与 MITM 线定契约时新增）。

每个用例造一对最小「基线目录 + 导出目录」，覆盖 shape 保真、合并语义、counts 自检、身份一致性。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import validate_save_export as vse

BASELINE_PROGRESSION = {
    "experience": 1,
    "achievements": [],
    "assignments": [{"id32": 1, "progress": 0, "expiresIn": 100, "setting": 0}],
    "packageProgress": [{"id32": 7, "level": 1, "progress": 10, "claimedLevels": 0, "sortOrder": 1}],
    "purchasedSeasonPassItems": [],
    "onboardingFlags": 0,
    "onboardingState": 0,
    "shipName": [],
    "selectedProgressionPackage": 1,
    "activeSeasonPass": 2,
    "unknownExtra": {},
}
BASELINE_INVENTORY = {
    "items": [{"mixId": 100, "amount": 5, "isDiscovered": False},
              {"mixId": 200, "amount": 1000, "isDiscovered": True}],
    "limitSettings": [],
    "loadouts": [],
    "customization": [],
    "avatarCustomization": {},
}
BASELINE_STATS = {"kills": 0, "deaths": 0, "shots": 0}


def _write_baseline(root: Path, progression=None, inventory=None, stats=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "aaaa_api_Progression.json").write_text(
        json.dumps(progression or BASELINE_PROGRESSION, ensure_ascii=False), encoding="utf-8")
    (root / "bbbb_api_Progression_inventory.json").write_text(
        json.dumps(inventory or BASELINE_INVENTORY, ensure_ascii=False), encoding="utf-8")
    (root / "cccc_api_Stats_profile_summary.json").write_text(
        json.dumps(stats or BASELINE_STATS, ensure_ascii=False), encoding="utf-8")
    return root


def _manifest(sid="76561198968648344", *, counts=None, catalog="93e7575a/3021/1007203",
              hd2="11893343864225233967", baseline_sha="a" * 64) -> dict:
    return {
        "schema": 1,
        "saveId": f"steam_{sid}",
        "identity": {"steamId64": sid, "hd2AccountId": hd2},
        "catalogBuild": catalog,
        "generatedAt": "2026-11-24T13:00:00+08:00",
        "engine": {"version": "0.7.0-alpha.1", "mergePolicy": "max-monotonic/v1"},
        "source": {"baselineSha256": baseline_sha, "ledgerSha256": "b" * 64,
                   "baselineDir": "data/fixtures/saves/fresh"},
        "counts": counts or {"experience": 1, "inventoryRows": 2, "statsNonZero": 0, "missions": 0},
    }


def _write_export(root: Path, sid="76561198968648344", *, progression, inventory, stats,
                  manifest=None, catalog=None) -> Path:
    pdir = root / "players" / sid
    pdir.mkdir(parents=True, exist_ok=True)
    man = manifest if manifest is not None else _manifest(sid)
    (pdir / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (pdir / "progression.json").write_text(json.dumps(progression, ensure_ascii=False), encoding="utf-8")
    (pdir / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
    (pdir / "stats_profile.json").write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    (root / "index.json").write_text(json.dumps({
        "schema": 1,
        "players": [{"steamId64": sid, "hd2AccountId": "11893343864225233967",
                     "saveId": f"steam_{sid}", "updatedAt": "2026-11-24T13:00:00+08:00"}],
    }, ensure_ascii=False), encoding="utf-8")
    return root


def _ideal():
    """一个合法导出：exp 抬到 210、成就新增 1 条、统计抬升、库存只加不降。"""
    prog = json.loads(json.dumps(BASELINE_PROGRESSION))
    prog["experience"] = 210
    prog["achievements"] = [{"id": 6, "value": 1680}]
    inv = json.loads(json.dumps(BASELINE_INVENTORY))
    inv["items"].append({"mixId": 897894480, "amount": 1, "isDiscovered": True})
    stats = {"kills": 356105, "deaths": 0, "shots": 12}
    man = _manifest(counts={"experience": 210, "inventoryRows": 3, "statsNonZero": 2,
                            "missions": 1, "achievements": 1})
    return prog, inv, stats, man


def test_valid_export_passes(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    assert report["verdict"] == "PASS", [c for c in report["criteria"] if c["status"] == "FAIL"]


def test_missing_file_fails(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    (exp / "players" / "76561198968648344" / "stats_profile.json").unlink()
    report = vse.check(exp, base)
    assert report["verdict"] == "FAIL"
    assert any(c["id"].startswith("S03") and c["status"] == "FAIL" for c in report["criteria"])


def test_key_order_drift_fails(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    reordered = {k: prog[k] for k in reversed(list(prog))}   # 只改键序
    exp = _write_export(tmp_path / "exp", progression=reordered, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    assert any(c["id"].startswith("F01") and c["status"] == "FAIL" for c in report["criteria"])


def test_inventory_row_lost_or_lowered_fails(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    inv["items"] = [{"mixId": 100, "amount": 1, "isDiscovered": False}]   # 丢一行 + 降一行
    man = dict(man, counts={"experience": 210, "inventoryRows": 1, "statsNonZero": 2, "missions": 1})
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    ids = {c["id"].split("[")[0] for c in report["criteria"] if c["status"] == "FAIL"}
    assert {"V01", "V02"} <= ids


def test_stats_regression_fails(tmp_path: Path):
    # 基线统计非零、导出反而变小 ⇒ V03 必须 fail（模拟"把本地战绩抹掉"）
    base = _write_baseline(tmp_path / "base", stats={"kills": 100, "deaths": 0, "shots": 5})
    prog, inv, stats, man = _ideal()
    stats = {"kills": 50, "deaths": 0, "shots": 5}
    man = dict(man, counts={"experience": 210, "inventoryRows": 3, "statsNonZero": 2, "missions": 1})
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    assert any(c["id"].startswith("V03") and c["status"] == "FAIL" for c in report["criteria"])


def test_counts_mismatch_fails(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    man = dict(man, counts={"experience": 999, "inventoryRows": 99, "statsNonZero": 1, "missions": 1})
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    assert any(c["id"].startswith("V04") and c["status"] == "FAIL" for c in report["criteria"])


def test_identity_mismatch_fails(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    man = dict(man, identity={"steamId64": "76500000000000000", "hd2AccountId": "11893343864225233967"})
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    assert any(c["id"].startswith("M01") and c["status"] == "FAIL" for c in report["criteria"])


def test_catalog_build_key_must_exist_but_may_be_null(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats,
                        manifest=dict(man, catalogBuild=None))
    assert vse.check(exp, base)["verdict"] == "PASS"

    man2 = dict(man)
    man2.pop("catalogBuild")
    exp2 = _write_export(tmp_path / "exp2", progression=prog, inventory=inv, stats=stats, manifest=man2)
    report = vse.check(exp2, base)
    assert any(c["id"].startswith("M04") and c["status"] == "FAIL" for c in report["criteria"])


def test_shape_ref_supplies_element_shape_for_empty_baseline_array(tmp_path: Path):
    """空号基线 achievements 为空 ⇒ 用主号基线（shape-ref）学元素键，再比对导出元素。"""
    base = _write_baseline(tmp_path / "base")
    ref_prog = json.loads(json.dumps(BASELINE_PROGRESSION))
    ref_prog["achievements"] = [{"id": 6, "value": 1680}]
    ref = _write_baseline(tmp_path / "ref", progression=ref_prog)
    prog, inv, stats, man = _ideal()          # achievements = [{id,value}] ✓
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    assert vse.check(exp, base, ref)["verdict"] == "PASS"

    prog_bad = json.loads(json.dumps(prog))
    prog_bad["achievements"] = [{"id": 6, "value": 1680, "extra": True}]   # 多字段
    exp2 = _write_export(tmp_path / "exp2", progression=prog_bad, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp2, base, ref)
    assert any(c["id"].startswith("F04") and c["status"] == "FAIL" for c in report["criteria"])


def test_cli_exit_codes(tmp_path: Path, capsys):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    assert vse.main(["--export", str(exp), "--baseline", str(base), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "PASS" and data["failed"] == 0

    (exp / "index.json").unlink()      # 缺 index.json 应报 FAIL 而不是抛异常
    assert vse.main(["--export", str(exp), "--baseline", str(base)]) == 1


# ---- baselineFiles 自描述（alpha.1 新增，2026-11-24） -------------------------

def _manifest_with_baseline_files(tmp_path: Path, *, tamper_sha: bool = False,
                                  tamper_recipe: bool = False) -> dict:
    import hashlib
    base = tmp_path / "base"
    files = {}
    for ep, name in (("progression", "aaaa_api_Progression.json"),
                     ("inventory", "bbbb_api_Progression_inventory.json"),
                     ("stats_profile", "cccc_api_Stats_profile_summary.json")):
        sha = hashlib.sha256((base / name).read_bytes()).hexdigest()
        if tamper_sha and ep == "progression":
            sha = "0" * 64
        files[ep] = {"name": name, "sha256": sha}
    recipe = "".join(f"{k}:{files[k]['sha256']}" + chr(10) for k in sorted(files))
    total = hashlib.sha256(recipe.encode()).hexdigest()
    if tamper_recipe:
        total = "1" * 64
    man = _manifest(counts={"experience": 210, "inventoryRows": 3, "statsNonZero": 2,
                            "missions": 1, "achievements": 1})
    man["source"].update({"baselineId": "fresh", "baselineDirResolved": str(base.resolve()),
                          "baselineFiles": files, "baselineSha256": total})
    return man


def test_baseline_files_recipe_is_verified(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, _ = _ideal()
    man = _manifest_with_baseline_files(tmp_path)
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    assert report["verdict"] == "PASS", [c for c in report["criteria"] if c["status"] == "FAIL"]


def test_baseline_sha_recipe_mismatch_fails(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, _ = _ideal()
    man = _manifest_with_baseline_files(tmp_path, tamper_recipe=True)
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    assert any(c["id"].startswith("M06") and c["status"] == "FAIL" for c in report["criteria"])


def test_baseline_file_sha_mismatch_against_local_baseline_fails(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, _ = _ideal()
    man = _manifest_with_baseline_files(tmp_path, tamper_sha=True)   # 声明的 progression sha 与本地基线不符
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    assert any(c["id"].startswith("M07") and c["status"] == "FAIL" for c in report["criteria"])


def test_old_export_without_baseline_files_still_passes(tmp_path: Path):
    """alpha.0 时代没有 baselineFiles：只提示，不判失败（校验器向后可用）。"""
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    assert "baselineFiles" not in man["source"]
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    report = vse.check(exp, base)
    assert report["verdict"] == "PASS"


# ---- settlements.jsonl 事件流（2026-11-24 新增） ---------------------------

def _jline(**kw) -> str:
    base = {"schema": 1, "event": "settlement", "ts": "2026-11-24T13:39:43+08:00",
            "saveId": "steam_76561198968648344", "steamId64": "76561198968648344",
            "hd2AccountId": "11893343864225233967", "missionId": "m-1", "operationId": 1,
            "missionIndex": 2, "experience": 936, "items": {"897894480": 7},
            "packages": {"277818845": {"level": 5, "progress": 459,
                                       "platformAccountId": "11893343864225233967"}},
            "itemSource": "rewards", "catalogBuild": "93e7575a/3021/1007203",
            "source": "mission_end"}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def _write_jsonl(tmp_path, lines: list[str]) -> Path:
    p = tmp_path / "settlements.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


EXPECTED = {"steamId64": "76561198968648344", "hd2AccountId": "11893343864225233967"}


def test_settlements_valid_file_passes(tmp_path: Path):
    p = _write_jsonl(tmp_path, [_jline(missionId="m-1"), _jline(missionId="m-2", packages={})])
    crit = vse.check_settlements(p, expected=EXPECTED)
    assert all(c["status"] == "PASS" for c in crit), [c for c in crit if c["status"] == "FAIL"]


def test_settlements_bad_json_line_fails(tmp_path: Path):
    p = _write_jsonl(tmp_path, [_jline(missionId="m-1"), "{not-json"])
    crit = vse.check_settlements(p, expected=EXPECTED)
    assert any(c["id"].startswith("J01") and c["status"] == "FAIL" for c in crit)


def test_settlements_missing_required_field_fails(tmp_path: Path):
    line = json.loads(_jline(missionId="m-1"))
    line.pop("catalogBuild")
    p = _write_jsonl(tmp_path, [json.dumps(line, ensure_ascii=False)])
    crit = vse.check_settlements(p, expected=EXPECTED)
    assert any(c["id"].startswith("J02") and c["status"] == "FAIL" for c in crit)


def test_settlements_duplicate_mission_id_fails(tmp_path: Path):
    p = _write_jsonl(tmp_path, [_jline(missionId="m-1"), _jline(missionId="m-1")])
    crit = vse.check_settlements(p, expected=EXPECTED)
    assert any(c["id"].startswith("J05") and c["status"] == "FAIL" for c in crit)


def test_settlements_packages_shape_and_identity_fails(tmp_path: Path):
    bad_level = json.loads(_jline(missionId="m-1"))
    bad_level["packages"] = {"277818845": {"level": "5", "progress": 459}}
    p = _write_jsonl(tmp_path, [json.dumps(bad_level, ensure_ascii=False)])
    assert any(c["id"].startswith("J07") and c["status"] == "FAIL"
               for c in vse.check_settlements(p, expected=EXPECTED))

    wrong_pa = json.loads(_jline(missionId="m-2"))
    wrong_pa["packages"] = {"277818845": {"level": 5, "progress": 459,
                                          "platformAccountId": "99999999999999999999"}}
    p2 = _write_jsonl(tmp_path, [json.dumps(wrong_pa, ensure_ascii=False)])
    assert any(c["id"].startswith("J07") and c["status"] == "FAIL"
               for c in vse.check_settlements(p2, expected=EXPECTED))


def test_settlements_ts_without_timezone_fails(tmp_path: Path):
    p = _write_jsonl(tmp_path, [_jline(missionId="m-1", ts="2026-11-24T13:39:43")])
    crit = vse.check_settlements(p, expected=EXPECTED)
    assert any(c["id"].startswith("J08") and c["status"] == "FAIL" for c in crit)


def test_settlements_source_enums_and_cursor(tmp_path: Path):
    lines = [_jline(missionId="m-1", itemSource="mission_end"),     # 非法 itemSource
             _jline(missionId="m-2", source="unknown-src")]         # 非法 source
    p = _write_jsonl(tmp_path, lines)
    crit = vse.check_settlements(p, expected=EXPECTED, cursor={"lines": 1, "lastMissionId": "m-1"})
    ids = {c["id"].split(" ")[0]: c["status"] for c in crit}
    assert ids["J09"] == "FAIL"
    assert ids["J10"] == "FAIL"          # 游标行数/末条都对不上


def test_cli_settlements_file_only(tmp_path: Path, capsys):
    p = _write_jsonl(tmp_path, [_jline(missionId="m-1")])
    rc = vse.main(["--settlements-file", str(p), "--steam-id64", EXPECTED["steamId64"],
                   "--hd2-account-id", EXPECTED["hd2AccountId"], "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "PASS" and data["passed"] >= 9


def test_settlements_source_required_and_counter_consistent(tmp_path: Path):
    # 缺 source ⇒ J11 FAIL
    line = json.loads(_jline(missionId="m-1", source="mission_end"))
    line.pop("source")
    p = _write_jsonl(tmp_path, [json.dumps(line, ensure_ascii=False)])
    crit = vse.check_settlements(p, expected=EXPECTED)
    assert any(c["id"].startswith("J11") and c["status"] == "FAIL" for c in crit)

    # 计数不一致 ⇒ J11 FAIL；一致 ⇒ PASS
    p2 = _write_jsonl(tmp_path, [_jline(missionId="m-2", source="ledger-only")])
    bad = vse.check_settlements(p2, expected=EXPECTED, counters={"ledgerOnly": 5})
    assert any(c["id"].startswith("J11") and c["status"] == "FAIL" for c in bad)
    good = vse.check_settlements(p2, expected=EXPECTED, counters={"ledgerOnly": 1})
    assert all(c["status"] == "PASS" for c in good)


# ---- 一键验收：--auto-baseline（2026-11-24 收口用） --------------------------

def test_detect_baseline_id_from_manifest(tmp_path: Path):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    man = json.loads((exp / "players" / "76561198968648344" / "manifest.json").read_text(encoding="utf-8"))
    man["source"]["baselineId"] = "fresh"
    (exp / "players" / "76561198968648344" / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False), encoding="utf-8")
    assert vse.detect_baseline_id(exp) == "fresh"


def test_auto_baseline_unknown_id_errors(tmp_path: Path, capsys):
    base = _write_baseline(tmp_path / "base")
    prog, inv, stats, man = _ideal()
    exp = _write_export(tmp_path / "exp", progression=prog, inventory=inv, stats=stats, manifest=man)
    m = json.loads((exp / "players" / "76561198968648344" / "manifest.json").read_text(encoding="utf-8"))
    m["source"]["baselineId"] = "custom:weird"
    (exp / "players" / "76561198968648344" / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        vse.main(["--export", str(exp), "--auto-baseline"])
