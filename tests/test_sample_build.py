"""脱敏样本构建：原值不残留 + 结构保留 + 仍可回放。"""

from __future__ import annotations

import json
from pathlib import Path

from mock.capture import CaptureStore
from mock.handler import MockHandler
from sample_build import build, copy_mission_end

# 全部为**合成**测试值：结构与长度与真实抓包一致（107 字符 / 20 位账号），但不含任何真实数据。
# 目的：验证脱敏规则确实按「字段名 + 长度」处理，而不是靠碰巧。
SENSITIVE_CONN = ("cv2:00000000-1111-4222-8333-444455556666.r-20260323|619689|"
                  "kv1:" + "A" * 44)
SENSITIVE_NAME = "TestPlayer_Sensitive"
SENSITIVE_ACCT = "11111111111111111111"


def _capture_with_secrets(tmp: Path) -> Path:
    from conftest import write_capture

    records = [
        ("20260101T000000Z", "/api/Account/Login", {"sessionId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}),
        ("20260101T000001Z", "/api/lobby/accounts", {
            "accountLobbies": [{"account": SENSITIVE_ACCT, "lobbyId": "7262c34b-ccef-4a9c-82ad-cb66f67a46b0.r-20260323"}],
            "lobbies": [{
                "lobbyId": "7262c34b-ccef-4a9c-82ad-cb66f67a46b0.r-20260323",
                "connectionString": SENSITIVE_CONN,
                "numLobbyMembers": 1,
                "players": [{"memberAccountId": SENSITIVE_ACCT, "playerName": SENSITIVE_NAME,
                             "isHost": True, "hasInfo": True, "sourcePlatform": 1}],
                "seed": "f3fc7322d97df97b" * 8,
            }],
        }),
        ("20260101T000002Z", "/api/WarSeason/801/Status", {"warId": 801, "time": 1234}),
    ]
    return write_capture(tmp / "secrets", records)


def test_scrub_removes_all_originals_and_keeps_shape(tmp_path: Path):
    src = _capture_with_secrets(tmp_path)
    dst = tmp_path / "sample"
    report = build(src, dst, scrub=True)
    assert report["leak_check"] == "PASS", report["leaks"]
    assert report["scrubbed"] is True

    blob = b"".join(p.read_bytes() for p in dst.glob("*.json")).decode("utf-8")
    for secret in (SENSITIVE_CONN, SENSITIVE_NAME, SENSITIVE_ACCT,
                   "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"):
        assert secret not in blob, secret
    # 结构/长度保留：connectionString 仍是 cv2 骨架且长度一致
    lobby_file = [p for p in dst.glob("*lobby_accounts*")][0]
    doc = json.loads(lobby_file.read_text(encoding="utf-8"))
    scrubbed = doc["lobbies"][0]["connectionString"]
    assert scrubbed.startswith("cv2:") and "kv1:" in scrubbed
    assert len(scrubbed) == len(SENSITIVE_CONN)
    assert doc["lobbies"][0]["players"][0]["playerName"] == "Player"
    assert set(doc["lobbies"][0]["players"][0]["memberAccountId"]) == {"0"}


def test_scrubbed_sample_still_replays(tmp_path: Path):
    src = _capture_with_secrets(tmp_path)
    dst = tmp_path / "sample"
    build(src, dst, scrub=True)
    store = CaptureStore.load(dst)
    h = MockHandler(store)
    for e in store.entries:
        r = h.handle("POST" if "Login" in e.base_path else "GET", e.raw_path)
        assert r.status in (200, 202), f"{e.raw_path} -> {r.status}"
        assert not r.source.startswith("fallback"), e.raw_path


def test_index_timestamps_preserved(tmp_path: Path):
    src = _capture_with_secrets(tmp_path)
    dst = tmp_path / "sample"
    build(src, dst, scrub=True)
    src_store = CaptureStore.load(src)
    dst_store = CaptureStore.load(dst)
    assert [e.captured_at for e in src_store.entries] == [e.captured_at for e in dst_store.entries]
    assert [e.raw_path for e in src_store.entries] == [e.raw_path for e in dst_store.entries]


def test_real_capture_scrub_and_replay(tmp_path: Path, real_capture: Path):
    dst = tmp_path / "real-sample"
    report = build(real_capture, dst, scrub=True)
    assert report["leak_check"] == "PASS", report["leaks"]
    assert report["records"] >= 70
    store = CaptureStore.load(dst)
    h = MockHandler(store)
    fallbacks = [e.raw_path for e in store.entries
                 if h.handle("GET", e.raw_path).source.startswith("fallback")]
    assert fallbacks == []
    # 脱敏后所有响应仍是合法 JSON（除已知非 JSON）
    for e in store.entries:
        if e.kind == "json" and e.body:
            json.loads(e.body.decode("utf-8"))


def test_mission_end_template_scrubbed(tmp_path: Path):
    src = tmp_path / "gwe"
    src.mkdir()
    (src / "abc12345_api_Operation_Mission_end.json").write_text(json.dumps({
        "operationId": 1237, "missionIndex": 0, "missionId": "x",
        "experience": 258, "rewards": [{"mixId": 1, "amount": 2}],
        "progressionPackageUpdates": [{"platformAccountId": SENSITIVE_ACCT, "id32": 1}],
    }), encoding="utf-8")
    out = tmp_path / "mission-end"
    names = copy_mission_end(src, out)
    assert names == ["abc12345.json"]
    doc = json.loads((out / names[0]).read_text(encoding="utf-8"))
    assert doc["progressionPackageUpdates"][0]["platformAccountId"] != SENSITIVE_ACCT
    assert doc["rewards"] == [{"mixId": 1, "amount": 2}]
