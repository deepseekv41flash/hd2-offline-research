"""客户端测试包的防回归检查（不需要 Windows、不需要游戏）。

PowerShell 5.1 读 `.ps1` 时靠 **UTF-8 BOM** 判断编码；没有 BOM 就按系统 ANSI（中文机 = GBK）
解析，中文注释/字符串立刻变乱码甚至语法错误。2026-11-21 就踩过一次：用编辑器改完
`lib.ps1` 丢了 BOM，志愿者一跑就报「表达式中缺少右括号」。这里把这条锁住。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KIT = REPO / "kit" / "stage2"
BOM = b"\xef\xbb\xbf"

REQUIRED = [
    "lib.ps1", "install-hosts.ps1", "install-proxy.ps1", "crl-server.ps1",
    "selfcheck.ps1", "collect-logs.ps1", "rollback-all.ps1",
    "README.md", "test-card.md",
]


def _ps1_files() -> list[Path]:
    return sorted(KIT.glob("*.ps1"))


def test_kit_files_present():
    missing = [name for name in REQUIRED if not (KIT / name).exists()]
    assert missing == [], f"kit/stage2 缺文件: {missing}"


@pytest.mark.parametrize("path", _ps1_files(), ids=lambda p: p.name)
def test_ps1_has_utf8_bom(path: Path):
    raw = path.read_bytes()
    assert raw.startswith(BOM), f"{path.name} 缺 UTF-8 BOM（PS 5.1 会按 GBK 解析中文而报错）"


@pytest.mark.parametrize("path", _ps1_files(), ids=lambda p: p.name)
def test_ps1_has_no_null_bytes(path: Path):
    raw = path.read_bytes()
    assert b"\x00" not in raw, f"{path.name} 含 NUL 字节（多半是被 UTF-16 保存过）"


def test_install_hosts_wires_steam_unblock_and_crl():
    text = (KIT / "install-hosts.ps1").read_text(encoding="utf-8-sig")
    assert "Disable-SteamBlockLines" in text
    assert "Start-CrlServer" in text


def test_rollback_restores_everything():
    text = (KIT / "rollback-all.ps1").read_text(encoding="utf-8-sig")
    for fn in ("Stop-CrlServer", "Restore-SteamBlockLines", "Remove-HostsEntry",
               "Clear-UserProxy", "Remove-UserCert"):
        assert fn in text, f"rollback-all.ps1 没调用 {fn}"


def test_selfcheck_covers_steam_crl_clock():
    text = (KIT / "selfcheck.ps1").read_text(encoding="utf-8-sig")
    assert "Get-SteamBlockLines" in text
    assert "Test-CrlEndpoint" in text
    assert "Get-TimeSyncStatus" in text
