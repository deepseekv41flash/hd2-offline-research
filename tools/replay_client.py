#!/usr/bin/env python3
"""离线验证：用抓包样本回放「登录 + 主界面 + 任务链」，**不需要启动游戏**。

三种模式:
  handler  （默认）在进程内把抓包条目按原顺序喂给 mock handler：
           验证「每个抓到的端点都被接住（fallback=0）+ 时间字段按预期平移」。
  scenario 脚本化一条完整启动链 + 任务链 + 大厅链，逐步断言响应语义。
  server   对**正在运行的 mock 服务**（TLS 443/自检端口）回放同一批请求，
           验证 socket/TLS/HTTP 层也通。

用法:
    python tools/replay_client.py --src D:/mod/tmp/capture-store-season-20261118/raw
    python tools/replay_client.py --src ... --mode scenario
    python tools/replay_client.py --src ... --mode server --url 127.0.0.1:18443 --ca <ca.pem>

退出码 0 = 全 PASS。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mock.capture import CaptureStore  # noqa: E402
from mock.client import HttpsClient  # noqa: E402
from mock.handler import MockHandler  # noqa: E402

SNI_HOST = "api.live.prod.thehelldiversgame.com"

POST_PATTERNS = [
    r"^/api/Account/Login$",
    r"^/api/Account/ReportPosition$",
    r"^/api/Operation/(Create|Abandon)$",
    r"^/api/Operation/Mission/(Start|end|SetMaxRewards|ActivityV2)$",
    r"^/api/lobby/accounts$",
    r"^/api/lobby/[^/]+/member$",
    r"^/api/Pes/",
    r"^/api/Mission/Session$",
]
PUT_PATTERNS = [r"^/api/lobby$", r"^/api/Progression/Items/Customize/v2$"]
DELETE_PATTERNS = [r"^/api/lobby/clear$"]


def method_for(base_path: str) -> str:
    for pat in POST_PATTERNS:
        if re.match(pat, base_path):
            return "POST"
    for pat in PUT_PATTERNS:
        if re.match(pat, base_path):
            # 改装保存：抓包里 GET 更常见，按 GET 回放（state 路由只拦 PUT）
            if base_path == "/api/Progression/Items/Customize/v2":
                return "GET"
            return "PUT"
    for pat in DELETE_PATTERNS:
        if re.match(pat, base_path):
            return "DELETE"
    return "GET"


class Checks:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> list[dict]:
        return [i for i in self.items if not i["ok"]]

    def summary(self) -> dict:
        return {"total": len(self.items), "passed": len(self.items) - len(self.failed),
                "failed": len(self.failed)}


def build_handler(src: Path, *, fixed_now: datetime | None = None,
                  profile: dict | None = None,
                  mission_end_dir: Path | None = None) -> MockHandler:
    store = CaptureStore.load(src)
    template = None
    customize = None
    if mission_end_dir and mission_end_dir.exists():
        cands = sorted(mission_end_dir.glob("*.json"))
        if cands:
            template = json.loads(cands[0].read_text(encoding="utf-8"))
    entry = store.find(base_path="/api/Progression/Items/Customize/v2")
    if entry and entry.doc is not None:
        customize = entry.doc
    now_fn = (lambda: fixed_now) if fixed_now else None
    return MockHandler(store, profile=profile, now_fn=now_fn,
                       mission_end_template=template, customize_template=customize)


# ---------------------------------------------------------------- handler 模式
def mode_handler(args, checks: Checks) -> int:
    store = CaptureStore.load(args.src)
    now = _fixed_now(args) or datetime.now(timezone.utc)
    handler = build_handler(args.src, fixed_now=now, profile=args.profile,
                            mission_end_dir=args.mission_end)
    total = 0
    fallbacks = []
    bad_status = []
    bad_json = []
    for e in store.entries:
        method = method_for(e.base_path)
        resp = handler.handle(method, e.raw_path)
        total += 1
        if resp.source.startswith("fallback"):
            fallbacks.append(f"{method} {e.raw_path}")
        if resp.status not in (200, 201, 202, 204):
            bad_status.append(f"{method} {e.raw_path} -> {resp.status}")
        if e.kind == "json" and resp.body:
            try:
                json.loads(resp.body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                bad_json.append(f"{method} {e.raw_path}")
    checks.add(f"抓包 {total} 条响应全部被 mock 接住（fallback=0）", not fallbacks,
               f"fallback: {fallbacks[:5]}" if fallbacks else f"{total}/{total} 命中")
    checks.add("状态码全部 2xx", not bad_status, str(bad_status[:5]))
    checks.add("JSON 响应全部可解析", not bad_json, str(bad_json[:5]))
    checks.add("未出现未识别时间字段（下次抓包可扩展规则）",
               not handler.unknown_time_fields,
               str(sorted(handler.unknown_time_fields)[:5]))

    now = _fixed_now(args) or datetime.now(timezone.utc)
    # ---- 时间平移正确性（用 handler 实际命中的那条抓包做锚点）----
    status_entry = handler.index.lookup("GET", "/api/WarSeason/801/Status")
    status_entry = status_entry.entry if status_entry else None
    if status_entry and status_entry.doc is not None:
        got = handler.handle("GET", status_entry.raw_path).json()
        expected = status_entry.doc["time"] + int((now - status_entry.captured_at).total_seconds())
        checks.add("Status.time 按抓包时刻平移到当前战争时钟",
                   abs(got["time"] - expected) <= 3,
                   f"recorded={status_entry.doc['time']} got={got['time']} expected≈{expected}")
        checks.add("Status 时间平移后仍在抓包 warId 上", got.get("warId") == status_entry.doc.get("warId"),
                   f"warId={got.get('warId')}")
    tss_entry = handler.index.lookup("GET", "/api/WarSeason/801/timeSinceStart")
    tss_entry = tss_entry.entry if tss_entry else None
    if tss_entry and tss_entry.doc is not None:
        got = handler.handle("GET", tss_entry.raw_path).json()
        expected = tss_entry.doc["secondsSinceStart"] + int(
            (now - tss_entry.captured_at).total_seconds())
        checks.add("timeSinceStart 独立计数器同样平移",
                   abs(got["secondsSinceStart"] - expected) <= 3,
                   f"recorded={tss_entry.doc['secondsSinceStart']} got={got['secondsSinceStart']}")
    # ---- 登录 ----
    login = handler.handle("POST", "/api/Account/Login", b"")
    try:
        sid = login.json()["sessionId"]
        uuid.UUID(sid)
        ok = True
    except Exception:  # noqa: BLE001
        sid, ok = "", False
    checks.add("Account/Login 返回合法 sessionId", ok, sid)
    # ---- 商店轮换不过期 ----
    rot = handler.handle("GET", "/api/StoreFront/rotation")
    try:
        expire = rot.json()["expireTime"]
        checks.add("StoreFront/rotation 过期时间不在过去", expire > int(now.timestamp()),
                   f"expireTime={expire} now={int(now.timestamp())}")
    except Exception as exc:  # noqa: BLE001
        checks.add("StoreFront/rotation 可解析", False, repr(exc))
    # ---- 未知端点 fallback ----
    unknown = handler.handle("GET", "/api/NotARealEndpoint")
    checks.add("未知端点走 fallback 且不 4xx", unknown.status == 200,
               f"{unknown.status} {unknown.text[:40]}")
    h = handler.health()
    checks.add("health 统计与索引自洽", h["index"]["base"] >= 60,
               f"index={h['index']} stats={h['stats']}")
    return 0 if not checks.failed else 1


# ---------------------------------------------------------------- scenario 模式
def mode_scenario(args, checks: Checks) -> int:
    now = _fixed_now(args) or datetime.now(timezone.utc)
    handler = build_handler(args.src, fixed_now=now,
                            profile={"lobby": {"mode": "state", "echo_put": False}},
                            mission_end_dir=args.mission_end)
    st = handler.state
    st.account_id = "76561190000000000"

    def call(method: str, path: str, body=None):
        payload = b"" if body is None else (
            body.encode() if isinstance(body, str) else json.dumps(body).encode())
        return handler.handle(method, path, payload)

    def expect_json(name: str, method: str, path: str, body=None, keys=()):
        r = call(method, path, body)
        ok = r.status in (200, 202) and not r.source.startswith("fallback")
        detail = f"{r.status} [{r.source}]"
        if ok and keys:
            try:
                doc = r.json()
                missing = [k for k in keys if k not in doc]
                ok = not missing
                detail += f" keys_ok={not missing}" + (f" missing={missing}" if missing else "")
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"{r.status} json_error={exc!r}"
        checks.add(name, ok, detail)
        return r

    # ---- 主界面启动链 ----
    expect_json("① 登录", "POST", "/api/Account/Login", keys=("sessionId",))
    expect_json("② 客户端配置", "GET", "/api/Configuration/GameClient",
                keys=("featureConfiguration", "pollingConfiguration"))
    wid = expect_json("③ 当前 warId", "GET", "/api/WarSeason/current/WarId", keys=("id",))
    checks.add("warId = 801", wid.json().get("id") == 801, str(wid.json()))
    st_resp = expect_json("④ 战况板 Status", "GET", "/api/WarSeason/801/Status",
                          keys=("warId", "time", "planetStatus", "campaigns"))
    expect_json("⑤ warinfo", "GET", "/api/WarSeason/801/warinfo", keys=("planetInfos",))
    expect_json("⑥ 战争已进行秒数", "GET", "/api/WarSeason/801/timeSinceStart",
                keys=("secondsSinceStart",))
    expect_json("⑦ GalacticWarEffects", "GET", "/api/WarSeason/GalacticWarEffects")
    expect_json("⑧ NewsTicker", "GET", "/api/WarSeason/NewsTicker", keys=("messages",))
    for path in ("/api/Progression", "/api/Progression/items", "/api/Progression/inventory",
                 "/api/Progression/levelspec", "/api/Progression/ItemPackages",
                 "/api/Progression/ProgressionPackages", "/api/Progression/Items/Customize/v2"):
        expect_json(f"养成 {path}", "GET", path)
    expect_json("大厅列表", "GET", "/api/lobby/accounts")
    expect_json("商店首页", "GET", "/api/StoreFront")
    expect_json("商店轮换", "GET", "/api/StoreFront/rotation", keys=("expireTime",))
    expect_json("战争债券列表", "GET", "/api/SeasonPass")
    expect_json("结算奖励表", "GET", "/api/Mission/RewardEntries")
    expect_json("MajorOrder(war)", "GET", "/api/v2/Assignment/War/801")
    expect_json("每日命令(player)", "GET", "/api/v2/Assignment/Player")
    expect_json("赛季 Episode", "GET", "/api/Episode/801/status?maxEntries=1", keys=("episodes",))
    expect_json("好友请求列表", "GET", "/api/FriendsV2/Request?offset=0&limit=100")
    expect_json("新闻流", "GET", "/api/NewsFeed/801?maxEntries=16&fromTimeStamp=0")
    expect_json("邮件", "POST", "/api/Mail/inbox", {})
    expect_json("全局战况", "GET", "/api/Stats/war/801/summary")
    expect_json("个人战绩", "GET", "/api/Stats/profile/summary")

    # ---- 任务链 ----
    opl = expect_json("作战列表", "GET", "/api/Operation", keys=("nextOperationId", "operations"))
    next_id = opl.json()["nextOperationId"]
    op_body = {"operations": [{
        "operationSettingsId": 538077316, "jointOperationId": -1, "type": 0,
        "id": next_id + 1, "seed": 123456789, "race": 2, "warId": 801,
        "placementNodeIndex": 3, "campaignId": 51715, "planetIndex": 268,
        "difficulty": 1, "missions": [{"type": 1, "state": 1, "index": 0}],
    }]}
    created = call("POST", "/api/Operation/Create", op_body)
    try:
        new_id = created.json()["operations"][0]["id"]
        ok = new_id == next_id + 1
    except Exception:  # noqa: BLE001
        new_id, ok = -1, False
    checks.add("Operation/Create 回显客户端提出的 id", ok,
               f"client={next_id + 1} resp={new_id} [{created.source}]")
    opl2 = call("GET", "/api/Operation").json()
    checks.add("创建后的作战出现在 Operation 列表",
               any(o.get("id") == new_id for o in opl2.get("operations", [])),
               f"ops={[o.get('id') for o in opl2.get('operations', [])]}")
    start = call("POST", "/api/Operation/Mission/Start",
                 {"planetIndex": 268, "warId": 801, "regionIndex": -1,
                  "operationId": new_id, "missionIndex": 0, "territoryIndex": 12,
                  "participants": ["11111111111111111111"]})
    try:
        mission_id = start.json()["missionId"]
        uuid.UUID(mission_id)
        ok = True
    except Exception:  # noqa: BLE001
        mission_id, ok = "", False
    checks.add("Mission/Start 分配合法 missionId", ok, mission_id)
    call("POST", "/api/Operation/Mission/SetMaxRewards",
         {"operationId": new_id, "maxRewards": 4})
    call("POST", "/api/Operation/Mission/ActivityV2",
         {"uniqueId": 1, "activityId32": 2, "missionId": mission_id})
    sess = call("POST", "/api/Mission/Session", {"operationId": new_id, "type": 1})
    checks.add("Mission/Session 返回 202", sess.status == 202, f"{sess.status}")
    end = call("POST", "/api/Operation/Mission/end",
               {"warSeasonId": 801, "type": 3, "planetIndex": 268, "campaignId": 51715,
                "objectives": [{"id": 1, "succeeded": True}], "operationId": new_id,
                "missionIndex": 0, "missionId": mission_id})
    try:
        end_doc = end.json()
        ok = (end_doc.get("operationId") == new_id and end_doc.get("missionId") == mission_id
              and "rewards" in end_doc)
        detail = (f"operationId={end_doc.get('operationId')} "
                  f"missionId_match={end_doc.get('missionId') == mission_id} "
                  f"rewards={len(end_doc.get('rewards') or [])} [{end.source}]")
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, repr(exc)
    checks.add("Mission/end 回填 operationId/missionId 且带 rewards", ok, detail)
    accs = {u.get("platformAccountId") for u in (end_doc.get("progressionPackageUpdates") or [])
            if isinstance(u, dict)} if ok else set()
    checks.add("结算里的 platformAccountId 已换成 mock 账号",
               accs <= {st.account_id}, f"accountIds={accs}")
    checks.add("结算不虚构社区战果（othersImpact*=0）",
               all((end_doc.get(k) or 0) == 0 for k in
                   ("othersImpactPlanet", "othersImpactCommunityTarget",
                    "othersImpactPlanetEvent")) if ok else False,
               f"othersImpactPlanet={end_doc.get('othersImpactPlanet') if ok else 'n/a'}")
    ab = call("POST", "/api/Operation/Abandon", {"operationId": new_id})
    checks.add("Abandon 后作战从列表移除",
               all(o.get("id") != new_id for o in call("GET", "/api/Operation").json()["operations"]),
               f"status={ab.status}")

    # ---- 大厅链（自建联机的信令层最小闭环）----
    lobby_body = {
        "startTime": "0", "lobbyId": "11111111-2222-4333-8444-555555555555.r-00000000",
        "players": [
            {"sourcePlatform": 1, "memberAccountId": st.account_id, "isHost": True,
             "playerName": "Helldiver", "playerRank": 199, "experience": 2759536,
             "hasInfo": True, "commendRank": 0},
            {"sourcePlatform": 5, "memberAccountId": "0", "isHost": False, "playerName": "",
             "playerRank": 0, "experience": 0, "hasInfo": False, "commendRank": 0},
        ],
        "connectionString": "cv2:11111111-2222-4333-8444-555555555555.r-00000000|000000|kv1:AAAA",
        "memberAccountIds": [None, st.account_id], "inLoadout": False,
        "buildGameHash": "0000000000000000", "warId": -1, "state": 1, "difficulty": 0,
        "hostability": 0, "privacyMode": 2, "onboardingState": 0, "numLobbyMembers": 1,
        "sosBeacons": 0, "planetId": -1, "territoryId": -1, "race": 0,
        "crossplayMode": 1, "preferenceGroup": 0, "regionIndex": -1,
    }
    put = call("PUT", "/api/lobby", lobby_body)
    checks.add("PUT /api/lobby 收下并存储", put.status == 200, f"{put.status} [{put.source}]")
    got = call("GET", "/api/lobby/" + lobby_body["lobbyId"])
    try:
        got_doc = got.json()
        ok = (got_doc.get("lobbyId") == lobby_body["lobbyId"]
              and got_doc.get("connectionString") == lobby_body["connectionString"])
        detail = f"lobbyId_match={got_doc.get('lobbyId') == lobby_body['lobbyId']} " \
                 f"connstring_forwarded={got_doc.get('connectionString') == lobby_body['connectionString']}"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, repr(exc)
    checks.add("GET /api/lobby/{id} 原样返回 connectionString（自建信令核心）", ok, detail)
    joined = call("POST", "/api/lobby/" + lobby_body["lobbyId"] + "/member",
                  {"memberAccountId": "76561190000000001"})
    try:
        jdoc = joined.json()
        ok = jdoc.get("numLobbyMembers") == 2
    except Exception:  # noqa: BLE001
        jdoc, ok = {}, False
    checks.add("POST /api/lobby/{id}/member 加入后成员数 +1", ok,
               f"numLobbyMembers={jdoc.get('numLobbyMembers')}")
    accs2 = call("GET", "/api/lobby/accounts").json()
    checks.add("GET /api/lobby/accounts 反映自建大厅",
               any(l.get("lobbyId") == lobby_body["lobbyId"] for l in accs2.get("lobbies", [])),
               f"lobbies={[l.get('lobbyId') for l in accs2.get('lobbies', [])]}")
    cleared = call("DELETE", "/api/lobby/clear")
    checks.add("DELETE /api/lobby/clear 清空", cleared.status == 200
               and not call("GET", "/api/lobby/accounts").json().get("lobbies"),
               f"{cleared.status}")

    # ---- 杂项 ----
    checks.add("ReportPosition 返回 200",
               call("POST", "/api/Account/ReportPosition", {"x": 1}).status == 200)
    checks.add("未知端点不返回 4xx",
               call("GET", "/api/NotARealEndpoint").status == 200)
    checks.add("根路径可访问", call("GET", "/").status == 200)
    return 0 if not checks.failed else 1


# ---------------------------------------------------------------- server 模式
def mode_server(args, checks: Checks) -> int:
    store = CaptureStore.load(args.src)
    host, _, port = args.url.rpartition(":")
    client = HttpsClient(host or "127.0.0.1", int(port or 443), sni_host=SNI_HOST,
                         ca_file=args.ca, insecure=args.insecure)
    total, bad, fallback = 0, [], []
    for e in store.entries:
        method = method_for(e.base_path)
        r = client.request(method, e.raw_path)
        total += 1
        if r.error:
            bad.append(f"{method} {e.raw_path} err={r.error}")
            continue
        if r.status not in (200, 201, 202, 204):
            bad.append(f"{method} {e.raw_path} -> {r.status}")
        src = r.headers.get("x-mock-source", "")
        if src.startswith("fallback"):
            fallback.append(f"{method} {e.raw_path}")
    checks.add(f"TLS 回放 {total} 条请求全部成功", not bad, str(bad[:5]))
    checks.add("服务端 fallback=0（全部命中回放）", not fallback, str(fallback[:5]))
    health = client.request("GET", "/health")
    checks.add("GET /health 可读", health.status == 200 and health.json().get("ok") is True,
               f"{health.status} {health.text[:80]}")
    war = client.request("GET", "/api/WarSeason/current/WarId")
    checks.add("TLS 下的 warId 响应正确", war.status == 200 and war.json().get("id") == 801,
               war.text[:60])
    login = client.request("POST", "/api/Account/Login", b"")
    try:
        sid = login.json()["sessionId"]
        uuid.UUID(sid)
        ok = True
    except Exception:  # noqa: BLE001
        sid, ok = "", False
    checks.add("TLS 下登录发 sessionId", ok, sid)
    return 0 if not checks.failed else 1


# ---------------------------------------------------------------- 公共
def _fixed_now(args) -> datetime | None:
    if getattr(args, "fixed_now", None):
        return datetime.fromisoformat(args.fixed_now.replace("Z", "+00:00"))
    if getattr(args, "now_offset_seconds", 0):
        return datetime.now(timezone.utc) + timedelta(seconds=args.now_offset_seconds)
    return None


def load_profile(path: Path | None) -> dict | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True, help="抓包目录（含 index.txt）")
    ap.add_argument("--mode", choices=("handler", "scenario", "server"), default="handler")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--profile", type=Path, default=None)
    ap.add_argument("--mission-end", type=Path, default=REPO / "samples" / "mission-end")
    ap.add_argument("--fixed-now", default=None, help="固定 now（ISO8601），用于确定性断言")
    ap.add_argument("--now-offset-seconds", type=int, default=0)
    ap.add_argument("--url", default="127.0.0.1:18443", help="server 模式：host:port")
    ap.add_argument("--ca", default=str(REPO / "state" / "certs" / "ca.pem"))
    ap.add_argument("--insecure", action="store_true", help="server 模式跳过证书校验")
    args = ap.parse_args(argv)
    args.profile = load_profile(args.profile)
    checks = Checks()
    runner = {"handler": mode_handler, "scenario": mode_scenario, "server": mode_server}[args.mode]
    try:
        code = runner(args, checks)
    except Exception as exc:  # noqa: BLE001 —— 让失败也能产出结构化结果
        checks.add(f"{args.mode} 模式执行异常", False, repr(exc))
        code = 1
    summary = checks.summary()
    print(f"\nSUMMARY mode={args.mode}: {summary['passed']} passed, "
          f"{summary['failed']} failed, {summary['total']} total")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"mode": args.mode, "summary": summary,
                                         "checks": checks.items, "ok": code == 0},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写 {args.json}")
    return code


if __name__ == "__main__":
    sys.exit(main())
