"""T6a：旁路 HTTPS 内容取证用的中间人代理（CONNECT 代理 + 选择性 TLS 终止）。

背景：游戏对官方 API 的链路已被 mock 接管；但 PlayFab / EAC / Sony 等旁路仍走真实服务器，
我们只知道域名和字节数，不知道**传的是什么**。本工具用研究线自己的 CA（已装进
`CurrentUser\\Root`，客户端 WinHTTP/Schannel 会信任）对这些域名做 TLS 终止，
把请求/响应解析成 **schema（字段名/长度/字符类/hash）**——不落原值，与 B9 同一纪律。

两种连接：

* **命中 `--intercept` 的域名**：本地 TLS 终止（叶证书含多 SAN），解析 HTTP/1.1，
  再以真实服务器身份向**上游真服务器**发起 HTTPS（正常校验真证书），双向转发并记 schema。
* **其它域名**：透明链路——CONNECT 转发给 `--chain-proxy`（默认用户本机代理
  `127.0.0.1:7897`），纯字节搬运、不记录。

用法：
    python tools\\mitm_proxy.py --port 18090 \\
        --intercept 974a9.playfabapi.com,modules-cdn.eac-prod.on.epicgames.com \\
        --cert-dir state\\certs --log state\\evidence\\t6\\mitm.jsonl

判据：客户端拿到的是**研究 CA 签发的叶证书**（不是真服务器证书）⇒ MITM 生效。
自带信任链的 SDK（EOS 等）会握手失败 → 日志里 `tls_handshake_failed` 就是结论。
"""

from __future__ import annotations

import argparse
import gzip
import json
import socket
import ssl
import sys
import threading
import time
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MAX_CAPTURE = 1_000_000
BUFSIZE = 65536
READ_TIMEOUT = 30.0


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class HttpError(Exception):
    pass


# ----------------------------------------------------------------- HTTP/1.1 读取
def _read_until(sock, marker: bytes, buf: bytearray) -> bytes:
    while marker not in buf:
        chunk = sock.recv(BUFSIZE)
        if not chunk:
            raise HttpError(f"连接在读到 {marker!r} 前关闭")
        buf.extend(chunk)
    idx = buf.index(marker) + len(marker)
    out = bytes(buf[:idx])
    del buf[:idx]
    return out


def _recv_exact(sock, n: int, buf: bytearray) -> bytes:
    while len(buf) < n:
        chunk = sock.recv(BUFSIZE)
        if not chunk:
            raise HttpError("连接在读完 body 前关闭")
        buf.extend(chunk)
    out = bytes(buf[:n])
    del buf[:n]
    return out


def _parse_head(head: bytes) -> tuple[str, dict[str, str], list[tuple[str, str]]]:
    lines = head.decode("latin-1").split("\r\n")
    headers: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
        pairs.append((k.strip(), v.strip()))
    return lines[0], headers, pairs


def _read_body(sock, headers: dict[str, str], buf: bytearray) -> tuple[bytes, bytes]:
    """返回 (原始 body 帧, 解码后的 body)。支持 content-length 与 chunked。"""
    if headers.get("transfer-encoding", "").lower() == "chunked":
        raw = bytearray()
        while True:
            line = _read_until(sock, b"\r\n", buf)
            raw.extend(line)
            try:
                size = int(line.split(b";")[0].strip(), 16)
            except ValueError as exc:
                raise HttpError(f"chunk 长度非法: {line!r}") from exc
            if size == 0:
                raw.extend(_read_until(sock, b"\r\n", buf))
                break
            raw.extend(_recv_exact(sock, size + 2, buf))
        out = bytearray()
        pos = 0
        while pos < len(raw):
            eol = raw.index(b"\r\n", pos)
            size = int(bytes(raw[pos:eol]).split(b";")[0].strip(), 16)
            pos = eol + 2
            if size == 0:
                break
            out.extend(raw[pos:pos + size])
            pos += size + 2
        return bytes(raw), bytes(out)
    length = int(headers.get("content-length") or 0)
    if length <= 0:
        return b"", b""
    raw = _recv_exact(sock, length, buf)
    return raw, raw


def _status_code(head: bytes) -> int:
    try:
        return int(head.split(b" ")[1])
    except (IndexError, ValueError):
        return 0


def _strip_proxy_headers(head: bytes) -> bytes:
    """去掉 Proxy-Connection 之类只对代理有意义的头，其余原样。"""
    lines = head.split(b"\r\n")
    keep = [lines[0]]
    for line in lines[1:]:
        low = line.lower()
        if low.startswith(b"proxy-connection:") or low.startswith(b"proxy-authorization:"):
            continue
        keep.append(line)
    return b"\r\n".join(keep)


# ----------------------------------------------------------------- 代理本体
class MitmProxy:
    def __init__(self, *, host: str, port: int, intercept: set[str],
                 chain_proxy: tuple[str, int] | None, cert_dir: Path,
                 log_path: Path, capture: bool = True, upstream_cafile: Path | None = None,
                 mock_hosts: set[str] | None = None, mock_ca: Path | None = None):
        self.host = host
        self.port = port
        self.intercept = {h.lower() for h in intercept}
        self.chain_proxy = chain_proxy
        self.cert_dir = Path(cert_dir)
        self.log_path = Path(log_path)
        self.capture = capture
        self.upstream_cafile = Path(upstream_cafile) if upstream_cafile else None
        self.mock_hosts = {h.lower() for h in (mock_hosts or set())}
        self.mock_ca = Path(mock_ca) if mock_ca else None
        self._lock = threading.Lock()
        self.stats = {"connect": 0, "intercepted": 0, "chained": 0, "requests": 0,
                      "bytes_in": 0, "bytes_out": 0, "tls_fail": 0}
        self._srv: socket.socket | None = None
        self._stop = threading.Event()
        self._ctx = self._make_ctx()

    # ---- 我们作为 TLS 服务器 ----
    def _make_ctx(self) -> ssl.SSLContext:
        chain = self.cert_dir / "server.chain.pem"
        key = self.cert_dir / "server.key"
        if not chain.exists() or not key.exists():
            raise SystemExit(f"缺少证书文件：{chain} / {key}（先跑 mockctl cert-gen --hosts ...）")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(chain), str(key))
        ctx.set_alpn_protocols(["http/1.1"])
        return ctx

    # ---- 日志：只落 schema ----
    def _log(self, rec: dict) -> None:
        from mock.bodycapture import shape_of
        line = {"t": _now(), **rec}
        req_body = line.pop("_req_body", None)
        resp_body = line.pop("_resp_body", None)
        if self.capture and req_body is not None:
            line["req"] = (shape_of(req_body) if len(req_body) <= MAX_CAPTURE
                           else {"bytes": len(req_body), "format": "too-large"})
        if self.capture and resp_body is not None:
            line["resp"] = (shape_of(resp_body) if len(resp_body) <= MAX_CAPTURE
                            else {"bytes": len(resp_body), "format": "too-large"})
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(f"[mitm] {line['t']} {line.get('host')} {line.get('method','')} "
              f"{str(line.get('path',''))[:70]} -> {line.get('status','')} "
              f"{line.get('resp_len','')}B", flush=True)

    # ---- 生命周期 ----
    @property
    def bound_port(self) -> int:
        assert self._srv is not None
        return self._srv.getsockname()[1]

    def start(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(64)
        self._srv = srv
        print(f"[mitm] 监听 {self.host}:{self.port}  拦截={sorted(self.intercept)}  "
              f"链路={self.chain_proxy}", flush=True)

    def serve_forever(self) -> None:
        if self._srv is None:
            self.start()
        assert self._srv is not None
        while not self._stop.is_set():
            try:
                client, addr = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client, addr), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
            self._srv = None

    # ---- 分派 ----
    def _handle(self, client: socket.socket, addr) -> None:
        client.settimeout(READ_TIMEOUT)
        try:
            buf = bytearray()
            head = _read_until(client, b"\r\n\r\n", buf)
            first, _headers, _pairs = _parse_head(head)
            parts = first.split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
                return
            host, _, port_s = parts[1].rpartition(":")
            port = int(port_s or 443)
            with self._lock:
                self.stats["connect"] += 1
            if host.lower() in self.intercept:
                with self._lock:
                    self.stats["intercepted"] += 1
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                try:
                    tls = self._ctx.wrap_socket(client, server_side=True)
                except (ssl.SSLError, OSError) as exc:
                    with self._lock:
                        self.stats["tls_fail"] += 1
                    self._log({"host": host, "event": "tls_handshake_failed",
                               "error": type(exc).__name__ + ": " + str(exc)[:120]})
                    return
                self._serve_tls(tls, host, port)
            else:
                with self._lock:
                    self.stats["chained"] += 1
                self._chain(client, host, port)
        except (HttpError, OSError, ssl.SSLError):
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass

    # ---- 非拦截：转发给既有代理 ----
    def _chain(self, client: socket.socket, host: str, port: int) -> None:
        if self.chain_proxy is None:
            self._log({"host": host, "event": "no_chain_proxy"})
            return
        try:
            up = socket.create_connection(self.chain_proxy, timeout=10)
        except OSError:
            return
        up.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        upbuf = bytearray()
        resp = _read_until(up, b"\r\n\r\n", upbuf)
        if _status_code(resp) != 200:
            up.close()
            return
        client.sendall(resp)
        if upbuf:
            client.sendall(bytes(upbuf))

        def pump(src, dst, key):
            try:
                while True:
                    data = src.recv(BUFSIZE)
                    if not data:
                        break
                    dst.sendall(data)
                    with self._lock:
                        self.stats[key] += len(data)
            except OSError:
                pass
            finally:
                try:
                    dst.close()
                except OSError:
                    pass

        t = threading.Thread(target=pump, args=(client, up, "bytes_in"), daemon=True)
        t.start()
        pump(up, client, "bytes_out")
        t.join(timeout=5)
        up.close()

    # ---- 拦截：TLS 终止 + 转发真服务器 ----
    def _serve_tls(self, tls: ssl.SSLSocket, host: str, port: int) -> None:
        buf = bytearray()
        while True:
            try:
                head = _read_until(tls, b"\r\n\r\n", buf)
            except HttpError:
                return
            first, headers, _pairs = _parse_head(head)
            parts = first.split()
            if len(parts) < 3:
                return
            method, path = parts[0], parts[1]
            try:
                _req_raw, req_body = _read_body(tls, headers, buf)
            except HttpError:
                return
            res = self._forward(host, port, _strip_proxy_headers(head), req_body)
            if res is None:
                return
            status, resp_raw, resp_body, ctype, req_len = res
            try:
                tls.sendall(resp_raw)
            except OSError:
                return
            with self._lock:
                self.stats["requests"] += 1
            self._log({
                "host": host, "method": method, "path": path,
                "ua": headers.get("user-agent"),
                "req_len": req_len, "_req_body": req_body,
                "status": status, "resp_len": len(resp_raw),
                "resp_ctype": ctype, "_resp_body": resp_body,
            })
            if status == 0 or headers.get("connection", "").lower() == "close":
                return

    def _forward(self, host: str, port: int, head: bytes, body: bytes):
        """返回 (status, 原始响应字节, 可解析 body, ctype, 请求体长度)；失败返回 None。"""
        try:
            raw_up = socket.create_connection((host, port), timeout=15)
        except OSError as exc:
            self._log({"host": host, "event": "upstream_connect_failed",
                       "error": type(exc).__name__})
            return None
        try:
            if host.lower() in self.mock_hosts and self.mock_ca:
                cafile = str(self.mock_ca)
            else:
                cafile = str(self.upstream_cafile) if self.upstream_cafile else None
            upctx = ssl.create_default_context(cafile=cafile)
            up = upctx.wrap_socket(raw_up, server_hostname=host)
        except (ssl.SSLError, OSError) as exc:
            raw_up.close()
            self._log({"host": host, "event": "upstream_tls_failed",
                       "error": type(exc).__name__})
            return None
        try:
            up.sendall(head + body)
            rbuf = bytearray()
            rhead = _read_until(up, b"\r\n\r\n", rbuf)
            status = _status_code(rhead)
            _first, rheaders, _pairs = _parse_head(rhead)
            ctype = rheaders.get("content-type")
            try:
                raw_body, decoded = _read_body(up, rheaders, rbuf)
            except HttpError:
                raw_body, decoded = b"", b""
            enc = (rheaders.get("content-encoding") or "").lower()
            if decoded and enc:
                try:
                    if "gzip" in enc:
                        decoded = gzip.decompress(decoded)
                    elif "deflate" in enc:
                        decoded = zlib.decompress(decoded)
                except (OSError, zlib.error):
                    pass
            want_body = bool(ctype) and ("json" in ctype or "text" in ctype)
            return status, rhead + raw_body, (decoded if want_body else None), ctype, len(body)
        except (ssl.SSLError, OSError, HttpError):
            return None
        finally:
            try:
                up.close()
            except OSError:
                pass


def _parse_hp(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError(f"要 host:port，收到 {text!r}")
    return host, int(port)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="T6a 旁路 HTTPS 取证 MITM 代理")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=18090)
    p.add_argument("--intercept", default="", help="逗号分隔的域名（命中则 TLS 终止）")
    p.add_argument("--chain-proxy", type=_parse_hp, default=("127.0.0.1", 7897),
                   help="未拦截域名转发到的上游代理（默认本机 7897）")
    p.add_argument("--no-chain", action="store_true")
    p.add_argument("--cert-dir", type=Path, default=Path("state/certs"))
    p.add_argument("--upstream-ca", type=Path, default=None,
                   help="上游真服务器的 CA 文件（默认用系统/公共 CA；测试用）")
    p.add_argument("--mock-hosts", default="",
                   help="逗号分隔：这些域名转发到本机 mock，上游用 --mock-ca 校验")
    p.add_argument("--mock-ca", type=Path, default=Path("state/certs/ca.pem"))
    p.add_argument("--log", type=Path, default=Path("state/evidence/t6/mitm.jsonl"))
    args = p.parse_args(argv)
    proxy = MitmProxy(
        host=args.host, port=args.port,
        intercept={h.strip() for h in args.intercept.split(",") if h.strip()},
        chain_proxy=None if args.no_chain else args.chain_proxy,
        cert_dir=args.cert_dir, log_path=args.log, upstream_cafile=args.upstream_ca,
        mock_hosts={h.strip() for h in args.mock_hosts.split(",") if h.strip()},
        mock_ca=args.mock_ca)
    try:
        proxy.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        proxy.stop()
        print(f"[mitm] 统计 {proxy.stats}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
