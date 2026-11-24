#!/usr/bin/env python3
"""分析 EAC `/anticheat/v8` 上报体的内容层次（离线，纯读文件）。

**要回答的问题**：EAC 的上报里有没有"模块清单 / 文件哈希 / 路径 / 驱动名"？
如果是，那么往游戏进程里注入模块**几乎必然被看到**（决定 C2' 的风险等级）；
如果只有心跳/计数/性能，注入的可见性就低得多。

三层扫描（由浅入深，先做便宜的）：
  1. **字符串层**：ASCII 与 UTF-16LE 可打印串（≥6），并按类别标注
     （十六进制串 / 路径 / 模块扩展名 / 驱动·系统 DLL / 版本串 / 产品串）；
  2. **protobuf 层**：顶层 wire 解析，输出**字段树（field number + wire type + 是否 repeated）**，
     只给结构不给值；
  3. **整体性质**：熵、压缩能否解开、前 64 B 十六进制（判"加密 vs 结构化"）。

用法：
    python tools/analyze_eac_body.py D:/mod/tmp/eac-p3/raw            # 整个目录
    python tools/analyze_eac_body.py <单个 .bin> --json
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sys
import zlib
from pathlib import Path

HEX_LONG = re.compile(r"^[0-9a-fA-F]{32,128}$")
HEX_SEG = re.compile(rb"[0-9a-fA-F]{32,128}")
WIN_PATH = re.compile(rb"[A-Za-z]:\\\\?[\x20-\x7e]{3,120}|\\\\[A-Za-z0-9_.-]{2,60}\\\\")
EXTS = (b".exe", b".dll", b".sys", b".pdb", b".bin", b".json", b".ini", b".pak")
SYS_TOKENS = (b"System32", b"SysWOW64", b"ntdll", b"kernel32", b"kernelbase", b"user32",
              b"EasyAntiCheat", b"EasyAntiCheat_EOS", b"helldivers", b"Helldivers",
              b"start_protected", b"runtime.dll", b"preloader")
VER_TOKENS = (b"1.8.47139", b"1.19.1.2", b"Bootstrapper", b"EOS-SDK")


def ascii_runs(b: bytes, minlen: int = 6) -> list[str]:
    """可打印 ASCII 连续段。"""
    out, cur = [], bytearray()
    for x in b:
        if 32 <= x <= 126:
            cur.append(x)
        else:
            if len(cur) >= minlen:
                out.append(cur.decode("ascii", "replace"))
            cur = bytearray()
    if len(cur) >= minlen:
        out.append(cur.decode("ascii", "replace"))
    return out


def utf16_runs(b: bytes, minlen: int = 6) -> list[str]:
    """UTF-16LE 可打印串（Windows 二进制常见）。"""
    out, cur = [], []
    for i in range(0, len(b) - 1, 2):
        lo, hi = b[i], b[i + 1]
        if hi == 0 and 32 <= lo <= 126:
            cur.append(chr(lo))
        else:
            if len(cur) >= minlen:
                out.append("".join(cur))
            cur = []
    if len(cur) >= minlen:
        out.append("".join(cur))
    return out


def classify(strings: list[str]) -> dict:
    """按类别归并，给"EAC 报了什么"的可读判据。"""
    cats = {"hex32_128": [], "windows_path": [], "module_ext": [], "system_driver": [],
            "version_product": [], "other": []}
    for s in strings:
        raw = s.encode("ascii", "ignore")
        if HEX_LONG.match(s):
            cats["hex32_128"].append(s)
        elif b":\\" in raw or raw.startswith(b"\\\\"):
            cats["windows_path"].append(s)
        elif any(e in raw for e in EXTS):
            cats["module_ext"].append(s)
        elif any(t in raw for t in SYS_TOKENS):
            cats["system_driver"].append(s)
        elif any(t in raw for t in VER_TOKENS):
            cats["version_product"].append(s)
        else:
            cats["other"].append(s)
    return cats


def _varint(b: bytes, i: int, n: int) -> tuple[int, int]:
    shift = val = 0
    while True:
        if i >= n:
            raise ValueError("eof")
        x = b[i]
        i += 1
        val |= (x & 0x7F) << shift
        shift += 7
        if not (x & 0x80):
            return val, i


def pb_tree(b: bytes) -> dict:
    """顶层 protobuf wire 解析 → 字段树（只给结构）。"""
    i, n = 0, len(b)
    fields: list[dict] = []
    try:
        while i < n:
            key, i = _varint(b, i, n)
            tag, wt = key >> 3, key & 7
            if tag == 0:
                raise ValueError("tag0")
            item = {"field": tag, "wire": wt}
            if wt == 0:
                item["value"], i = _varint(b, i, n)
            elif wt == 1:
                item["len"] = 8
                i += 8
            elif wt == 2:
                ln, i = _varint(b, i, n)
                if ln > n - i:
                    raise ValueError("len-oob")
                item["len"] = ln
                i += ln
            elif wt == 5:
                item["len"] = 4
                i += 4
            else:
                raise ValueError("wire %d" % wt)
            fields.append(item)
        counts = collections.Counter((f["field"], f["wire"]) for f in fields)
        return {"ok": True, "fields": fields[:80], "field_count": len(fields),
                "repeated": [{"field": k[0], "wire": k[1], "times": v}
                             for k, v in counts.most_common(20) if v > 1]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "parsed_fields": len(fields)}


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    c = collections.Counter(b)
    return -sum((v / len(b)) * math.log2(v / len(b)) for v in c.values())


def analyze(path: Path) -> dict:
    b = path.read_bytes()
    asc = ascii_runs(b)
    wide = utf16_runs(b)
    cats = classify(asc + wide)
    comp = {}
    for name, fn in (("zlib", zlib.decompress), ("raw-deflate", lambda x: zlib.decompress(x, -15))):
        try:
            out = fn(b)
            comp[name] = len(out)
        except Exception:  # noqa: BLE001
            comp[name] = None
    return {
        "file": path.name, "bytes": len(b),
        "entropy": round(entropy(b), 2),
        "head64_hex": b[:64].hex(),
        "first_u32_le": int.from_bytes(b[:4], "little") if len(b) >= 4 else None,
        "decompress": comp,
        "ascii_strings": len(asc), "utf16_strings": len(wide),
        "categories": {k: v[:20] for k, v in cats.items()},
        "category_counts": {k: len(v) for k, v in cats.items()},
        "protobuf": pb_tree(b),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="分析 EAC v8 上报体（离线）")
    ap.add_argument("target", help=".bin 文件或目录")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=20, help="目录模式下最多分析多少个文件")
    args = ap.parse_args(argv)

    t = Path(args.target)
    files = sorted(t.glob("*.bin")) if t.is_dir() else [t]
    files = files[: args.limit]
    results = []
    for f in files:
        b = f.read_bytes()
        if len(b) < 64 or b.startswith(b"PREFLIGHT"):
            continue          # 跳过自检与空体
        results.append(analyze(f))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0

    print("== 分析 %d 个 body ==" % len(results))
    for r in results:
        cats = r["category_counts"]
        pb = r["protobuf"]
        print("\n-- %s  %d B  熵=%.2f  首4B(LE)=%s" % (r["file"], r["bytes"], r["entropy"],
                                                    r["first_u32_le"]))
        print("   字符串: ascii=%d utf16=%d | 分类: %s"
              % (r["ascii_strings"], r["utf16_strings"],
                 {k: v for k, v in cats.items() if v}))
        print("   protobuf: %s" % ("可解析，字段数=%d，repeated=%d 组"
                                   % (pb["field_count"], len(pb["repeated"]))
                                   if pb["ok"] else "不可解析（%s）" % pb["error"]))
        for k in ("hex32_128", "windows_path", "module_ext", "system_driver", "version_product"):
            vals = r["categories"].get(k) or []
            if vals:
                print("   [%s] %s" % (k, vals[:8]))
        if not any(cats.values()):
            print("   ⚠ 字符串层**干净**（无可打印的路径/哈希/模块名）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
