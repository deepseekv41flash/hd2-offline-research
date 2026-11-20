"""回放矩阵：**多份**真实抓包都要求 100% 命中 + 时间字段全部有规则 + 响应可解析。

这是「离线单机 mock」最重要的回归门禁：换一个账号/换一次登录，端点集合若变化，
这里会立刻失败并列出缺失端点（= 下一次抓包要补的清单）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mock.capture import CaptureStore
from mock.handler import MockHandler
from replay_client import method_for

CAPTURES = {
    "store-season-20261118": Path("D:/mod/tmp/capture-store-season-20261118/raw"),
    "new-account-20261117": Path("D:/mod/tmp/capture-new-account-20261117/raw"),
}


@pytest.mark.parametrize("name", sorted(CAPTURES))
def test_capture_replays_completely(name: str):
    src = CAPTURES[name]
    if not (src / "index.txt").exists():
        pytest.skip(f"抓包不存在: {src}")
    store = CaptureStore.load(src)
    h = MockHandler(store, now_fn=lambda: datetime.now(timezone.utc))
    missed, bad_status, bad_json = [], [], []
    for e in store.entries:
        method = method_for(e.base_path)
        r = h.handle(method, e.raw_path)
        if r.source.startswith("fallback"):
            missed.append(f"{method} {e.raw_path}")
        if r.status not in (200, 201, 202, 204):
            bad_status.append(f"{method} {e.raw_path} -> {r.status}")
        if e.kind == "json" and r.body:
            try:
                json.loads(r.body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                bad_json.append(e.raw_path)
    assert missed == [], f"{name} 缺失端点: {missed}"
    assert bad_status == [], f"{name} 状态码异常: {bad_status}"
    assert bad_json == [], f"{name} JSON 解析失败: {bad_json}"
    assert h.unknown_time_fields == set(), \
        f"{name} 有未识别时间字段（下次抓包可扩展规则）: {sorted(h.unknown_time_fields)}"
    assert len(store.entries) >= 50


def test_replay_is_deterministic_for_same_clock(real_capture: Path):
    store = CaptureStore.load(real_capture)
    fixed = datetime(2026, 2, 1, tzinfo=timezone.utc)
    a = MockHandler(store, now_fn=lambda: fixed)
    b = MockHandler(store, now_fn=lambda: fixed)
    for e in store.entries:
        m = method_for(e.base_path)
        ra = a.handle(m, e.raw_path)
        rb = b.handle(m, e.raw_path)
        assert ra.status == rb.status
        if e.base_path != "/api/Account/Login":  # 登录每次发新 sessionId
            assert ra.body == rb.body, e.raw_path


def test_time_advances_between_two_polls(real_capture: Path):
    store = CaptureStore.load(real_capture)
    t0 = datetime.now(timezone.utc)
    holder = {"now": t0}
    h = MockHandler(store, now_fn=lambda: holder["now"])
    first = h.handle("GET", "/api/WarSeason/801/Status").json()["time"]
    holder["now"] = t0.replace(microsecond=0) + timedelta(seconds=900)
    second = h.handle("GET", "/api/WarSeason/801/Status").json()["time"]
    assert second - first == 900, (first, second)


def test_login_rotates_session_but_static_replay_stable(real_capture: Path):
    store = CaptureStore.load(real_capture)
    h = MockHandler(store)
    s1 = h.handle("POST", "/api/Account/Login", b"").json()["sessionId"]
    s2 = h.handle("POST", "/api/Account/Login", b"").json()["sessionId"]
    assert s1 != s2
    a = h.handle("GET", "/api/WarSeason/GalacticWarEffects").body
    b = h.handle("GET", "/api/WarSeason/GalacticWarEffects").body
    assert a == b  # 静态目录两次回放一致
