"""B9 登录体 schema 采集：结构正确 + **绝不泄漏原值**。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from mock.bodycapture import BodyShapeCapture, shape_of, string_class


def test_string_class():
    assert string_class("") == "empty"
    assert string_class("12345") == "digits"
    assert string_class("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") == "uuid"
    assert string_class("deadbeef" * 8) == "hex"
    assert string_class("A" * 43 + "=") == "base64"
    assert string_class("hello world") == "ascii"
    assert string_class("中文") == "text"


def test_shape_of_json_has_no_raw_values():
    secret = "SENSITIVE_TICKET_VALUE_0123456789abcdef"
    body = json.dumps({"platform": "STEAM", "code": secret,
                       "nested": {"token": "x" * 64, "n": 7, "flag": True}}).encode()
    shape = shape_of(body)
    blob = json.dumps(shape, ensure_ascii=False)
    assert secret not in blob                     # 原值绝不出现
    assert "SENSITIVE" not in blob
    assert shape["format"] == "json"
    assert shape["bytes"] == len(body)
    keys = shape["json"]["keys"]
    assert keys == ["platform", "code", "nested"]
    code = shape["json"]["children"]["code"]
    assert code["type"] == "str" and code["len"] == len(secret) and "sha8" in code
    nest = shape["json"]["children"]["nested"]
    assert nest["keys"] == ["token", "n", "flag"]
    assert nest["children"]["n"]["value"] == 7
    assert nest["children"]["flag"]["value"] is True


def test_shape_of_non_json():
    shape = shape_of(b"\x00\x01\x02BINARY")
    assert shape["format"] == "non-json"
    assert shape["bytes"] == 9
    assert "json" not in shape


def test_capture_disabled_by_default(tmp_path: Path):
    cap = BodyShapeCapture.from_profile({})
    assert cap.enabled is False
    assert cap.capture("POST", "/api/Account/Login", b"{}") is None


def test_capture_enabled_and_path_filter(tmp_path: Path):
    cap = BodyShapeCapture({"enabled": True, "dir": str(tmp_path)})
    assert cap.capture("POST", "/api/Other", b'{"a":1}') is None
    rec = cap.capture("POST", "/api/Account/Login", b'{"a":1}',
                      now=datetime(2026, 11, 21, 2, 0, 0))
    assert rec is not None
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["path"] == "/api/Account/Login"
    assert saved["t"].startswith("2026-11-21T02:00:00")
    idx = (tmp_path / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(idx) == 1 and json.loads(idx[0])["bytes"] == 7


def test_capture_never_writes_raw_body(tmp_path: Path):
    secret = "STEAM_TICKET_ABCDEF0123456789"
    body = json.dumps({"platform": "STEAM", "code": secret}).encode()
    cap = BodyShapeCapture({"enabled": True, "dir": str(tmp_path)})
    cap.capture("POST", "/api/Account/Login", body)
    dumped = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*"))
    assert secret not in dumped
    assert re.search(r"STEAM_TICKET", dumped) is None
