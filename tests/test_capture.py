"""capture 解析：index.txt → 条目 / 响应体 / 战争时钟锚点。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mock.capture import CaptureStore, parse_index, slugify

from conftest import SYNTHETIC, write_capture


def test_slugify_matches_product_rule():
    assert slugify("/api/WarSeason/801/Status") == "api_WarSeason_801_Status"
    long_path = "/api/" + "x" * 200
    assert len(slugify(long_path)) <= 60


def test_parse_index_and_bodies(synthetic_capture: Path):
    entries = parse_index(synthetic_capture)
    assert len(entries) == len(SYNTHETIC)
    first = entries[0]
    assert first.base_path == "/api/Account/Login"
    assert first.raw_path == "/api/Account/Login"
    assert first.captured_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert first.kind == "json"

    store = CaptureStore.load(synthetic_capture)
    e = store.find(base_path="/api/WarSeason/801/Status")
    assert e is not None and e.doc is not None
    assert e.doc["time"] == 1_000_000
    binary = store.find(base_path="/api/Binary/Blob")
    assert binary is not None and binary.doc is None and binary.body == b"\x00\x01\x02"


def test_query_is_separated(synthetic_capture: Path):
    write_capture(synthetic_capture, [("20260101T000000Z",
                                       "/api/NewsFeed/801?maxEntries=16&fromTimeStamp=0",
                                       {"messages": []})])
    e = parse_index(synthetic_capture)[0]
    assert e.base_path == "/api/NewsFeed/801"
    assert e.query == "maxEntries=16&fromTimeStamp=0"


def test_war_anchors_two_independent_counters(synthetic_capture: Path):
    store = CaptureStore.load(synthetic_capture)
    anchors = store.war_anchors()
    assert anchors["war_clock"]["value"] == 1_000_000
    assert anchors["war_elapsed"]["value"] == 2_900_000
    # 两个计数器刻意不同 —— 不能用其中一个替代另一个
    assert anchors["war_clock"]["value"] != anchors["war_elapsed"]["value"]
    assert anchors["capture_first"] == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_real_capture_shape(real_capture: Path):
    store = CaptureStore.load(real_capture)
    assert len(store.entries) >= 70
    anchors = store.war_anchors()
    assert anchors["war_clock"]["value"] > 80_000_000
    assert anchors["war_elapsed"]["value"] > 80_000_000
    json_count = sum(1 for e in store.json_entries())
    assert json_count >= len(store.entries) - 1
