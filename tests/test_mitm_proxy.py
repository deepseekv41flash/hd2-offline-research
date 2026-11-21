"""T6a MITM 代理：能解密命中域名、只落 schema；非命中域名走链路不记录。"""

from __future__ import annotations

import gzip
import http.server
import json
import socket
import ssl
import threading
from pathlib import Path

from mock import certs
from tools.mitm_proxy import MitmProxy

SECRET = "SECRET_TICKET_VALUE_0123456789"


def _origin_server(cert_dir: Path):
    """一个只回 JSON 的 HTTPS 源站（用研究 CA 的叶证书，模拟"真服务器"）。"""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"ok": True, "token": SECRET, "n": 7}).encode()
            headers = {"Content-Type": "application/json"}
            if self.path == "/gzip":
                body = gzip.compress(body)
                headers["Content-Encoding"] = "gzip"
            self.send_response(200)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # 静音
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_dir / "server.chain.pem"), str(cert_dir / "server.key"))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _client_get(proxy_port: int, target_port: int, ca_pem: Path) -> bytes:
    """模拟游戏客户端：先 CONNECT，再在自己的 TLS 里发 HTTP 请求。"""
    raw = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    raw.sendall(f"CONNECT localhost:{target_port} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += raw.recv(4096)
    assert b"200" in resp.split(b"\r\n")[0], resp
    ctx = ssl.create_default_context(cafile=str(ca_pem))
    tls = ctx.wrap_socket(raw, server_hostname="localhost")
    tls.sendall(b"GET /api/test HTTP/1.1\r\nHost: localhost\r\n"
                b"User-Agent: hd2-test/1.0\r\nConnection: close\r\n\r\n")
    out = b""
    while True:
        chunk = tls.recv(65536)
        if not chunk:
            break
        out += chunk
    tls.close()
    return out


def test_mitm_intercepts_and_logs_only_schema(tmp_path: Path):
    cert_dir = tmp_path / "certs"
    certs.generate(cert_dir, hosts=["localhost"], crl_port=18099)
    origin = _origin_server(cert_dir)
    log = tmp_path / "mitm.jsonl"
    proxy = MitmProxy(host="127.0.0.1", port=0, intercept={"localhost"},
                      chain_proxy=None, cert_dir=cert_dir, log_path=log,
                      upstream_cafile=cert_dir / "ca.pem")
    proxy.start()
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    try:
        port = origin.server_address[1]
        resp = _client_get(proxy.bound_port, port, cert_dir / "ca.pem")
        assert b"200" in resp.split(b"\r\n")[0]
        assert SECRET.encode() in resp            # 明文确实穿过代理回到客户端

        recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(recs) == 1
        rec = recs[0]
        assert rec["host"] == "localhost"
        assert rec["method"] == "GET" and rec["path"] == "/api/test"
        assert rec["ua"] == "hd2-test/1.0"
        assert rec["status"] == 200
        # 响应 JSON 被解析成结构，字符串只有 len/class/sha8 —— 绝不落原值
        keys = rec["resp"]["json"]["keys"]
        assert keys == ["ok", "token", "n"]
        assert SECRET not in log.read_text(encoding="utf-8")
        assert proxy.stats["requests"] == 1
    finally:
        proxy.stop()
        origin.shutdown()


def test_mitm_non_intercepted_host_is_chained_without_logging(tmp_path: Path):
    """非命中域名必须走链路且不产生任何记录（避免把用户其它流量写进证据）。"""
    cert_dir = tmp_path / "certs"
    certs.generate(cert_dir, hosts=["localhost"], crl_port=18099)
    log = tmp_path / "mitm.jsonl"
    proxy = MitmProxy(host="127.0.0.1", port=0, intercept=set(),
                      chain_proxy=None, cert_dir=cert_dir, log_path=log)
    proxy.start()
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    try:
        raw = socket.create_connection(("127.0.0.1", proxy.bound_port), timeout=5)
        raw.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
        raw.settimeout(3)
        try:
            raw.recv(1024)
        except OSError:
            pass
        raw.close()
        assert proxy.stats["chained"] == 1          # 走了链路分支
        # 非命中域名不得产生内容记录（只有"没有链路代理"这类事件行）
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    assert "req" not in rec and "resp" not in rec and "ua" not in rec
    finally:
        proxy.stop()


def test_mitm_decompresses_gzip_response_for_schema(tmp_path: Path):
    """EOS 配置接口是 gzip 的：必须解压后再抽 schema，否则只能记成 non-json。"""
    cert_dir = tmp_path / "certs"
    certs.generate(cert_dir, hosts=["localhost"], crl_port=18099)
    origin = _origin_server(cert_dir)
    log = tmp_path / "mitm.jsonl"
    proxy = MitmProxy(host="127.0.0.1", port=0, intercept={"localhost"},
                      chain_proxy=None, cert_dir=cert_dir, log_path=log,
                      upstream_cafile=cert_dir / "ca.pem")
    proxy.start()
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    try:
        raw = socket.create_connection(("127.0.0.1", proxy.bound_port), timeout=5)
        raw.sendall(f"CONNECT localhost:{origin.server_address[1]} HTTP/1.1\r\n"
                    f"Host: localhost\r\n\r\n".encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += raw.recv(4096)
        ctx = ssl.create_default_context(cafile=str(cert_dir / "ca.pem"))
        tls = ctx.wrap_socket(raw, server_hostname="localhost")
        tls.sendall(b"GET /gzip HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        out = b""
        while True:
            chunk = tls.recv(65536)
            if not chunk:
                break
            out += chunk
        tls.close()
        assert b"200" in out.split(b"\r\n")[0]
        rec = json.loads(log.read_text(encoding="utf-8").strip())
        assert rec["resp"]["format"] == "json"
        assert rec["resp"]["json"]["keys"] == ["ok", "token", "n"]
        assert SECRET not in log.read_text(encoding="utf-8")
    finally:
        proxy.stop()
        origin.shutdown()
