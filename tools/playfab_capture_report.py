"""把 PlayFab MITM 抓包（`tools/playfab_mitm.py` 产出的 JSONL）汇总成一份可读报告。

用途：端到端回答「这次加入/建房到底发生了什么」——调用分布、错误码、每次 JoinLobby 的
后续生命周期（Party 网络连接 → 认证 → P2P → 销毁），以及房主侧 CreateLobby/RequestParty。

用法：
    python tools/playfab_capture_report.py                     # 自动取 state/mitm 最新一份
    python tools/playfab_capture_report.py <capture.jsonl> --out state/evidence/e2prep/report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load(path: Path) -> list[dict]:
    recs: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return recs


def _pairs(recs: list[dict]) -> dict[int, dict]:
    by_id: dict[int, dict] = defaultdict(dict)
    for r in recs:
        if "id" in r and r.get("kind") in ("request", "response"):
            by_id[r["id"]][r["kind"]] = r
    return by_id


def _json_text(rec: dict | None, key: str) -> str:
    if not rec:
        return ""
    body = rec.get(key) or {}
    txt = body.get("text")
    if txt is not None:
        return txt
    return ""


def _lobby_id_from_conn(conn: str) -> str:
    if not conn.startswith("cv2:"):
        return ""
    rest = conn[4:]
    return rest.split("|")[0]


def build_report(recs: list[dict], source: str) -> str:
    pairs = _pairs(recs)
    reqs = [v["request"] for v in pairs.values() if "request" in v]
    reqs.sort(key=lambda r: r.get("t", ""))
    path_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    errors: list[dict] = []
    for rid, pair in pairs.items():
        req, resp = pair.get("request"), pair.get("response")
        if not req:
            continue
        path = (req.get("path") or "").split("?")[0]
        path_counts[path] += 1
        status = (resp or {}).get("status", "(no response)")
        status_counts[status] += 1
        if resp and "200" not in status:
            errors.append({
                "t": req.get("t"), "id": rid, "path": path, "status": status,
                "req": _json_text(req, "req_body")[:300],
                "resp": _json_text(resp, "resp_body")[:300],
            })

    lines: list[str] = []
    span = f"{reqs[0].get('t')} → {reqs[-1].get('t')}" if reqs else "(empty)"
    lines.append(f"# PlayFab 抓包报告\n")
    lines.append(f"- 来源: `{source}`")
    lines.append(f"- 时间跨度: {span}")
    lines.append(f"- 请求数: {len(reqs)}  记录数: {len(recs)}\n")

    lines.append("## 1 调用分布\n")
    lines.append("| path | 次数 |")
    lines.append("|---|---|")
    for path, n in path_counts.most_common():
        lines.append(f"| `{path}` | {n} |")
    lines.append("")
    lines.append("## 2 状态码分布\n")
    for status, n in status_counts.most_common():
        lines.append(f"- `{status}` × {n}")
    lines.append("")

    lines.append("## 3 非 200 明细（错误码就是答案所在）\n")
    if not errors:
        lines.append("- 无")
    for e in errors:
        lines.append(f"### {e['t']} `{e['path']}` → {e['status']}\n")
        lines.append(f"- req: `{e['req']}`")
        lines.append(f"- resp: `{e['resp']}`\n")
    lines.append("")

    # ---- 每次加入的完整生命周期
    joins: list[dict] = []
    for rid, pair in pairs.items():
        req = pair.get("request")
        if not req or (req.get("path") or "").split("?")[0] != "/Lobby/JoinLobby":
            continue
        body = _json_text(req, "req_body")
        try:
            conn = json.loads(body).get("ConnectionString", "")
        except Exception:  # noqa: BLE001
            conn = ""
        resp = pair.get("response") or {}
        joins.append({"t": req.get("t"), "conn": conn,
                      "lobby": _lobby_id_from_conn(conn),
                      "status": resp.get("status", "?"),
                      "resp": _json_text(resp, "resp_body")[:200]})
    joins.sort(key=lambda j: j["t"])

    # ---- Party 网络生命周期（按 NetworkIdentifier 聚合）
    party: dict[str, dict] = {}
    for r in reqs:
        if "Telemetry" not in (r.get("path") or ""):
            continue
        try:
            doc = json.loads(_json_text(r, "req_body"))
        except Exception:  # noqa: BLE001
            continue
        for ev in doc.get("Events") or []:
            pl = ev.get("Payload") or {}
            net = pl.get("NetworkIdentifier") or ""
            name = ev.get("Name") or ""
            if not net:
                continue
            ent = party.setdefault(net, {"events": [], "connected_ms": None, "p2p": [],
                                         "auth": None, "api_errors": [], "destroyed": None})
            ent["events"].append((r.get("t"), name))
            if name == "client_connected_to_network":
                ent["connected_ms"] = pl.get("ConnectDurationInMs")
                ent["connect_result"] = pl.get("ResultMessage")
            elif name == "client_authentication_completed":
                ent["auth"] = pl.get("ResultMessage")
            elif name == "client_join_with_p2p_connectivity_completed":
                ent["p2p"].append({"code": pl.get("ErrorCode"),
                                   "latency": pl.get("DirectPeerConnectionLatencyInMilliseconds"),
                                   "direct": pl.get("RelaySaidToUseDirectPeerConnectivity")})
            elif name == "client_api_error_occurred":
                ent["api_errors"].append(pl.get("ErrorDetail"))
            elif name == "client_network_destroyed":
                ent["destroyed"] = r.get("t")
            ent["last"] = r.get("t")

    lines.append("## 4 加入（JoinLobby）与 Party 生命周期\n")
    lines.append("| # | JoinLobby 时间 | lobby | 结果 | Party 网络 | 连接 | 认证 | P2P | API 错误 | 网络销毁 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for idx, j in enumerate(joins, 1):
        # 找 JoinLobby 之后最近的 party 网络（按时间）
        cand = [(k, v) for k, v in party.items()
                if v["events"] and v["events"][0][0] >= j["t"]]
        cand.sort(key=lambda kv: kv[1]["events"][0][0])
        net, ent = (cand[0] if cand else ("", {}))
        p2p = "/".join(str(p.get("code")) for p in ent.get("p2p") or []) or "-"
        lines.append("| {} | {} | `{}` | {} | `{}` | {} ms | {} | {} | {} | {} |".format(
            idx, (j["t"] or "")[11:19], (j["lobby"] or "")[:8] + "…", j["status"],
            (net or "")[:8] + "…", ent.get("connected_ms") or "-", ent.get("auth") or "-",
            p2p, ",".join(str(x) for x in ent.get("api_errors") or []) or "-",
            (ent.get("destroyed") or "-")[-8:] if ent.get("destroyed") else "-"))
    lines.append("")

    lines.append("## 5 房间侧（房主）\n")
    for path in ("/Party/RequestParty", "/Lobby/CreateLobby", "/Lobby/FindLobbies"):
        for rid, pair in sorted(pairs.items(), key=lambda kv: (kv[1].get("request") or {}).get("t", "")):
            req = pair.get("request")
            if not req or (req.get("path") or "").split("?")[0] != path:
                continue
            resp = pair.get("response") or {}
            body = _json_text(resp, "resp_body")
            if path == "/Lobby/FindLobbies":
                try:
                    n = len(json.loads(body).get("data", {}).get("Lobbies") or [])
                except Exception:  # noqa: BLE001
                    n = "?"
                lines.append(f"- {req.get('t')} FindLobbies → {n} 个厅")
            elif path == "/Party/RequestParty":
                try:
                    d = json.loads(body).get("data", {})
                    lines.append(f"- {req.get('t')} RequestParty → {d.get('IPV4Address')} "
                                 f"UDP {(d.get('Ports') or [{}])[0].get('Num')} ({d.get('Region')})")
                except Exception:  # noqa: BLE001
                    lines.append(f"- {req.get('t')} RequestParty → {body[:120]}")
            else:
                try:
                    d = json.loads(body).get("data", {})
                    lines.append(f"- {req.get('t')} CreateLobby → {d.get('LobbyId')}")
                except Exception:  # noqa: BLE001
                    lines.append(f"- {req.get('t')} CreateLobby → {body[:120]}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PlayFab 抓包报告")
    ap.add_argument("capture", nargs="?", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    if args.capture:
        path = Path(args.capture)
    else:
        cands = sorted((REPO / "state" / "mitm").glob("capture-*.jsonl"),
                       key=lambda p: p.stat().st_mtime)
        if not cands:
            print("没有找到抓包文件（state/mitm/capture-*.jsonl）", file=sys.stderr)
            return 1
        path = cands[-1]

    report = build_report(load(path), str(path))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"报告写入 {out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
