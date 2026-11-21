"""反代（nginx）形态：客户端地址解析 + 账号归属不被反代抹平。

背景：业务机 443 被 nginx 占用，发现层改用「mock 只绑 127.0.0.1:8443 + nginx 反代」形态。
反代后 TCP 对端恒为回环，若仍按对端 IP 归属账号，两台客户端会被并成同一个账号。
本测试锁住：
  * 直连模式（trust_proxy=False）行为不变；
  * 反代模式只在对端是回环时采信 X-Real-IP / X-Forwarded-For；
  * 非回环对端伪造这两个头无效；
  * 端到端：同一台机器上两个不同 X-Real-IP 的登录 → 两个账号（直连模式 → 一个账号）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mock import certs
from mock.capture import CaptureStore
from mock.client import HttpsClient
from mock.handler import MockHandler
from mock.server import MockServer, effective_client_addr

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 纯函数
def test_direct_mode_ignores_forwarding_headers():
    assert effective_client_addr("203.0.113.5", {"x-real-ip": "10.9.9.9"}, False) == "203.0.113.5"


def test_proxy_mode_uses_x_real_ip():
    assert effective_client_addr("127.0.0.1", {"x-real-ip": "203.0.113.5"}, True) == "203.0.113.5"


def test_proxy_mode_uses_first_x_forwarded_for_hop():
    hdrs = {"x-forwarded-for": "203.0.113.5, 10.0.0.2"}
    assert effective_client_addr("127.0.0.1", hdrs, True) == "203.0.113.5"


def test_proxy_mode_rejects_spoof_from_non_loopback_peer():
    # 直连客户端（对端不是回环）自报 X-Real-IP 不能冒充别人
    assert effective_client_addr("198.51.100.7", {"x-real-ip": "10.0.0.1"}, True) == "198.51.100.7"


def test_proxy_mode_falls_back_when_header_invalid():
    assert effective_client_addr("::1", {"x-real-ip": "not-an-ip"}, True) == "::1"
    assert effective_client_addr("127.0.0.1", {}, True) == "127.0.0.1"


# ---------------------------------------------------------------- profile
def test_nginx_profile_declares_proxy_and_loopback_bind():
    prof = json.loads((REPO / "profiles" / "coop-discovery-nginx.json").read_text(encoding="utf-8"))
    assert prof["bind"]["host"] == "127.0.0.1"
    assert prof["bind"]["port"] == 8443
    assert prof["net"]["trust_proxy_headers"] is True
    assert prof["lobby"]["mode"] == "discovery"
    assert prof["body_shapes"]["enabled"] is True


# ---------------------------------------------------------------- 端到端
@pytest.fixture(scope="module")
def cert_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("certs-proxy")
    certs.generate(d, crl_port=18098)
    return d


def _handler(capture: Path) -> MockHandler:
    store = CaptureStore.load(capture)
    return MockHandler(store, profile={"lobby": {"mode": "discovery", "echo_put": True}})


def _login_via(srv: MockServer, cert_dir: Path, real_ip: str) -> dict:
    client = HttpsClient("127.0.0.1", srv.port, sni_host=certs.DEFAULT_HOST,
                         ca_file=str(cert_dir / "ca.pem"))
    res = client.request("POST", "/api/Account/Login", b'{"instanceId":"i-%s"}',
                         headers={"X-Real-IP": real_ip})
    assert res.status == 200, f"login 失败: {res.error or res.raw[:200]!r}"
    return res.json()


def test_two_clients_behind_proxy_get_distinct_accounts(synthetic_capture: Path, cert_dir: Path):
    handler = _handler(synthetic_capture)
    srv = MockServer(handler, host="127.0.0.1", port=0, cert_dir=cert_dir,
                     serve_crl=False, trust_proxy_headers=True)
    srv.start()
    try:
        _login_via(srv, cert_dir, "203.0.113.5")
        _login_via(srv, cert_dir, "198.51.100.7")
        assert handler.discovery.stats()["accounts"] == 2
    finally:
        srv.stop()


def test_direct_mode_merges_loopback_clients_into_one_account(synthetic_capture: Path, cert_dir: Path):
    handler = _handler(synthetic_capture)
    srv = MockServer(handler, host="127.0.0.1", port=0, cert_dir=cert_dir,
                     serve_crl=False, trust_proxy_headers=False)
    srv.start()
    try:
        _login_via(srv, cert_dir, "203.0.113.5")
        _login_via(srv, cert_dir, "198.51.100.7")
        # 直连模式下两个请求都来自 127.0.0.1 → 同一个账号（这是反代形态必须修掉的旧行为）
        assert handler.discovery.stats()["accounts"] == 1
    finally:
        srv.stop()
