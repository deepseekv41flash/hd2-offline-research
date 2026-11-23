"""明文 HTTP 模式：给产品引擎的 `upstreamTest: "host:port"` 用。

为什么要这个模式（2026-11-23 与 MITM 线约定的明天 mock 验收）：
- 产品引擎在 legacy config 里把上游指向 `host:port` 时发的是**明文 HTTP**；
- 研究线 mock 原来只支持 TLS（`ssl.SSLContext.wrap_socket`），没法对接；
- 明文模式必须**不占 443、不加载证书、不起 CRL**，否则会跟产品引擎抢端口/抢信任。

这些测试不需要管理员、不需要游戏、不需要证书文件。
"""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

from mock.capture import CaptureStore  # noqa: E402
from mock.handler import MockHandler  # noqa: E402
from mock.server import MockServer  # noqa: E402


def _plain_server(handler: MockHandler, **kw) -> MockServer:
    kw.setdefault("host", "127.0.0.1")
    kw.setdefault("port", 0)
    kw.setdefault("cert_dir", None)
    kw.setdefault("plaintext", True)
    return MockServer(handler, **kw)


def _request(port: int, method: str, path: str, body: bytes = b"") -> tuple[int, bytes]:
    """裸 http.client —— 不走 urllib，避免系统代理（Clash）介入 127.0.0.1。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body,
                     headers={"Host": "api.live.prod.thehelldiversgame.com"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def test_plaintext_serves_core_endpoints(synthetic_capture: Path):
    store = CaptureStore.load(synthetic_capture)
    srv = _plain_server(MockHandler(store))
    srv.start()
    try:
        status, payload = _request(srv.port, "GET", "/health")
        assert status == 200
        assert json.loads(payload)["ok"] is True

        status, payload = _request(srv.port, "GET", "/api/WarSeason/current/WarId")
        assert status == 200 and json.loads(payload)["id"] == 801

        status, payload = _request(srv.port, "POST", "/api/Account/Login", b"")
        assert status == 200 and "sessionId" in json.loads(payload)

        # 未识别端点也必须 200（否则客户端轮询被打断）
        status, _ = _request(srv.port, "GET", "/api/Nope/Nope")
        assert status == 200
    finally:
        srv.stop()


def test_plaintext_needs_no_cert_and_no_crl(synthetic_capture: Path):
    """明文模式：cert_dir 可省、强制不起 CRL —— 不能跟产品引擎抢 443/信任。"""
    store = CaptureStore.load(synthetic_capture)
    srv = _plain_server(MockHandler(store), serve_crl=True)  # 显式要 CRL 也必须被忽略
    srv.start()
    try:
        assert srv.plaintext is True
        assert srv.cert_dir is None
        assert srv.serve_crl is False
        assert srv.crl_srv is None
    finally:
        srv.stop()


def test_plaintext_local_probe_marks_ssl_off(synthetic_capture: Path):
    store = CaptureStore.load(synthetic_capture)
    srv = _plain_server(MockHandler(store))
    srv.start()
    try:
        probe = srv.local_probe()
        assert set(probe) == {"/health", "/api/WarSeason/current/WarId", "/api/Account/Login"}
        for path, info in probe.items():
            assert "error" not in info, f"{path} 探测失败: {info}"
            assert info["status"] == 200
            assert info["ssl"] == "off"       # 明文标记，便于日志区分
    finally:
        srv.stop()


def test_plaintext_port_zero_is_dynamic_and_second_bind_conflicts(synthetic_capture: Path):
    """port=0 时回填真实端口；同端口二次绑定要报人话错误（沿用 §8.1 文案）。"""
    store = CaptureStore.load(synthetic_capture)
    first = _plain_server(MockHandler(store))
    first.start()
    try:
        assert isinstance(first.port, int) and first.port > 0
        second = _plain_server(MockHandler(store), port=first.port)
        with pytest.raises(RuntimeError) as excinfo:
            second.start()
        assert "443 只能有一个主人" in str(excinfo.value)
    finally:
        first.stop()


def test_tls_mode_still_requires_cert_dir(synthetic_capture: Path):
    """回归：TLS 模式不能因为加了 plaintext 参数就把证书校验悄悄关掉。"""
    store = CaptureStore.load(synthetic_capture)
    with pytest.raises(ValueError):
        MockServer(MockHandler(store), host="127.0.0.1", port=0, cert_dir=None,
                   serve_crl=False)


def test_mockctl_serve_exposes_plain_flag():
    """CLI 契约：`mockctl serve --plain` 必须存在（明天验收现场靠这条命令）。"""
    out = subprocess.run(
        [sys.executable, str(REPO / "tools" / "mockctl.py"), "serve", "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "--plain" in out.stdout
    assert "9099" in out.stdout
