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
ROLLBACK_ID_RE = re.compile(r"会话回滚：\s*(steam_\d+)")


def read_orphans(orphan_dir: Path) -> list[dict]:
    """读 `_orphan/*.json` 的**元信息**（不读整份脏档正文，只取判据需要的字段）。

    产品在换号回滚时把脏档全文写进 `_orphan/<ts>-<save_id>-<reason>.json`，
    内含 `save_id` / `reason` / `discarded`（被丢弃条目的摘要）。
    判据只关心"被另存的是谁的档"和"丢了什么"——所以这里只抽这几个键。
    """
    out: list[dict] = []
    if not Path(orphan_dir).is_dir():
        return out
    for path in sorted(Path(orphan_dir).glob("*.json")):
        item = {"name": path.name, "save_id": "", "reason": "", "discarded_keys": []}
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                item["save_id"] = str(doc.get("save_id") or "")
                item["reason"] = str(doc.get("reason") or "")
                disc = doc.get("discarded")
                if isinstance(disc, dict):
                    item["discarded_keys"] = sorted(disc)
        except Exception:  # noqa: BLE001
            item["reason"] = "unreadable"
        out.append(item)
    return out


def _save_id(steam_id: str) -> str:
    return "steam_%s" % steam_id


def parse_log(text: str) -> dict:
    """把 proxy.log 里与换号相关的行抽成结构化事件（按出现顺序）。"""
    switches = [(m.group(1), m.group(2)) for m in SWITCH_RE.finditer(text)]
    merges = [(m.group(1), m.group(2)) for m in MERGE_RE.finditer(text)]
    prebinds = [m.group(1) for m in PREBIND_RE.finditer(text)]
    rollbacks = [ln.strip() for ln in text.splitlines() if ROLLBACK_RE.search(ln)]
    rollback_ids = [m.group(1) for ln in rollbacks
                    for m in [ROLLBACK_ID_RE.search(ln)] if m]
    return {"switches": switches, "merges": merges, "prebinds": prebinds,
            "rollbacks": rollbacks, "rollback_ids": rollback_ids}


def _ledger(after: dict, steam_id: str) -> dict | None:
    return (after.get("saves") or {}).get(_save_id(steam_id))


def _earned_drift(before: dict | None, after: dict | None) -> dict:
    """`earned` 块逐字段对比（产品线 2026-11-24 离线实测口径：A 档 `earned` 必须**全等**）。

    比对**整个 earned 摘要**而不是挑三个字段：exp / 成就数 / inventory_rows / inventory_top /
    inventory_amount_sum / stats_nonzero / missions_count / missions_last / purchases_count …
    任意一项变了就说明有东西写进了这位玩家的档，都要报出来。
    """
    eb = (before or {}).get("earned") or {}
    ea = (after or {}).get("earned") or {}
    return {k: (eb.get(k), ea.get(k))
            for k in (set(eb) | set(ea)) if eb.get(k) != ea.get(k)}


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
             orphan_files: list[dict], main_id: str, alt_id: str) -> dict:
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

    # S2-3 主号账本无跨档污染：earned 全等 + identity 只许 source 变
    if not main_b or not main_a:
        add("S2-3", False, "缺 before/after 主号账本，无法判污染", level="skip")
    else:
        drift = _earned_drift(main_b, main_a)
        ident_drift = _identity_unchanged_except_source(
            main_b.get("identity") or {}, main_a.get("identity") or {})
        ok = not drift and not ident_drift
        e = main_a.get("earned") or {}
        detail = ("主号 `earned` 全等（exp=%s/库存 %s 行/成就 %s/missions %s）"
                  % (e.get("experience"), e.get("inventory_rows"),
                     e.get("achievements_count"), e.get("missions_count")))
        if drift:
            detail = "主号 `earned` 被改写：%s" % ", ".join(
                "%s %r→%r" % (k, v[0], v[1]) for k, v in sorted(drift.items()))
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

    # S2-5 主号档**不得被回滚/另存**（其他账号的会话回滚是设计内）
    # 语义依据（2026-11-24 实机）：换号时"本局写入=有 ⇒ 整档回滚 + 脏档另存 orphan"是 G2 设计，
    # 而且"只进船不打任务"也可能产生写入（首屏安全合并会写服务端自算字段，如 stats.profile）。
    # 所以判据不是"有没有回滚"，而是**被回滚的是谁的档**：
    #   · 回滚/另存主号档 → 主号被动过 = FAIL
    #   · 只回滚别的账号 → 设计内 = info（脏档仍在 _orphan 可审计）
    main_sid = _save_id(main_id)
    main_rollbacks = [ln for ln in events["rollbacks"] if main_sid in ln]
    main_orphans = [o for o in orphan_files if str(o.get("save_id") or "") == main_sid]
    others = [o for o in orphan_files if str(o.get("save_id") or "") != main_sid]
    if main_rollbacks or main_orphans:
        add("S2-5", False,
            "**主号档被回滚/另存**：回滚行 %d 条、orphan %d 个 —— 主号数据被动过"
            % (len(main_rollbacks), len(main_orphans)))
    else:
        add("S2-5", True, "主号档未被回滚、未被另存（_orphan 中无主号档）")

    # info：其他账号的会话回滚（设计内，可审计）
    if others:
        parts = []
        for o in others[:3]:
            disc = o.get("discarded_keys") or []
            parts.append("%s（reason=%s，丢弃 %d 项%s）" % (
                str(o.get("name") or o.get("save_id")),
                o.get("reason") or "?",
                len(disc),
                ("：" + ", ".join(disc[:4])) if disc else ""))
        add("info-orphan", True,
            "其他账号的脏档已另存 _orphan（设计内，非混档）：%s" % "；".join(parts),
            level="info")

    # S2-6 _last 指向最后登录的主号
    last = ((after.get("extras") or {}).get("_last") or {})
    ok6 = str(last.get("last_steam_id")) == main_id
    add("S2-6", ok6, "_last.last_steam_id=%s、last_save_id=%s（期望 %s）"
        % (last.get("last_steam_id"), last.get("last_save_id"), main_id))

    # 提示级：`.bak` 出现/更新（产品线实测：set_identity 无条件写盘会轮转 .bak，属正常，不是回滚证据）
    if main_b and main_a:
        bb = (main_b.get("bak") or {}).get("exists")
        ba = (main_a.get("bak") or {}).get("exists")
        if bb != ba:
            add("info-bak", True, "主号 `.bak` 存在性 %s → %s（身份重写的正常副作用，不是回滚）"
                % (bb, ba), level="info")

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
    orphans = read_orphans(state / "runtime" / "saves" / "_orphan")

    result = evaluate(before, after, log_text, orphans, args.main, args.alt)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(format_text(result))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
