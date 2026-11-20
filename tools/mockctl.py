#!/usr/bin/env python3
"""研究线 mock 控制台（唯一入口）。

子命令:
  ports          查 443 / 8080 / 17897 / 18081 归属（交接档 §8.1 的纪律）
  cert-gen       签发研究线会话证书（CA + 叶子 + CRL，2 天有效）
  cert-status    证书状态（可选查 Windows 信任存储）
  cert-install   把研究 CA 装进信任根（默认 CurrentUser，非管理员）
  cert-uninstall 卸载研究 CA
  serve          起 mock（默认 LAN IP:443 + CRL 18081）
  health         读运行中 mock 的 /health
  selftest       离线门禁：证书 + TLS 自检 + handler/scenario 回放全过

所有动作都只写**研究线自己的目录**（默认 `state/`），不碰产品仓库与用户运行态。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mock import certs  # noqa: E402
from mock.client import HttpsClient  # noqa: E402
from mock.handler import MockHandler  # noqa: E402
from mock.server import MockServer, check_ports  # noqa: E402

DEFAULT_PROFILE = REPO / "profiles" / "offline-mock.json"
SNI = certs.DEFAULT_HOST


def _load_profile(path: Path | None) -> dict:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {}


def _resolve(profile: dict, key: str, default: str) -> Path:
    val = (profile.get(key) or default)
    p = Path(val)
    return p if p.is_absolute() else REPO / p


# ---------------------------------------------------------------- 子命令
def cmd_ports(args) -> int:
    info = check_ports(args.host, args.port)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("\n口诀（§8.1）：443 只能有一个主人；8080/17897 是产品专属；本 mock 的 CRL 用 18081。")
    return 0


def cmd_cert_gen(args) -> int:
    cert_dir = Path(args.dir)
    meta = certs.generate(cert_dir, host=args.host, days=args.days, crl_port=args.crl_port)
    print(f"[cert-gen] {cert_dir}")
    print(f"  mode={meta['mode']} san={meta['san_dns']} not_after={meta['not_after']}")
    print(f"  CA thumbprint={meta['trust_thumbprint_sha1']}")
    print(f"  CDP={meta['cdp_url']}  self_check={meta['self_check']}")
    print(f"  装信任（非管理员）: python tools/mockctl.py cert-install --dir {cert_dir}")
    return 0


def cmd_cert_status(args) -> int:
    cert_dir = Path(args.dir)
    meta = certs.load_meta(cert_dir)
    if not meta:
        print(f"[cert-status] 没有证书：{cert_dir}")
        return 1
    left = certs.remaining_hours(cert_dir)
    out = {
        "dir": str(cert_dir),
        "mode": meta.get("mode"),
        "host": meta.get("host"),
        "san": meta.get("san_dns"),
        "not_after": meta.get("not_after"),
        "remaining_hours": None if left is None else round(left, 1),
        "self_check": meta.get("self_check"),
        "cdp_url": meta.get("cdp_url"),
        "trust_thumbprint_sha1": meta.get("trust_thumbprint_sha1"),
        "preflight": certs.preflight(cert_dir, host=args.host),
    }
    if args.check_trust:
        out["trusted"] = {s: certs.is_trusted(cert_dir, s)
                          for s in ("CurrentUser", "LocalMachine")}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not out["preflight"] else 1


def cmd_cert_install(args) -> int:
    print(json.dumps(certs.install_trust(args.dir, scope=args.scope),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_cert_uninstall(args) -> int:
    print(json.dumps(certs.uninstall_trust(args.dir, scope=args.scope),
                     ensure_ascii=False, indent=2))
    return 0


def _build_handler(data_dir: Path, policy: dict, mission_end_dir: Path | None) -> MockHandler:
    from mock.capture import CaptureStore

    store = CaptureStore.load(data_dir)
    template = None
    if mission_end_dir and mission_end_dir.exists():
        cands = sorted(mission_end_dir.glob("*.json"))
        if cands:
            template = json.loads(cands[0].read_text(encoding="utf-8"))
    entry = store.find(base_path="/api/Progression/Items/Customize/v2")
    customize = entry.doc if entry and entry.doc is not None else None
    return MockHandler(store, profile=policy, mission_end_template=template,
                       customize_template=customize)


def cmd_serve(args) -> int:
    profile = _load_profile(Path(args.profile) if args.profile else DEFAULT_PROFILE)
    if args.capture_bodies:
        cfg = dict(profile.get("body_shapes") or {})
        cfg.setdefault("paths", ["/api/Account/Login"])
        cfg.setdefault("dir", "state/evidence/b9")
        cfg["enabled"] = True
        profile["body_shapes"] = cfg
    elif (profile.get("body_shapes") or {}).get("enabled"):
        print("[serve] body_shapes 已由 profile 启用：只记 schema，不落原值")
    data_dir = Path(args.data) if args.data else _resolve(profile, "data_dir", "samples/store-season")
    cert_dir = Path(args.cert_dir) if args.cert_dir else _resolve(
        profile, "cert_dir", "state/certs")
    log_dir = Path(args.log_dir) if args.log_dir else _resolve(profile, "log_dir", "state/logs")
    bind = profile.get("bind") or {}
    host = args.host or bind.get("host", "127.0.0.1")
    port = args.port or int(bind.get("port", 443))
    crl_port = int(bind.get("crl_port", 18081))
    if args.dry_run:
        host, port = "127.0.0.1", 18443

    problems = certs.preflight(cert_dir)
    if problems:
        print("[serve] 证书自检失败：")
        for p in problems:
            print("  -", p)
        print(f"  先跑: python tools/mockctl.py cert-gen --dir {cert_dir}")
        return 2
    meta = certs.load_meta(cert_dir) or {}
    print(f"[serve] 证书剩余 {certs.remaining_hours(cert_dir):.1f} h  CDP={meta.get('cdp_url')}")
    if not certs.is_trusted(cert_dir, "CurrentUser") and not args.insecure_ok:
        print("[serve] ⚠ 研究 CA 未装在 CurrentUser\\Root/ LocalMachine\\Root。"
              "装法（非管理员）: python tools/mockctl.py cert-install")

    mission_end_dir = Path(args.mission_end) if args.mission_end else REPO / "samples" / "mission-end"
    handler = _build_handler(data_dir, profile, mission_end_dir)
    srv = MockServer(handler, host=host, port=port, cert_dir=cert_dir, log_dir=log_dir,
                     log_headers=args.log_headers, crl_port=crl_port,
                     serve_crl=not args.no_crl)
    try:
        srv.start()
    except RuntimeError as exc:
        print(f"[serve] {exc}")
        print(json.dumps(check_ports(host, port), ensure_ascii=False, indent=2))
        return 3
    probe = srv.local_probe()
    print(f"[serve] TLS 自检: {json.dumps(probe, ensure_ascii=False)}")
    print(f"[serve] data={data_dir}  host={host}:{port}  crl={'127.0.0.1:' + str(crl_port)}")
    print("[serve] Ctrl+C 停止；实验结束记得还原 hosts（hd2proxy.exe install 或手改）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 收到 Ctrl+C")
    finally:
        srv.stop()
    return 0


def cmd_health(args) -> int:
    host, _, port = args.url.rpartition(":")
    client = HttpsClient(host or "127.0.0.1", int(port or 18443), sni_host=SNI,
                         ca_file=args.ca, insecure=args.insecure)
    r = client.request("GET", "/health")
    if r.error or r.status != 200:
        print(f"[health] 失败: status={r.status} error={r.error}")
        return 1
    print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:4000])
    return 0


def cmd_selftest(args) -> int:
    """离线门禁：证书 → CRL → TLS → handler/scenario 回放。"""
    from tools import replay_client  # noqa: WPS433  (同仓工具，运行时导入)

    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and cond
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    data_dir = Path(args.data)
    if not (data_dir / "index.txt").exists():
        print(f"[selftest] 数据目录没有 index.txt: {data_dir}")
        return 2

    with tempfile.TemporaryDirectory(prefix="hd2mock-cert-") as tmp:
        cert_dir = Path(tmp) / "certs"
        meta = certs.generate(cert_dir, crl_port=args.crl_port)
        check("证书签发 + 自检", meta["self_check"] == "OK", meta["self_check"])
        check("preflight 无问题", not certs.preflight(cert_dir), "")
        check("CRL 已生成", (cert_dir / "ca.crl").stat().st_size > 0)
        check("CA 私钥已删除", not (cert_dir / "ca.key").exists())

        handler = _build_handler(data_dir, {"lobby": {"mode": "state"}},
                                 REPO / "samples" / "mission-end")
        srv = MockServer(handler, host="127.0.0.1", port=0, cert_dir=cert_dir,
                         log_dir=None, crl_port=0, serve_crl=False)
        srv.start()
        try:
            probe = srv.local_probe()
            check("TLS（Python 客户端，校验研究 CA）", all(
                isinstance(v, dict) and v.get("status") == 200 for v in probe.values()),
                json.dumps(probe, ensure_ascii=False)[:300])
        finally:
            srv.stop()

    # ---- 回放门禁 ----
    for mode in ("handler", "scenario"):
        code = replay_client.main(["--src", str(data_dir), "--mode", mode,
                                   "--mission-end", str(REPO / "samples" / "mission-end"),
                                   "--json", str(Path(args.out) / f"replay-{mode}.json")
                                   if args.out else ""] )
        check(f"回放 {mode} 模式全 PASS", code == 0, f"exit={code}")
    print(f"\nSELFTEST {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mockctl")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ports"); p.add_argument("--host", default="100.67.169.17")
    p.add_argument("--port", type=int, default=443); p.set_defaults(fn=cmd_ports)

    p = sub.add_parser("cert-gen")
    p.add_argument("--dir", default=str(REPO / "state" / "certs"))
    p.add_argument("--host", default=SNI)
    p.add_argument("--days", type=int, default=2)
    p.add_argument("--crl-port", type=int, default=18081)
    p.set_defaults(fn=cmd_cert_gen)

    p = sub.add_parser("cert-status")
    p.add_argument("--dir", default=str(REPO / "state" / "certs"))
    p.add_argument("--host", default=SNI)
    p.add_argument("--check-trust", action="store_true")
    p.set_defaults(fn=cmd_cert_status)

    p = sub.add_parser("cert-install")
    p.add_argument("--dir", default=str(REPO / "state" / "certs"))
    p.add_argument("--scope", choices=("CurrentUser", "LocalMachine"), default="CurrentUser")
    p.set_defaults(fn=cmd_cert_install)

    p = sub.add_parser("cert-uninstall")
    p.add_argument("--dir", default=str(REPO / "state" / "certs"))
    p.add_argument("--scope", choices=("CurrentUser", "LocalMachine"), default="CurrentUser")
    p.set_defaults(fn=cmd_cert_uninstall)

    p = sub.add_parser("serve")
    p.add_argument("--data", default=None)
    p.add_argument("--profile", default=None)
    p.add_argument("--cert-dir", default=None)
    p.add_argument("--log-dir", default=None)
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--mission-end", default=None)
    p.add_argument("--log-headers", action="store_true")
    p.add_argument("--capture-bodies", action="store_true",
                   help="B9：只记登录体 schema（字段名/长度/字符类/hash），不落原值")
    p.add_argument("--no-crl", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="绑 127.0.0.1:18443，不抢 443")
    p.add_argument("--insecure-ok", action="store_true", help="跳过未装信任的告警")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("health")
    p.add_argument("--url", default="127.0.0.1:18443")
    p.add_argument("--ca", default=str(REPO / "state" / "certs" / "ca.pem"))
    p.add_argument("--insecure", action="store_true")
    p.set_defaults(fn=cmd_health)

    p = sub.add_parser("selftest")
    p.add_argument("--data", default=str(REPO / "samples" / "store-season"))
    p.add_argument("--crl-port", type=int, default=0)
    p.add_argument("--out", default=str(REPO / "state" / "selftest"))
    p.set_defaults(fn=cmd_selftest)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
