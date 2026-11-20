"""登录体 **schema** 采集（B9）：只记字段名/长度/字符类/哈希，**绝不落原值**。

背景：`POST /api/Account/Login` 的请求体含 Steam 票据（敏感）。B3 侦察已确认本地没有 body 样本，
只能从「62 次登录全部 437 B」推断票据类型；T4b 又发现未绑定 PSN 的账号登录体是 **346 B**（差 91 B），
需要对比两份 schema 才能判定差异来源（票据长度 vs 账号/P SN 关联字段）。

本模块的纪律：
  * 字符串只记 `len` + 字符类（hex / base64 / uuid / digits / text）+ `sha8`；
  * 不写任何原值、不写票据内容；
  * 非 JSON body 只记长度与字符类猜测；
  * 产物落在研究仓 `state/`（gitignore）。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

DEFAULT_PATHS = ("/api/Account/Login",)
MAX_DEPTH = 4

_HEX = re.compile(r"^[0-9a-fA-F]+$")
_B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DIGITS = re.compile(r"^\d+$")


def string_class(s: str) -> str:
    if not s:
        return "empty"
    if _UUID.match(s):
        return "uuid"
    if _DIGITS.match(s):
        return "digits"
    if len(s) >= 16 and _HEX.match(s):
        return "hex"
    if len(s) >= 16 and _B64.match(s):
        return "base64"
    if all(32 <= ord(c) < 127 for c in s):
        return "ascii"
    return "text"


def _sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:8]


def shape_of_value(v, depth: int = 0) -> dict:
    """把任意值压成「结构描述」，不含原值。"""
    if isinstance(v, dict):
        out = {"type": "obj", "keys": list(v.keys())}
        if depth < MAX_DEPTH:
            out["children"] = {k: shape_of_value(x, depth + 1) for k, x in v.items()}
        return out
    if isinstance(v, list):
        out = {"type": "arr", "len": len(v)}
        if v and depth < MAX_DEPTH:
            out["item"] = shape_of_value(v[0], depth + 1)
        return out
    if isinstance(v, bool):
        return {"type": "bool", "value": v}
    if isinstance(v, (int, float)):
        return {"type": "number", "value": v}
    if v is None:
        return {"type": "null"}
    if isinstance(v, str):
        return {"type": "str", "len": len(v), "class": string_class(v), "sha8": _sha8(v)}
    return {"type": type(v).__name__}


def shape_of(body: bytes) -> dict:
    """任意请求体 → schema 描述（JSON 会展开；非 JSON 只给长度/字符类）。"""
    base = {
        "bytes": len(body),
        "sha256": hashlib.sha256(body or b"").hexdigest()[:16],
    }
    if not body:
        base.update({"format": "empty"})
        return base
    text = body.decode("utf-8", "replace")
    try:
        doc = json.loads(text)
    except Exception:  # noqa: BLE001
        base.update({"format": "non-json", "class": string_class(text),
                     "printable_ratio": round(sum(32 <= ord(c) < 127 for c in text) / max(1, len(text)), 3)})
        return base
    base.update({"format": "json", "json": shape_of_value(doc)})
    return base


class BodyShapeCapture:
    """按 profile 配置把匹配请求的 schema 写到目录（每次一个文件 + index.jsonl）。"""

    def __init__(self, cfg: dict | None):
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled"))
        self.paths = tuple(self.cfg.get("paths") or DEFAULT_PATHS)
        self.dir = Path(self.cfg.get("dir") or "state/evidence/b9")

    @classmethod
    def from_profile(cls, profile: dict | None) -> "BodyShapeCapture":
        cfg = (profile or {}).get("body_shapes") or {}
        return cls(cfg)

    def matches(self, raw_path: str) -> bool:
        base = raw_path.split("?")[0]
        return any(p in base for p in self.paths)

    def capture(self, method: str, raw_path: str, body: bytes,
                now: datetime | None = None) -> dict | None:
        if not self.enabled or not self.matches(raw_path) or not body:
            return None
        self.dir.mkdir(parents=True, exist_ok=True)
        now = now or datetime.now()
        rec = {
            "t": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "method": method.upper(),
            "path": raw_path.split("?")[0],
            "shape": shape_of(body),
        }
        fname = f"{now.strftime('%Y%m%d-%H%M%S')}-{rec['shape']['sha256'][:8]}.json"
        (self.dir / fname).write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        with open(self.dir / "index.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": rec["t"], "path": rec["path"],
                                 "bytes": rec["shape"]["bytes"],
                                 "sha256": rec["shape"]["sha256"],
                                 "file": fname}, ensure_ascii=False) + "\n")
        return rec
