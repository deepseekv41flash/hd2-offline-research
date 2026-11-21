#!/usr/bin/env python3
"""把 kit/stage2 + 证书 + 服务器信息打成可分发 zip。

证书与服务器 IP 属于「临时研究物」，不进公开仓库：本脚本把
   * kit/stage2/*.ps1 / README / test-card
   * --ca 指定的 ca.cer、--crl 指定的 ca.crl
   * 现场生成的 server.txt（IP / 域名 / CA 指纹 / 证书到期 / 部署形态）
打进 state/dist/ 下的 zip，并打印 SHA256（发给人时一起给）。

用法:
    python tools/package_stage2_kit.py \
        --ca state/dist/stage2-server-A/ca.cer \
        --crl state/dist/stage2-server-A/ca.crl \
        --server-ip 106.52.201.42 \
        --domain api.live.prod.thehelldiversgame.com \
        --name stage2-kit-A
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KIT = REPO / "kit" / "stage2"
DIST = REPO / "state" / "dist"

KIT_FILES = [
    "lib.ps1", "install-hosts.ps1", "install-proxy.ps1", "crl-server.ps1",
    "selfcheck.ps1", "collect-logs.ps1", "rollback-all.ps1",
    "README.md", "test-card.md",
]


def cert_info(ca_path: Path) -> dict:
    """用 cryptography 读 CA 证书的 subject / 有效期 / SHA256 指纹。"""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
    except ImportError:
        return {}
    raw = ca_path.read_bytes()
    try:
        cert = x509.load_der_x509_certificate(raw)
    except ValueError:
        cert = x509.load_pem_x509_certificate(raw)
    fp = cert.fingerprint(hashes.SHA256()).hex().upper()
    not_after = cert.not_valid_after_utc.astimezone(timezone.utc)
    return {
        "subject": cert.subject.rfc4514_string(),
        "sha256_fingerprint": fp,
        "not_after_utc": not_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "not_after_local": not_after.astimezone().strftime("%Y-%m-%d %H:%M %z"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="打包阶段2 客户端测试包")
    ap.add_argument("--ca", required=True, help="ca.cer 路径（DER）")
    ap.add_argument("--crl", required=True, help="ca.crl 路径")
    ap.add_argument("--server-ip", required=True)
    ap.add_argument("--domain", default="api.live.prod.thehelldiversgame.com")
    ap.add_argument("--mode", default="nginx")
    ap.add_argument("--name", default="stage2-kit")
    ap.add_argument("--out-dir", default=str(DIST))
    args = ap.parse_args(argv)

    ca, crl = Path(args.ca), Path(args.crl)
    for p in (ca, crl):
        if not p.exists():
            raise SystemExit(f"缺文件: {p}")
    missing = [f for f in KIT_FILES if not (KIT / f).exists()]
    if missing:
        raise SystemExit(f"kit/stage2 缺文件: {missing}")

    info = cert_info(ca)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    zip_path = out_dir / f"{args.name}-{datetime.now().strftime('%Y%m%d')}.zip"
    if zip_path.exists():
        zip_path.unlink()

    server_txt = "\n".join([
        "HD2 阶段2 测试包 —— 连接信息（由主持方提供）",
        "=" * 46,
        f"发现层 IP      : {args.server_ip}",
        f"测试域名       : {args.domain}",
        f"部署形态       : {args.mode}（本机 nginx 反代 -> mock 回环 8443）",
        f"打包时间       : {stamp}",
        "",
        "测试根证书 ca.cer：",
        f"  Subject      : {info.get('subject', '?')}",
        f"  SHA256 指纹  : {info.get('sha256_fingerprint', '?')}",
        f"  有效期至     : {info.get('not_after_local', '?')}",
        "",
        "注意：证书有效期只有约 2 天；若测试改期，主持方会重新签发并给你新包。",
        "",
    ])

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in KIT_FILES:
            zf.write(KIT / name, name)
        zf.write(ca, "ca.cer")
        zf.write(crl, "ca.crl")
        zf.writestr("server.txt", server_txt)

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(json.dumps({
        "zip": str(zip_path),
        "bytes": zip_path.stat().st_size,
        "sha256": digest,
        "ca": info,
        "server_ip": args.server_ip,
        "domain": args.domain,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
