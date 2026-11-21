"""研究线证书：签发自检 + Windows 信任存储命令构造（不触碰真实存储）。"""

from __future__ import annotations

from pathlib import Path

from mock import certs


def test_generate_and_preflight(tmp_path: Path):
    meta = certs.generate(tmp_path / "certs", crl_port=18099)
    assert meta["self_check"] == "OK"
    assert certs.preflight(tmp_path / "certs") == []
    assert not (tmp_path / "certs" / "ca.key").exists()   # 签完即删 CA 私钥


def test_generate_multi_san_for_mitm(tmp_path: Path):
    """MITM 需要一张叶证书覆盖多个旁路域名（T6a）。"""
    hosts = ["api.live.prod.thehelldiversgame.com",
             "974a9.playfabapi.com",
             "modules-cdn.eac-prod.on.epicgames.com"]
    d = tmp_path / "certs"
    meta = certs.generate(d, hosts=hosts, crl_port=18099)
    assert meta["self_check"] == "OK"
    assert meta["san_dns"] == hosts
    assert meta["host"] == hosts[0]
    # preflight：覆盖则通过，不覆盖则报错
    assert certs.preflight(d, host=hosts[1]) == []
    assert certs.preflight(d, hosts=hosts) == []
    assert certs.preflight(d, host="not-in-san.example.com") != []


def test_trust_argv_current_user_install(tmp_path: Path):
    argv = certs.trust_argv(tmp_path / "certs", "ABC123", "CurrentUser", "install")
    assert argv[0] == "certutil" and argv[1] == "-user"
    assert argv[2] == "-addstore" and argv[3] == "-f" and argv[4] == "Root"
    assert argv[5].endswith("ca.cer")


def test_trust_argv_local_machine_has_no_user_flag(tmp_path: Path):
    argv = certs.trust_argv(tmp_path / "certs", "ABC123", "LocalMachine", "install")
    assert argv[0] == "certutil" and argv[1] == "-addstore"


def test_trust_argv_check_and_uninstall(tmp_path: Path):
    check = certs.trust_argv(tmp_path, "FF00", "CurrentUser", "check")
    assert check[2] == "-verifystore" and check[3] == "Root" and check[4] == "FF00"
    rm = certs.trust_argv(tmp_path, "FF00", "CurrentUser", "uninstall")
    assert rm[2] == "-delstore" and rm[4] == "FF00"


def test_is_trusted_without_meta_is_none(tmp_path: Path):
    assert certs.is_trusted(tmp_path) is None


def test_uninstall_requires_thumbprint(tmp_path: Path):
    import pytest

    with pytest.raises(RuntimeError):
        certs.uninstall_trust(tmp_path)
