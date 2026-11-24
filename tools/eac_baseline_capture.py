#!/usr/bin/env python3
"""EAC 上报基线采集（第 0 步：零注入、零阻断，只忠实转发 + 落原始字节）。

背景：EAC 的反作弊上报走 `POST /anticheat/v8/games/client/message/`（octet-stream，
约每 12 s 一条、持续整局），与 EOS 共用 host `api.epicgames.dev`（见
`docs/EAC-NET-ATTRIBUTION-2026-11-25.md`）。要判断"注入后被察觉"，先要有**干净会话的
上报指纹**（长度序列 + 间隔序列 + 原始字节），否则无从对比。

本脚本只做四件事：
  1. 把系统/进程代理由**用户自己**确认后指向我们的 MITM（可 `--dry-run` 先看计划）；
  2. 起 `tools/mitm_proxy.py`：对 `api.epicgames.dev` 的 `/anticheat/*` **落完整原始字节**，
     其余域名只记 schema（沿用 T6a 的"不落原值"纪律）；
  3. 并行起 `tshark` 抓 loopback，取 **JA3 / SNI / TLS 版本**（客户端指纹，注入后若突变是早期信号）；
  4. 采集结束后产出 `index.jsonl`（每条：ms 时间戳/长度/文件/sha256）+ `meta.json` + `README.md`。

**不做**：不改游戏文件、不注入、不阻断、不伪造任何应答（R2/R6 已证伪造即死）。

用法（**先把游戏关掉**，脚本起来后再启动游戏）：

    python tools/eac_baseline_capture.py \
        --out-dir state/evidence/eac-antichat-baseline-<ts> \
        --duration 300 --dry-run          # 先看计划
    python tools/eac_baseline_capture.py \
        --out-dir state/evidence/eac-antichat-baseline-<ts> --duration 300

结束后按 `README.md` 里的判据看：`index.jsonl` 的 `req_len` 序列与 `dt_ms` 序列就是"上报指纹"。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 本期只关心这两类；官方 API 与 modules-cdn 只统计不落原值
ANTICHEAT_HOST = "api.epicgames.dev"
ANTICHEAT_PATH_RE = r"^/(anticheat/v[0-9]+/|anticheat)"
INTERCEPT_HOSTS = [
    "api.epicgames.dev",
    "modules-cdn.eac-prod.on.epicgames.com",
    "api.live.prod.thehelldiversgame.com",
    "974a9.playfabapi.com",
]
GAME_API_HOST = "api.live.prod.thehelldiversgame.com"
MODULES_HOST = "modules-cdn.eac-prod.on.epicgames.com"

TSHARK_CANDIDATES = [
    r"C:\Program Files\Wireshark\tshark.exe",
    r"C:\Program Files (x86)\Wireshark\tshark.exe",
]


# ---------------------------------------------------------------- 纯函数（可离线测）
def build_index(jsonl_path: Path) -> list[dict]:
    """把 mitm_proxy 的 jsonl 转成"上报指纹"索引：只保留 anticheat 请求/响应条目。

    每条记录输出：
      ts / dt_ms（与同方向上一条的间隔）/ direction / req_len / resp_len /
      file（若落了原始字节）/ sha256 / truncated
    """
    rows: list[dict] = []
    prev: dict[str, int] = {}
    if not Path(jsonl_path).exists():
        return rows
    for line in Path(jsonl_path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if str(rec.get("host") or "") != ANTICHEAT_HOST:
            continue
        path = str(rec.get("path") or "")
        if not re.search(ANTICHEAT_PATH_RE, path):
            continue
        t_ms = _ts_to_ms(rec.get("t"))
        raw = {d.get("direction"): d for d in (rec.get("raw") or [])}
        for direction in ("req", "resp"):
            item = raw.get(direction) or {}
            blob = (rec.get(direction) or {})
            length = int(rec.get("%s_len" % direction) or blob.get("bytes") or 0)
            dt = (t_ms - prev[direction]) if (t_ms and direction in prev) else None
            if t_ms:
                prev[direction] = t_ms
            rows.append({
                "ts": rec.get("t"), "ts_ms": t_ms, "dt_ms": dt,
                "method": rec.get("method"), "path": path,
                "direction": direction, "len": length,
                "status": rec.get("status") if direction == "resp" else None,
                "file": item.get("file") or "", "sha256": item.get("sha256") or "",
                "truncated": bool(item.get("truncated")),
            })
    return rows


def _ts_to_ms(ts) -> int:
    """`HH:MM:SS(.mmm)` 或 ISO 时间 → epoch 毫秒（当天内足够用于算间隔）。"""
    if not ts:
        return 0
    s = str(ts)
    try:
        if "T" in s:
            return int(time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)
        parts = s.split(":")
        if len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), float(parts[2])
            return int(((h * 60 + m) * 60 + sec) * 1000)
    except Exception:  # noqa: BLE001
        return 0
    return 0


def summarize(rows: list[dict], *, started: str, ended: str,
              game_pid: int | None = None) -> dict:
    """会话元信息 + 指纹序列（长度/间隔），供注入前后做差分。"""
    reqs = [r for r in rows if r["direction"] == "req"]
    resps = [r for r in rows if r["direction"] == "resp"]
    dts = [r["dt_ms"] for r in reqs if r["dt_ms"] is not None]
    return {
        "started_at": started, "ended_at": ended,
        "game_pid": game_pid,
        "anticheat_requests": len(reqs),
        "anticheat_responses": len(resps),
        "req_len_series": [r["len"] for r in reqs],
        "dt_ms_series": dts,
        "dt_ms_min": min(dts) if dts else None,
        "dt_ms_max": max(dts) if dts else None,
        "req_bytes_total": sum(r["len"] for r in reqs),
        "resp_bytes_total": sum(r["len"] for r in resps),
        "raw_files": sum(1 for r in rows if r["file"]),
        "truncated_files": sum(1 for r in rows if r["truncated"]),
    }


def count_hosts(jsonl_path: Path) -> dict:
    """对照统计：各 host 的请求条数/字节数（含 game API、modules-cdn 是否发生）。"""
    out: dict[str, dict] = {}
    p = Path(jsonl_path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        host = str(rec.get("host") or "")
        if not host:
            continue
        slot = out.setdefault(host, {"requests": 0, "req_bytes": 0, "resp_bytes": 0})
        slot["requests"] += 1
        slot["req_bytes"] += int(rec.get("req_len") or 0)
        slot["resp_bytes"] += int(rec.get("resp_len") or 0)
    return out


# ---------------------------------------------------------------- 系统代理（可 dry-run）
def snapshot_ie_proxy() -> dict:
    """读当前 HKCU WinINET 代理设置（只读）。"""
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    out = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
            try:
                out[name] = winreg.QueryValueEx(k, name)[0]
            except FileNotFoundError:
                out[name] = None
    return out


def apply_ie_proxy(server: str) -> None:
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)


def restore_ie_proxy(saved: dict) -> None:
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        if saved.get("ProxyEnable") is not None:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, int(saved["ProxyEnable"]))
        for name in ("ProxyServer", "ProxyOverride", "AutoConfigURL"):
            val = saved.get(name)
            if val is None:
                continue
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(val))


def extract_ja3(tshark: str, pcap: Path, out_tsv: Path) -> int:
    """从 loopback pcap 抽 JA3/SNI/TLS 版本（ClientHello 明文，无需解密）。返回行数。"""
    if not Path(pcap).exists():
        return 0
    cmd = [tshark, "-r", str(pcap),
           "-Y", "tls.handshake.extensions_server_name",
           "-T", "fields",
           "-e", "frame.time_epoch", "-e", "ip.dst", "-e", "tcp.dstport",
           "-e", "tls.handshake.extensions_server_name",
           "-e", "tls.handshake.ja3", "-e", "tls.handshake.ja3_full",
           "-e", "tls.handshake.version"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception:  # noqa: BLE001
        return 0
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if lines:
        header = "time_epoch\tdst_ip\tdst_port\tsni\tja3\tja3_full\ttls_version"
        out_tsv.write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def find_tshark() -> str | None:
    for cand in TSHARK_CANDIDATES:
        if Path(cand).exists():
            return cand
    return shutil.which("tshark")


# ---------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EAC 上报基线采集（零注入）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--duration", type=int, default=300, help="采集秒数（默认 300）")
    ap.add_argument("--proxy-port", type=int, default=18092)
    ap.add_argument("--cert-dir", default=str(REPO / "state" / "certs-eac"))
    ap.add_argument("--hosts", default=",".join(INTERCEPT_HOSTS))
    ap.add_argument("--dump-limit", type=int, default=0, help="原始字节每 body 落多少（0=全量）")
    ap.add_argument("--no-tshark", action="store_true", help="不抓 JA3/SNI")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不起进程、不改注册表")
    args = ap.parse_args(argv)

    out = Path(args.out_dir)
    jsonl = out / "mitm.jsonl"
    dump_dir = out / "raw"
    plan = {
        "out_dir": str(out),
        "duration_s": args.duration,
        "proxy": "127.0.0.1:%d" % args.proxy_port,
        "intercept_hosts": args.hosts.split(","),
        "dump_anticheat_to": str(dump_dir),
        "dump_limit": args.dump_limit,
        "tshark": "off" if args.no_tshark else (find_tshark() or "MISSING"),
        "cert_dir": args.cert_dir,
    }
    if args.dry_run:
        print(json.dumps({"plan": plan, "ie_proxy_now": snapshot_ie_proxy()},
                         ensure_ascii=False, indent=1))
        return 0

    cert_dir = Path(args.cert_dir)
    if not (cert_dir / "server.chain.pem").exists():
        print("缺少证书：先跑\n  python tools/mockctl.py cert-gen --dir %s --hosts %s"
              % (cert_dir, args.hosts))
        return 2

    out.mkdir(parents=True, exist_ok=True)
    saved = snapshot_ie_proxy()
    (out / "ie-proxy-backup.json").write_text(
        json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")

    proxy_cmd = [sys.executable, str(REPO / "tools" / "mitm_proxy.py"),
                 "--port", str(args.proxy_port), "--intercept", args.hosts,
                 "--cert-dir", str(cert_dir), "--no-chain", "--log", str(jsonl),
                 "--dump-bodies", str(dump_dir), "--dump-hosts", ANTICHEAT_HOST,
                 "--dump-paths", "/anticheat/", "--dump-limit", str(args.dump_limit)]
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    procs: list[subprocess.Popen] = []
    tsh = None
    pcap_path: Path | None = None
    try:
        print("[1/4] 系统代理 → %s:18092（原值已备份到 ie-proxy-backup.json）" % "127.0.0.1")
        apply_ie_proxy("127.0.0.1:%d" % args.proxy_port)
        print("[2/4] 起 MITM：%s" % " ".join(proxy_cmd[-6:]))
        procs.append(subprocess.Popen(proxy_cmd))
        if not args.no_tshark and (tsh := find_tshark()):
            pcap = out / "loopback.pcapng"
            ja3 = out / "ja3-sni.tsv"
            # loopback 上 ClientHello 是明文 ⇒ 无需解密即可取 JA3/SNI
            tproc = subprocess.Popen(
                [tsh, "-i", r"\Device\NPF_Loopback", "-f", "tcp port %d" % args.proxy_port,
                 "-w", str(pcap), "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append(tproc)
            pcap_path = pcap
            print("[3/4] tshark 抓 loopback → %s（结束后自动抽 JA3/SNI）" % pcap.name)
        else:
            print("[3/4] 跳过 tshark")
        print("[4/4] 现在启动游戏（主菜单停留即可，%d 秒后自动收尾；Ctrl+C 可提前结束）"
              % args.duration)
        deadline = time.time() + args.duration
        while time.time() < deadline:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] 提前结束")
    finally:
        print("[*] 收尾：停进程 + 还原系统代理")
        for p in procs:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        for p in procs:
            try:
                p.wait(timeout=8)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass
        try:
            restore_ie_proxy(saved)
            print("[*] 系统代理已还原：%s" % json.dumps(saved, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            print("[!] 代理还原失败，请手工恢复：%r" % (exc,))
        if pcap_path is not None and tsh:
            n = extract_ja3(tsh, pcap_path, out / "ja3-sni.tsv")
            print("[*] JA3/SNI 提取 %d 条 → ja3-sni.tsv" % n)
        ended = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = build_index(jsonl)
        meta = summarize(rows, started=started, ended=ended)
        meta["hosts"] = count_hosts(jsonl)
        meta["game_api_requests"] = meta["hosts"].get(GAME_API_HOST, {}).get("requests", 0)
        meta["modules_cdn_seen"] = MODULES_HOST in meta["hosts"]
        (out / "index.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
            encoding="utf-8")
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        print("\n== 完成 ==")
        print(" anticheat 请求 %d 条 / 响应 %d 条；原始文件 %d 个"
              % (meta["anticheat_requests"], meta["anticheat_responses"], meta["raw_files"]))
        if meta["dt_ms_series"]:
            print(" 间隔序列(ms): %s" % meta["dt_ms_series"][:12])
            print(" 长度序列(B): %s" % meta["req_len_series"][:12])
        print(" 产物：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
