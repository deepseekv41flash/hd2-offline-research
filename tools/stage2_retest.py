#!/usr/bin/env python3
"""阶段2 复测（公钥时序缝隙 A+B）的就绪自检 / 基线快照 / 判据判定。

为什么单独做一个工具：复测窗口很短（实机 10 分钟）、出错成本高，而现场最容易犯两类错——

  1. **少做一步**：CA 信任 / hosts / 本机 CRL / 中继 / A 机发现层状态重置，任一缺失在
     游戏里都表现为「点加入没反应」，现场很难区分是哪一环。本工具把这些前置逐条查出来，
     每项失败都给可直接复制的补救命令。
  2. **判据算错**：验收判据是**增量**（`keys_miss` 不增、`keys_bind_hit`/`keys_waited` 有增、
     `lobby_member:rejected` 不增、`join` 有增），拿两次 `/health` 肉眼看容易看错，且
     中途若发生状态重置，增量全部失真。`judge` 把这一步固定成可复算的规则。

三个子命令（全部可离线单测，纯函数在 `run_checks` / `judge_snapshots` / `parse_member_evidence`）：

    python tools/stage2_retest.py check                      # 只读自检，不改任何系统状态
    python tools/stage2_retest.py snapshot --out state/evidence/retest/health-before.json
    python tools/stage2_retest.py judge --before <before.json> --after <after.json> \
        --log-file state/evidence/retest/mock-watch.log

判据为什么要求「兜底被触发」：缝隙只有在「对方刚登录、还没 PUT 身份」时才会出现。若没复现
缝隙（`keys_hit` 有增、`bind_hit`/`waited` 都不增），说明测的不是 A+B 那条路径，必须重测 ——
所以这项 fail 的含义是「未证明」，不是「功能坏了」。

本工具不做任何写操作：不装 CA、不改 hosts、不起进程、不碰 A 机。
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOMAIN = "api.live.prod.thehelldiversgame.com"
DEFAULT_RELAY_IP = "100.67.88.219"
DEFAULT_CRL_PORT = 18081
DEFAULT_CERT_DIR = REPO / "state" / "certs-a"

# mock 日志里成员保活/加入的两类行（格式见 state/evidence 实测日志）：
#   POST   /api/lobby/<id>/member 4B -> 200 1183B [discovery:lobby_member] via 127.0.0.1
#   POST   /api/lobby/<id>/member 4B -> 200 2B [discovery:lobby_member:rejected] via 127.0.0.1
MEMBER_OK_RE = re.compile(r"/member\s+\d+B\s+->\s+200\s+(\d+)B\s+\[discovery:lobby_member\]")
MEMBER_REJECTED_RE = re.compile(r"\[discovery:lobby_member:rejected\]")

# 成员保活回包的体积（E3 两轮真实日志直方图校准，别凭感觉定阈值）：
#   * 占位/空壳体：344 / 349 / 369 B（新建占位厅、空状态）
#   * **1 人**真实厅体：1158 / 1159 / 1177 / 1183 / 1184 B  ← 注意：1 人也能到 1184 B
#   * **2 人**厅体：1236 / 1237 B                        ← 加入成功的判据
#   * 任务已开始：1340 / 1345 / 1448 / 1457 B（也是 2 人）
# 所以「双人」的分界必须高于 1184，取 1200；用 1000 会把单人厅体误判成双人体。
TWO_PLAYER_MIN_BODY = 1200
# 低于这个体积说明不是「真实厅体」（用来区分「没进来」和「进来了但只有自己」）。
REAL_BODY_MIN = 1000

OK, FAIL, WARN, SKIP = "ok", "fail", "warn", "skip"
VERDICT_PASS, VERDICT_FAIL, VERDICT_INCOMPLETE = "PASS", "FAIL", "INCOMPLETE"


# ---------------------------------------------------------------- 基础读取

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hosts_path() -> Path:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return Path(root) / "System32" / "drivers" / "etc" / "hosts"


def read_hosts(path: Path | None = None) -> str:
    return (path or hosts_path()).read_text(encoding="utf-8", errors="replace")


def hosts_mapping(text: str, domain: str = DOMAIN) -> str | None:
    """返回 hosts 里生效（未注释）的 domain → IP 映射；没有则 None。"""
    want = domain.strip().lower()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        if any(p.lower() == want for p in parts[1:]):
            return parts[0]
    return None


def load_ca_info(cert_dir: Path | str = DEFAULT_CERT_DIR) -> dict:
    """读本机保存的 A 机 CA（DER），给出到期时间/剩余小时/SHA1 指纹。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    cer = Path(cert_dir) / "ca.cer"
    if not cer.exists():
        raise FileNotFoundError(f"缺少 {cer}")
    ca = x509.load_der_x509_certificate(cer.read_bytes())
    left = (ca.not_valid_after_utc - datetime.now(timezone.utc)).total_seconds() / 3600.0
    return {
        "path": str(cer),
        "subject": ca.subject.rfc4514_string(),
        "not_before": ca.not_valid_before_utc.isoformat(),
        "not_after": ca.not_valid_after_utc.isoformat(),
        "remaining_hours": round(left, 2),
        "thumbprint_sha1": ca.fingerprint(hashes.SHA1()).hex().upper(),
    }


def sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- 网络探针

def tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def crl_endpoint_sha(port: int = DEFAULT_CRL_PORT, timeout: float = 2.0) -> str:
    """取本机 CRL 服务返回的派生 CRL 的 sha256；失败抛异常。"""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(b"GET /ca.crl HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    if not head.startswith(b"HTTP/1.1 200") and not head.startswith(b"HTTP/1.0 200"):
        raise RuntimeError(f"CRL 服务返回非 200：{head[:80]!r}")
    return hashlib.sha256(body).hexdigest()


def fetch_health(*, host: str = DEFAULT_RELAY_IP, port: int = 443, domain: str = DOMAIN,
                 ca_file: Path | str | None = None, timeout: float = 6.0) -> tuple[dict, dict]:
    """经本机中继做一次完整 TLS（SNI=域名、显式信任 A 机 CA）→ GET /health。

    返回 (health_json, 服务端叶子信息)。链路里任一环（CRL 缺失会让 Schannel 拒握手、中继没起、
    A 机没起、证书过期）都会在这里失败 —— 这是「游戏之前的总自检」。
    """
    from cryptography import x509

    ca_path = Path(ca_file) if ca_file else Path(DEFAULT_CERT_DIR) / "ca.pem"
    ctx = ssl.create_default_context(cafile=str(ca_path))
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=domain) as tls:
            tls.settimeout(timeout)
            tls.sendall(
                (f"GET /health HTTP/1.1\r\nHost: {domain}\r\n"
                 f"User-Agent: stage2-retest\r\nAccept: */*\r\nConnection: close\r\n\r\n").encode()
            )
            resp = http.client.HTTPResponse(tls, method="GET")
            resp.begin()
            status = resp.status
            body = resp.read()
            der = tls.getpeercert(binary_form=True)
            resp.close()
    if status != 200:
        raise RuntimeError(f"/health HTTP {status}: {body[:200]!r}")
    leaf = x509.load_der_x509_certificate(der)
    left = (leaf.not_valid_after_utc - datetime.now(timezone.utc)).total_seconds() / 3600.0
    return json.loads(body.decode("utf-8")), {
        "subject": leaf.subject.rfc4514_string(),
        "not_after": leaf.not_valid_after_utc.isoformat(),
        "remaining_hours": round(left, 2),
    }


def certutil_trusted(thumbprint: str, scope: str = "CurrentUser") -> bool:
    """根存储里是否存在该指纹（certutil 返回码即判据）。"""
    argv = ["certutil", "-user"] if scope.lower() == "currentuser" else ["certutil"]
    argv += ["-verifystore", "Root", thumbprint]
    try:
        return subprocess.run(argv, capture_output=True, timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- check

def run_checks(*, relay_ip: str = DEFAULT_RELAY_IP, port: int = 443, domain: str = DOMAIN,
               crl_port: int = DEFAULT_CRL_PORT, cert_dir: Path | str = DEFAULT_CERT_DIR,
               health_fetcher=fetch_health, trust_checker=certutil_trusted,
               hosts_reader=read_hosts, crl_sha_getter=crl_endpoint_sha,
               tcp_probe=tcp_open) -> list[dict]:
    """8 项前置自检。每项 {id,name,status,detail,remedy}；纯只读。"""
    out: list[dict] = []

    def add(cid: str, name: str, status: str, detail: str, remedy: str = "") -> None:
        out.append({"id": cid, "name": name, "status": status,
                    "detail": detail, "remedy": remedy})

    cert_dir = Path(cert_dir)
    cer, pem, crl = cert_dir / "ca.cer", cert_dir / "ca.pem", cert_dir / "ca.crl"

    # CA01 证书材料
    ca: dict | None = None
    missing = [p.name for p in (cer, pem, crl) if not p.exists()]
    if missing:
        add("CA01", "证书材料", FAIL, f"缺文件 {missing}（{cert_dir}）",
            "从 A 机取回 ca.cer/ca.crl，或重跑 cert-gen 后重新分发")
    else:
        try:
            ca = load_ca_info(cert_dir)
            status = WARN if ca["remaining_hours"] <= 1 else OK
            add("CA01", "证书材料", status,
                f"CA 到期 {ca['not_after']}（剩 {ca['remaining_hours']} h）指纹 {ca['thumbprint_sha1']}")
        except Exception as exc:  # noqa: BLE001
            add("CA01", "证书材料", FAIL, f"读取失败：{exc!r}")

    # CA02 本机信任
    if ca:
        try:
            trusted = bool(trust_checker(ca["thumbprint_sha1"]))
        except Exception:  # noqa: BLE001
            trusted = False
        if trusted:
            add("CA02", "本机信任", OK, f"CurrentUser\\Root 含 {ca['thumbprint_sha1'][:8]}…")
        else:
            add("CA02", "本机信任", FAIL,
                f"根存储里没有 {ca['thumbprint_sha1'][:8]}…（游戏会直接握手失败）",
                f'certutil -user -addstore Root "{cer}"')

    # HOST01 hosts 映射
    try:
        text = hosts_reader()
        ip = hosts_mapping(text, domain)
    except Exception as exc:  # noqa: BLE001
        ip, text = None, ""
        add("HOST01", "hosts 映射", WARN, f"读不到 hosts：{exc!r}")
    if "HOST01" not in {c["id"] for c in out}:
        if ip is None:
            add("HOST01", "hosts 映射", FAIL, f"hosts 里没有生效的 {domain} → 中继 IP",
                f"管理员执行：Add-Content $env:SystemRoot\\System32\\drivers\\etc\\hosts "
                f"'{relay_ip} {domain} # HD2-STAGE2-TUNNEL'（先备份 hosts）")
        elif ip != relay_ip:
            add("HOST01", "hosts 映射", FAIL,
                f"hosts 指向 {ip}，本轮中继监听 {relay_ip}（指向旧实验会连不上）",
                f"把该行改成 {relay_ip} {domain} # HD2-STAGE2-TUNNEL")
        else:
            add("HOST01", "hosts 映射", OK, f"{ip} → {domain}")

    # CRL01 本机 CRL 服务
    if not crl.exists():
        add("CRL01", "本机 CRL", FAIL, "缺 ca.crl，无法核对 CRL 服务", "先取回 ca.crl")
    else:
        try:
            got = crl_sha_getter(crl_port)
            want = sha256_file(crl)
            if got == want:
                add("CRL01", "本机 CRL", OK, f"127.0.0.1:{crl_port} 提供的 CRL 与本地一致")
            else:
                add("CRL01", "本机 CRL", FAIL,
                    f"127.0.0.1:{crl_port} 的 CRL 与本地不一致（旧 CA 的服务还开着？）",
                    f"停旧服务后重跑：pwsh -File tools/crl_serve.ps1 -CrlPath {crl}")
        except Exception as exc:  # noqa: BLE001
            add("CRL01", "本机 CRL", FAIL, f"127.0.0.1:{crl_port} 取不到 CRL：{exc!r}",
                f"pwsh -NoProfile -File tools/crl_serve.ps1 -CrlPath {crl}  # 需前台常驻")

    # TRL01 中继监听
    if tcp_probe(relay_ip, port):
        add("TRL01", "中继监听", OK, f"{relay_ip}:{port} 可连")
    else:
        add("TRL01", "中继监听", FAIL, f"{relay_ip}:{port} 不可连",
            "python tools/e2_tunnel.py --via server2 --listen-host "
            f"{relay_ip} --listen-port {port} --target 106.52.201.42:{port}")

    # E2E01 端到端 /health（证书链 + CRL + 中继 + A 机一起证明）
    health: dict | None = None
    leaf: dict | None = None
    try:
        health, leaf = health_fetcher(host=relay_ip, port=port, domain=domain,
                                     ca_file=pem, timeout=8.0)
        add("E2E01", "端到端 /health", OK,
            f"TLS+HTTP 通；服务端叶子到期 {leaf['not_after']}（剩 {leaf['remaining_hours']} h）")
    except Exception as exc:  # noqa: BLE001
        add("E2E01", "端到端 /health", FAIL, f"{type(exc).__name__}: {exc}",
            "按 CA02/HOST01/CRL01/TRL01 的顺序先修前面的项（本项失败通常是它们的连带结果）")

    # CERT01 证书剩余时间（优先用服务端实际叶子）
    if leaf is None:
        add("CERT01", "证书剩余", SKIP, "端到端未通，读不到服务端证书")
    elif leaf:
        if leaf["remaining_hours"] <= 0:
            add("CERT01", "证书剩余", FAIL, f"服务端叶子已过期（{leaf['not_after']}）",
                "在 A 机跑 deploy/discovery/cert-issue.sh，然后重新分发 ca.cer/ca.crl")
        elif leaf["remaining_hours"] <= 1:
            add("CERT01", "证书剩余", WARN,
                f"只剩 {leaf['remaining_hours']} h（{leaf['not_after']}），测试窗口要赶在到期前",
                "到期后重签会生成新 CA，志愿者需替换 ca.cer/ca.crl")
        else:
            add("CERT01", "证书剩余", OK, f"{leaf['remaining_hours']} h（{leaf['not_after']}）")

    # A01 A 机发现层状态净空（B 兜底生效的前提）
    if health is None:
        add("A01", "A 机状态净空", SKIP, "端到端未通，无法读取 A 机发现层诊断")
    else:
        d = health.get("discovery") or {}
        stats = d.get("stats") or {}
        accounts = int(d.get("accounts") or 0)
        logins = int(stats.get("login") or 0)
        bindings = int(d.get("bindings") or 0)
        if accounts == 0 and logins == 0 and bindings == 0:
            add("A01", "A 机状态净空", OK, "accounts=0 login=0 bindings=0（B 兜底可触发）")
        else:
            add("A01", "A 机状态净空", FAIL,
                f"accounts={accounts} login={logins} bindings={bindings}：残留「已登录未上报」账号会"
                f"让单未知候选 ≥2、B 兜底不触发",
                "在 A 机：systemctl stop hd2-discovery → 删 state/discovery-state.json → start → 复查")
    return out


def _glyphs() -> dict:
    """终端能编码就发勾/叉/感叹号；老控制台（GBK 管道）退回 ASCII，别让排版把自己搞崩。"""
    try:
        "✓✗⚠".encode(sys.stdout.encoding or "ascii")
    except Exception:  # noqa: BLE001
        return {OK: "OK", FAIL: "X", WARN: "!", SKIP: "-"}
    return {OK: "✓", FAIL: "✗", WARN: "⚠", SKIP: "-"}


def format_checks(checks: list[dict]) -> str:
    marks = _glyphs()
    lines = ["== 阶段2 复测就绪自检（只读，不改任何系统状态） =="]
    for c in checks:
        lines.append(f"  [{marks.get(c['status'], '?')}] {c['id']} {c['name']}  {c['detail']}")
        if c["remedy"] and c["status"] in (FAIL, WARN):
            lines.append(f"      补救：{c['remedy']}")
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in (OK, WARN, FAIL, SKIP)}
    lines.append(f"  合计 {len(checks)} 项：" + " / ".join(
        f"{s} {counts[s]}" for s in (OK, WARN, FAIL, SKIP)))
    if counts[FAIL]:
        lines.append("  结论：✗ 未就绪 —— 按上面的补救命令逐项修，再跑一次 check。")
    elif counts[WARN]:
        lines.append("  结论：⚠ 就绪但有提醒（证书快到期等），可以开测。")
    else:
        lines.append("  结论：✓✓ 就绪。")
    return "\n".join(lines)


# ---------------------------------------------------------------- snapshot

def make_snapshot(health: dict, leaf: dict, *, endpoint: str, domain: str = DOMAIN) -> dict:
    return {"schema": "hd2-stage2-retest-snapshot-v1", "ts": _now_iso(),
            "domain": domain, "endpoint": endpoint,
            "served_not_after": leaf.get("not_after"),
            "served_remaining_hours": leaf.get("remaining_hours"),
            "health": health}


def discover_summary(obj: dict) -> dict:
    """从「快照包装体」或「裸 /health 体」里取出发现层计数。"""
    health = obj.get("health") if isinstance(obj.get("health"), dict) else obj
    d = health.get("discovery") or {}
    stats = d.get("stats") or {}
    out = {k: int(v) for k, v in stats.items() if isinstance(v, (int, float))}
    for key in ("accounts", "identities", "keys_known", "bindings", "lobbies",
                "tombstones", "generation"):
        out[key] = int(d.get(key) or 0)
    return out


# ---------------------------------------------------------------- judge

def parse_member_evidence(log_text: str) -> dict:
    """从 mock 日志里数成员保活/加入的证据（含双人厅体大小）。"""
    ok_sizes = [int(m.group(1)) for m in MEMBER_OK_RE.finditer(log_text)]
    rejected = len(MEMBER_REJECTED_RE.findall(log_text))
    real = [s for s in ok_sizes if s >= REAL_BODY_MIN]
    two_player = [s for s in ok_sizes if s >= TWO_PLAYER_MIN_BODY]
    return {
        "member_ok": len(ok_sizes),
        "member_rejected": rejected,
        "max_body": max(ok_sizes) if ok_sizes else 0,
        "real_bodies": len(real),
        # 真实厅体但没到双人档：疑似「只有自己在大厅里」
        "single_player_bodies": len([s for s in real if s < TWO_PLAYER_MIN_BODY]),
        "two_player_bodies": len(two_player),
        "two_player_max": max(two_player) if two_player else 0,
    }


def judge_snapshots(before: dict, after: dict, log_text: str | None = None) -> dict:
    """按判据比较两次快照（+ 可选 mock 日志）。返回 {criteria, verdict, deltas, before, after}。"""
    b, a = discover_summary(before), discover_summary(after)
    deltas = {k: a.get(k, 0) - b.get(k, 0) for k in sorted(set(b) | set(a))}
    criteria: list[dict] = []

    def add(cid: str, desc: str, status: str, detail: str) -> None:
        criteria.append({"id": cid, "desc": desc, "status": status, "detail": detail})

    def dl(key: str) -> int:
        return deltas.get(key, 0)

    # V01 增量本身是否可信（中途清过状态 ⇒ 增量失真）
    if a.get("accounts", 0) < b.get("accounts", 0) or a.get("login", 0) < b.get("login", 0):
        add("V01", "增量可信（未中途重置）", FAIL,
            f"accounts {b.get('accounts', 0)}→{a.get('accounts', 0)}、"
            f"login {b.get('login', 0)}→{a.get('login', 0)}：状态被清过，增量不可用，需重测")
    else:
        add("V01", "增量可信（未中途重置）", OK,
            f"accounts {b.get('accounts', 0)}→{a.get('accounts', 0)}，"
            f"login {b.get('login', 0)}→{a.get('login', 0)}")

    # C01 关键：A+B 的兜底路径是否真的被触发（没触发=没复现缝隙，不等于功能坏了）
    hit = dl("keys_bind_hit") + dl("keys_waited")
    detail = (f"keys_bind_hit Δ{dl('keys_bind_hit')}、keys_waited Δ{dl('keys_waited')}；"
              f"keys_hit Δ{dl('keys_hit')}、keys_miss Δ{dl('keys_miss')}")
    if hit > 0:
        add("C01", "兜底被触发（缝隙已复现）", OK, detail)
    else:
        add("C01", "兜底被触发（缝隙已复现）", FAIL,
            detail + " —— 未观察到兜底路径；确认「对方重启游戏后**立刻**点加入」是否照做")

    # C02 keys_miss 不增（A 的等待没有退化成未命中）
    add("C02", "keys_miss 未增加", OK if dl("keys_miss") <= 0 else FAIL,
        f"Δ{dl('keys_miss')}（before {b.get('keys_miss', 0)} → after {a.get('keys_miss', 0)}）")

    # C03 无成员被拒
    add("C03", "join_rejected 未增加", OK if dl("join_rejected") <= 0 else FAIL,
        f"Δ{dl('join_rejected')}（before {b.get('join_rejected', 0)} → "
        f"after {a.get('join_rejected', 0)}）")

    # C04 双方都完成登录
    if dl("login") >= 2:
        add("C04", "双方都完成登录", OK, f"login Δ{dl('login')}（≥2 即双方各一次）")
    elif dl("login") == 1:
        add("C04", "双方都完成登录", WARN, f"login Δ1：只有一侧登录过，另一侧可能没重启/没进游戏")
    else:
        add("C04", "双方都完成登录", FAIL, f"login Δ{dl('login')}：没有新登录")

    # C05 双方都上报过身份（PUT /api/lobby）+ 双方都查过 Keys
    ok5 = dl("put") >= 1 and dl("get") >= 1
    add("C05", "双方上报身份且查过 Keys", OK if ok5 else FAIL,
        f"put Δ{dl('put')}、get Δ{dl('get')}")

    # C06 加入被接受（/health 的 join 计数）
    add("C06", "加入被接受（join 增量）", OK if dl("join") >= 1 else FAIL,
        f"join Δ{dl('join')}（before {b.get('join', 0)} → after {a.get('join', 0)}）")

    # C07 绑定/身份留痕（B 的产物应当可见）
    add("C07", "留痕（bindings/identities）", OK if a.get("bindings", 0) >= 1 else WARN,
        f"bindings={a.get('bindings', 0)}、identities={a.get('identities', 0)}、"
        f"keys_known={a.get('keys_known', 0)}")

    # L01/L02 日志证据（有日志才算数，没有就是未证）
    ev = parse_member_evidence(log_text) if log_text else None
    if ev is None:
        add("L01", "日志：双人厅成员体", SKIP, "未给 --log-file（/health 无法区分单人/双人成员体）")
        add("L02", "日志：member 拒绝", SKIP, "未给 --log-file")
    else:
        if ev["two_player_bodies"] >= 1:
            add("L01", "日志：双人厅成员体", OK,
                f"{ev['two_player_bodies']} 次 ≥{TWO_PLAYER_MIN_BODY}B（最大 {ev['two_player_max']}B）")
        elif ev["single_player_bodies"] >= 1:
            add("L01", "日志：双人厅成员体", WARN,
                f"有真实厅体（最大 {ev['max_body']}B）但没到双人档 ≥{TWO_PLAYER_MIN_BODY}B："
                f"疑似只进了单人厅（1 人实测上限 1184B）")
        else:
            add("L01", "日志：双人厅成员体", FAIL,
                f"member 200 共 {ev['member_ok']} 次，最大 {ev['max_body']}B"
                f"（真实厅体应 ≥{REAL_BODY_MIN}B、双人 ≥{TWO_PLAYER_MIN_BODY}B）")
        add("L02", "日志：member 拒绝", OK if ev["member_rejected"] == 0 else FAIL,
            f"{ev['member_rejected']} 次 [discovery:lobby_member:rejected]（必须为 0）")

    if any(c["status"] == FAIL for c in criteria):
        verdict = VERDICT_FAIL
    elif any(c["status"] == SKIP for c in criteria):
        verdict = VERDICT_INCOMPLETE
    else:
        verdict = VERDICT_PASS
    return {"verdict": verdict, "criteria": criteria, "deltas": deltas,
            "before": b, "after": a,
            "log_evidence": ev}


def format_judge(result: dict) -> str:
    g = _glyphs()
    tri = {VERDICT_PASS: f"{g[OK]}{g[OK]} 通过", VERDICT_FAIL: f"{g[FAIL]} 未通过",
           VERDICT_INCOMPLETE: f"{g[WARN]} 证据不全"}
    lines = ["== 阶段2 复测判定（A+B 时序缝隙） =="]
    for c in result["criteria"]:
        lines.append(f"  [{g.get(c['status'], '?')}] {c['id']} {c['desc']}  {c['detail']}")
    lines.append(f"  判定：{tri.get(result['verdict'], result['verdict'])}")
    if result["verdict"] == VERDICT_INCOMPLETE:
        lines.append("  说明：核心 /health 判据已过，但缺日志证据（补 --log-file 可定案）。")
    if result["verdict"] == VERDICT_FAIL:
        lines.append("  说明：见上面 FAIL 项；C01 失败通常表示本轮没复现缝隙，需要按顺序重测。")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def _cmd_check(args: argparse.Namespace) -> int:
    checks = run_checks(relay_ip=args.relay_ip, port=args.port, domain=args.domain,
                        crl_port=args.crl_port, cert_dir=Path(args.cert_dir))
    if args.json:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
    else:
        print(format_checks(checks))
    return 0 if not any(c["status"] == FAIL for c in checks) else 1


def _cmd_snapshot(args: argparse.Namespace) -> int:
    health, leaf = fetch_health(host=args.relay_ip, port=args.port, domain=args.domain,
                                ca_file=Path(args.cert_dir) / "ca.pem", timeout=10.0)
    snap = make_snapshot(health, leaf, endpoint=f"{args.relay_ip}:{args.port}",
                         domain=args.domain)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    d = discover_summary(snap)
    print(f"快照已写 {out}（{_now_iso()}）")
    print("  " + " ".join(f"{k}={d.get(k, 0)}" for k in
                          ("accounts", "identities", "keys_known", "bindings",
                           "keys_hit", "keys_bind_hit", "keys_miss", "keys_waited",
                           "login", "put", "get", "join", "join_rejected")))
    return 0


def _cmd_judge(args: argparse.Namespace) -> int:
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    log_text = Path(args.log_file).read_text(encoding="utf-8", errors="replace") \
        if args.log_file else None
    result = judge_snapshots(before, after, log_text=log_text)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_judge(result))
    return {VERDICT_PASS: 0, VERDICT_FAIL: 1, VERDICT_INCOMPLETE: 2}[result["verdict"]]


def _force_utf8_stdio() -> None:
    """Windows 管道下 Python 默认用 cp936，勾/叉会 UnicodeEncodeError；统一转 utf-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(
        description="阶段2 复测（A+B）：就绪自检 / 基线快照 / 判据判定（全部只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--relay-ip", default=DEFAULT_RELAY_IP,
                       help=f"本机中继监听 IP（默认 {DEFAULT_RELAY_IP}）")
        p.add_argument("--port", type=int, default=443)
        p.add_argument("--domain", default=DOMAIN)
        p.add_argument("--crl-port", type=int, default=DEFAULT_CRL_PORT)
        p.add_argument("--cert-dir", default=str(DEFAULT_CERT_DIR))
        p.add_argument("--json", action="store_true", help="机器可读输出")

    c = sub.add_parser("check", help="8 项前置自检（只读）")
    common(c)
    c.set_defaults(func=_cmd_check)

    s = sub.add_parser("snapshot", help="经中继抓一次 /health 存成 JSON")
    common(s)
    s.add_argument("--out", required=True, help="快照输出路径")
    s.set_defaults(func=_cmd_snapshot)

    j = sub.add_parser("judge", help="比较两份快照（+ 可选日志）给判定")
    common(j)
    j.add_argument("--before", required=True, help="测试前快照 JSON")
    j.add_argument("--after", required=True, help="测试后快照 JSON")
    j.add_argument("--log-file", default="", help="mock 日志（取双人成员体/拒绝证据）")
    j.set_defaults(func=_cmd_judge)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
