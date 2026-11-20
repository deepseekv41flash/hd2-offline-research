"""B9 对照工具：字节重建必须与真实 body 完全相等，归因两段必须等于 delta。"""

from __future__ import annotations

import json

import pytest

from mock.bodycapture import shape_of

from tools.compare_shapes import compare, predict_len, _core, _unwrap

# 观测到的真实序列化风格（见 docs/B9-LOGIN-BODY-SCHEMA-2026-11-21.md）
STYLE = {"item_sep": ", ", "kv_sep": " : ", "trailing": "\n"}


def _body(doc: dict) -> bytes:
    return (json.dumps(doc, separators=(STYLE["item_sep"], STYLE["kv_sep"]))
            + STYLE["trailing"]).encode()


def _record(doc: dict) -> dict:
    """按 mock/bodycapture.py 的记录格式包一层。"""
    return {"t": "2026-11-21T02:03:09", "method": "POST",
            "path": "/api/Account/Login", "shape": shape_of(_body(doc))}


BODY_B = {
    "buildIdentifier": "X" * 61,
    "publicKey": "Y" * 44,
    "platform": 3,
    "instanceId": "123456",
    "token": "T" * 116,
    "clientVersion": 1007203,
}

BODY_A = {
    "isSuspended": False,
    "publicKey": "Y" * 44,
    "platform": 3,
    "isBanned": False,
    "instanceId": "123456",
    "token": "T" * 116,
    "betaToolkitAccountId": "9" * 19,
    "buildIdentifier": "X" * 61,
    "clientVersion": 1007203,
}


def test_predict_len_round_trips_real_style():
    """schema -> 预测长度，必须等于真实 body 字节数（B 346 / A 437 的合成版）。"""
    for doc in (BODY_B, BODY_A):
        body = _body(doc)
        assert predict_len(_core(_unwrap(_record(doc)))) == len(body)


def test_predict_len_rejects_non_json_record():
    rec = {"shape": {"bytes": 3, "format": "non-json"}}
    with pytest.raises(ValueError):
        _core(_unwrap(rec))


def test_compare_explains_91_byte_delta():
    r = compare(_record(BODY_A), _record(BODY_B), "A", "B")
    assert r["delta"] == len(_body(BODY_A)) - len(_body(BODY_B)) == 91
    assert r["reconciles"] is True
    assert r["common_diff"] == []          # 共同字段长度一个都没变
    assert [f["key"] for f in r["only_in_a"]] == [
        "isSuspended", "isBanned", "betaToolkitAccountId"]
    assert r["only_in_b"] == []
    assert r["attribution"]["sum_check"] is True
    assert r["attribution"]["extra_fields_json"] + \
        r["attribution"]["extra_fields_style"] == 91


def test_compare_is_symmetric_on_identical_bodies():
    r = compare(_record(BODY_B), _record(BODY_B), "B1", "B2")
    assert r["delta"] == 0
    assert r["only_in_a"] == [] and r["only_in_b"] == []
    assert r["common_diff"] == []
    assert r["reconciles"] is True
