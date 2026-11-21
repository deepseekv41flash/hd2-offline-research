#!/usr/bin/env python3
"""E2 本机中继：把「游戏 → 本机 LAN IP:443」的 TLS 原样转发到发现层。

为什么需要：某些网络（校园/企业网关）会拦截「游戏域名 SNI + 非官方 IP」的 TLS 连接
（实测：api.live.prod.thehelldiversgame.com 直连服务器 A 被 RST，经 SSH 隧道则正常）。
本工具让游戏连本机 LAN IP，TLS 字节流经 SSH 隧道（本机 → B → A）到发现层，
网关只能看到加密的 SSH 连接。

链路：
    游戏 --(hosts: api.live -> 本机 LAN IP)--> e2_tunnel:443
         --(paramiko direct-tcpip via B)--> A:443

用法（B 的连接名取自 DSH ssh-mcp 的 servers.json）：
    python tools/e2_tunnel.py --servers-json <path>/servers.json --via server2 \
        --listen-host 100.67.88.219 --listen-port 443 --target 106.52.201.42:443
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

try:
    import paramiko
except ImportError:  # pragma: no cover
    sys.exit("缺 paramiko：pip install paramiko")


class Tunnel:
    def __init__(self, cred: dict, target: tuple[str, int], log=print):
        self.cred = cred
        self.target = target
        self.log = log
        self._lock = threading.Lock()
        self._client: paramiko.SSHClient | None = None

    def _connect(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(self.cred["host"], port=int(self.cred.get("port", 22)),
                       username=self.cred["username"], password=self.cred["password"],
                       timeout=20, look_for_keys=False, allow_agent=False)
        transport = client.get_transport()
        assert transport is not None
        transport.set_keepalive(15)
        return client

    def channel(self):
        """打开一条到目标的转发通道；SSH 断了就重连一次。"""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._client is None or not self._client.get_transport() \
                            or not self._client.get_transport().is_active():
                        self._client = self._connect()
                        self.log("[tunnel] SSH 已连上 %s" % self.cred["host"])
                    return self._client.get_transport().open_channel(
                        "direct-tcpip", self.target, ("127.0.0.1", 0))
                except Exception as exc:  # noqa: BLE001
                    self.log(f"[tunnel] 建通道失败（第 {attempt} 次）: {exc!r}")
                    try:
                        if self._client:
                            self._client.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._client = None
                    time.sleep(0.5)
        raise RuntimeError("无法建立 SSH 转发通道")


def pump(src, dst, on_finish=None):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            if hasattr(dst, "shutdown_write"):
                dst.shutdown_write()
            else:
                dst.shutdown(socket.SHUT_WR)
        except Exception:  # noqa: BLE001
            pass
        if on_finish:
            on_finish()


def handle_client(client: socket.socket, tunnel: Tunnel, log):
    peer = f"{client.getpeername()[0]}:{client.getpeername()[1]}" if client.getpeername() else "?"
    try:
        chan = tunnel.channel()
    except Exception as exc:  # noqa: BLE001
        log(f"[tunnel] {peer} 无法转发: {exc!r}")
        client.close()
        return
    log(f"[tunnel] {peer} -> {tunnel.target[0]}:{tunnel.target[1]} 已接通")
    t1 = threading.Thread(target=pump, args=(client, chan), daemon=True)
    t2 = threading.Thread(target=pump, args=(chan, client), daemon=True)
    t1.start(); t2.start()
    t2.join()
    try:
        chan.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
    log(f"[tunnel] {peer} 结束")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E2 本机中继（SSH 隧道 -> 发现层）")
    ap.add_argument("--servers-json", required=True)
    ap.add_argument("--via", default="server2")
    ap.add_argument("--listen-host", default="0.0.0.0")
    ap.add_argument("--listen-port", type=int, default=443)
    ap.add_argument("--target", required=True, help="如 106.52.201.42:443")
    ap.add_argument("--log-file", default="")
    args = ap.parse_args(argv)

    entries = json.loads(Path(args.servers_json).read_text(encoding="utf-8"))
    cred = next((e for e in entries if e.get("name") == args.via), None)
    if cred is None:
        sys.exit(f"servers.json 里没有 {args.via!r}")
    host, _, port = args.target.rpartition(":")
    target = (host, int(port))

    log_file = None
    if args.log_file:
        Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
        log_file = open(args.log_file, "a", encoding="utf-8")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        if log_file:
            log_file.write(line + "\n")
            log_file.flush()

    tunnel = Tunnel(cred, target, log)
    tunnel.channel().close()          # 预热 + 验证凭据
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.listen_host, args.listen_port))
    srv.listen(64)
    log(f"[tunnel] 监听 {args.listen_host}:{args.listen_port} -> {target[0]}:{target[1]}（经 {args.via}）")
    try:
        while True:
            client, _addr = srv.accept()
            threading.Thread(target=handle_client, args=(client, tunnel, log), daemon=True).start()
    except KeyboardInterrupt:
        log("[tunnel] Ctrl+C 停止")
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
