#!/usr/bin/env python3
"""S2「换号不混档」一条命令出判定（2026-11-24 加）。

为什么需要：S2 的判据散在「账本 + `_last.json` + `_orphan/` + proxy.log」四处，
靠人工逐条看容易漏（尤其"日志里到底有没有换号痕迹"和"主号账本有没有被别人的
GetMotd 回声污染"这两条）。本工具把四处证据合成一张判定表 + 一行结论。

    python tools/save_switch_verdict.py \
        --state-dir D:/mod/tmp/save-test-20261124/s2/state \
        --before    D:/mod/tmp/save-test-20261124/s2/before.json \
        --main 76561198968648344 --alt 76561199881579811

判定项（对应 `docs/SAVE-TEST-SINGLE-PERSON-2026-11-24.md` §8）：
  S2-1 主号账本仍在、identity 仍是主号
  S2-2 副号账本被创建、identity 指向副号
  S2-3 主号账本**无跨档污染**（exp/库存行数/成就数与 before 相同）
  S2-4 日志有「身份切换」痕迹（主→副、副→主 两趟；也接受「身份确认…已合并」旧档未识别形态）
  S2-5 本局无写入 ⇒ 无「会话回滚」行、`_orphan/` 为空
  S2-6 `_last.json` 指向最后登录的主号

设计取舍：
  * **状态是事实，日志是佐证**：S2-1/2/3/5/6 只看磁盘状态；S2-4 才依赖日志文案。
  * 主号账本 sha 变化**不算失败**（引擎重写同内容/更新时间戳都会变），只作提示——
    真正的混档证据是 `S2-3` 的字段级变化（别人进度写进我的档）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from save_state_snapshot import capture  # noqa: E402

IDENTITY_TOLERANCE = ("source",)   # 允许变化的 identity 子字段（身份来源会随校验推进）

# 产品侧文案（src/hd2proxy/engine/active_save.py:760 / saves.py:797）：
#   "⚠ 身份切换：steam_A（已识别）→ steam_B；**不合并**（避免跨玩家混档）。本局写入=无，回滚=none"
#   "身份确认：steam_A（local/未识别）已合并进 steam_B 并归档"
# 产品线 2026-11-24 回复：实跑只会出现 active_save.py 那条；saves.py:797 的
# `身份切换：旧档 X（已识别）→ 新档 Y` 变体目前只在单测里，但将来可能合并——这里顺带容错。
SWITCH_RE = re.compile(
    r"身份切换：\s*(?:旧档\s*)?(steam_\d+)\s*（已识别）\s*→\s*(?:新档\s*)?(steam_\d+)")
MERGE_RE = re.compile(r"身份确认：\s*(steam_\d+)（[^）]*）\s*已合并进\s*(steam_\d+)")
PREBIND_RE = re.compile(r"首屏预绑定\s*(steam_\d+)")
ROLLBACK_RE = re.compile(r"会话回滚")


def _save_id(steam_id: str) -> str:
    return "steam_%s" % steam_id


def parse_log(text: str) -> dict:
    """把 proxy.log 里与换号相关的行抽成结构化事件（按出现顺序）。"""
    switches = [(m.group(1), m.group(2)) for m in SWITCH_RE.finditer(text)]
    merges = [(m.group(1), m.group(2)) for m in MERGE_RE.finditer(text)]
    prebinds = [m.group(1) for m in PREBIND_RE.finditer(text)]
    rollbacks = [ln.strip() for ln in text.splitlines() if ROLLBACK_RE.search(ln)]
    return {"switches": switches, "merges": merges,
            "prebinds": prebinds, "rollbacks": rollbacks}


def _ledger(after: dict, steam_id: str) -> dict | None:
    return (after.get("saves") or {}).get(_save_id(steam_id))


def _earned_counts(ledger: dict | None) -> dict:
    if not ledger:
        return {}
    e = ledger.get("earned") or {}
    return {
        "experience": e.get("experience"),
        "inventory_rows": e.get("inventory_rows"),
        "achievements_count": e.get("achievements_count"),
    }


def _identity_unchanged_except_source(before_ident: dict, after_ident: dict) -> list[str]:
    """返回 identity 里超出容许范围的字段变化（空列表 = 未变）。"""
    out = []
    for key in set(before_ident) | set(after_ident):
        if key in IDENTITY_TOLERANCE:
            continue
        if before_ident.get(key) != after_ident.get(key):
            out.append("%s: %r → %r" % (key, before_ident.get(key), after_ident.get(key)))
    return out


def evaluate(before: dict, after: dict, log_text: str,
             orphan_files: list[str], main_id: str, alt_id: str) -> dict:
    checks: list[dict] = []

    def add(cid, ok, detail, *, level="fail"):
        checks.append({"id": cid, "ok": bool(ok), "detail": detail, "level": level})

    main_b, main_a = _ledger(before, main_id), _ledger(after, main_id)
    alt_a = _ledger(after, alt_id)

    # S2-1 主号账本仍在、身份仍是主号
    if not main_a:
        add("S2-1", False, "主号账本 %s 消失" % _save_id(main_id))
    else:
        ident = main_a.get("identity") or {}
        ok = str(ident.get("steam_id")) == main_id
        add("S2-1", ok, "主号账本在，identity.steam_id=%s（期望 %s）"
            % (ident.get("steam_id"), main_id))

    # S2-2 副号账本被创建、identity 指向副号
    if not alt_a:
        add("S2-2", False, "副号账本 %s 未创建（换号没发生，或副号走了别的档）"
            % _save_id(alt_id))
    else:
        ident = alt_a.get("identity") or {}
        ok = str(ident.get("steam_id")) == alt_id
        add("S2-2", ok, "副号账本已建，identity.steam_id=%s（期望 %s）"
            % (ident.get("steam_id"), alt_id))

    # S2-3 主号账本无跨档污染（字段级）
    if not main_b or not main_a:
        add("S2-3", False, "缺 before/after 主号账本，无法判污染", level="skip")
    else:
        cb, ca = _earned_counts(main_b), _earned_counts(main_a)
        drift = {k: (cb.get(k), ca.get(k)) for k in cb if cb.get(k) != ca.get(k)}
        ident_drift = _identity_unchanged_except_source(
            main_b.get("identity") or {}, main_a.get("identity") or {})
        ok = not drift and not ident_drift
        detail = "主号进度未变（exp=%s/库存 %s 行/成就 %s）" % (
            ca.get("experience"), ca.get("inventory_rows"), ca.get("achievements_count"))
        if drift:
            detail = "主号进度被改写：%s" % ", ".join(
                "%s %r→%r" % (k, v[0], v[1]) for k, v in drift.items())
        if ident_drift:
            detail += "；identity 变化：%s" % "; ".join(ident_drift)
        add("S2-3", ok, detail)

    # S2-4 日志有换号痕迹（两趟）
    events = parse_log(log_text or "")
    pairs = events["switches"] + events["merges"]
    want = [(_save_id(main_id), _save_id(alt_id)), (_save_id(alt_id), _save_id(main_id))]
    hit = [w for w in want if w in pairs]
    if hit == want:
        add("S2-4", True, "日志两趟齐全：%s" % " ; ".join("%s→%s" % p for p in want))
    elif hit:
        missing = [w for w in want if w not in pairs]
        add("S2-4", False, "日志只见到 %d/2 趟，缺 %s" % (
            len(hit), " ; ".join("%s→%s" % p for p in missing)))
    elif pairs:
        add("S2-4", False, "日志有换号痕迹但方向不符：%s" % " ; ".join(
            "%s→%s" % p for p in pairs))
    else:
        add("S2-4", False, "日志无「身份切换 / 已合并」行（引擎没认出换号，或日志被截断）")

    # S2-5 无写入 ⇒ 无回滚、无 orphan
    ok5 = not events["rollbacks"] and not orphan_files
    detail5 = "无会话回滚、_orphan/ 为空"
    if events["rollbacks"]:
        detail5 = "出现 %d 条「会话回滚」——本局有过写入（换号前打了任务）" % len(
            events["rollbacks"])
    if orphan_files:
        detail5 += "；_orphan/ 有 %d 个文件：%s" % (
            len(orphan_files), ", ".join(orphan_files[:3]))
    add("S2-5", ok5, detail5)

    # S2-6 _last 指向最后登录的主号
    last = ((after.get("extras") or {}).get("_last") or {})
    ok6 = str(last.get("last_steam_id")) == main_id
    add("S2-6", ok6, "_last.last_steam_id=%s、last_save_id=%s（期望 %s）"
        % (last.get("last_steam_id"), last.get("last_save_id"), main_id))

    # 提示级：sha 变化（不算失败）
    if main_b and main_a and main_b.get("sha256") != main_a.get("sha256"):
        add("info-sha", True, "主号账本 sha 变了（%s→%s）——引擎重写过，但字段级无污染"
            % (str(main_b.get("sha256"))[:12], str(main_a.get("sha256"))[:12]),
            level="info")

    failed = [c for c in checks if not c["ok"] and c["level"] == "fail"]
    skipped = [c for c in checks if c["level"] == "skip"]
    return {
        "verdict": "PASS" if not failed and not skipped else ("SKIP" if skipped else "FAIL"),
        "checks": checks,
        "events": events,
        "saves_before": sorted((before.get("saves") or {})),
        "saves_after": sorted((after.get("saves") or {})),
        "failed": [c["id"] for c in failed],
    }


def format_text(result: dict) -> str:
    lines = ["== S2 换号不混档判定 =="]
    marks = {"fail": "", "skip": "[skip] ", "info": "[info] "}
    for c in result["checks"]:
        """汇总一处：ok -> PASS/FAIL 标签放最前，方便肉眼扫。"""
        tag = ("[info] " if c["level"] == "info"
               else ("[skip] " if c["level"] == "skip"
                     else ("PASS " if c["ok"] else "FAIL ")))
        lines.append("  %s%s  %s" % (tag, c["id"], c["detail"]))
    ev = result["events"]
    if ev["prebinds"]:
        lines.append("  预绑定序列：" + " → ".join(ev["prebinds"]))
    lines.append("  账本（before）：" + (", ".join(result["saves_before"]) or "（无）"))
    lines.append("  账本（after） ：" + (", ".join(result["saves_after"]) or "（无）"))
    lines.append("== 结论：%s ==" % result["verdict"])
    if result["failed"]:
        lines.append("  未通过项：" + ", ".join(result["failed"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S2 换号不混档判定")
    ap.add_argument("--state-dir", required=True, help="引擎隔离状态目录")
    ap.add_argument("--before", required=True, help="S2 开始前的快照 JSON")
    ap.add_argument("--main", required=True, help="主号 SteamID64")
    ap.add_argument("--alt", required=True, help="副号 SteamID64")
    ap.add_argument("--log", help="proxy.log 路径（默认 <state>/logs/proxy.log）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    state = Path(args.state_dir)
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = capture(state)
    log_path = Path(args.log) if args.log else state / "logs" / "proxy.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    orphan_dir = state / "runtime" / "saves" / "_orphan"
    orphans = sorted(p.name for p in orphan_dir.glob("*")) if orphan_dir.is_dir() else []

    result = evaluate(before, after, log_text, orphans, args.main, args.alt)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(format_text(result))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
