"""TLS 层自检：研究线证书 + mock HTTPS 服务 + CRL 服务（不需要游戏、不需要管理员）。"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest
from cryptography import x509

from mock import certs
from mock.capture import CaptureStore
from mock.client import HttpsClient
from mock.handler import MockHandler
from mock.server import MockServer, _Logger, start_crl_server


@pytest.fixture(scope="module")
def cert_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("certs")
    certs.generate(d, crl_port=18099)
    return d


def test_cert_generation_passes_self_check(cert_dir: Path):
    meta = certs.load_meta(cert_dir)
    assert meta is not None
    assert meta["self_check"] == "OK"
    assert meta["mode"] == "ca"
    assert meta["san_dns"] == [certs.DEFAULT_HOST]
    assert certs.preflight(cert_dir) == []
    # CA 私钥必须已经删掉（签完即删）
    assert not (cert_dir / "ca.key").exists()
    assert (cert_dir / "ca.crl").stat().st_size > 0


def test_leaf_has_cdp_and_single_san(cert_dir: Path):
    leaf = x509.load_pem_x509_certificate((cert_dir / "server.pem").read_bytes())
    cdp = leaf.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
    urls = [d.full_name[0].value for d in cdp]
    assert urls and urls[0].endswith("/ca.crl")
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert list(san.get_values_for_type(x509.DNSName)) == [certs.DEFAULT_HOST]


def test_crl_server_serves_valid_crl(cert_dir: Path):
    logger = _Logger(None)
    srv = start_crl_server(cert_dir, 0, logger)
    assert srv is not None
    try:
        port = srv.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ca.crl", timeout=5) as resp:
            data = resp.read()
        crl = x509.load_der_x509_crl(data)
        assert crl.issuer.rfc4514_string().endswith(certs.CA_CN)
    finally:
        srv.shutdown()
        srv.server_close()
    logger.close()


def test_https_server_end_to_end(cert_dir: Path, synthetic_capture: Path):
    store = CaptureStore.load(synthetic_capture)
    handler = MockHandler(store)
    srv = MockServer(handler, host="127.0.0.1", port=0, cert_dir=cert_dir,
                     log_dir=None, serve_crl=False)
    srv.start()
    try:
        port = srv.port
        client = HttpsClient("127.0.0.1", port, sni_host=certs.DEFAULT_HOST,
                             ca_file=str(cert_dir / "ca.pem"))
        health = client.request("GET", "/health")
        assert health.status == 200 and health.json()["ok"] is True

        war = client.request("GET", "/api/WarSeason/current/WarId")
        assert war.status == 200 and war.json()["id"] == 801

        login = client.request("POST", "/api/Account/Login", b"")
        assert login.status == 200 and "sessionId" in login.json()

        status = client.request("GET", "/api/WarSeason/801/Status")
        assert status.status == 200 and status.json()["warId"] == 801
        assert status.headers.get("x-mock-source")

        # 未识别端点也不能 4xx（否则客户端轮询会被打断）
        unknown = client.request("GET", "/api/Nope/Nope")
        assert unknown.status == 200

        # 负面对照：SNI/校验收到的域名与证书 SAN 不符时必须失败（证明我们真在验链）
        bad = HttpsClient("127.0.0.1", port, sni_host="wrong.example.com",
                          ca_file=str(cert_dir / "ca.pem"))
        assert bad.request("GET", "/health").error != ""
    finally:
        srv.stop()


def test_server_reports_bind_conflict(cert_dir: Path, synthetic_capture: Path):
    store = CaptureStore.load(synthetic_capture)
    first = MockServer(MockHandler(store), host="127.0.0.1", port=0, cert_dir=cert_dir,
                       serve_crl=False)
    first.start()
    try:
        port = first.port
        second = MockServer(MockHandler(store), host="127.0.0.1", port=port,
                            cert_dir=cert_dir, serve_crl=False)
        with pytest.raises(RuntimeError) as excinfo:
            second.start()
        assert "443 只能有一个主人" in str(excinfo.value)
    finally:
        first.stop()


def test_body_shape_capture_through_tls(cert_dir: Path, synthetic_capture: Path, tmp_path: Path):
    """B9 端到端：登录体经过 TLS 后按 profile 配置落 schema，且不含原值。"""
    store = CaptureStore.load(synthetic_capture)
    b9dir = tmp_path / "b9"
    handler = MockHandler(store, profile={"body_shapes": {"enabled": True, "dir": str(b9dir)}})
    srv = MockServer(handler, host="127.0.0.1", port=0, cert_dir=cert_dir,
                     serve_crl=False)
    srv.start()
    try:
        client = HttpsClient("127.0.0.1", srv.port, sni_host=certs.DEFAULT_HOST,
                             ca_file=str(cert_dir / "ca.pem"))
        secret = "TICKET_ABCDEF0123456789SECRET"
        body = json.dumps({"platform": "STEAM", "code": secret}).encode()
        r = client.request("POST", "/api/Account/Login", body)
        assert r.status == 200
        files = list(b9dir.glob("*.json"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert secret not in text
        rec = json.loads(text)
        assert rec["shape"]["bytes"] == len(body)
        assert rec["shape"]["json"]["keys"] == ["platform", "code"]
        assert rec["shape"]["json"]["children"]["code"]["len"] == len(secret)
    finally:
        srv.stop()


def test_preflight_rejects_tampered_dir(cert_dir: Path, tmp_path: Path):
    import shutil

    broken = tmp_path / "broken"
    shutil.copytree(cert_dir, broken)
    (broken / "server.chain.pem").unlink()
    problems = certs.preflight(broken)
    assert problems and "server.chain.pem" in " ".join(problems)
