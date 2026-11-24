"""研究线证书：签发自检 + Windows 信任存储命令构造（不触碰真实存储）。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

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


# ---- 有效期策略（2026-11-24 与产品线对齐：默认 2 天、可显式放宽到 7 天、硬上限 7）----

def test_days_default_two(tmp_path: Path):
    meta = certs.generate(tmp_path / "c2", host="x.example.com")
    assert meta["days"] == 2 and meta["self_check"] == "OK"


def test_days_three_rejected_by_default(tmp_path: Path):
    """未显式放宽时，3 天必须被拒 —— 保证"默认行为完全不变"。"""
    with pytest.raises(RuntimeError) as ei:
        certs.generate(tmp_path / "c3", host="x.example.com", days=3)
    assert "上限" in str(ei.value)


def test_days_seven_allowed_when_explicit(tmp_path: Path):
    """显式 max_days=7 时允许 7 天（把 UAC 频率从 36h 降到每周）。"""
    meta = certs.generate(tmp_path / "c7", host="x.example.com", days=7, max_days=7)
    assert meta["days"] == 7 and meta["self_check"] == "OK"
    left = (datetime.fromisoformat(meta["not_after"].replace("Z", "+00:00"))
            - datetime.now(timezone.utc)).total_seconds() / 3600
    assert 160 < left <= 169


def test_days_hard_cap_cannot_be_exceeded(tmp_path: Path):
    """硬上限 7 天不可被参数突破（max_days=8 也不行）。"""
    with pytest.raises(RuntimeError) as ei:
        certs.generate(tmp_path / "c8", host="x.example.com", days=8, max_days=8)
    assert "7 天" in str(ei.value)


def test_ensure_passes_max_days_through(tmp_path: Path):
    """`ensure()` 也要能把 max_days 透传给 generate（否则续期路径用不上 7 天）。"""
    meta = certs.ensure(tmp_path / "cs", host="x.example.com", days=7, max_days=7)
    assert meta["days"] == 7
