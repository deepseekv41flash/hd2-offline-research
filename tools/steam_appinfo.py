"""Steam appinfo 分片格式（magic 0x0756442a）只读解析器。

用途
----
Steam 客户端把 app 元数据缓存成 `appcache/appinfo/appinfo_<h>.vdf` 分片。2024 之后的
新格式不是老的 binary-VDF「键内联」，而是：

    [header][string table][app record ...]

  - string table：固定 `count` 条以 \\0 结尾的字符串，按首次出现顺序编号 0..count-1；
    键（以及少量共享值）用 uint32 下标引用。
  - 每个 app record：uint32 appid，紧接一棵 binary-VDF 树，**键是 uint32 下标**，
    值内联（0x01 字符串 / 0x02 int32 / 0x03 float / 0x07 uint64），
    0x00 开子对象、0x08 关对象。

header 实测布局（appinfo_84.vdf / appinfo_f6.vdf 验证）：
    0x00 magic 0x0756442a | 0x04 universe | 0x10 app 数 | 0x18 字符串条数 | 0x1C 字符串区字节数
字符串区起点靠「按 count 条 \\0 结尾串读，正好吃掉 size 字节」扫描定位。

本工具只读，用于回答「某个 Steam 游戏的官方启动项是什么」。不改动任何文件。

用法
----
    python tools/steam_appinfo.py --file E:\\Steam\\appcache\\appinfo\\appinfo_84.vdf --launch
    python tools/steam_appinfo.py --file ... --appid 553850 --launch
    python tools/steam_appinfo.py --list-apps            # 扫整个 appcache 目录
"""
from __future__ import annotations

import argparse
import glob
import os
import struct
import sys
from pathlib import Path

MAGIC = 0x0756442A
TYPE_NEST = 0x00
TYPE_STR = 0x01
TYPE_I32 = 0x02
TYPE_F32 = 0x03
TYPE_PTR = (0x04, 0x05, 0x06)
TYPE_U64 = 0x07
TYPE_END = 0x08


class AppInfoError(RuntimeError):
    pass


def _rd(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise AppInfoError(f"unterminated string at 0x{pos:X}")
    return data[pos:end].decode("utf-8", "replace"), end + 1


def _find_str_table(data: bytes) -> tuple[list[str], int, int]:
    """返回 (names, offset, size)。header 若不可信则抛错。"""
    if len(data) < 0x20 or struct.unpack_from("<I", data, 0)[0] != MAGIC:
        raise AppInfoError("bad magic")
    count = struct.unpack_from("<I", data, 0x18)[0]
    size = struct.unpack_from("<I", data, 0x1C)[0]
    if not (0 < count < 100_000 and 0 < size < len(data)):
        raise AppInfoError(f"implausible header count={count} size={size}")
    for start in range(0x20, min(len(data), 0x2000)):
        p = start
        try:
            for _ in range(count):
                _, p = _rd(data, p)
        except AppInfoError:
            continue
        if p - start == size:
            names = []
            q = start
            for _ in range(count):
                s, q = _rd(data, q)
                names.append(s)
            return names, start, size
    raise AppInfoError("string table not located")


def _parse_tree(data: bytes, pos: int, names: list[str]) -> tuple[dict, int]:
    """解析一棵以隐式根对象开始的树；返回 (root, end_pos)。"""
    root: dict = {}
    stack: list[dict] = [root]
    n = len(names)
    while True:
        if pos >= len(data):
            raise AppInfoError("truncated tree")
        t = data[pos]
        pos += 1
        if t == TYPE_END:
            if len(stack) == 1:
                return root, pos
            stack.pop()
            continue
        kidx = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        key = names[kidx] if kidx < n else f"#{kidx}"
        if t == TYPE_NEST:
            child: dict = {}
            stack[-1][key] = child
            stack.append(child)
        elif t == TYPE_STR:
            stack[-1][key], pos = _rd(data, pos)
        elif t == TYPE_I32:
            stack[-1][key] = struct.unpack_from("<i", data, pos)[0]
            pos += 4
        elif t == TYPE_F32:
            stack[-1][key] = struct.unpack_from("<f", data, pos)[0]
            pos += 4
        elif t == TYPE_U64:
            stack[-1][key] = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
        elif t in TYPE_PTR:
            stack[-1][key] = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        else:
            raise AppInfoError(f"unknown kv type {t:#x} at 0x{pos - 5:X}")


def load(path: str | Path) -> dict[int, dict]:
    """解析一个 appinfo 分片，返回 {appid: tree}。"""
    data = Path(path).read_bytes()
    names, _, _ = _find_str_table(data)
    idx_common = names.index("common")
    idx_name = names.index("name")
    sig = b"\x00" + struct.pack("<I", idx_common) + b"\x01" + struct.pack("<I", idx_name)
    apps: dict[int, dict] = {}
    pos = 0
    while True:
        hit = data.find(sig, pos)
        if hit < 0:
            break
        appid = struct.unpack_from("<i", data, hit - 4)[0] if hit >= 4 else -1
        try:
            tree, end = _parse_tree(data, hit, names)
        except AppInfoError:
            pos = hit + 1
            continue
        if 0 < appid < 1_000_000_000 and "common" in tree:
            apps[appid] = tree
            pos = end
        else:
            pos = hit + 1
    return apps


def launch_options(tree: dict) -> list[dict]:
    """抽出 config.launch 下的启动项，按 key 排序（key 即 Steam 内部 option 序号）。"""
    launch = (tree.get("config") or {}).get("launch") or {}

    def sort_key(k: str):
        return (0, int(k)) if str(k).isdigit() else (1, str(k))

    out = []
    for key in sorted(launch, key=sort_key):
        ent = launch[key]
        if not isinstance(ent, dict):
            continue
        dl = ent.get("description_loc") or {}
        out.append(
            {
                "option": key,
                "exe": ent.get("executable"),
                "type": ent.get("type"),
                "description": ent.get("description"),
                "description_english": dl.get("english") if isinstance(dl, dict) else None,
                "arguments": ent.get("arguments"),
                "beta_branch": ((ent.get("config") or {}).get("BetaKey") if isinstance(ent.get("config"), dict) else None),
            }
        )
    return out


def name_of(tree: dict) -> str:
    return ((tree.get("common") or {}).get("name")) or "?"


def _iter_files(patterns: list[str]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        out.extend(sorted(glob.glob(pat)))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="只读解析 Steam appinfo 分片，导出启动项配置")
    ap.add_argument("--file", default=None, help="appinfo 分片路径")
    ap.add_argument("--dir", default=r"E:\Steam\appcache\appinfo", help="appinfo 目录（--list-apps 用）")
    ap.add_argument("--appid", type=int, default=None, help="只输出该 appid")
    ap.add_argument("--launch", action="store_true", help="输出 config.launch 启动项")
    ap.add_argument("--list-apps", action="store_true", help="扫描整目录，列出 appid/name/启动项数")
    args = ap.parse_args(argv)

    if args.list_apps:
        files = _iter_files([os.path.join(args.dir, "appinfo_*.vdf")])
        if not files:
            print(f"未找到 appinfo 分片：{args.dir}", file=sys.stderr)
            return 2
        total = 0
        for f in files:
            try:
                apps = load(f)
            except AppInfoError as exc:
                print(f"{os.path.basename(f)}: {exc}", file=sys.stderr)
                continue
            for appid, tree in sorted(apps.items()):
                lo = launch_options(tree)
                total += 1
                if lo:
                    print(f"{appid:>9}  {name_of(tree)}  [{os.path.basename(f)}]  {len(lo)} launch options")
        print(f"--- {total} apps ---")
        return 0

    if not args.file:
        ap.error("需要 --file（或使用 --list-apps）")
    apps = load(args.file)
    if args.appid is not None:
        targets = {args.appid: apps[args.appid]} if args.appid in apps else {}
        if not targets:
            print(f"appid {args.appid} 不在 {args.file} 中（该文件含 {sorted(apps)}）", file=sys.stderr)
            return 1
    else:
        targets = apps
    for appid, tree in sorted(targets.items()):
        print(f"=== appid {appid}  {name_of(tree)} ===")
        if args.launch:
            for o in launch_options(tree):
                print(
                    "  option %-3s type=%-8s exe=%-22r args=%r desc=%r branch=%r"
                    % (
                        o["option"],
                        o["type"],
                        o["exe"],
                        o["arguments"],
                        o["description"] or o["description_english"],
                        o["beta_branch"],
                    )
                )
        else:
            print(f"  keys: {sorted(tree)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
