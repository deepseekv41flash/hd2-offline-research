#!/usr/bin/env python3
"""EAC 上报采集（**CONNECT 代理模式**，2026-11-25 铁证后的正解）。

铁证：EAC-off 线用 mihomo `/connections` 按 sourcePort 反查，确认**游戏进程确实把
`api.epicgames.dev` 走系统代理**（`127.0.0.1:7897`）。所以第 3 轮正解是恢复 T6a 的
"系统代理 + CONNECT 终结"，**不需要 hosts、不需要 env、不需要重启 Steam**。

流程：
  1. 快照 HKCU IE 代理（`ProxyServer=127.0.0.1:7897` 等）→ 指向我们的 CONNECT 代理；
  2. 起 `tools/mitm_proxy.py`（**CONNECT 模式**，非透明）：只拦截 `api.epicgames.dev`，
     对 `/anticheat/` 与 `/telemetry/` **落完整原始字节**，其余域名只记 schema；
  3. 并行起 `tshark` 抓 loopback 取 **JA3/SNI**；
  4. 到时收尾：停进程 → **精确还原 IE 代理** → 产 `index.jsonl` / `meta.json`（含 Bootstrapper 事件）。

判据（EAC-off 线的口径）：
  * 出现 `POST /anticheat/v8/games/client/message/`（~12 s 一条）⇒ **完胜**；
  * 只有 telemetry/Bootstrapper ⇒ 说明 anticheat 走的是 env/libcurl，下一步加 env；
  * 一条都没有 ⇒ 检查端口/协议（本模式必须是 CONNECT，不能是透明）。

**不需要管理员**（只改 HKCU）。用法：
    python tools/eac_capture_proxy_run.py --out-dir D:/mod/tmp/eac-p1 --dry-run
    python tools/eac_capture_proxy_run.py --out-dir D:/mod/tmp/eac-p1 --duration 300
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eac_baseline_capture as base   # 复用索引/汇总/IE 代理/JA3 等函数

REPO = Path(__file__).resolve().parent.parent
ANTICHEAT_HOST = base.ANTICHEAT_HOST



def preflight_through_proxy(port: int, ca_file: Path, host: str, timeout: float = 15.0) -> dict:
    """开窗前的自检：经我们的 CONNECT 代理打一条假上报，确认 TLS 终结 + 落盘链路能工作。

    为什么必须在**起游戏之前**做：一次启动窗口很贵（本机 3.5 s↔64 s 抖动），
    如果代理/证书/落盘任一处有问题，宁可现在就失败，也不要浪费窗口。
    """
    import socket as _s
    import ssl as _ssl
    CRLF = chr(13) + chr(10)
    try:
        sock = _s.create_connection(("127.0.0.1", port), timeout=timeout)
        sock.sendall(("CONNECT %s:443 HTTP/1.1" % host + CRLF
                      + "Host: %s:443" % host + CRLF + CRLF).encode())
        head = sock.recv(200)
        if b" 200 " not in head.split(CRLF.encode())[0]:
            return {"ok": False, "detail": "CONNECT 未返回 200：%r" % head[:80], "status": 0}
        ctx = _ssl.create_default_context(cafile=str(ca_file))
        tls = ctx.wrap_socket(sock, server_hostname=host)
        payload = b"PREFLIGHT" * 8
        head_req = ("POST /anticheat/v8/games/client/message/ HTTP/1.1" + CRLF
                    + "Host: %s" % host + CRLF
                    + "Content-Type: application/octet-stream" + CRLF
                    + "Connection: close" + CRLF
                    + "Content-Length: %d" % len(payload) + CRLF + CRLF)
        tls.sendall(head_req.encode() + payload)
        data = tls.recv(400)
        status = int(data.split(b" ")[1]) if data.startswith(b"HTTP/") else 0
        return {"ok": status > 0, "detail": "TLS ok, 上游状态 %d" % status, "status": status}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc), "status": 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EAC 上报采集（CONNECT 代理模式）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--duration", type=int, default=300)
    ap.add_argument("--proxy-port", type=int, default=18092)
    ap.add_argument("--cert-dir", default=str(REPO / "state" / "certs-eac"))
    ap.add_argument("--intercept", default=ANTICHEAT_HOST)
    ap.add_argument("--dump-paths", default="/anticheat/|/telemetry/")
    ap.add_argument("--dump-limit", type=int, default=0)
    ap.add_argument("--chain-proxy", default="",
                    help="非拦截域名转发到的上游代理（默认沿用原系统代理，如 127.0.0.1:7897）；"
                         "留空且原来没有代理时用 --no-chain")
    ap.add_argument("--no-chain", action="store_true")
    ap.add_argument("--no-preflight", action="store_true",
                    help="跳过开窗前自检（默认会经代理打一条假上报，确认链路可用）")
    ap.add_argument("--no-tshark", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    out = Path(args.out_dir)
    plan = {
        "mode": "connect-proxy", "out_dir": str(out), "duration_s": args.duration,
        "ie_proxy_now": base.snapshot_ie_proxy(),
        "proxy_will_be": "127.0.0.1:%d" % args.proxy_port,
        "intercept": args.intercept, "dump_paths": args.dump_paths,
        "cert_dir": args.cert_dir,
        "tshark": "off" if args.no_tshark else (base.find_tshark() or "MISSING"),
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=1))
        return 0

    cert_dir = Path(args.cert_dir)
    if not (cert_dir / "server.chain.pem").exists():
        print("缺少证书：python tools/mockctl.py cert-gen --dir %s --hosts %s"
              % (cert_dir, args.intercept))
        return 2

    out.mkdir(parents=True, exist_ok=True)
    saved = base.snapshot_ie_proxy()
    (out / "ie-proxy-backup.json").write_text(json.dumps(saved, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
    jsonl, dump_dir = out / "mitm.jsonl", out / "raw"
    procs: list[subprocess.Popen] = []
    tsh = None
    pcap_path: Path | None = None
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        print("[1/4] IE 代理：%s → 127.0.0.1:%d（原值已备份）"
              % (saved.get("ProxyServer"), args.proxy_port))
        base.apply_ie_proxy("127.0.0.1:%d" % args.proxy_port)
        chain = args.chain_proxy or (saved.get("ProxyServer") or "")
        # 防御：若快照里的代理就是本脚本自己的监听地址（上次异常退出没还原），不能链回自身
        if chain.replace(" ", "") in ("127.0.0.1:%d" % args.proxy_port,
                                      "localhost:%d" % args.proxy_port):
            print("[!] 快照里的上游代理是本脚本自己（上次未还原），改用 127.0.0.1:7897")
            chain = "127.0.0.1:7897"
        cmd = [sys.executable, str(REPO / "tools" / "mitm_proxy.py"),
               "--port", str(args.proxy_port), "--intercept", args.intercept,
               "--cert-dir", str(cert_dir), "--log", str(jsonl),
               "--dump-bodies", str(dump_dir), "--dump-hosts", args.intercept,
               "--dump-paths", args.dump_paths, "--dump-limit", str(args.dump_limit)]
        if chain and not args.no_chain:
            cmd += ["--chain-proxy", chain]
            print("[2/4] 非拦截域名将链回上游 %s（保住系统其它流量）" % chain)
        else:
            cmd += ["--no-chain"]
        print("[2/4] CONNECT 代理监听 127.0.0.1:%d（拦截 %s）" % (args.proxy_port, args.intercept))
        procs.append(subprocess.Popen(cmd))
        if not args.no_tshark and (tsh := base.find_tshark()):
            pcap_path = out / "loopback.pcapng"
            procs.append(subprocess.Popen(
                [tsh, "-i", r"\Device\NPF_Loopback", "-f", "tcp port %d" % args.proxy_port,
                 "-w", str(pcap_path), "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            print("[3/4] tshark 抓 loopback（结束后抽 JA3/SNI）")
        else:
            print("[3/4] 跳过 tshark")
        if not args.no_preflight:
            import tempfile
            ca_file = cert_dir / "ca.pem"
            pf = preflight_through_proxy(args.proxy_port, ca_file, args.intercept)
            print("[*] 开窗自检：%s（%s）" % ("PASS" if pf["ok"] else "FAIL", pf["detail"]))
            if not pf["ok"]:
                print("[!] 自检失败 —— 现在停下来，别浪费游戏窗口")
                return 3
        print("[4/4] **现在启动游戏**（主菜单停留即可，%d s 后自动收尾；Ctrl+C 提前结束）"
              % args.duration)
        deadline = time.time() + args.duration
        while time.time() < deadline:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] 提前结束")
    finally:
        print("[*] 收尾：停进程 → 还原 IE 代理")
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
        restored_ok = False
        try:
            base.restore_ie_proxy(saved)
            now = base.snapshot_ie_proxy()
            restored_ok = (now.get("ProxyServer") == saved.get("ProxyServer")
                           and int(now.get("ProxyEnable") or 0) == int(saved.get("ProxyEnable") or 0))
            print("[*] IE 代理已还原：%s（一致=%s）" % (now.get("ProxyServer"), restored_ok))
        except Exception as exc:  # noqa: BLE001
            print("[!] 代理还原失败，请手工恢复：%r" % (exc,))
        if pcap_path is not None and tsh:
            n = base.extract_ja3(tsh, pcap_path, out / "ja3-sni.tsv", args.proxy_port)
            print("[*] JA3/SNI 提取 %d 条" % n)
        ended = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = base.build_index(jsonl)
        tele = base.build_telemetry_index(jsonl)
        meta = base.summarize(rows, started=started, ended=ended)
        meta.update({
            "mode": "connect-proxy",
            "ie_proxy_before": saved, "ie_proxy_restored_identical": restored_ok,
            "intercept": args.intercept, "dump_paths": args.dump_paths,
            "hosts": base.count_hosts(jsonl),
            "game_api_requests": base.count_hosts(jsonl).get(base.GAME_API_HOST, {}).get("requests", 0),
            "modules_cdn_seen": base.MODULES_HOST in base.count_hosts(jsonl),
            "telemetry": {
                "requests": len(tele),
                "app_ids": sorted({t["app_id"] for t in tele if t["app_id"]}),
                "upload_types": sorted({t["upload_type"] for t in tele if t["upload_type"]}),
                "event_names": sorted({e.get("EventName") for t in tele
                                       for e in base._telemetry_events(out, t)}),
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
        print(" anticheat 请求 %d / 响应 %d | telemetry %d（app_ids=%s）"
              % (meta["anticheat_requests"], meta["anticheat_responses"],
                 len(tele), meta["telemetry"]["app_ids"]))
        if meta["dt_ms_series"]:
            print(" 间隔序列(ms): %s" % meta["dt_ms_series"][:12])
            print(" 长度序列(B): %s" % meta["req_len_series"][:12])
        print(" 产物：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
