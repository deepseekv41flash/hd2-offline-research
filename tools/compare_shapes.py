"""B9：对照两份 `POST /api/Account/Login` body schema（只含结构，无原值）。

两份输入是 `mock/bodycapture.py` 产出的记录（`state/evidence/b9/*.json`）。
本工具做三件事：

1. **字段级 diff**：共同字段的长度/类型是否一致；只在一侧出现的字段列出来；
2. **字节数对账**：用观测到的序列化风格（`, ` + ` : ` + 结尾换行）从 schema
   重建长度，必须与实测 `bytes` 完全相等，否则报 MISMATCH；
3. **差异归因**：把 `bytes(A) - bytes(B)` 拆成「新增字段的 JSON 本体」与
   「新增字段带来的分隔符风格开销」两段，并输出每段数值。

用法：
    python tools/compare_shapes.py state/evidence/b9/<A>.json state/evidence/b9/<B>.json
    python tools/compare_shapes.py ... --label-a "account A (PSN-bound)" --label-b "account B (unbound)"
    python tools/compare_shapes.py ... --json          # 机器可读

退出码：0 = 对账通过；1 = 长度重建对不上（schema 记录不全或风格假设错）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 观测到的序列化风格：`{"k" : v, "k2" : v2}` + 结尾换行
STYLE = {"item_sep": ", ", "kv_sep": " : ", "trailing": "\n"}


def _scalar_len(shape: dict) -> int:
    """一个叶子值在 JSON 里占多少字节（字符串按长度 + 两个引号、数字按字面）。"""
    t = shape.get("type")
    if t == "str":
        return int(shape.get("len", 0)) + 2  # 引号
    if t == "bool":
        return len("true" if shape.get("value") else "false")
    if t == "number":
        v = shape.get("value")
        return len(str(int(v)) if float(v).is_integer() else str(v))
    if t == "null":
        return len("null")
    raise ValueError(f"无法估算叶子类型: {t!r}")


def predict_len(shape: dict, style: dict = STYLE) -> int:
    """按 schema 重建 JSON body 的字节长度（不关心具体值，只关心长度）。"""
    if shape.get("type") == "obj":
        children = shape.get("children") or {}
        keys = shape.get("keys") or list(children)
        total = 2 + len(style["trailing"])  # { }
        if not keys:
            return total
        for k in keys:
            total += len(k) + 2              # 引号 + 键名
            total += len(style["kv_sep"])    # 键值分隔符
            total += predict_len(children[k], style)
        total += (len(keys) - 1) * len(style["item_sep"])
        return total
    if shape.get("type") == "arr":
        n = int(shape.get("len", 0))
        if n == 0:
            return 2
        item = predict_len(shape["item"], style)
        return 2 + n * item + (n - 1) * len(style["item_sep"])
    return _scalar_len(shape)


def _unwrap(rec: dict) -> dict:
    """完整采集记录 -> shape 记录（含 bytes/sha256/format + json 子树）。"""
    return rec.get("shape", rec)


def _core(shape_rec: dict) -> dict:
    """shape 记录 -> 真正的 JSON 结构（bodycapture 把结构放在 `json` 里）。

    非 JSON body 没有 `json` 子树，此时无法做字段级对照，由调用方拒绝。
    """
    if "json" in shape_rec:
        return shape_rec["json"]
    raise ValueError("该记录不是 JSON body（无 json 子树），无法做字段级对照")


def _fields(shape: dict) -> dict:
    """{字段名: 叶子/子结构 shape}。"""
    children = shape.get("children") or {}
    return {k: children[k] for k in (shape.get("keys") or list(children))}


def describe(shape: dict) -> str:
    t = shape.get("type")
    if t == "str":
        return f"str(len={shape.get('len')}, class={shape.get('class')})"
    if t == "bool":
        return f"bool({shape.get('value')})"
    if t == "number":
        return f"number({shape.get('value')})"
    if t == "arr":
        return f"arr(len={shape.get('len')})"
    if t == "obj":
        return f"obj(keys={len(shape.get('keys') or [])})"
    return str(t)


def compare(rec_a: dict, rec_b: dict, label_a: str, label_b: str) -> dict:
    a_rec, b_rec = _unwrap(rec_a), _unwrap(rec_b)
    a, b = _core(a_rec), _core(b_rec)
    fa, fb = _fields(a), _fields(b)
    common = [k for k in fa if k in fb]
    only_a = [k for k in fa if k not in fb]
    only_b = [k for k in fb if k not in fa]

    same, diff = [], []
    for k in common:
        if describe(fa[k]).replace(f"class={fa[k].get('class')}", "class=*") == \
                describe(fb[k]).replace(f"class={fb[k].get('class')}", "class=*"):
            same.append(k)
        else:
            diff.append(k)

    bytes_a, bytes_b = a_rec.get("bytes"), b_rec.get("bytes")
    pred_a, pred_b = predict_len(a), predict_len(b)
    report = {
        "label_a": label_a,
        "label_b": label_b,
        "bytes_a": bytes_a,
        "bytes_b": bytes_b,
        "delta": (bytes_a - bytes_b) if (bytes_a is not None and bytes_b is not None) else None,
        "predicted_a": pred_a,
        "predicted_b": pred_b,
        "reconciles": pred_a == bytes_a and pred_b == bytes_b,
        "fields_a": len(fa),
        "fields_b": len(fb),
        "common_same": same,
        "common_diff": diff,
        "only_in_a": [{"key": k, "shape": describe(fa[k])} for k in only_a],
        "only_in_b": [{"key": k, "shape": describe(fb[k])} for k in only_b],
    }

    # 差异归因：新增字段的「本体」+「分隔符风格」两段
    if bytes_a is not None and bytes_b is not None and only_a and not only_b:
        compact_a = predict_len(a, {"item_sep": ",", "kv_sep": ":", "trailing": ""})
        compact_b = predict_len(b, {"item_sep": ",", "kv_sep": ":", "trailing": ""})
        report["attribution"] = {
            "extra_fields_json": compact_a - compact_b,
            "extra_fields_style": (bytes_a - bytes_b) - (compact_a - compact_b),
            "note": "style = 每个新增字段的 ' : ' 两空格 + ', ' 一空格",
        }
        report["attribution"]["sum_check"] = (
            report["attribution"]["extra_fields_json"]
            + report["attribution"]["extra_fields_style"]
            == report["delta"]
        )
    return report


def render(r: dict) -> str:
    L = []
    L.append(f"A = {r['label_a']}  ({r['bytes_a']} B, {r['fields_a']} fields)")
    L.append(f"B = {r['label_b']}  ({r['bytes_b']} B, {r['fields_b']} fields)")
    L.append(f"delta = A - B = {r['delta']} B")
    L.append("")
    L.append(f"[字节对账] 按 schema 重建: A={r['predicted_a']} B={r['predicted_b']} "
             f"-> {'MATCH' if r['reconciles'] else 'MISMATCH'}")
    L.append("")
    L.append(f"[共同且一致的字段] {len(r['common_same'])}: {', '.join(r['common_same'])}")
    if r["common_diff"]:
        L.append(f"[共同但形状不同] {', '.join(r['common_diff'])}")
    if r["only_in_a"]:
        L.append("[只出现在 A 的字段]")
        for f in r["only_in_a"]:
            L.append(f"  + {f['key']}: {f['shape']}")
    if r["only_in_b"]:
        L.append("[只出现在 B 的字段]")
        for f in r["only_in_b"]:
            L.append(f"  + {f['key']}: {f['shape']}")
    if "attribution" in r:
        at = r["attribution"]
        L.append("")
        L.append("[差异归因]")
        L.append(f"  新增字段 JSON 本体 = {at['extra_fields_json']} B")
        L.append(f"  新增字段分隔符风格 = {at['extra_fields_style']} B")
        L.append(f"  合计 = {at['extra_fields_json'] + at['extra_fields_style']} B "
                 f"(= delta {r['delta']} ? {'YES' if at['sum_check'] else 'NO'})")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="B9 登录体 schema 对照")
    p.add_argument("a", type=Path, help="账号 A 的采集记录（含 body 更长的那个）")
    p.add_argument("b", type=Path, help="账号 B 的采集记录")
    p.add_argument("--label-a", default="A")
    p.add_argument("--label-b", default="B")
    p.add_argument("--json", action="store_true", help="输出 JSON 而不是报告文本")
    args = p.parse_args(argv)

    rec_a = json.loads(args.a.read_text(encoding="utf-8"))
    rec_b = json.loads(args.b.read_text(encoding="utf-8"))
    r = compare(rec_a, rec_b, args.label_a, args.label_b)
    print(json.dumps(r, ensure_ascii=False, indent=2) if args.json else render(r))
    return 0 if r["reconciles"] else 1


if __name__ == "__main__":
    sys.exit(main())
