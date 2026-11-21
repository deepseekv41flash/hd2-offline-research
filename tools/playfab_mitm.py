"""PlayFab 明文抓包 MITM（阶段2 E3 / A3 前置）。

为什么需要它：自建发现层只能看到 `/api/lobby*`；玩家真正"入厅"由游戏内的
**PlayFab Multiplayer / Party SDK** 直接和 PlayFab 云交互（`974a9.playfabapi.com`），
我们的后端完全看不见。要判断"卡在 Receiving Data"的原因，必须拿到那几条 HTTPS 的
请求体与返回码。

工作方式（不碰 Clash 配置，只切系统代理）：
  * 在 `127.0.0.1:18080` 起一个 HTTP 代理，支持 `CONNECT`；
  * `--inspect` 列表里的 host（默认 `974a9.playfabapi.com`）做 **TLS 终止**：
    用本工具自造的 CA 现场签发叶证书，把明文请求/响应写进 JSONL；
  * 其他 host 一律原样隧道到 `--upstream`（默认 `127.0.0.1:7897` 即 Clash），
    因此把系统代理切过来不会影响别的程序；
  * 上游（真 PlayFab）可选直连或经 Clash（`--upstream-mode direct|proxy`）。

用法：
    python tools/playfab_mitm.py --install-ca                      # 装 CA 到 CurrentUser\\Root（一次）
    python tools/playfab_mitm.py --port 18080 --inspect 974a9.playfabapi.com
    powershell tools/system_proxy.ps1 -Set 127.0.0.1:18080          # 系统代理指向 MITM
    powershell tools/system_proxy.ps1 -Restore                      # 恢复

抓包结果：`state/mitm/capture-<时间>.jsonl`（每行一条 request/response，body 截断 64KB）。
注意：**只抓 TCP/HTTPS**；PlayFab Party 的 UDP relay 流量抓不到（见执行档说明）。
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

MAX_BODY = 64 * 1024
# 这些网段的「目标」直连（不走 Clash）：hosts 把 api.live 指到本机中继/Tailscale IP，
# 若把它也塞进上游代理，Clash 会用远端 DNS 解析成官方 IP，发现层直接失效。
_LOCAL_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"))
DEFAULT_INSPECT = "974a9.playfabapi.com"


# ---------------------------------------------------------------- 证书
CRL_PORT_DEFAULT = 18082


def _crl_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/ca.crl"


def _ensure_crl(ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate,
                state_dir: Path, crl_port: int) -> bytes:
    """生成（空的）吊销列表：Windows Schannel 默认要查吊销，缺 CRL 会判 "revocation unknown"。"""
    now = datetime.utcnow()
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now)
        .next_update(now.replace(year=now.year + 1))
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    der = crl.public_bytes(serialization.Encoding.DER)
    (state_dir / "ca.crl").write_bytes(der)
    return der


def load_or_create_ca(state_dir: Path, crl_port: int = CRL_PORT_DEFAULT
                      ) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    cert_p = state_dir / "ca.pem"
    key_p = state_dir / "ca.key"
    if cert_p.exists() and key_p.exists():
        key = serialization.load_pem_private_key(key_p.read_bytes(), password=None)
        cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
        if not (state_dir / "ca.crl").exists():
            _ensure_crl(key, cert, state_dir, crl_port)
        return key, cert
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HD2 PlayFab Capture CA")])
    now = datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now.replace(year=now.year + 5))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    (state_dir / "ca.der").write_bytes(cert.public_bytes(serialization.Encoding.DER))
    _ensure_crl(key, cert, state_dir, crl_port)
    print(f"[mitm] 新建 CA: {cert_p}")
    return key, cert


def leaf_for(host: str, ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate,
             cache_dir: Path, crl_port: int = CRL_PORT_DEFAULT) -> tuple[bytes, bytes]:
    """现场签发 host 的叶证书；同一次运行内缓存到磁盘。带 CRL DP（Schannel 要查吊销）。"""
    safe = host.replace("*", "wildcard")
    cert_p = cache_dir / f"{safe}.cert.pem"
    key_p = cache_dir / f"{safe}.key.pem"
    if cert_p.exists() and key_p.exists():
        return cert_p.read_bytes(), key_p.read_bytes()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now.replace(year=now.year + 1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
                       critical=False)
        .add_extension(x509.CRLDistributionPoints([x509.DistributionPoint(
            full_name=[x509.UniformResourceIdentifier(_crl_url(crl_port))],
            relative_name=None, reasons=None, crl_issuer=None)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return cert_p.read_bytes(), key_p.read_bytes()


def start_crl_server(state_dir: Path, port: int) -> threading.Thread:
    """极简 CRL 服务：GET /ca.crl → DER（Schannel 按叶证书的 CRL DP 来取）。"""
    crl_path = state_dir / "ca.crl"

    def serve() -> None:
        import http.server

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.split("?")[0] in ("/ca.crl", "/ca.crl/"):
                    data = crl_path.read_bytes() if crl_path.exists() else b""
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pkix-crl")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):  # noqa: D401
                return

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        srv.serve_forever()

    t = threading.Thread(target=serve, daemon=True, name="crl-server")
    t.start()
    return t


def install_ca(state_dir: Path) -> int:
    der = state_dir / "ca.der"
    if not der.exists():
        raise SystemExit("先跑一次本工具生成 CA（或 --init-only）")
    cmd = ["certutil", "-user", "-addstore", "-f", "Root", str(der)]
    print("[mitm] " + " ".join(cmd))
    return subprocess.call(cmd)


# ---------------------------------------------------------------- IO 工具
def read_http_head(sock) -> tuple[str, list[tuple[str, str]], bytes]:
    """读 HTTP 头；返回 (首行, 头列表, **已多读到的字节**)。

    多读到的字节必须交给 body 解析——头和 body 常在同一个 TCP 包里到达，
    丢弃它们会让 body 长度对不上（2026-11-21 抓包预演踩过）。
    """
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            return "", [], b""
        buf += chunk
        if len(buf) > 64 * 1024:
            break
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = []
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers.append((k.strip(), v.strip()))
    return (lines[0] if lines else ""), headers, rest


def read_body(sock, headers: list[tuple[str, str]], initial: bytes = b"") -> bytes:
    h = {k.lower(): v for k, v in headers}
    buf = initial

    def fill(n: int) -> None:
        nonlocal buf
        while len(buf) < n:
            chunk = sock.recv(65536)
            if not chunk:
                return
            buf += chunk

    if h.get("transfer-encoding", "").lower().startswith("chunked"):
        out = b""
        while True:
            while b"\r\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    return out
                buf += chunk
            line, _, buf = buf.partition(b"\r\n")
            try:
                size = int(line.split(b";")[0].strip(), 16)
            except ValueError:
                return out
            if size == 0:
                return out
            fill(size + 2)
            out += buf[:size]
            buf = buf[size + 2:]
    length = int(h.get("content-length", "0") or 0)
    if not length:
        return buf
    fill(length)
    return buf[:length]


def _readline(sock) -> bytes:
    buf = b""
    while not buf.endswith(b"\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            return buf
        buf += chunk
    return buf


def _readn(sock, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            break
        out += chunk
    return out


# ---------------------------------------------------------------- MITM
class MitmProxy:
    def __init__(self, *, port: int, inspect: set[str], state_dir: Path,
                 upstream: str, upstream_mode: str, crl_port: int = CRL_PORT_DEFAULT):
        self.port = port
        self.inspect = inspect
        self.state_dir = state_dir
        self.upstream = upstream
        self.upstream_mode = upstream_mode
        self.crl_port = crl_port
        self.ca_key, self.ca_cert = load_or_create_ca(state_dir, crl_port)
        start_crl_server(state_dir, crl_port)
        self.cert_dir = state_dir / "certs"
        self.cert_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.capture_path = state_dir / f"capture-{stamp}.jsonl"
        self._lock = threading.Lock()
        self._conn_no = 0

    # ---- 日志
    def _dbg(self, cid: int, msg: str) -> None:
        if os.environ.get("HD2_MITM_DEBUG") == "1":
            self.log({"t": datetime.now().isoformat(timespec="milliseconds"),
                      "kind": "debug", "id": cid, "msg": msg})

    def log(self, rec: dict) -> None:
        with self._lock:
            with open(self.capture_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _trunc(b: bytes) -> dict:
        try:
            return {"text": b[:MAX_BODY].decode("utf-8")}
        except UnicodeDecodeError:
            return {"hex": b[:MAX_BODY].hex()}

    # ---- 连接入口
    def serve(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", self.port))
        srv.listen(64)
        print(f"[mitm] 监听 127.0.0.1:{self.port}  inspect={sorted(self.inspect)}  "
              f"upstream={self.upstream}({self.upstream_mode})  capture={self.capture_path}")
        while True:
            try:
                conn, addr = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    def _handle(self, conn: socket.socket, addr) -> None:
        try:
            line, headers, _rest = read_http_head(conn)
            if not line:
                conn.close()
                return
            method, target, _ = (line.split(" ") + ["", "", ""])[:3]
            if method.upper() == "CONNECT":
                host, _, port_s = target.partition(":")
                port = int(port_s or 443)
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                if host in self.inspect:
                    self._intercept(conn, host, port)
                else:
                    self._tunnel(conn, host, port)
            else:
                # 明文 HTTP 代理请求：直接转发到上游代理
                self._forward_plain(conn, line, headers)
        except Exception as exc:  # noqa: BLE001
            import traceback
            self.log({"t": datetime.now().isoformat(timespec="milliseconds"),
                      "error": f"{type(exc).__name__}: {exc}",
                      "trace": traceback.format_exc().splitlines()[-6:]})
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _tunnel(self, conn: socket.socket, host: str, port: int) -> None:
        up = self._connect_target(host, port)
        self._pipe(conn, up)

    @staticmethod
    def _resolved_ips(host: str, port: int) -> list[str]:
        try:
            return [sockaddr[0] for _af, _st, _pr, _cn, sockaddr in
                    socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)]
        except OSError:
            return []

    def _is_local_target(self, host: str, port: int) -> bool:
        for ip_s in self._resolved_ips(host, port):
            try:
                ip = ipaddress.ip_address(ip_s)
            except ValueError:
                continue
            if any(ip in net for net in _LOCAL_NETS):
                return True
        return False

    def _direct_connect(self, host: str, port: int) -> socket.socket:
        last_exc: Exception | None = None
        for af, socktype, proto, _canon, sa in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
            try:
                s = socket.socket(af, socktype, proto)
                s.settimeout(15)
                s.connect(sa)
                return s
            except OSError as exc:  # noqa: PERF203
                last_exc = exc
                try:
                    s.close()
                except OSError:
                    pass
        raise last_exc or OSError(f"无法连接 {host}:{port}")

    def _connect_via_clash(self, host: str, port: int) -> socket.socket:
        """经 Clash 的 CONNECT 隧道（域名交给远端解析，绕开本机 DNS/网络限制）。"""
        up_host, _, up_port = self.upstream.partition(":")
        s = socket.create_connection((up_host, int(up_port or 7897)), timeout=15)
        req = ("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n"
               % (host, port, host, port)).encode()
        s.sendall(req)
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        if b" 200 " not in resp.split(b"\r\n")[0] + b" ":
            raise OSError("Clash CONNECT failed: %r" % (resp[:80],))
        return s

    def _connect_target(self, host: str, port: int) -> socket.socket:
        """隧道目标：本机/内网/hosts 指过来的 → 直连；其余 → **一律经 Clash**。

        为什么非本机一律走 Clash：本机 DNS 对部分域名不可解析（实测 gaierror），
        而 Clash 远端解析可用；且切换系统代理后不应让其它程序的连通性变差。
        """
        if self._is_local_target(host, port):
            return self._direct_connect(host, port)
        return self._connect_via_clash(host, port)

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket) -> None:
        def pump(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t = threading.Thread(target=pump, args=(a, b), daemon=True)
        t.start()
        pump(b, a)
        t.join(timeout=30)

    def _connect_upstream(self, host: str, port: int) -> socket.socket:
        if self.upstream_mode == "proxy":
            up_host, _, up_port = self.upstream.partition(":")
            s = socket.create_connection((up_host, int(up_port or 7897)), timeout=15)
            req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()
            s.sendall(req)
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
            if b" 200 " not in resp.split(b"\r\n")[0] + b" ":
                raise OSError(f"upstream CONNECT 失败: {resp[:80]!r}")
            return s
        # 明确只用 IPv4：本机 IPv6 出站会挂（PlayFab 有 AAAA 记录，create_connection 会先试 IPv6）
        last_exc: Exception | None = None
        for af, socktype, proto, _canon, sa in socket.getaddrinfo(
                host, port, socket.AF_INET, socket.SOCK_STREAM):
            try:
                s = socket.socket(af, socktype, proto)
                s.settimeout(8)
                s.connect(sa)
                return s
            except OSError as exc:  # noqa: PERF203
                last_exc = exc
                try:
                    s.close()
                except OSError:
                    pass
        raise last_exc or OSError(f"无法连接 {host}:{port}")

    # ---- TLS 终结 + 明文记录
    def _intercept(self, conn: socket.socket, host: str, port: int) -> None:
        cert, key = leaf_for(host, self.ca_key, self.ca_cert, self.cert_dir, self.crl_port)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tmp = self.cert_dir / f"_server-{threading.get_ident()}.pem"
        tmp.write_bytes(cert + key)
        ctx.load_cert_chain(str(tmp))
        try:
            tls = ctx.wrap_socket(conn, server_side=True)
        except ssl.SSLError as exc:
            self.log({"t": datetime.now().isoformat(timespec="milliseconds"),
                      "host": host, "error": f"TLS 握手失败: {exc}"})
            return
        while True:
            line, headers, rest = read_http_head(tls)
            if not line:
                return
            method, path, _ = (line.split(" ") + ["", "", ""])[:3]
            body = read_body(tls, headers, rest)
            self._conn_no += 1
            cid = self._conn_no
            self.log({
                "t": datetime.now().isoformat(timespec="milliseconds"), "kind": "request",
                "id": cid, "host": host, "method": method, "path": path,
                "req_headers": {k: v for k, v in headers},
                "req_body": self._trunc(body),
            })
            try:
                self._dbg(cid, "upstream connecting")
                up = self._connect_upstream(host, port)
                self._dbg(cid, "upstream tcp ok")
                tls_up = ssl.create_default_context().wrap_socket(up, server_hostname=host)
                self._dbg(cid, "upstream tls ok")
            except Exception as exc:  # noqa: BLE001
                self.log({"t": datetime.now().isoformat(timespec="milliseconds"), "kind": "error",
                          "id": cid, "error": f"上游连接失败: {type(exc).__name__}: {exc}"})
                return
            # 转发（强制 close，逐请求连接，语义等价且简单）
            fwd = f"{method} {path} HTTP/1.1\r\n".encode("latin-1")
            for k, v in headers:
                if k.lower() in ("connection", "proxy-connection"):
                    continue
                fwd += f"{k}: {v}\r\n".encode("latin-1")
            fwd += b"Connection: close\r\n\r\n" + body
            try:
                tls_up.sendall(fwd)
                self._dbg(cid, "request forwarded")
                status_line, resp_headers, resp_rest = read_http_head(tls_up)
                self._dbg(cid, f"response head: {status_line!r}")
                resp_body = read_body(tls_up, resp_headers, resp_rest)
            except Exception as exc:  # noqa: BLE001
                self.log({"t": datetime.now().isoformat(timespec="milliseconds"), "kind": "error",
                          "id": cid, "error": f"上游读写失败: {type(exc).__name__}: {exc}"})
                tls_up.close()
                return
            tls_up.close()
            self.log({
                "t": datetime.now().isoformat(timespec="milliseconds"), "kind": "response",
                "id": cid, "host": host, "status": status_line,
                "resp_headers": {k: v for k, v in resp_headers},
                "resp_body": self._trunc(resp_body),
            })
            out = (status_line + "\r\n").encode("latin-1")
            for k, v in resp_headers:
                if k.lower() in ("connection", "transfer-encoding", "content-length"):
                    continue
                out += f"{k}: {v}\r\n".encode("latin-1")
            out += f"Content-Length: {len(resp_body)}\r\nConnection: keep-alive\r\n\r\n".encode()
            tls.sendall(out + resp_body)
            if any(k.lower() == "connection" and v.lower() == "close" for k, v in headers):
                return

    def _forward_plain(self, conn: socket.socket, line: str, headers: list[tuple[str, str]]) -> None:
        up_host, _, up_port = self.upstream.partition(":")
        up = socket.create_connection((up_host, int(up_port or 7897)), timeout=15)
        raw = (line + "\r\n").encode("latin-1")
        for k, v in headers:
            raw += f"{k}: {v}\r\n".encode("latin-1")
        up.sendall(raw + b"\r\n")
        self._pipe(conn, up)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PlayFab 明文抓包 MITM")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--inspect", default=DEFAULT_INSPECT, help="逗号分隔的要解密 host")
    ap.add_argument("--state-dir", default="state/mitm")
    ap.add_argument("--upstream", default="127.0.0.1:7897")
    ap.add_argument("--upstream-mode", choices=("direct", "proxy"), default="direct",
                    help="上游（真 PlayFab）直连或经 Clash")
    ap.add_argument("--crl-port", type=int, default=CRL_PORT_DEFAULT,
                    help="本地 CRL 服务端口（叶证书 CRL DP 指向它；Schannel 要查吊销）")
    ap.add_argument("--install-ca", action="store_true", help="把 CA 装进 CurrentUser\\Root")
    ap.add_argument("--init-only", action="store_true", help="只生成 CA 后退出")
    args = ap.parse_args(argv)

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    if args.init_only or args.install_ca:
        load_or_create_ca(state_dir, args.crl_port)
        if args.install_ca:
            code = install_ca(state_dir)
            print(f"[mitm] certutil exit={code}")
            return code
        return 0
    proxy = MitmProxy(port=args.port, inspect={h.strip() for h in args.inspect.split(",") if h.strip()},
                      state_dir=state_dir, upstream=args.upstream, upstream_mode=args.upstream_mode,
                      crl_port=args.crl_port)
    try:
        proxy.serve()
    except KeyboardInterrupt:
        print("\n[mitm] 停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
