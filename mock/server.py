"""把 MockHandler 挂到 HTTPS（默认 LAN IP:443）+ 本机 CRL 服务。

为什么绑 LAN IP 而不是回环：新版客户端拒绝连回环地址（交接档 §8.4），
所以 hosts 指向本机 LAN IP，mock 必须在该 IP 的 443 上监听。

端口纪律（交接档 §8.1）:
  * 443 同一时间只能有一个主人 —— 产品 MITM 引擎或本 mock，二选一；
  * 本 mock 的 CRL 用 **127.0.0.1:18081**（不占产品的 8080），CDP 也写这个地址；
  * 不碰产品控制面 17897。

任何绑定失败都打印占用者线索，方便按 §8.1 的流程排查。
"""

from __future__ import annotations

import gzip
import json
import socket
import ssl
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bodycapture import BodyShapeCapture
from .handler import MockHandler, Response

MAX_BODY = 8 << 20


def _now_stamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class _Logger:
    """线程安全地写 stdout + 可选文件。"""

    def __init__(self, path: Path | None = None):
        self._lock = threading.Lock()
        self._fh = None
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8")

    def log(self, line: str) -> None:
        with self._lock:
            print(line, flush=True)
            if self._fh:
                self._fh.write(line + "\n")
                self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None


class _ExclusiveHTTPServer(ThreadingHTTPServer):
    """独占绑定：与产品引擎一致，**不允许** SO_REUSEADDR 抢同一端口（交接档 §8.1）。"""

    allow_reuse_address = False
    daemon_threads = True


class _CrlHandler(BaseHTTPRequestHandler):
    server_version = "hd2-research-crl/0.1"
    crl_path: Path | None = None

    def do_GET(self):  # noqa: N802
        path, _, _q = self.path.partition("?")
        crl = self.server.crl_path  # type: ignore[attr-defined]
        if crl is None or path not in ("/ca.crl", "/ca.crl.der", "/"):
            self.send_error(404)
            return
        data = crl.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pkix-crl")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # noqa: A003
        return


def start_crl_server(cert_dir: Path, port: int, logger: _Logger) -> ThreadingHTTPServer | None:
    crl = Path(cert_dir) / "ca.crl"
    if not crl.exists():
        logger.log(f"[crl] 缺少 {crl} -> 跳过 CRL 服务（Schannel 可能拒绝握手）")
        return None
    srv = ThreadingHTTPServer(("127.0.0.1", port), _CrlHandler)
    srv.daemon_threads = True
    srv.crl_path = crl  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="crl-server")
    t.start()
    logger.log(f"[crl] http://127.0.0.1:{port}/ca.crl ({crl.stat().st_size} B)")
    return srv


class _ApiHandler(BaseHTTPRequestHandler):
    """HTTP/1.1，处理 keep-alive / Content-Length / chunked 请求体。"""

    protocol_version = "HTTP/1.1"
    server_version = "hd2-research-mock/0.1"
    timeout = 60

    # ---- 请求体 -----------------------------------------------------------
    def _read_body(self) -> bytes:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            chunks: list[bytes] = []
            total = 0
            while True:
                line = self.rfile.readline(65536).strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline(65536)  # trailer
                    break
                data = self.rfile.read(size)
                chunks.append(data)
                total += size
                if total > MAX_BODY:
                    break
                self.rfile.readline(65536)
            raw = b"".join(chunks)
        else:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            length = min(length, MAX_BODY)
            raw = self.rfile.read(length) if length > 0 else b""
        if (self.headers.get("Content-Encoding") or "").lower() == "gzip" and raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except Exception:  # noqa: BLE001
                pass
        return raw

    # ---- 响应 -------------------------------------------------------------
    def _send(self, resp: Response) -> None:
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type or "application/json")
        self.send_header("Content-Length", str(len(resp.body or b"")))
        self.send_header("Cache-Control", "no-store")
        if resp.source:
            self.send_header("X-Mock-Source", resp.source)
        self.end_headers()
        if self.command != "HEAD" and resp.body:
            self.wfile.write(resp.body)

    # ---- 分发 -------------------------------------------------------------
    def _dispatch(self) -> None:
        server = self.server
        mock: MockHandler = server.mock  # type: ignore[attr-defined]
        logger: _Logger = server.logger  # type: ignore[attr-defined]
        body = self._read_body()
        headers = {k.lower(): v for k, v in self.headers.items()}
        # B9：只记登录体 schema（字段名/长度/字符类/hash），绝不落原值
        cap = getattr(server, "body_capture", None)
        if cap is not None and body:
            try:
                rec = cap.capture(self.command, self.path, body)
                if rec:
                    logger.log(
                        f"{_now_stamp()}   SHAPE {self.command:6s} {self.path} "
                        f"{rec['shape']['bytes']}B sha={rec['shape']['sha256'][:8]} "
                        f"format={rec['shape'].get('format')}")
            except Exception as exc:  # noqa: BLE001
                logger.log(f"{_now_stamp()} !! body-shape 采集失败: {exc!r}")
        try:
            resp = mock.handle(self.command, self.path, body, headers,
                               client_addr=(self.client_address[0] if self.client_address else ""))
        except Exception as exc:  # noqa: BLE001
            logger.log(f"{_now_stamp()} !! {self.command} {self.path} handler 异常: {exc!r}")
            resp = Response(500, b'{"error":"handler crashed"}', "application/json", "error")
        try:
            self._send(resp)
        except (BrokenPipeError, ConnectionResetError):
            logger.log(f"{_now_stamp()} !! {self.command} {self.path} 客户端断开")
            return
        logger.log(
            f"{_now_stamp()} {self.command:6s} {self.path} "
            f"{len(body)}B -> {resp.status} {len(resp.body or b'')}B [{resp.source}]"
        )
        if getattr(server, "log_headers", False):
            shapes = ",".join(
                f"{k}={len(v)}" for k, v in headers.items()
                if k not in ("authorization", "cookie", "x-session-id")
            )
            logger.log(f"{_now_stamp()}   HDR {self.command} {self.path} {shapes}")

    def do_GET(self):  # noqa: N802
        self._dispatch()

    def do_POST(self):  # noqa: N802
        self._dispatch()

    def do_PUT(self):  # noqa: N802
        self._dispatch()

    def do_DELETE(self):  # noqa: N802
        self._dispatch()

    def do_PATCH(self):  # noqa: N802
        self._dispatch()

    def do_HEAD(self):  # noqa: N802
        self._dispatch()

    def do_OPTIONS(self):  # noqa: N802
        self._dispatch()

    def log_message(self, *args):  # noqa: A003
        return

    def address_string(self) -> str:
        return self.client_address[0] if self.client_address else "-"


def make_ssl_context(cert_dir: Path) -> ssl.SSLContext:
    cert_dir = Path(cert_dir)
    chain = cert_dir / "server.chain.pem"
    key = cert_dir / "server.key"
    if not chain.exists() or not key.exists():
        raise FileNotFoundError(f"证书不完整：缺 {chain.name} 或 {key.name}（先跑 mockctl cert-gen）")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=str(chain), keyfile=str(key))
    return ctx


class MockServer:
    """研究线 mock 服务端（443 TLS + 18081 CRL）。"""

    def __init__(self, handler: MockHandler, *, host: str, port: int,
                 cert_dir: Path | str, log_dir: Path | str | None = None,
                 log_headers: bool = False, crl_port: int = 18081,
                 serve_crl: bool = True):
        self.handler = handler
        self.host = host
        self.port = port
        self.cert_dir = Path(cert_dir)
        self.log_dir = Path(log_dir) if log_dir else None
        log_path = (self.log_dir / "mock.log") if self.log_dir else None
        self.logger = _Logger(log_path)
        self.log_headers = log_headers
        self.crl_port = crl_port
        self.serve_crl = serve_crl
        self.httpd: ThreadingHTTPServer | None = None
        self.crl_srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.body_capture = BodyShapeCapture.from_profile(self.handler.profile)

    # ---- 生命周期 ---------------------------------------------------------
    def start(self) -> "MockServer":
        ctx = make_ssl_context(self.cert_dir)
        try:
            httpd = _ExclusiveHTTPServer((self.host, self.port), _ApiHandler)
        except OSError as exc:
            raise RuntimeError(
                f"绑定 {self.host}:{self.port} 失败（{exc}）。"
                f"按交接档 §8.1：443 只能有一个主人，先确认产品引擎/mock 是否在跑"
                f"（Get-NetTCPConnection -State Listen -LocalPort {self.port}）"
            ) from exc
        httpd.daemon_threads = True
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        httpd.mock = self.handler          # type: ignore[attr-defined]
        httpd.logger = self.logger          # type: ignore[attr-defined]
        httpd.log_headers = self.log_headers  # type: ignore[attr-defined]
        httpd.body_capture = self.body_capture  # type: ignore[attr-defined]
        self.httpd = httpd
        try:
            self.port = int(httpd.server_address[1])  # port=0 时取实际端口
        except Exception:  # noqa: BLE001
            pass
        if self.serve_crl:
            self.crl_srv = start_crl_server(self.cert_dir, self.crl_port, self.logger)
        # 统一在 start() 里起 serve_forever 线程 —— 这样 stop() 的 shutdown() 不会阻塞
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="mock-http")
        self._thread.start()
        self.logger.log(
            f"[serve] https://{self.host}:{self.port} 证书={self.cert_dir} "
            f"shaping={'on' if self.handler.profile['timeshift'].get('enabled') else 'off'}"
        )
        return self

    def start_background(self) -> "MockServer":
        return self.start() if self.httpd is None else self

    def serve_forever(self) -> None:
        if self.httpd is None:      # 允许外部先 start() 再进来等（幂等）
            self.start()
        assert self._thread is not None
        try:
            self._thread.join()
        except KeyboardInterrupt:
            self.logger.log("[serve] Ctrl+C -> 停止")
        finally:
            self.stop()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()      # serve_forever 已在线程里跑，这里不会阻塞
            self.httpd.server_close()
            self.httpd = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        if self.crl_srv is not None:
            self.crl_srv.shutdown()
            self.crl_srv.server_close()
            self.crl_srv = None
        self.logger.log("[serve] stopped")
        self.logger.close()

    # ---- 自检 -------------------------------------------------------------
    def local_probe(self) -> dict:
        """用 Python ssl 客户端验一次 TLS 链路（不经过 Windows 信任存储）。"""
        ctx = ssl.create_default_context(cafile=str(self.cert_dir / "ca.pem"))
        host_for_sni = self.handler.profile.get("host") or "api.live.prod.thehelldiversgame.com"
        results = {}
        for path in ("/health", "/api/WarSeason/current/WarId", "/api/Account/Login"):
            try:
                with socket.create_connection((self.host if self.host != "0.0.0.0" else "127.0.0.1",
                                               self.port), timeout=5) as sock:
                    with ctx.wrap_socket(sock, server_hostname=host_for_sni) as tls:
                        req = f"GET {path} HTTP/1.1\r\nHost: {host_for_sni}\r\nConnection: close\r\n\r\n"
                        tls.sendall(req.encode())
                        data = b""
                        while True:
                            chunk = tls.recv(65536)
                            if not chunk:
                                break
                            data += chunk
                head, _, payload = data.partition(b"\r\n\r\n")
                status = head.split(b" ")[1].decode() if head else "?"
                results[path] = {"status": int(status) if status.isdigit() else status,
                                 "bytes": len(payload), "ssl": "ok"}
            except Exception as exc:  # noqa: BLE001
                results[path] = {"error": repr(exc)}
        return results


def free_port_probe(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def check_ports(host: str, port: int) -> dict:
    """按 §8.1 查 443/8080/17897 归属 + 本线 CRL 端口，给绑定失败时的人话提示。"""
    out: dict = {}
    for p in {port, 8080, 17897, 18081}:
        busy = not free_port_probe("127.0.0.1", p)
        out[p] = "busy" if busy else "free"
    out["target"] = "busy" if not free_port_probe(host, port) else "free"
    return out


def describe_ports(info: dict) -> str:
    return json.dumps(info, ensure_ascii=False)


__all__ = ["MockServer", "make_ssl_context", "start_crl_server", "check_ports",
           "describe_ports", "MAX_BODY", "time"]
