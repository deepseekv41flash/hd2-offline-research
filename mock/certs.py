"""研究线自签证书：CA + 叶子 + 空 CRL（窗口 Schannel 可接受的配方）。

配方来源（产品仓库只读参考 `src/hd2proxy/engine/certs.py`，研究线独立实现、互不依赖）：

  * **必须 CA 模式**：叶子自签时 Windows Schannel 找不到 CRL，会在握手前断开；
  * 叶子必须带 **CDP（CRL Distribution Point）**，且该 URL 要真能取到 CRL，
    否则 Schannel 报 `CRYPT_E_NO_REVOCATION_CHECK`；
  * SAN 只放目标域名；有效期 ≤2 天；
  * CA 签完叶子与 CRL 后**立即删掉 CA 私钥**（磁盘上不留能签任意域名的钥匙）。

与本项目产品的差别（研究线自己的隔离配置）:
  * CA 名字、目录、端口全独立（CRL 用 127.0.0.1:18081，不占产品的 8080）；
  * 信任安装默认 **CurrentUser\\Root**（非管理员、可逆），产品用 LocalMachine。

任何「装信任 / 改 hosts」的动作都不在本模块自动执行，只生成文件与给命令。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_HOST = "api.live.prod.thehelldiversgame.com"
# 注意：本机 18080 被用户另一个进程（gh_connect_proxy.py）占用，研究线 CRL 固定用 18081，
# 避免与产品 8080 / 用户进程抢端口（交接档 §8.1 的纪律同样适用于研究线自己的端口）。
DEFAULT_CRL_PORT = 18081
CA_CN = "HD2 Offline Research CA"


def _new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    try:
        path.chmod(0o600)
    except Exception:  # noqa: BLE001
        pass


def cdp_url(port: int = DEFAULT_CRL_PORT) -> str:
    return f"http://127.0.0.1:{port}/ca.crl"


def _leaf_builder(host: str, pub, issuer_name, days: int, crl_port: int,
                  issuer_key=None):
    now = datetime.now(timezone.utc)
    b = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(issuer_name)
        .public_key(pub)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(pub), critical=False)
        .add_extension(x509.CRLDistributionPoints([
            x509.DistributionPoint(
                full_name=[x509.UniformResourceIdentifier(cdp_url(crl_port))],
                relative_name=None, reasons=None, crl_issuer=None),
        ]), critical=False)
        .add_extension(x509.AuthorityInformationAccess([
            x509.AccessDescription(
                x509.oid.AuthorityInformationAccessOID.CA_ISSUERS,
                x509.UniformResourceIdentifier(cdp_url(crl_port))),
        ]), critical=False)
    )
    if issuer_key is not None:
        # OpenSSL 3 链校验要求叶子带 AKI（与 CA 的 SKI 对应），缺了会
        # "Missing Authority Key Identifier" —— Python/curl 都会拒，Schannel 也要求。
        b = b.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False)
    return b


def generate(cert_dir: Path | str, *, host: str = DEFAULT_HOST, days: int = 2,
             crl_port: int = DEFAULT_CRL_PORT) -> dict:
    """签发一套研究线会话证书。返回 session 元数据（同时写 session.json）。"""
    out = Path(cert_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    # ---- CA ----
    ca_key = _new_key()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_CN)])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.NameConstraints(permitted_subtrees=[x509.DNSName(host)],
                                            excluded_subtrees=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    # ---- 叶子 ----
    leaf_key = _new_key()
    leaf = (_leaf_builder(host, leaf_key.public_key(), ca.subject, days, crl_port,
                          issuer_key=ca_key)
            .sign(ca_key, hashes.SHA256()))

    _write_key(out / "server.key", leaf_key)
    (out / "server.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (out / "server.chain.pem").write_bytes(
        leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    (out / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (out / "ca.cer").write_bytes(ca.public_bytes(serialization.Encoding.DER))

    # ---- 空 CRL（签完就删 CA 私钥）----
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca.subject)
        .last_update(now - timedelta(minutes=5))
        .next_update(now + timedelta(days=days + 1))
        .sign(ca_key, hashes.SHA256())
    )
    (out / "ca.crl").write_bytes(crl.public_bytes(serialization.Encoding.DER))
    del ca_key

    problems = _check(leaf, ca, host, days)
    meta = {
        "schema": "hd2-offline-research-cert-v1",
        "host": host,
        "mode": "ca",
        "san_dns": [host],
        "days": days,
        "not_before": leaf.not_valid_before_utc.isoformat(),
        "not_after": leaf.not_valid_after_utc.isoformat(),
        "subject": leaf.subject.rfc4514_string(),
        "ca_subject": ca.subject.rfc4514_string(),
        "thumbprint_sha1": leaf.fingerprint(hashes.SHA1()).hex().upper(),
        "trust_thumbprint_sha1": ca.fingerprint(hashes.SHA1()).hex().upper(),
        "crl_port": crl_port,
        "cdp_url": cdp_url(crl_port),
        "created": now.isoformat(),
        "files": sorted(p.name for p in out.iterdir() if p.is_file()),
        "self_check": "OK" if not problems else "FAIL: " + "; ".join(problems),
    }
    (out / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    if problems:
        raise RuntimeError("证书自检未通过: " + "; ".join(problems))
    return meta


def _check(leaf, ca, host: str, days: int) -> list[str]:
    problems: list[str] = []
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns = list(san.get_values_for_type(x509.DNSName))
    if dns != [host]:
        problems.append(f"SAN 必须只有 {host}，实际 {dns}")
    left = (leaf.not_valid_after_utc - datetime.now(timezone.utc)).total_seconds()
    if left <= 0:
        problems.append("证书已过期")
    if days > 2:
        problems.append("有效期超过 2 天")
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    if ExtendedKeyUsageOID.SERVER_AUTH not in eku:
        problems.append("缺少 serverAuth EKU")
    if not leaf.extensions.get_extension_for_class(x509.CRLDistributionPoints):
        problems.append("缺少 CDP（CRL 分发点）")
    bc = ca.extensions.get_extension_for_class(x509.BasicConstraints).value
    if not bc.ca:
        problems.append("信任锚不是 CA")
    return problems


def load_meta(cert_dir: Path | str) -> dict | None:
    p = Path(cert_dir) / "session.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def remaining_hours(cert_dir: Path | str) -> float | None:
    meta = load_meta(cert_dir)
    if not meta:
        return None
    try:
        exp = datetime.fromisoformat(str(meta["not_after"]).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return max(0.0, (exp - datetime.now(timezone.utc)).total_seconds() / 3600.0)
    except Exception:  # noqa: BLE001
        return None


def ensure(cert_dir: Path | str, *, host: str = DEFAULT_HOST, days: int = 2,
           crl_port: int = DEFAULT_CRL_PORT, min_remaining_hours: float = 6.0) -> dict:
    """证书可用则复用，否则重签（重签后**必须重新装信任**，CA 变了）。"""
    meta = load_meta(cert_dir)
    left = remaining_hours(cert_dir)
    if meta and left is not None and left > min_remaining_hours \
            and meta.get("host") == host and meta.get("self_check") == "OK" \
            and (Path(cert_dir) / "server.key").exists():
        meta["reused"] = True
        meta["remaining_hours"] = round(left, 1)
        return meta
    meta = generate(cert_dir, host=host, days=days, crl_port=crl_port)
    meta["reused"] = False
    meta["remaining_hours"] = round(remaining_hours(cert_dir) or 0.0, 1)
    return meta


def preflight(cert_dir: Path | str, *, host: str = DEFAULT_HOST) -> list[str]:
    """起服务前的自检（返回问题列表，空 = OK）。"""
    out = Path(cert_dir)
    problems: list[str] = []
    meta = load_meta(out)
    if not meta:
        return [f"缺少 session.json: {out}（先跑 mockctl cert-gen）"]
    if meta.get("san_dns") != [host]:
        problems.append(f"证书 SAN 不是单一目标域名: {meta.get('san_dns')!r}")
    if meta.get("mode") != "ca":
        problems.append(f"证书模式是 {meta.get('mode')!r}：必须是 ca（叶子模式无 CRL，客户端握手前断开）")
    if meta.get("self_check") != "OK":
        problems.append(f"证书自检未通过: {meta.get('self_check')}")
    for name in ("server.key", "server.chain.pem", "ca.crl"):
        if not (out / name).exists():
            problems.append(f"缺少文件: {name}")
    left = remaining_hours(out)
    if left is None:
        problems.append("无法读取证书有效期")
    elif left <= 0:
        problems.append(f"证书已过期（{meta.get('not_after')}）-> 重跑 mockctl cert-gen")
    stray = [p.name for p in out.glob("*.key") if p.name != "server.key"]
    if stray:
        problems.append(f"证书目录里有多余私钥: {stray}")
    return problems


# ---- Windows 信任存储（只在 mockctl 里显式调用；不自动执行） ------------------
# 用系统原生 `certutil`，不用 PowerShell `Import-Certificate`：
# 后者在 `powershell -NoProfile -NonInteractive` 下会出现 "Cannot find drive 'Cert'"（实测 2026-11-21），
# 而 certutil 无此依赖，且退出码可直接当判据。

def _certutil(scope: str) -> list[str]:
    """certutil 的 scope 前缀：CurrentUser → `-user`；LocalMachine → 无（需管理员）。"""
    return ["certutil", "-user"] if scope.lower() == "currentuser" else ["certutil"]


def trust_argv(cert_dir: Path | str, thumbprint: str, scope: str = "CurrentUser",
               action: str = "install") -> list[str]:
    """构造 certutil 命令（纯函数，便于单测）。

    action: install | check | uninstall
    """
    base = _certutil(scope)
    cer = str(Path(cert_dir).resolve() / "ca.cer")
    suffix = ["-f", "Root", cer]
    if action == "install":
        return base + ["-addstore"] + suffix
    if action == "check":
        return base + ["-verifystore", "Root", thumbprint]
    if action == "uninstall":
        return base + ["-delstore", "Root", thumbprint]
    raise ValueError(f"未知 action: {action}")


def _certutil_run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=60,
                          encoding="utf-8", errors="replace")


def is_trusted(cert_dir: Path | str, scope: str = "CurrentUser") -> bool | None:
    meta = load_meta(cert_dir)
    if not meta:
        return None
    thumb = meta.get("trust_thumbprint_sha1")
    if not thumb:
        return None
    try:
        proc = _certutil_run(trust_argv(cert_dir, thumb, scope, "check"))
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return None


def install_trust(cert_dir: Path | str, scope: str = "CurrentUser") -> dict:
    """把 CA 装进信任根存储。CurrentUser 不需要管理员；LocalMachine 需要。"""
    out = Path(cert_dir).resolve()
    cer = out / "ca.cer"
    if not cer.exists():
        raise FileNotFoundError(f"缺少 {cer}（先跑 cert-gen）")
    meta = load_meta(out) or {}
    thumb = meta.get("trust_thumbprint_sha1", "")
    before = is_trusted(out, scope)
    if before is True:
        return {"scope": scope, "action": "already-trusted", "thumbprint": thumb}
    proc = _certutil_run(trust_argv(out, thumb, scope, "install"))
    if proc.returncode != 0:
        raise RuntimeError(f"certutil 导入失败({proc.returncode}): "
                           f"{(proc.stdout + proc.stderr).strip()[-400:]}")
    after = is_trusted(out, scope)
    if after is not True:
        raise RuntimeError(f"导入后仍查不到信任（scope={scope}）")
    return {"scope": scope, "action": "installed", "thumbprint": thumb,
            "subject": meta.get("ca_subject"), "not_after": meta.get("not_after")}


def uninstall_trust(cert_dir: Path | str, scope: str = "CurrentUser") -> dict:
    out = Path(cert_dir)
    meta = load_meta(out) or {}
    thumb = meta.get("trust_thumbprint_sha1")
    if not thumb:
        raise RuntimeError("session.json 里没有 trust_thumbprint_sha1")
    proc = _certutil_run(trust_argv(out, thumb, scope, "uninstall"))
    if proc.returncode != 0:
        raise RuntimeError(f"certutil 删除失败({proc.returncode}): "
                           f"{(proc.stdout + proc.stderr).strip()[-400:]}")
    return {"scope": scope, "action": "removed", "thumbprint": thumb,
            "trusted_after": is_trusted(out, scope)}


def fingerprint_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
