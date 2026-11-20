#!/usr/bin/env python3
"""把线上抓包目录转成**脱敏可入库**的回放样本目录（与 capture 同格式）。

为什么要有这一步：抓包里有 `connectionString` / `playerName` / `MemberAccountId` /
`sessionId` 等敏感值（交接档 §7 明确「任何文档/提交都不得粘贴原值」）。样本目录用
同一套 index.txt + 响应体格式，mock 不需要任何额外代码就能读。

用法:
    python tools/sample_build.py --src <capture_dir> --dst samples/store-season
    python tools/sample_build.py --src ... --dst ... --mission-end-src D:/mod/out/gwe

产物:
    <dst>/index.txt            与原抓包同格式（时间戳保留 => 时间平移仍成立）
    <dst>/<sha8>_<slug>.json   脱敏后的响应体
    <dst>/SANITIZE.json        脱敏统计 + 原值残留自检结果
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mock.capture import CaptureStore, slugify  # noqa: E402

ZERO_UUID = "00000000-0000-4000-8000-000000000000"
PLACEHOLDER_NAME = "Player"


def _zeros(v: str) -> str:
    return "0" * max(1, len(v))


def _fill(v: str) -> str:
    return "A" * max(1, len(v))


def _connection_string(v: str) -> str:
    """保留长度与可解析骨架，换掉全部熵（uuid / 日期 / 密钥）。"""
    suffix = ".r-00000000|000000|kv1:"
    head = "cv2:" + "0" * 36
    body = head + suffix
    if len(v) <= len(body):
        return body[: len(v)]
    return body + "A" * (len(v) - len(body))


def _uuid_like(v: str) -> str:
    return ZERO_UUID


def _id_list(v: list) -> list:
    out = []
    for item in v:
        out.append(_zeros(str(item)) if item is not None and str(item) != "0" else item)
    return out


# key pattern -> (mode, transform)
RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^connectionString$", re.I), "connection"),
    (re.compile(r"^playerName$", re.I), "name"),
    (re.compile(r"^(memberAccountId|platformAccountId|accountId)$", re.I), "zeros"),
    (re.compile(r"^account$", re.I), "zeros"),
    (re.compile(r"^memberAccountIds$", re.I), "idlist"),
    (re.compile(r"^sessionId$", re.I), "uuid"),
    (re.compile(r"^lobbyId$", re.I), "lobbyid"),
    (re.compile(r"^seed$", re.I), "fill"),
    (re.compile(r"^name$", re.I), "name"),
]


class Scrubber:
    def __init__(self) -> None:
        self.originals: set[str] = set()
        self.counts: dict[str, int] = {}

    def scrub_value(self, key: str, value):
        for pat, mode in RULES:
            if not pat.match(key):
                continue
            if isinstance(value, str) and value:
                self.originals.add(value)
            self.counts[mode] = self.counts.get(mode, 0) + 1
            if mode == "connection":
                return _connection_string(value) if isinstance(value, str) else value
            if mode == "name":
                return PLACEHOLDER_NAME
            if mode == "zeros":
                return _zeros(value) if isinstance(value, str) else value
            if mode == "idlist":
                return _id_list(value) if isinstance(value, list) else value
            if mode == "uuid":
                return ZERO_UUID
            if mode == "lobbyid":
                return ZERO_UUID + ".r-00000000"
            if mode == "fill":
                return _fill(value) if isinstance(value, str) else value
            return value
        return value

    def walk(self, node):
        if isinstance(node, dict):
            return {k: (self.scrub_value(k, v) if not isinstance(v, (dict, list))
                        else self.walk(v)) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v) if isinstance(v, (dict, list)) else v for v in node]
        return node


def build(src: Path, dst: Path, *, scrub: bool = True) -> dict:
    store = CaptureStore.load(src)
    dst.mkdir(parents=True, exist_ok=True)
    scrubber = Scrubber()
    index_lines: list[str] = []
    written = 0
    for e in store.entries:
        if not e.body:
            continue
        if scrub and e.doc is not None:
            new_doc = scrubber.walk(e.doc)
            body = json.dumps(new_doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        else:
            body = e.body
        sha = hashlib.sha256(body).hexdigest()
        fname = f"{sha[:8]}_{slugify(e.raw_path)}.json"
        out = dst / fname
        if not out.exists():
            out.write_bytes(body)
            written += 1
        kind = "json" if e.doc is not None else e.kind
        index_lines.append(f"{e.captured_at.strftime('%Y%m%dT%H%M%SZ')}\t{e.raw_path}\t"
                           f"{len(body)}\t{sha}\t{kind}")
    (dst / "index.txt").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    # ---- 原值残留自检（硬门禁）----
    leaks: list[str] = []
    if scrub and scrubber.originals:
        blobs = b"".join(p.read_bytes() for p in dst.glob("*.json"))
        text = blobs.decode("utf-8", "replace")
        for orig in sorted(scrubber.originals, key=len, reverse=True):
            if len(orig) >= 6 and orig in text:
                leaks.append(orig[:4] + "…" + f"({len(orig)})")
    report = {
        "schema": "hd2-replay-sample-v1",
        "source": str(src),
        "scrubbed": scrub,
        "records": len(index_lines),
        "files_written": written,
        "scrub_counts": scrubber.counts,
        "sensitive_originals_seen": len(scrubber.originals),
        "leak_check": "PASS" if not leaks else "FAIL",
        "leaks": leaks,
    }
    (dst / "SANITIZE.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    if leaks:
        raise SystemExit(f"脱敏残留自检失败：{leaks}")
    return report


def copy_mission_end(src_dir: Path, dst: Path, scrubber: Scrubber | None = None) -> list[str]:
    """从业务抓包目录挑 Mission/end 响应，脱敏后作为结算模板。"""
    dst.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    scrubber = scrubber or Scrubber()
    for p in sorted(Path(src_dir).glob("*api_Operation_Mission_end.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        new_doc = scrubber.walk(doc)
        name = p.name.split("_api_")[0] + ".json"
        (dst / name).write_text(json.dumps(new_doc, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        out.append(name)
        if len(out) >= 5:
            break
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--dst", type=Path, required=True)
    ap.add_argument("--no-scrub", action="store_true",
                    help="不脱敏（仅限 gitignore 的本地调试目录）")
    ap.add_argument("--mission-end-src", type=Path, default=None,
                    help="额外从该目录挑 Mission/end 响应做结算模板")
    args = ap.parse_args(argv)
    report = build(args.src, args.dst, scrub=not args.no_scrub)
    print(f"[sample] {args.dst}: {report['records']} 条, leak_check={report['leak_check']}, "
          f"scrub={report['scrub_counts']}")
    if args.mission_end_src:
        names = copy_mission_end(args.mission_end_src, args.dst.parent / "mission-end")
        print(f"[sample] mission-end 模板 {len(names)} 份 -> {args.dst.parent / 'mission-end'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
