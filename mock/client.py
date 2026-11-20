"""极简 HTTPS 客户端：给「不启动游戏也能验 TLS 链路」用。

为什么不用 ``http.client``/``requests``：mock 的证书 SAN 是游戏 API 域名，而实际连接地址是
LAN IP。需要 **连接 IP + SNI/校验用域名** 的组合，手写 socket + ssl 最直接。

（只用于研究线自检，不参与游戏路径。）
"""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass, field


@dataclass
class HttpsResult:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    error: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class HttpsClient:
    def __init__(self, connect_host: str, port: int, *, sni_host: str,
                 ca_file: str | None = None, timeout: float = 10.0,
                 insecure: bool = False):
        self.connect_host = connect_host
        self.port = port
        self.sni_host = sni_host
        self.timeout = timeout
        if insecure:
            self.ctx = ssl._create_unverified_context()  # noqa: S323 —— 仅研究线自检
        else:
            self.ctx = ssl.create_default_context(cafile=ca_file)

    def request(self, method: str, path: str, body: bytes | str | None = None,
                headers: dict | None = None) -> HttpsResult:
        payload = b""
        if body is not None:
            payload = body.encode("utf-8") if isinstance(body, str) else body
        hdrs = {"Host": self.sni_host, "Connection": "close",
                "Accept": "application/json"}
        if payload:
            hdrs["Content-Type"] = "application/json"
            hdrs["Content-Length"] = str(len(payload))
        hdrs.update(headers or {})
        head = f"{method.upper()} {path} HTTP/1.1\r\n" + "".join(
            f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
        try:
            with socket.create_connection((self.connect_host, self.port),
                                          timeout=self.timeout) as sock:
                with self.ctx.wrap_socket(sock, server_hostname=self.sni_host) as tls:
                    tls.sendall(head.encode() + payload)
                    data = b""
                    while True:
                        chunk = tls.recv(65536)
                        if not chunk:
                            break
                        data += chunk
        except Exception as exc:  # noqa: BLE001
            return HttpsResult(0, error=repr(exc))
        raw_head, _, raw_body = data.partition(b"\r\n\r\n")
        lines = raw_head.decode("iso-8859-1", "replace").split("\r\n")
        status = 0
        if lines and " " in lines[0]:
            try:
                status = int(lines[0].split(" ")[1])
            except Exception:  # noqa: BLE001
                status = 0
        hdrs_out = {}
        for line in lines[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                hdrs_out[k.strip().lower()] = v.strip()
        return HttpsResult(status, hdrs_out, raw_body)
