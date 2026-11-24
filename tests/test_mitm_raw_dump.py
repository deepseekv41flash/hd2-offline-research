"""`mitm_proxy` 的原始字节落盘（--dump-bodies）回归测试。

为什么需要：EAC 上报体是不可读的 octet-stream，将来要对比"注入前后 EAC 到底报了什么"，
就必须先能**受控地**把干净会话的原始字节落盘 —— 而且**只能**落在指定 host+path 上，
其余域名保持 T6a 的"只记 schema、不落原值"纪律。
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import mitm_proxy as m

AC = "api.epicgames.dev"


def _proxy(tmp_path: Path, *, dump_hosts=(AC,), dump_paths="/anticheat/v8",
           limit=0) -> m.MitmProxy:
    """绕开 TLS ctx（不需要真证书），只装配 dump 相关状态。"""
    p = object.__new__(m.MitmProxy)
    p.dump_dir = tmp_path / "raw"
    p.dump_hosts = {h.lower() for h in dump_hosts}
    p._dump_re = re.compile(dump_paths) if dump_paths else None
    p.dump_limit = limit
    p._dump_seq = 0
    p._lock = threading.Lock()
    return p


def _rec(path="/anticheat/v8/games/client/message/", host=AC, method="POST"):
    return {"host": host, "method": method, "path": path}


def test_dumps_anticheat_body_verbatim(tmp_path: Path):
    p = _proxy(tmp_path)
    body = bytes(range(256))
    item = p._maybe_dump(_rec(), direction="req", body=body)
    assert item and not item["truncated"] and item["bytes"] == 256
    saved = (tmp_path / "raw" / item["file"]).read_bytes()
    assert saved == body


def test_other_paths_and_hosts_not_dumped(tmp_path: Path):
    p = _proxy(tmp_path)
    assert p._maybe_dump(_rec(path="/sdk/v1/default"), direction="resp", body=b"{}") is None
    assert p._maybe_dump(_rec(host="api.live.prod.thehelldiversgame.com"),
                         direction="req", body=b"abc") is None
    assert list((tmp_path / "raw").glob("*.bin")) == []


def test_limit_truncates_but_keeps_full_sha(tmp_path: Path):
    p = _proxy(tmp_path, limit=16)
    body = b"X" * 1000
    item = p._maybe_dump(_rec(), direction="req", body=body)
    assert item["truncated"] and item["written"] == 16 and item["bytes"] == 1000
    import hashlib
    assert item["sha256"] == hashlib.sha256(body).hexdigest()


def test_filename_is_path_safe(tmp_path: Path):
    p = _proxy(tmp_path)
    item = p._maybe_dump(_rec(path="/anticheat/v8/games/client/message/?a=1&b=2"),
                         direction="resp", body=b"ok")
    assert "/" not in item["file"] and "?" not in item["file"] and "=" not in item["file"]


def test_empty_body_is_skipped(tmp_path: Path):
    p = _proxy(tmp_path)
    assert p._maybe_dump(_rec(), direction="req", body=b"") is None
