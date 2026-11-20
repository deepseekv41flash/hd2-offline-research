"""pytest 公共夹具：合成抓包 / 真实抓包 / 脱敏样本。"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from mock.capture import slugify  # noqa: E402

REAL_CAPTURE = Path("D:/mod/tmp/capture-store-season-20261118/raw")
SECOND_CAPTURE = Path("D:/mod/tmp/capture-new-account-20261117/raw")
MISSION_END_SRC = Path("D:/mod/out/gwe")


def write_capture(dst: Path, records: list[tuple[str, str, object]]) -> Path:
    """records: [(stamp, path, doc|bytes)] → 写一份产品格式的抓包目录。"""
    dst.mkdir(parents=True, exist_ok=True)
    lines = []
    for stamp, path, body in records:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        fname = f"{sha[:8]}_{slugify(path)}.json"
        (dst / fname).write_bytes(raw)
        kind = "json" if not isinstance(body, bytes) else "not-json"
        lines.append(f"{stamp}\t{path}\t{len(raw)}\t{sha}\t{kind}")
    (dst / "index.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dst


SYNTHETIC = [
    ("20260101T000000Z", "/api/Account/Login", {"sessionId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}),
    ("20260101T000001Z", "/api/WarSeason/current/WarId", {"id": 801}),
    ("20260101T000002Z", "/api/WarSeason/801/Status", {
        "warId": 801, "time": 1_000_000,
        "globalEvents": [{"id32": 1, "expireTime": 1_000_500}],
        "spaceStations": [{"id32": 2, "currentElectionEndWarTime": 1_000_400}],
        "planetStatus": [{"index": 0, "health": 1_000_000}],
        "campaigns": [], "planetEvents": [],
    }),
    ("20260101T000003Z", "/api/WarSeason/801/timeSinceStart", {"secondsSinceStart": 2_900_000}),
    ("20260101T000004Z", "/api/WarSeason/801/warinfo",
     {"warId": 801, "startDate": 1_700_000_000, "endDate": 1_800_000_000, "planetInfos": []}),
    ("20260101T000005Z", "/api/StoreFront/rotation",
     {"expireTime": 1, "salesPage": {"id32": 7}}),
    ("20260101T000006Z", "/api/Progression/Items/Customize/v2",
     {"items": [{"mixId": 42, "modes": [0, 0, 0, 0], "activeIndex": 0, "customizations": []}]}),
    ("20260101T000007Z", "/api/lobby/accounts", {"accountLobbies": [], "lobbies": []}),
    ("20260101T000008Z", "/api/Progression", {"experience": 1, "assignments": [{"expiresIn": 0}]}),
    ("20260101T000009Z", "/api/v2/Assignment/War/801",
     [{"id32": 9, "startTime": 999_000, "expiresIn": 60_000}]),
    ("20260101T000010Z", "/api/Binary/Blob", b"\x00\x01\x02"),
]


@pytest.fixture()
def synthetic_capture(tmp_path: Path) -> Path:
    return write_capture(tmp_path / "capture", SYNTHETIC)


@pytest.fixture(scope="session")
def real_capture() -> Path:
    if not (REAL_CAPTURE / "index.txt").exists():
        pytest.skip(f"真实抓包不存在: {REAL_CAPTURE}")
    return REAL_CAPTURE


@pytest.fixture(scope="session")
def second_capture() -> Path:
    if not (SECOND_CAPTURE / "index.txt").exists():
        pytest.skip(f"第二份抓包不存在: {SECOND_CAPTURE}")
    return SECOND_CAPTURE


@pytest.fixture()
def scrubbed_sample(tmp_path: Path, real_capture: Path) -> Path:
    from sample_build import build

    dst = tmp_path / "sample"
    build(real_capture, dst, scrub=True)
    return dst


def fixed_now() -> datetime:
    return datetime(2026, 1, 2, tzinfo=timezone.utc)
