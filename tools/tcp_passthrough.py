"""透明 TCP 转发：把「指向本机」的 443 端口原样转给真实后端。

用途（B9 §4.2 的判别实验）：hosts 把 `api.live.prod.thehelldiversgame.com` 钉到本机，
想让游戏**真的**登录一次官方服务器时，不必改 hosts（改 hosts 需要管理员），
只要停掉 mock、在同一 `IP:443` 上起这个转发器，把 TCP 流直通真实 API IP 即可。

TLS 端点仍然是客户端 ↔ 真服务器（转发器不终止 TLS，只搬字节），所以：

* 客户端看到的是**真服务器证书**（公共 CA，正常校验通过）；
* 转发器看不到任何明文（只统计字节数，不落内容）——符合本研究的脱敏纪律。

日志（`--log`）只记：连接建立/关闭时间、客户端→后端字节数、后端→客户端字节数。
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

BUFSIZE = 65536


class Passthrough:
    def __init__(self, listen: tuple[str, int], upstream: tuple[str, int],
                 log_path: Path | None = None, timeout: float = 600.0):
        self.listen = listen
        self.upstream = upstream
        self.log_path = log_path
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.total_conns = 0
        self.total_up = 0
        self.total_down = 0

    # ---- 日志 ----
    def _log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # ---- 管道 ----
    def _pump(self, src: socket.socket, dst: socket.socket, counter: list[int]) -> None:
        try:
            while not self._stop.is_set():
                data = src.recv(BUFSIZE)
                if not data:
                    break
                dst.sendall(data)
                counter[0] += len(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _handle(self, client: socket.socket, addr) -> None:
        try:
            upstream = socket.create_connection(self.upstream, timeout=10)
        except OSError as exc:
            self._log(f"!! upstream {self.upstream[0]}:{self.upstream[1]} 连接失败: {exc}")
            client.close()
            return
        up = [0]   # 客户端 -> 后端
        down = [0]  # 后端 -> 客户端
        with self._lock:
            self.total_conns += 1
        self._log(f"--> {addr[0]} 连入，转发到 {self.upstream[0]}:{self.upstream[1]}")
        t_up = threading.Thread(target=self._pump, args=(client, upstream, up), daemon=True)
        t_down = threading.Thread(target=self._pump, args=(upstream, client, down), daemon=True)
        t_up.start()
        t_down.start()
        t_up.join(self.timeout)
        t_down.join(self.timeout)
        for s in (client, upstream):
            try:
                s.close()
            except OSError:
                pass
        with self._lock:
            self.total_up += up[0]
            self.total_down += down[0]
        self._log(f"<-- {addr[0]} 关闭：上行 {up[0]} B / 下行 {down[0]} B")

    # ---- 生命周期 ----
    @property
    def bound_port(self) -> int:
        """实际监听端口（`--listen` 传 0 时用于测试）。"""
        assert self._sock is not None
        return self._sock.getsockname()[1]

    def start(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(self.listen)
        srv.listen(16)
        self._sock = srv
        self._log(f"[passthrough] 监听 {self.listen[0]}:{self.listen[1]} "
                  f"-> {self.upstream[0]}:{self.upstream[1]}")

    def serve_forever(self) -> None:
        if self._sock is None:
            self.start()
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, addr = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client, addr),
                             daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def _parse_hp(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError(f"要 host:port，收到 {text!r}")
    return host, int(port)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="透明 TCP 转发（不终止 TLS）")
    p.add_argument("--listen", type=_parse_hp, required=True, help="如 100.67.169.17:443")
    p.add_argument("--upstream", type=_parse_hp, required=True, help="如 172.200.240.121:443")
    p.add_argument("--log", type=Path, default=None)
    args = p.parse_args(argv)
    pt = Passthrough(args.listen, args.upstream, args.log)
    try:
        pt.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        pt.stop()
        pt._log(f"[passthrough] 合计 {pt.total_conns} 连接 / "
                f"上行 {pt.total_up} B / 下行 {pt.total_down} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
