"""Steam appinfo 分片解析器：用合成样本固化解码口径（不依赖本机 Steam 缓存）。

真实分片格式（magic 0x0756442a）：
    header[0x18]=字符串条数  header[0x1C]=字符串区字节数
    [字符串表：count 条 \\0 结尾串]  [app record: uint32 appid + KV 树 ...]
KV 树里键是 uint32 字符串下标，值内联。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from tools.steam_appinfo import AppInfoError, launch_options, load

MAGIC = 0x0756442A
NEST, STR, I32, END = 0x00, 0x01, 0x02, 0x08


def _kv(depth: int, key: int, body: bytes) -> bytes:
    """开一个嵌套对象（depth 段）并返回 body。"""
    return bytes([NEST]) + struct.pack("<I", key) + body


def _s(key: int, value: str) -> bytes:
    return bytes([STR]) + struct.pack("<I", key) + value.encode() + b"\x00"


def _i(key: int, value: int) -> bytes:
    return bytes([I32]) + struct.pack("<I", key) + struct.pack("<i", value)


def _end(n: int = 1) -> bytes:
    return bytes([END]) * n


def build_appinfo(names: list[str], apps: list[tuple[int, bytes]], *, pad: int = 0x20) -> bytes:
    """按真实布局拼一个可分片：header(0x20) + 字符串表 + appid+树。"""
    blob = b"".join(n.encode() + b"\x00" for n in names)
    header = bytearray(0x20)
    struct.pack_into("<I", header, 0x00, MAGIC)
    struct.pack_into("<I", header, 0x04, 1)          # universe
    struct.pack_into("<I", header, 0x10, len(apps))  # app 数（解析器不依赖）
    struct.pack_into("<I", header, 0x18, len(names))  # 字符串条数
    struct.pack_into("<I", header, 0x1C, len(blob))   # 字符串区字节数
    body = bytes(header) + b"\x00" * pad + blob
    for appid, tree in apps:
        body += struct.pack("<i", appid) + tree
    return body


def _names_for_launch() -> list[str]:
    # 顺序即下标；内容对齐真实分片里的常见键名。
    # 注意：启动项的「option 键」本身也是字符串（"0"/"2"/"3"），必须在字符串表里。
    return [
        "appinfo", "appid", "common", "name", "type",                 # 0..4
        "config", "launch", "installdir", "executable", "arguments",   # 5..9
        "description", "description_loc", "english", "BetaKey",        # 10..13
        "depots",                                                      # 14
        "0", "2", "3",                                                 # 15..17
    ]


def _tree(launch_entries: dict[str, dict]) -> bytes:
    n = _names_for_launch()
    idx = {k: i for i, k in enumerate(n)}
    launch = b""
    for option, ent in launch_entries.items():
        inner = b""
        if "exe" in ent:
            inner += _s(idx["executable"], ent["exe"])
        if "type" in ent:
            inner += _s(idx["type"], ent["type"])
        if "arguments" in ent:
            inner += _s(idx["arguments"], ent["arguments"])
        if "description" in ent:
            inner += _s(idx["description"], ent["description"])
        if "beta" in ent:
            inner += _kv(0, idx["config"], _s(idx["BetaKey"], ent["beta"]) + _end())
        inner += _kv(0, idx["description_loc"], _s(idx["english"], ent.get("description", "")) + _end())
        launch += _kv(0, idx[str(option)], inner + _end())
    return (
        _kv(0, idx["common"], _s(idx["name"], "Test Game") + _end(1))
        + _kv(0, idx["config"], _s(idx["installdir"], "test_game") + _kv(0, idx["launch"], launch + _end()) + _end())
        + _kv(0, idx["depots"], _end(1))
        + _end(1)   # 关掉隐式根
    )


def _write(tmp_path: Path, data: bytes, name: str = "appinfo_00.vdf") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_load_multi_apps(tmp_path):
    names = _names_for_launch()
    tree_a = _tree({"0": {"exe": "launcher.exe", "type": "default"}})
    tree_b = _tree({"0": {"exe": "launcher.exe", "type": "option1"}, "2": {"exe": "game.exe", "type": "option1"}})
    path = _write(tmp_path, build_appinfo(names, [(111, tree_a), (222, tree_b)]))

    apps = load(path)
    assert sorted(apps) == [111, 222]
    assert apps[111]["common"]["name"] == "Test Game"


def test_launch_options_exe_only_difference(tmp_path):
    """复刻 Broken Arrow 的口径：两条启动项只有 executable 不同、arguments 为空。"""
    names = _names_for_launch()
    tree = _tree(
        {
            "0": {"exe": "EACLauncher.exe", "type": "option1", "description": "Broken Arrow"},
            "2": {"exe": "BrokenArrow.exe", "type": "option1", "description": "Anti-Cheat Disabled"},
        }
    )
    apps = load(_write(tmp_path, build_appinfo(names, [(1604270, tree)])))
    opts = launch_options(apps[1604270])

    assert [o["option"] for o in opts] == ["0", "2"]
    assert opts[0]["exe"] == "EACLauncher.exe" and opts[0]["type"] == "option1"
    assert opts[1]["exe"] == "BrokenArrow.exe"
    assert opts[1]["description"] == "Anti-Cheat Disabled"
    assert all(o["arguments"] is None for o in opts)


def test_launch_arguments_and_beta_branch(tmp_path):
    """HD2 口径：arguments 有值；beta 启动项带 config.BetaKey。"""
    names = _names_for_launch()
    tree = _tree(
        {
            "0": {"exe": "start_protected_game.exe", "arguments": "--bundle-dir data --release", "type": "default"},
            "3": {"exe": "game.exe", "type": "option1", "beta": "private_test_2"},
        }
    )
    apps = load(_write(tmp_path, build_appinfo(names, [(553850, tree)])))
    opts = {o["option"]: o for o in launch_options(apps[553850])}

    assert opts["0"]["arguments"] == "--bundle-dir data --release"
    assert opts["3"]["beta_branch"] == "private_test_2"
    assert opts["3"]["arguments"] is None


def test_bad_magic_rejected(tmp_path):
    data = bytearray(build_appinfo(_names_for_launch(), [(1, _tree({"0": {"exe": "a.exe"}}))]))
    struct.pack_into("<I", data, 0x00, 0xDEADBEEF)
    with pytest.raises(AppInfoError):
        load(_write(tmp_path, bytes(data)))


def test_string_table_size_mismatch_is_reported(tmp_path):
    """header 里的字符串区大小若与真实串表不符，解析器必须报错而不是静默错位。"""
    names = _names_for_launch()
    data = bytearray(build_appinfo(names, [(1, _tree({"0": {"exe": "a.exe"}}))]))
    struct.pack_into("<I", data, 0x1C, struct.unpack_from("<I", data, 0x1C)[0] + 3)
    with pytest.raises(AppInfoError):
        load(_write(tmp_path, bytes(data)))
