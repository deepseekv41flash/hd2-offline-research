#!/usr/bin/env python3
"""EAC 上报基线采集（第 0 步：零注入、零阻断，只忠实转发 + 落原始字节）。

背景：EAC 的反作弊上报走 `POST /anticheat/v8/games/client/message/`（octet-stream，
约每 12 s 一条、持续整局），与 EOS 共用 host `api.epicgames.dev`（见
`docs/EAC-NET-ATTRIBUTION-2026-11-25.md`）。要判断"注入后被察觉"，先要有**干净会话的
上报指纹**（长度序列 + 间隔序列 + 原始字节）。

★ 为什么用 hosts 而不是系统代理（2026-11-25 与 EAC-off 线实测对齐）：
  EOS/EAC 的 HTTP 栈是**直连**（源地址是物理网卡，不经本机 7897），IE 代理与
  `HTTP(S)_PROXY` env 都**抓不到**它；反过来把系统代理指向我们的 MITM 会把
  全系统 WinINET 流量卷进来，副作用大。所以改成：**hosts 把目标 host 指到本机 + 我们签的
  证书做 TLS 终结 + 忠实转发到真实 IP**（这条路 T6a 已验证"游戏正常跑通"）。

本脚本做四件事（**必须以管理员运行**，因为要改 hosts）：
  1. 解析并记录目标 host 的**真实 IP**（改 hosts 之前解析），逐行备份 hosts；
  2. hosts 加行 → 目标指向 `127.0.0.1`，刷新 DNS；
  3. 起 `tools/mitm_proxy.py --tls-listen 127.0.0.1:443`（冒充该 host，转发到真实 IP，
     对 `/anticheat/` **落完整原始字节**，其余只记 schema）；并行起 `tshark` 抓 loopback
     取 **JA3/SNI**；
  4. `--duration` 后收尾：停进程 → **逐行还原 hosts** → 刷新 DNS → 产
     `index.jsonl` / `meta.json`（含 hosts diff、真实 IP、CA 指纹"未卸载"）。

用法（**先把游戏关掉**，脚本起来后再启动游戏）：

    # 先看计划（不改任何东西）
    python tools/eac_baseline_capture.py --out-dir D:/mod/tmp/eac-baseline --dry-run
    # 正式采集（管理员 shell 里跑；会改 hosts）
    python tools/eac_baseline_capture.py --out-dir D:/mod/tmp/eac-baseline --duration 300

**不做**：不改游戏文件、不注入、不阻断、不伪造任何应答（R2/R6 已证伪造即死）。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOSTS_PATH = Path(r"C:\Windows\System32\drivers\etc\hosts")

ANTICHEAT_HOST = "api.epicgames.dev"
ANTICHEAT_PATH_RE = r"^/(anticheat/v[0-9]+/|anticheat)"
DUMP_PATHS_RE = r"/anticheat/|/telemetry/"   # 落原值的路径（telemetry 用来取 Bootstrapper 事件）
MODULES_HOST = "modules-cdn.eac-prod.on.epicgames.com"
GAME_API_HOST = "api.live.prod.thehelldiversgame.com"
DEFAULT_REDIRECT = [ANTICHEAT_HOST]
TSHARK_CANDIDATES = [r"C:\Program Files\Wireshark\tshark.exe",
                     r"C:\Program Files (x86)\Wireshark\tshark.exe"]


# ---------------------------------------------------------------- 纯函数（可离线测）
def build_index(jsonl_path: Path) -> list[dict]:
    """把 mitm jsonl 转成"上报指纹"索引：只保留 anticheat 请求/响应条目。

    输出字段：ts / ts_ms / dt_ms（同方向与上一条的间隔）/ direction / len / status /
              file（原始字节文件）/ sha256 / truncated
    """
    rows: list[dict] = []
    prev: dict[str, int] = {}
    p = Path(jsonl_path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
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
            length = int(rec.get("%s_len" % direction) or 0)
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
    if not ts:
        return 0
    s = str(ts)
    try:
        if "T" in s:
            return int(time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)
        parts = s.split(":")
        if len(parts) == 3:
            return int(((int(parts[0]) * 60 + int(parts[1])) * 60 + float(parts[2])) * 1000)
    except Exception:  # noqa: BLE001
        return 0
    return 0


def summarize(rows: list[dict], *, started: str, ended: str,
              game_pid: int | None = None) -> dict:
    """会话元信息 + 指纹序列（长度/间隔），供注入前后差分。"""
    reqs = [r for r in rows if r["direction"] == "req"]
    resps = [r for r in rows if r["direction"] == "resp"]
    dts = [r["dt_ms"] for r in reqs if r["dt_ms"] is not None]
    return {
        "started_at": started, "ended_at": ended, "game_pid": game_pid,
        "anticheat_requests": len(reqs), "anticheat_responses": len(resps),
        "req_len_series": [r["len"] for r in reqs],
        "dt_ms_series": dts,
        "dt_ms_min": min(dts) if dts else None,
        "dt_ms_max": max(dts) if dts else None,
        "req_bytes_total": sum(r["len"] for r in reqs),
        "resp_bytes_total": sum(r["len"] for r in resps),
        "raw_files": sum(1 for r in rows if r["file"]),
        "truncated_files": sum(1 for r in rows if r["truncated"]),
    }


def build_telemetry_index(jsonl_path: Path) -> list[dict]:
    """把 `/telemetry/...` 的请求抽出来，并从 query 解析 AppID/UploadType/SessionId。

    为什么值得单独抽：`AppID=EOSSDK.AntiCheatExt.Launcher` 是 **EAC 启动器（Bootstrapper）**
    自己的遥测通道，与游戏内 `/anticheat/v8` 检测流是**两条不同的上报路径**（2026-11-25 实测）。
    AppID 是 datarouter 天然的分类标志，将来做 A/B 或注入前后对比时按它分组。
    """
    rows: list[dict] = []
    p = Path(jsonl_path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        path = str(rec.get("path") or "")
        if "/telemetry/" not in path:
            continue
        query = path.split("?", 1)[1] if "?" in path else ""
        params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
        raw = {d.get("direction"): d for d in (rec.get("raw") or [])}
        item = raw.get("req") or {}
        rows.append({
            "ts": rec.get("t"), "host": rec.get("host"),
            "method": rec.get("method"), "path": path.split("?", 1)[0],
            "app_id": params.get("AppID"), "upload_type": params.get("UploadType"),
            "session_id": params.get("SessionId"),
            "req_len": int(rec.get("req_len") or 0),
            "status": rec.get("status"),
            "resp_len": int(rec.get("resp_len") or 0),
            "file": item.get("file") or "", "sha256": item.get("sha256") or "",
        })
    return rows



def _telemetry_events(out_dir: Path, item: dict) -> list[dict]:
    """从已落盘的 telemetry 请求体里抽事件名（EAC 启动器上报是**明文 JSON**）。

    2026-11-25 实测：`AppID=EOSSDK.AntiCheatExt.Launcher` 的 body 是
    `{"Events":[{"EventName":"AntiCheat.Info.BootstrapperResult",...}]}` 纯 JSON，
    含 `LaunchResult` 与各类耗时字段 —— 这是"启动器报了什么"的现成判据。
    """
    fname = item.get("file") or ""
    if not fname:
        return []
    try:
        doc = json.loads((Path(out_dir) / "raw" / fname).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    evs = doc.get("Events") if isinstance(doc, dict) else None
    return [e for e in evs if isinstance(e, dict)] if isinstance(evs, list) else []


def count_hosts(jsonl_path: Path) -> dict:
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


def hosts_diff(original: str, restored: str) -> dict:
    """逐行 diff：确认收尾把 hosts 还原干净（回执要求写进 meta）。"""
    o = original.splitlines()
    r = restored.splitlines()
    return {"identical": o == r, "original_lines": len(o), "restored_lines": len(r),
            "missing": [ln for ln in o if ln not in r][:10],
            "extra": [ln for ln in r if ln not in o][:10]}


# ---------------------------------------------------------------- 系统代理（HKCU，无需管理员）
IE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def snapshot_ie_proxy() -> dict:
    import winreg
    out = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IE_KEY) as k:
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
            try:
                out[name] = winreg.QueryValueEx(k, name)[0]
            except FileNotFoundError:
                out[name] = None
    return out


def apply_ie_proxy(server: str) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IE_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)


def restore_ie_proxy(saved: dict) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IE_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if saved.get("ProxyEnable") is not None:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, int(saved["ProxyEnable"]))
        for name in ("ProxyServer", "ProxyOverride", "AutoConfigURL"):
            val = saved.get(name)
            if val is not None:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(val))


# ---------------------------------------------------------------- hosts / 权限
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def resolve_real_ip(host: str) -> str | None:
    """改 hosts **之前**解析真实 IP（之后解析会得到 127.0.0.1）。"""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        v4 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET})
        return v4[0] if v4 else None
    except Exception:  # noqa: BLE001
        return None


def flush_dns() -> None:
    try:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        pass


def find_tshark() -> str | None:
    for cand in TSHARK_CANDIDATES:
        if Path(cand).exists():
            return cand
    return shutil.which("tshark")


def extract_ja3(tshark: str, pcap: Path, out_tsv: Path, port: int) -> int:
    """从 loopback pcap 抽 JA3/SNI（ClientHello 明文，无需解密）。"""
    if not Path(pcap).exists():
        return 0
    cmd = [tshark, "-r", str(pcap), "-Y", "tls.handshake.extensions_server_name",
           "-T", "fields",
           "-e", "frame.time_epoch", "-e", "ip.dst", "-e", "tcp.dstport",
           "-e", "tls.handshake.extensions_server_name",
           "-e", "tls.handshake.ja3", "-e", "tls.handshake.ja3_full",
           "-e", "tls.handshake.version"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception:  # noqa: BLE001
        return 0
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if lines:
        header = "time_epoch\tdst_ip\tdst_port\tsni\tja3\tja3_full\ttls_version"
        out_tsv.write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


# ---------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EAC 上报基线采集（零注入，hosts 重定向）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--duration", type=int, default=300)
    ap.add_argument("--hosts-file", default=str(HOSTS_PATH))
    ap.add_argument("--redirect", default=",".join(DEFAULT_REDIRECT),
                    help="要重定向到本机的 host（默认 api.epicgames.dev）")
    ap.add_argument("--listen-port", type=int, default=443, help="本机 TLS 终结端口（默认 443）")
    ap.add_argument("--cert-dir", default=str(REPO / "state" / "certs-eac"))
    ap.add_argument("--dump-limit", type=int, default=0, help="原始字节每 body 落多少（0=全量）")
    ap.add_argument("--no-tshark", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    out = Path(args.out_dir)
    hosts_file = Path(args.hosts_file)
    redirect = [h.strip() for h in args.redirect.split(",") if h.strip()]
    real_ips = {h: resolve_real_ip(h) for h in redirect}
    plan = {
        "out_dir": str(out), "duration_s": args.duration,
        "hosts_file": str(hosts_file), "redirect_to": "127.0.0.1",
        "listen_port": args.listen_port, "redirect_hosts": redirect,
        "real_ips": real_ips, "cert_dir": args.cert_dir,
        "dump_limit": args.dump_limit, "admin": is_admin(),
        "tshark": "off" if args.no_tshark else (find_tshark() or "MISSING"),
    }
    if args.dry_run:
        print(json.dumps({"plan": plan}, ensure_ascii=False, indent=1))
        return 0
    if not is_admin():
        print("需要管理员权限（要改 hosts）。请用提升的 shell 运行，例如：")
        print('  MSYS_NO_PATHCONV=1 sudo --disable-input pwsh -NoProfile -NonInteractive '
              '-Command \'python tools/eac_baseline_capture.py --out-dir %s --duration %d\''
              % (args.out_dir, args.duration))
        return 2
    if any(v is None for v in real_ips.values()):
        print("有 host 解析不到真实 IP，拒绝继续（会把自己的转发打入回环）：%s"
              % [h for h, v in real_ips.items() if not v])
        return 2
    cert_dir = Path(args.cert_dir)
    if not (cert_dir / "server.chain.pem").exists():
        print("缺少证书，先跑：python tools/mockctl.py cert-gen --dir %s --hosts %s"
              % (cert_dir, ",".join(redirect)))
        return 2

    out.mkdir(parents=True, exist_ok=True)
    original = hosts_file.read_text(encoding="utf-8", errors="replace")
    (out / "hosts-original.txt").write_text(original, encoding="utf-8")

    jsonl, dump_dir = out / "mitm.jsonl", out / "raw"
    procs: list[subprocess.Popen] = []
    tsh = None
    pcap_path: Path | None = None
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        print("[1/5] hosts 加行 → %s（原文件已备份 hosts-original.txt）" % redirect)
        extra = "".join("\n127.0.0.1 %s  # hd2-eac-baseline\n" % h for h in redirect)
        hosts_file.write_text(original.rstrip("\n") + "\n" + extra, encoding="utf-8")
        flush_dns()

        primary = redirect[0]
        cmd = [sys.executable, str(REPO / "tools" / "mitm_proxy.py"),
               "--tls-listen", "127.0.0.1:%d" % args.listen_port,
               "--tls-host", primary, "--forward-ip", str(real_ips[primary]),
               "--intercept", ",".join(redirect), "--cert-dir", str(cert_dir),
               "--no-chain", "--log", str(jsonl),
               "--dump-bodies", str(dump_dir), "--dump-hosts", primary,
               "--dump-paths", DUMP_PATHS_RE, "--dump-limit", str(args.dump_limit)]
        print("[2/5] 起 MITM 透明终结：127.0.0.1:%d → 真实上游 %s"
              % (args.listen_port, real_ips[primary]))
        procs.append(subprocess.Popen(cmd))
        if not args.no_tshark and (tsh := find_tshark()):
            pcap_path = out / "loopback.pcapng"
            procs.append(subprocess.Popen(
                [tsh, "-i", r"\Device\NPF_Loopback",
                 "-f", "tcp port %d" % args.listen_port, "-w", str(pcap_path), "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            print("[3/5] tshark 抓 loopback（结束后抽 JA3/SNI）")
        else:
            print("[3/5] 跳过 tshark")
        print("[4/5] **现在启动游戏**（停在主菜单即可，%d 秒后自动收尾；Ctrl+C 提前结束）"
              % args.duration)
        deadline = time.time() + args.duration
        while time.time() < deadline:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] 提前结束")
    finally:
        print("[*] 收尾：停进程 → 还原 hosts → 刷 DNS")
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
            p = None
        # 逐行还原：直接写回原始文本，再比对
        hosts_file.write_text(original, encoding="utf-8")
        flush_dns()
        restored = hosts_file.read_text(encoding="utf-8", errors="replace")
        diff = hosts_diff(original, restored)
        if pcap_path is not None and tsh:
            n = extract_ja3(tsh, pcap_path, out / "ja3-sni.tsv", args.listen_port)
            print("[*] JA3/SNI 提取 %d 条 → ja3-sni.tsv" % n)
        ended = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = build_index(jsonl)
        tele = build_telemetry_index(jsonl)          # EAC 启动器（Bootstrapper）遥测
        meta = summarize(rows, started=started, ended=ended)
        meta.update({
            "redirect_hosts": redirect, "real_ips": real_ips,
            "hosts_restore": diff,
            "system_proxy_untouched": True,
            "ca_note": "研究 CA 仍在 CurrentUser\\Root；机器根那份由 run.cmd 回收",
            "hosts": count_hosts(jsonl),
            "game_api_requests": count_hosts(jsonl).get(GAME_API_HOST, {}).get("requests", 0),
            "modules_cdn_seen": MODULES_HOST in count_hosts(jsonl),
            "telemetry": {
                "requests": len(tele),
                "app_ids": sorted({t["app_id"] for t in tele if t["app_id"]}),
                "upload_types": sorted({t["upload_type"] for t in tele if t["upload_type"]}),
                "event_names": sorted({e.get("EventName") for t in tele
                                       for e in _telemetry_events(out, t)}),
                "raw_files": sum(1 for t in tele if t["file"]),
                "items": tele[:50],
            },
        })
        (out / "index.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
            encoding="utf-8")
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        print("\n== 完成 ==")
        print(" anticheat 请求 %d / 响应 %d；原始文件 %d 个；hosts 还原一致=%s"
              % (meta["anticheat_requests"], meta["anticheat_responses"],
                 meta["raw_files"], diff["identical"]))
        if meta["dt_ms_series"]:
            print(" 间隔序列(ms): %s" % meta["dt_ms_series"][:12])
            print(" 长度序列(B): %s" % meta["req_len_series"][:12])
        print(" 产物：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
