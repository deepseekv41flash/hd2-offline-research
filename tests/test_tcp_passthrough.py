"""透明 TCP 转发：字节必须原样往返，且只统计字节数不落内容。"""

from __future__ import annotations

import socket
import threading
import time

from tools.tcp_passthrough import Passthrough


def _echo_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=_echo_one, args=(conn,), daemon=True).start()

    def _echo_one(conn: socket.socket):
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                conn.sendall(data)      # 原样回声（模拟"后端"）
        except OSError:
            pass
        finally:
            conn.close()

    threading.Thread(target=loop, daemon=True).start()
    return srv, stop


def test_passthrough_relays_bytes_both_ways():
    echo, stop = _echo_server()
    upstream = echo.getsockname()
    pt = Passthrough(("127.0.0.1", 0), upstream)
    pt.start()
    threading.Thread(target=pt.serve_forever, daemon=True).start()
    try:
        client = socket.create_connection(("127.0.0.1", pt.bound_port), timeout=5)
        payload = b"hello-passthrough" * 100   # 1500 B
        client.sendall(payload)
        got = b""
        while len(got) < len(payload):
            chunk = client.recv(65536)
            if not chunk:
                break
            got += chunk
        client.close()
        assert got == payload               # 字节原样
        time.sleep(0.2)                     # 等计数线程收尾
        assert pt.total_up == len(payload)  # 只统计字节数
        assert pt.total_down == len(payload)
    finally:
        pt.stop()
        stop.set()
        echo.close()


def test_passthrough_refuses_cleanly_when_upstream_down():
    """上游不可达时：不崩、连接被关闭、不泄漏异常到调用方。"""
    dead = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()                             # 端口已释放 → 连接会被拒

    pt = Passthrough(("127.0.0.1", 0), ("127.0.0.1", dead_port))
    pt.start()
    threading.Thread(target=pt.serve_forever, daemon=True).start()
    try:
        client = socket.create_connection(("127.0.0.1", pt.bound_port), timeout=5)
        client.settimeout(3)
        assert client.recv(16) == b""        # 转发器主动关闭
        client.close()
    finally:
        pt.stop()
