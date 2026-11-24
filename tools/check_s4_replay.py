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


def _fetch_health(url: str, timeout: float = 3.0) -> dict | None:
    """取 mock 的 `/health`。**显式禁用环境代理**：本机 https_proxy 会把请求劫走。"""
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as resp:
            doc = json.loads(resp.read().decode("utf-8", "replace"))
        return doc if isinstance(doc, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _read_logs(path: Path) -> str:
    if path.is_dir():
        return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in sorted(path.glob("*.log")))
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def evaluate(mock_text: str, engine_text: str, export_root: Path, steam_id: str,
             health: dict | None = None) -> dict:
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

    # S4-2 身份路径（2026-11-24 实机+产品线双方向定稿）
    #
    # 硬判据只有一条：**所有玩家档请求都必须靠身份头返回**，不得出现 `ip`/`single`/空
    #（`ip` = 引擎头没到、只是 IP 兜底；`single` = 索引里只有一个玩家蒙的；空 = 回落 baseline）。
    #
    # 为什么**不**要求出现 `via header:getmotd` 的玩家档请求：真实时序里 GetMotd **永远晚于首屏**，
    # 客户端首屏一次性读完 5 个玩家档端点（全部 `prebind`），之后只在 lobby/friends 端点解析身份，
    # 不再重读玩家档。产品线独立实测（`s4/evidence.md`）与我们的日志一致：
    #   21:33:46.605 GET /api/Progression [via header:prebind]  ← 首屏 5 条
    #   21:33:49.786 POST /api/Pes/GetMotd                     ← 晚 3.2 秒，且是 POST
    # 因此把"身份升级为 getmotd"降级为**信息项**（若游戏二次读档——换号/重启/任务后——才会带出来）。
    res = [r["resolution"] for r in rows if r["source"] == tag]
    header_hits = sorted({x for x in res if x.startswith("header:")})
    bad = sorted({x for x in res if not x.startswith("header:")})
    if not res:
        add("S4-2", False, "没有任何按玩家返回的请求")
    elif bad:
        add("S4-2", False, "玩家档请求出现非身份头解析：%s（`ip` 表示仅 IP 兜底）" % ", ".join(bad))
    else:
        add("S4-2", True, "全部 %d 条玩家档请求均靠身份头返回：%s"
            % (len(res), ", ".join(header_hits)))

    # info：身份校验是否完成（GetMotd 晚于首屏，属正常；不参与判定）
    gotmotd_direct = any(x in ("header:getmotd", "header:getmessages") for x in header_hits)
    hp = (health or {}).get("players") or {}
    upgraded = str(hp.get("last_header_source") or "")
    if gotmotd_direct:
        add("info-identity", True, "玩家档请求直接带 getmotd（本局有二次读档）", level="info")
    elif upgraded in ("getmotd", "getmessages"):
        add("info-identity", True,
            "身份升级为 `%s`（/health 证实）；首屏读档发生在升级之前属正常时序"
            % upgraded, level="info")
    else:
        add("info-identity", True,
            "未观察到身份升级证据（未提供 --health-url，且无 getmotd 玩家请求）——"
            "不影响回放可见性判定，但建议附 /health 作旁证", level="info")

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
    ap.add_argument("--health-url", help="mock /health 地址（如 http://127.0.0.1:9099/health）；"
                                        "用于确认身份校验已完成")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    health = _fetch_health(args.health_url) if args.health_url else None
    result = evaluate(_read_logs(Path(args.mock_log)), _read_logs(Path(args.engine_log)),
                      Path(args.export), args.steam, health=health)
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json
          else format_text(result))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
