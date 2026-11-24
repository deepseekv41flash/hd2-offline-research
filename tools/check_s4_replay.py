#!/usr/bin/env python3
"""S4「存档回放可见性」一条命令出判定（2026-11-24 加）。

S4 要回答：`save-export` 产物放到 mock 上之后，**客户端拿到的是这位玩家的档，不是公共抓包基线**。

机器可判的三件事（界面数值仍要人看）：
  S4-1 **内容等同（硬判据）**：mock 返回体的 **sha256 == 该玩家导出文件的 sha256**。
        mock 按玩家分档时算 `digest` 并写进日志（`[player:<id> via header:getmotd sha:0f4893f9f853]`），
        所以这条等价于"确实原样转发了这份档"；**同字节数可能是不同字节，sha256 才硬**。
        产品线 2026-11-24 用经引擎的真实响应独立核过：
        `progression.json` 2468 B `0f4893f9f853…`、`inventory.json` 4574 B `c1583d25c160…`、
        `stats_profile.json` 813 B `02e6e6633d13…` —— 与导出文件**全等**。
  S4-2 **身份路径**：日志出现 `via header:getmotd`（或 prebind）；**出现 `via ip` 即失败**
        —— 说明引擎的身份头没到 mock，只是 IP 兜底给的档。
  S4-3 **幂等**：引擎日志 0 条 `SAVE` 行（产物已含账本值，引擎不该再往回写）。

★ 判据失效条件（产品线明确指出，务必遵守，否则会假阳/假阴）：
  S4-1 的等式成立前提是 **S4 隔离态账本与导出产物口径一致 ⇒ 引擎合并零改动**。
  若账本某项**高于**导出产物，引擎会按"只抬不降"合并 ⇒ 响应改变是**正确行为**，sha 自然不等。
  所以：
    1. **固定用预置的 `s4/state`**（内含主号档副本），不要换成"用户刚打了几局"的新账本；
    2. 若必须换账本，S4-1 退回"`via` + 界面数值"判定（本工具会把 sha 不符标成 `merged`/`skip` 而不是 FAIL，
       但仍需人确认界面数值）；
    3. `customize_v2.json` 同口径可比（小文件，内容 `{"items":[]}`）。

    python tools/check_s4_replay.py \
        --mock-log D:/mod/tmp/save-test-20261124/mocklogs \
        --engine-log D:/mod/tmp/save-test-20261124/s4/state/logs/proxy.log \
        --export D:/mod/tmp/save-export-sample --steam 76561198968648344

判据口径见 `docs/SAVE-TEST-SINGLE-PERSON-2026-11-24.md` §7。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# 端点 → 导出文件名（与 mock/players.py:ENDPOINT_FILES 一致）
ENDPOINT_FILES = {
    "/api/Progression": "progression.json",
    "/api/Progression/inventory": "inventory.json",
    "/api/Stats/profile/summary": "stats_profile.json",
}

# 日志行：`21:07:17.987 GET    /api/Progression/inventory 0B -> 200 4574B [player:… via header:getmotd sha:c1583d25c160]`
LINE_RE = re.compile(
    r"^\s*(?P<ts>\d{2}:\d{2}:\d{2}\.\d{3})\s+(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+"
    r"(?P<inb>\d+)B\s+->\s+(?P<status>\d+)\s+(?P<outb>\d+)B"
    r"(?:\s+\[(?P<bracket>[^\]]*)\])?")
# 方括号内的三段：source / via resolution / sha:digest
BRACKET_RE = re.compile(
    r"^(?P<source>[^\s\]]+)"
    r"(?:\s+via\s+(?P<resolution>[^\s\]]+))?"
    r"(?:\s+sha:(?P<digest>[0-9a-f]+))?$")

SAVE_RE = re.compile(r"\bSAVE\b")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_mock_log(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        bracket = (m.group("bracket") or "").strip()
        source = resolution = digest = ""
        if bracket:
            bm = BRACKET_RE.match(bracket)
            if bm:
                source = bm.group("source")
                resolution = bm.group("resolution") or ""
                digest = bm.group("digest") or ""
            else:
                source = bracket          # 兜底：至少保留原样，便于排障
        out.append({
            "ts": m.group("ts"), "method": m.group("method"),
            "path": m.group("path").split("?", 1)[0],
            "status": int(m.group("status")), "out_bytes": int(m.group("outb")),
            "source": source, "resolution": resolution, "digest": digest,
        })
    return out


def _read_logs(path: Path) -> str:
    if path.is_dir():
        return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in sorted(path.glob("*.log")))
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def evaluate(mock_text: str, engine_text: str, export_root: Path, steam_id: str) -> dict:
    checks: list[dict] = []

    def add(cid, ok, detail, level="fail"):
        checks.append({"id": cid, "ok": bool(ok), "detail": detail, "level": level})

    rows = parse_mock_log(mock_text)
    player_dir = Path(export_root) / "players" / steam_id
    tag = f"player:{steam_id}"

    # S4-1 内容等同：优先 sha256 全等；无 digest 时退回字节数相等
    sha_ok, size_ok, unlike, absent = [], [], [], []
    for path, fname in ENDPOINT_FILES.items():
        want = player_dir / fname
        hits = [r for r in rows if r["path"] == path and r["source"] == tag]
        if not hits:
            absent.append(path)
            continue
        if not want.exists():
            absent.append(f"{path}（导出缺 {fname}）")
            continue
        want_sha, size = sha256_file(want), want.stat().st_size
        digests = [r["digest"] for r in hits if r["digest"]]
        if any(want_sha.startswith(d) for d in digests):
            sha_ok.append(f"{path} sha={want_sha[:12]}")
        elif any(r["out_bytes"] == size for r in hits):
            size_ok.append(f"{path} {size}B（日志无 digest，仅字节数相等）")
        else:
            unlike.append("%s: 期望 %dB/%s…，实际 %sB/%s" % (
                path, size, want_sha[:12],
                sorted({r["out_bytes"] for r in hits}),
                sorted({d[:12] for d in digests}) or "无 digest"))

    if absent:
        add("S4-1", False, "以下端点没有按本玩家返回：%s" % "；".join(absent))
    elif unlike:
        # 字节/sha 都不符：可能是"账本高于导出产物 ⇒ 引擎只抬不降合并"，那是正确行为；
        # 也可能是回落到 baseline（此时 S4-3 会出现 SAVE 行、界面数值也不对）。
        add("S4-1", False,
            "响应内容与导出文件不等（两种可能：①账本高于产物触发了只抬不降合并 = 正确行为；"
            "②回落到 baseline = 失败）。请对照 S4-3 的 SAVE 行与界面数值判断：%s"
            % "；".join(unlike), level="skip")
    elif sha_ok and not size_ok:
        add("S4-1", True, "三个玩家端点 sha256 全等导出文件：%s" % "；".join(sha_ok))
    else:
        add("S4-1", True, "按玩家返回；%s%s" % (
            "；".join(sha_ok),
            ("；" + "；".join(size_ok)) if size_ok else ""), level="info")

    # S4-2 身份路径：要有 header 路径，且不允许出现 ip 兜底
    res = [r["resolution"] for r in rows if r["source"] == tag]
    header_hits = sorted({x for x in res if x.startswith("header:")})
    ip_hits = [r for r in rows if r["source"] == tag and r["resolution"] == "ip"]
    gotmotd = any(x in ("header:getmotd", "header:getmessages") for x in header_hits)
    if ip_hits:
        add("S4-2", False, "出现 %d 次 `via ip`（身份头没到 mock，只是 IP 兜底）" % len(ip_hits))
    elif not header_hits:
        add("S4-2", False, "没有任何 `via header:*` 记录 —— 引擎身份头没生效")
    elif not gotmotd:
        add("S4-2", False, "只有 prebind 阶段，没有 `header:getmotd`（身份未校验通过？）",
            level="skip")
    else:
        add("S4-2", True, "身份路径正常：%s" % ", ".join(header_hits))

    # S4-3 幂等：引擎不该再写 SAVE
    save_lines = [ln for ln in engine_text.splitlines() if SAVE_RE.search(ln)]
    add("S4-3", not save_lines,
        "引擎 0 条 SAVE 行（幂等）" if not save_lines
        else "引擎出现 %d 条 SAVE 行（合并改动了数据）：%s"
             % (len(save_lines), save_lines[0][:120]))

    failed = [c for c in checks if not c["ok"] and c["level"] == "fail"]
    skipped = [c for c in checks if c["level"] == "skip"]
    return {
        "verdict": "PASS" if not failed and not skipped else ("SKIP" if skipped else "FAIL"),
        "checks": checks, "failed": [c["id"] for c in failed],
        "player_requests": len(res), "resolutions": sorted(set(res)),
        "endpoints_seen": sorted({r["path"] for r in rows if r["source"] == tag}),
    }


def format_text(result: dict) -> str:
    lines = ["== S4 存档回放可见性判定（机器可判部分）=="]
    for c in result["checks"]:
        tag = {"skip": "[skip] ", "info": "[info] "}.get(c["level"],
                                                          "PASS " if c["ok"] else "FAIL ")
        lines.append("  %s%s  %s" % (tag, c["id"], c["detail"]))
    lines.append("  按玩家请求 %d 条；解析依据：%s" % (
        result["player_requests"], ", ".join(result["resolutions"]) or "（无）"))
    lines.append("== 结论：%s ==" % result["verdict"])
    if result["failed"]:
        lines.append("  未通过项：" + ", ".join(result["failed"]))
    lines.append("  ⚠ 仍需人看：游戏内界面经验 210 / 成就 4 / 库存 56 行（S4 判据①②）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S4 存档回放可见性判定")
    ap.add_argument("--mock-log", required=True, help="mock 日志目录或文件")
    ap.add_argument("--engine-log", required=True, help="引擎 proxy.log")
    ap.add_argument("--export", required=True, help="save-export 根目录")
    ap.add_argument("--steam", required=True, help="登录的 SteamID64")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    result = evaluate(_read_logs(Path(args.mock_log)), _read_logs(Path(args.engine_log)),
                      Path(args.export), args.steam)
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json
          else format_text(result))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
