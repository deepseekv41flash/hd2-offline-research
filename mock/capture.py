"""读产品 capture 目录（`index.txt` + `<sha8>_<slug>.json` 响应体）。

capture 格式（产品 engine/hooks/capture.py 写入，研究线只读）:
    <ISO8601Z>\t<path>\t<size>\t<sha256>\t<kind>
    REQ <METHOD>\t<path>\t<size>\t<sha256>      # 请求体条目（本模块跳过，供其它工具用）

注意：**响应体条目不含 HTTP 方法**（抓包钩子只记路径），所以回放匹配以路径为准。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


STAMP_FMT = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True)
class CapturedEntry:
    """一条抓包响应。"""

    raw_path: str          # 含 query，与抓包一致
    base_path: str         # 不含 query
    query: str             # 原始 query 串（不含 ?）
    captured_at: datetime  # UTC
    size: int
    sha256: str
    kind: str              # json / not-json
    file: str              # 响应体文件名
    body: bytes = field(default=b"", compare=False)
    doc: object | None = field(default=None, compare=False)

    @property
    def is_json(self) -> bool:
        return self.kind == "json" and not self.body.startswith(b"\x1f\x8b")


def slugify(path: str) -> str:
    """与产品 capture 相同的文件名 slug 规则（取末 60 字符）。"""
    import re

    s = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    return s[-60:] if s else "body"


def parse_stamp(s: str) -> datetime:
    return datetime.strptime(s, STAMP_FMT).replace(tzinfo=timezone.utc)


def parse_index(capture_dir: Path, *, want_requests: bool = False) -> list[CapturedEntry]:
    """解析 index.txt，返回**响应**条目（按抓包顺序）。"""
    idx = capture_dir / "index.txt"
    if not idx.exists():
        raise FileNotFoundError(f"capture 目录缺少 index.txt: {idx}")
    out: list[CapturedEntry] = []
    for line in idx.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        stamp, path, size, sha, kind = parts[0], parts[1], parts[2], parts[3], parts[4]
        if path.startswith("REQ "):
            if not want_requests:
                continue
            # REQ 行：stamp \t "REQ GET" \t path \t size \t sha   （注意方法在第二列）
            out.append(
                CapturedEntry(
                    raw_path=path,
                    base_path=path,
                    query="",
                    captured_at=parse_stamp(stamp),
                    size=int(size),
                    sha256=sha,
                    kind=kind,
                    file="",
                )
            )
            continue
        base, _, query = path.partition("?")
        out.append(
            CapturedEntry(
                raw_path=path,
                base_path=base,
                query=query,
                captured_at=parse_stamp(stamp),
                size=int(size),
                sha256=sha,
                kind=kind,
                file=f"{sha[:8]}_{slugify(path)}.json",
            )
        )
    return out


class CaptureStore:
    """一份抓包快照：条目 + 响应体 + 时间锚点。"""

    def __init__(self, capture_dir: Path, entries: list[CapturedEntry]):
        self.capture_dir = Path(capture_dir)
        self.entries = entries
        self._by_file: dict[str, CapturedEntry] = {e.file: e for e in entries if e.file}

    # ---- 构造 -------------------------------------------------------------
    @classmethod
    def load(cls, capture_dir: Path, *, load_bodies: bool = True) -> "CaptureStore":
        capture_dir = Path(capture_dir)
        entries = parse_index(capture_dir)
        if load_bodies:
            for e in entries:
                fp = capture_dir / e.file
                if not fp.exists():
                    continue
                body = fp.read_bytes()
                doc = None
                if e.is_json:
                    try:
                        doc = json.loads(body.decode("utf-8"))
                    except Exception:  # noqa: BLE001
                        doc = None
                # dataclass frozen -> 用 object.__setattr__ 填充缓存
                object.__setattr__(e, "body", body)
                object.__setattr__(e, "doc", doc)
        return cls(capture_dir, entries)

    # ---- 查询 -------------------------------------------------------------
    def by_file(self, file: str) -> CapturedEntry | None:
        return self._by_file.get(file)

    def json_entries(self) -> Iterator[CapturedEntry]:
        for e in self.entries:
            if e.doc is not None:
                yield e

    def find(self, *, base_path: str | None = None, suffix: str | None = None) -> CapturedEntry | None:
        """按 base_path 精确或按路径后缀找第一条命中（调试/取锚点用）。"""
        for e in self.entries:
            if base_path is not None and e.base_path == base_path:
                return e
            if suffix is not None and e.base_path.endswith(suffix):
                return e
        return None

    def all_for(self, suffix: str) -> list[CapturedEntry]:
        return [e for e in self.entries if e.base_path.endswith(suffix)]

    # ---- 时间锚点 ---------------------------------------------------------
    def war_anchors(self) -> dict:
        """从抓包推导战争时钟锚点。

        两个**独立**计数器（2026-11 抓包实测差异约 1.9M 秒，不能互相替代）:
          * ``war_clock``   = Status.time（事件 expireTime / assignment startTime 用这个基准）
          * ``war_elapsed`` = timeSinceStart.secondsSinceStart（主界面「战争已进行」显示）
        每个锚点记录 (值, 该响应的抓包时刻)，回放时按「该响应抓包时刻 → 现在」的差平移。
        """
        anchors: dict = {"war_clock": None, "war_elapsed": None,
                         "capture_first": self.entries[0].captured_at if self.entries else None,
                         "capture_last": self.entries[-1].captured_at if self.entries else None}
        for e in self.json_entries():
            if e.base_path.endswith("/Status") and isinstance(e.doc, dict) and "time" in e.doc:
                if anchors["war_clock"] is None:
                    anchors["war_clock"] = {"value": e.doc["time"], "at": e.captured_at,
                                            "path": e.base_path}
            elif e.base_path.endswith("/timeSinceStart") and isinstance(e.doc, dict):
                v = e.doc.get("secondsSinceStart")
                if v is not None and anchors["war_elapsed"] is None:
                    anchors["war_elapsed"] = {"value": v, "at": e.captured_at,
                                              "path": e.base_path}
        return anchors


def read_request_bodies(capture_dir: Path) -> dict[str, bytes]:
    """读 REQ 请求体条目：key = ``"<METHOD> <path>"``。供分析工具用（回放不需要）。"""
    capture_dir = Path(capture_dir)
    idx = capture_dir / "index.txt"
    if not idx.exists():
        return {}
    out: dict[str, bytes] = {}
    for line in idx.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 5 or not parts[1].startswith("REQ "):
            continue
        method = parts[1][4:].strip()
        path, _, query = parts[2].partition("?")
        raw = parts[2]
        # 文件名：req_<TAG>_<sha8>_<slug>.json（产品 capture_request 的命名）
        sha = parts[4] if len(parts) >= 5 else ""
        for cand in capture_dir.glob(f"req_*_{sha[:8]}_*.json"):
            out[f"{method} {raw}"] = cand.read_bytes()
            break
    return out
