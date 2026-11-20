"""时间平移：三个基准 + 未知字段上报 + 不修改入参。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mock.timeshift import (ELAPSED, KEEP, WAR, ShiftStats, elapsed_now,
                            shift_document, war_clock_now)

CAPTURED = datetime(2026, 1, 1, tzinfo=timezone.utc)
STATUS_PATH = "/api/WarSeason/801/Status"


def status_doc() -> dict:
    return {
        "warId": 801, "time": 1_000_000, "impactMultiplier": 0.5,
        "globalEvents": [{"id32": 1, "expireTime": 1_000_500, "flag": 3}],
        "planetEvents": [{"id32": 2, "startTime": 999_000, "expireTime": 1_005_000}],
        "spaceStations": [{"id32": 3, "currentElectionEndWarTime": 1_000_400}],
        "planetStatus": [{"index": 0, "health": 1_000_000}],
    }


def test_war_fields_shift_by_elapsed():
    now = CAPTURED + timedelta(seconds=3600)
    out, st = shift_document(status_doc(), path=STATUS_PATH, captured_at=CAPTURED, now=now)
    assert st.delta_seconds == 3600
    assert out["time"] == 1_000_000 + 3600
    assert out["globalEvents"][0]["expireTime"] == 1_000_500 + 3600
    assert out["planetEvents"][0]["startTime"] == 999_000 + 3600
    assert out["planetEvents"][0]["expireTime"] == 1_005_000 + 3600
    assert out["spaceStations"][0]["currentElectionEndWarTime"] == 1_000_400 + 3600
    # 非时间字段不动
    assert out["planetStatus"][0]["health"] == 1_000_000
    assert out["impactMultiplier"] == 0.5
    assert len(st.shifted) == 5


def test_does_not_mutate_input():
    doc = status_doc()
    shift_document(doc, path=STATUS_PATH, captured_at=CAPTURED,
                   now=CAPTURED + timedelta(seconds=10))
    assert doc["time"] == 1_000_000
    assert doc["globalEvents"][0]["expireTime"] == 1_000_500


def test_second_counter_uses_elapsed_base():
    now = CAPTURED + timedelta(seconds=100)
    out, st = shift_document({"secondsSinceStart": 2_900_000},
                             path="/api/WarSeason/801/timeSinceStart",
                             captured_at=CAPTURED, now=now)
    assert out["secondsSinceStart"] == 2_900_100
    assert {b for _p, b, _o, _n in st.shifted} == {ELAPSED}


def test_warinfo_dates_are_kept():
    now = CAPTURED + timedelta(days=1)
    out, st = shift_document({"startDate": 1_700_000_000, "endDate": 1_800_000_000},
                             path="/api/WarSeason/801/warinfo", captured_at=CAPTURED, now=now)
    assert out["startDate"] == 1_700_000_000
    assert out["endDate"] == 1_800_000_000
    assert st.shifted == []
    assert {b for _p, b in st.kept} == {KEEP}


def test_assignment_start_shifts_but_expires_in_is_kept():
    now = CAPTURED + timedelta(seconds=600)
    out, st = shift_document([{"id32": 9, "startTime": 999_000, "expiresIn": 60_000}],
                             path="/api/v2/Assignment/War/801", captured_at=CAPTURED, now=now)
    assert out[0]["startTime"] == 999_600
    assert out[0]["expiresIn"] == 60_000  # 倒计时保持 => 订单仍然「还有 60000 秒」


def test_store_rotation_refreshed_only_when_past():
    now = CAPTURED
    stale, st = shift_document({"expireTime": 1}, path="/api/StoreFront/rotation",
                               captured_at=CAPTURED, now=now, refresh_span_seconds=86400)
    assert stale["expireTime"] > int(now.timestamp())
    assert len(st.refreshed) == 1

    future = int(now.timestamp()) + 10_000
    kept, st2 = shift_document({"expireTime": future}, path="/api/StoreFront/rotation",
                               captured_at=CAPTURED, now=now)
    assert kept["expireTime"] == future
    assert not st2.refreshed


def test_unknown_time_like_field_reported_but_untouched():
    doc = {"planetRegions": [{"health": 400000}], "mysteryValue": 1,
           "someFutureTime": 123456}
    out, st = shift_document(doc, path="/api/WarSeason/801/Status",
                             captured_at=CAPTURED, now=CAPTURED + timedelta(seconds=5))
    assert out["someFutureTime"] == 123456
    assert ("someFutureTime", "someFutureTime", 123456) in st.unknown
    assert st.summary()["unknown"] == 1


def test_duration_fields_not_shifted():
    out, st = shift_document({"totalMissionTime": 2622175, "expiresIn": 0},
                             path="/api/Stats/profile/summary",
                             captured_at=CAPTURED, now=CAPTURED + timedelta(seconds=60))
    assert out["totalMissionTime"] == 2622175
    assert out["expiresIn"] == 0
    assert st.shifted == []


def test_war_clock_helpers():
    anchors = {
        "war_clock": {"value": 1_000_000, "at": CAPTURED, "path": STATUS_PATH},
        "war_elapsed": {"value": 2_900_000, "at": CAPTURED,
                        "path": "/api/WarSeason/801/timeSinceStart"},
    }
    later = CAPTURED + timedelta(seconds=120)
    assert war_clock_now(anchors, later) == 1_000_120
    assert elapsed_now(anchors, later) == 2_900_120
    assert war_clock_now({}, later) is None


def test_stats_default_isolated():
    a = ShiftStats()
    b = ShiftStats()
    a.shifted.append(("p", WAR, 1, 2))
    assert b.shifted == []
