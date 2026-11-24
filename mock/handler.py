"""纯逻辑路由：(method, path, body) → Response。无 socket，可直接单测。

路由优先级:
  1. ``/health``（自身诊断，游戏不会请求）
  2. 有状态端点（登录 / 大厅 / 作战 / 任务链，见 :mod:`mock.rules` STATE_ROUTES）
  3. 抓包回放（exact → base → template，见 :mod:`mock.rules`）
  4. fallback（未知 /api/* 默认 200 `{}` —— 比 404 更安全，不打断客户端轮询）

时间字段在回放时按「该响应自己的抓包时刻 → 现在」做平移，见 :mod:`mock.timeshift`。
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from . import discovery, timeshift
from .capture import CaptureStore, CapturedEntry
from .rules import Match, ReplayIndex, match_state_route, split_path
from .players import PlayerStore
from .state import MockState


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "application/json"
    source: str = ""            # 命中来源，便于日志/断言：state:xxx / exact / base / template / fallback

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None


DEFAULT_PROFILE: dict = {
    "timeshift": {"enabled": True, "refresh_span_seconds": 7 * 86400},
    "fallback": {"status": 200, "body": "{}"},
    "lobby": {"mode": "replay", "echo_put": False,
              # discovery 模式（阶段2 联机发现层）专用：
              "ttl_seconds": 600, "max_members": 4},

    "mission": {"keep_template_outcome": False},
    "identity": {"account_id": "76561190000000000", "player_name": "Helldiver"},
}


def _account_hint(headers: dict) -> str:
    """显式账号头：`X-HD2-Account`（我方工具/探针用；真实客户端没有该头，走 IP 归属）。"""
    try:
        return str((headers or {}).get("x-hd2-account") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _query_ids(query: str) -> list[str]:
    """从 query 串里取 `id=<accountId>`（支持重复 `&id=` 与逗号分隔 `id=a,b`，保序去空）。

    `GET /api/Account/Keys` 用它点名要哪些账号的公钥；真实客户端一次只要一个。
    """
    out: list[str] = []
    for pair in (query or "").split("&"):
        key, _, value = pair.partition("=")
        if key.strip().lower() != "id":
            continue
        for item in value.split(","):
            ident = item.strip()
            if ident and ident not in out:
                out.append(ident)
    return out


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_json_body(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def _closed_lobby_body(res: dict) -> dict:
    """未知/已关闭大厅的终态 body（⚠ 字段取值待真实客户端校准，见设计档 §5）。"""
    return {
        "lobbyId": str(res.get("lobby_id") or ""),
        "players": [],
        "memberAccountIds": [],
        "numLobbyMembers": 0,
        "state": 0,
    }


class MockHandler:
    """把一个抓包快照变成「本地云端」。"""

    def __init__(
        self,
        store: CaptureStore,
        *,
        profile: dict | None = None,
        state: MockState | None = None,
        now_fn: Callable[[], datetime] | None = None,
        mission_end_template: dict | None = None,
        customize_template: dict | None = None,
        players_dir: Path | str | None = None,
    ):
        self.store = store
        # 按玩家分档覆盖层（自建服形态）：None = 关闭，全部走公共抓包数据
        self.players = PlayerStore(players_dir) if players_dir else None
        self.index = ReplayIndex(store)
        self.profile = _deep_merge(DEFAULT_PROFILE, profile or {})
        ident = self.profile.get("identity") or {}
        self.state = state or MockState(
            account_id=str(ident.get("account_id", "76561190000000000")),
            player_name=str(ident.get("player_name", "Helldiver")),
            keep_template_outcome=bool(self.profile["mission"].get("keep_template_outcome")),
        )
        lobby_cfg = self.profile.get("lobby") or {}
        self.discovery = (
            discovery.DiscoveryLobby(
                ttl_seconds=float(lobby_cfg.get("ttl_seconds", discovery.DEFAULT_TTL_SECONDS)),
                max_members=int(lobby_cfg.get("max_members", discovery.DEFAULT_MAX_MEMBERS)),
                persist_path=lobby_cfg.get("persist") or None,
                tombstone_ttl_seconds=float(
                    lobby_cfg.get("tombstone_ttl_seconds",
                                  discovery.DEFAULT_TOMBSTONE_TTL_SECONDS)),
                binding_ttl_seconds=float(
                    lobby_cfg.get("binding_ttl_seconds",
                                  discovery.DEFAULT_BINDING_TTL_SECONDS)),
            )
            if lobby_cfg.get("mode") == "discovery" else None
        )
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.anchors = store.war_anchors()
        self.mission_end_template = mission_end_template
        self.customize_template = customize_template

        # 统计/诊断
        self.stats = {"requests": 0, "state": 0, "exact": 0, "base": 0,
                      "template": 0, "fallback": 0, "health": 0, "player": 0}
        self.unmatched: dict[str, int] = {}
        self.unknown_time_fields: set[tuple[str, str]] = set()
        self.last_shift: timeshift.ShiftStats | None = None
        self.request_log: list[dict] = []

    # ---- 入口 -------------------------------------------------------------
    def handle(self, method: str, raw_path: str, body: bytes = b"",
               headers: dict | None = None, client_addr: str = "") -> Response:
        method = (method or "GET").upper()
        self.stats["requests"] += 1
        base, query = split_path(raw_path)

        if base == "/health":
            self.stats["health"] += 1
            return self._json(self.health(), source="health")

        # 按玩家覆盖层优先：save-export 产物里有的端点直接回该玩家的响应体
        if self.players is not None:
            player = self.players.resolve(headers=headers, client_addr=client_addr)
            if player is not None:
                payload = self.players.body_for(player, method, raw_path)
                if payload is not None:
                    resp = Response(200, payload, "application/json",
                                    source=f"player:{player.steam_id}")
                    self.stats["player"] += 1
                    self._record(method, raw_path, body, resp)
                    return resp

        route = match_state_route(method, raw_path)
        if route is not None:
            name, params = route
            resp = self._dispatch_state(name, params, method, raw_path, query, body,
                                        client_addr=client_addr, headers=headers or {})
            self.stats["state"] += 1
            self._record(method, raw_path, body, resp)
            return resp

        match = self.index.lookup(method, raw_path)
        if match is not None:
            resp = self._replay(match, raw_path)
            self.stats[match.kind] = self.stats.get(match.kind, 0) + 1
            self._record(method, raw_path, body, resp)
            return resp

        resp = self._fallback(method, raw_path)
        self.stats["fallback"] += 1
        self.unmatched[base] = self.unmatched.get(base, 0) + 1
        self._record(method, raw_path, body, resp)
        return resp

    # ---- 回放 -------------------------------------------------------------
    def _replay(self, match: Match, raw_path: str) -> Response:
        entry = match.entry
        if entry.doc is None:
            ctype = "application/json" if entry.kind == "json" else "application/octet-stream"
            return Response(200, entry.body, ctype, source=match.kind)
        doc = entry.doc
        if self.profile["timeshift"].get("enabled", True):
            doc, stats = timeshift.shift_document(
                entry.doc,
                path=entry.base_path,
                captured_at=entry.captured_at,
                now=self.now_fn(),
                refresh_span_seconds=int(
                    self.profile["timeshift"].get("refresh_span_seconds", 7 * 86400)),
            )
            self.last_shift = stats
            for ptr, _key, _val in stats.unknown:
                # 指针里的数组下标归一化，同一字段只报一次（e.g. planets_stats[42].missionTime）
                norm = re.sub(r"\[\d+\]", "[]", ptr)
                self.unknown_time_fields.add((entry.base_path, norm))
        return Response(200, json_bytes(doc), "application/json", source=match.kind)

    def _fallback(self, method: str, raw_path: str) -> Response:
        base, _q = split_path(raw_path)
        if not base.startswith("/api/"):
            return Response(200, b"", "text/plain", source="fallback:root")
        fb = self.profile.get("fallback") or {}
        status = int(fb.get("status", 200))
        raw = fb.get("body", "{}")
        return Response(status, raw.encode("utf-8"), "application/json", source="fallback")

    # ---- 有状态端点 -------------------------------------------------------
    def _dispatch_state(self, name: str, params: dict, method: str, raw_path: str,
                        query: str, body: bytes, *, client_addr: str = "",
                        headers: dict | None = None) -> Response:
        src = f"state:{name}"
        payload = parse_json_body(body)
        st = self.state
        headers = headers or {}
        try:
            if name == "account_login":
                if self.discovery is not None:
                    # account_hint 必须透传：否则带 X-HD2-Account 的探针会登录到**另一个**账号，
                    # 公钥就挂错人（2026-11-22 C14 抓到：login 建出 ...0001，PUT 落在 ...0003）
                    return self._json(self.discovery.login(client_addr=client_addr, body=payload,
                                                           account_hint=_account_hint(headers)),
                                      source="discovery:account_login")
                return self._json(st.login(), source=src)
            if name == "account_keys":
                # ?id=<accountId>[&id=…] 或 ?id=a,b —— 真实后端按 id 逐个回公钥，查不到的省略
                ids = _query_ids(query)
                if self.discovery is not None:
                    lobby_cfg = self.profile.get("lobby") or {}
                    # 时序缝隙方案 A/B（2026-11-22）：未命中先短暂等待，再考虑单未知绑定
                    return self._json(self.discovery.account_keys(
                        ids,
                        wait_seconds=float(lobby_cfg.get(
                            "account_keys_wait_seconds",
                            discovery.DEFAULT_KEYS_WAIT_SECONDS)),
                        allow_single_unknown=bool(lobby_cfg.get(
                            "account_keys_single_unknown_bind", True)),
                        unknown_window_seconds=float(lobby_cfg.get(
                            "account_keys_unknown_window_seconds",
                            discovery.DEFAULT_KEYS_UNKNOWN_WINDOW_SECONDS)),
                    ), source="discovery:account_keys" + ("" if ids else ":no-id"))
                return self._json({"accountKeys": []}, source=src + ":empty")
            if name == "report_position":
                return self._json({}, source=src)
            if name == "operation_list":
                return self._json(st.operation_list(), source=src)
            if name == "operation_create":
                return self._json(st.operation_create(payload), source=src)
            if name == "operation_abandon":
                st.operation_abandon(payload)
                return self._json({}, source=src)
            if name == "mission_start":
                return self._json(st.mission_start(payload), source=src)
            if name == "mission_end":
                return self._json(st.mission_end(payload, self.mission_end_template), source=src)
            if name == "mission_max_rewards":
                st.set_max_rewards(payload)
                return self._json({}, source=src)
            if name == "mission_activity":
                st.last_activity = payload if isinstance(payload, dict) else None
                return self._json({}, source=src)
            if name == "mission_session":
                return Response(202, b"", "application/json", source=src)

            # ---- 大厅（阶段2：discovery 模式由多客户端发现层接管）----
            disc = self.discovery
            if disc is not None:
                account = disc.account_for(client_addr=client_addr,
                                           account_hint=_account_hint(headers))
                if name == "lobby_put":
                    # 受控复现闸门（2026-11-23 A+B 复测）：MOCK_LOBBY_PUT_DELAY_MS > 0 时把
                    # 「上报大厅」的处理延后，用来人为放大客户端上报窗口。默认不设 = 不生效。
                    _delay_ms = os.environ.get("MOCK_LOBBY_PUT_DELAY_MS", "").strip()
                    if _delay_ms:
                        try:
                            time.sleep(min(120.0, max(0.0, float(_delay_ms) / 1000.0)))
                        except ValueError:
                            pass
                    saved = disc.put(account, payload if isinstance(payload, dict) else {})
                    echo = bool(self.profile["lobby"].get("echo_put"))
                    return self._json(saved if (echo and saved is not None) else {},
                                      source="discovery:lobby_put"
                                      + (":conflict" if saved is None else ""))
                if name == "lobby_accounts":
                    # 请求体 {"ids":[…]} 决定回显内容：真实后端逐 id 回一条 accountLobbies
                    return self._json(disc.accounts(account, ids=payload),
                                      source="discovery:lobby_accounts")
                if name == "lobby_clear":
                    disc.clear(account)
                    return self._json({}, source="discovery:lobby_clear")
                if name == "lobby_get":
                    policy = str((self.profile.get("lobby") or {}).get("unknown_lobby", "empty"))
                    res = disc.resolve(str(params.get("lobby_id", "")))
                    if res["kind"] == "live":
                        return self._json(res["lobby"], source="discovery:lobby_get")
                    if policy == "closed":
                        # 终态回答：让客户端知道"厅已经没了"，而不是空对象（它会把空对象当加载中）
                        return self._json(_closed_lobby_body(res),
                                          source=f"discovery:lobby_get:closed:{res.get('reason', '')}")
                    if policy.startswith("error"):
                        # 实验策略：直接 HTTP 错误（404/400），用于验证客户端对"不存在"的真实期待
                        try:
                            code = int(policy.split(":", 1)[1])
                        except (IndexError, ValueError):
                            code = 404
                        return Response(code, b'{"error":"lobby not found"}', "application/json",
                                        f"discovery:lobby_get:error{code}")
                    return self._json({}, source="discovery:lobby_get:unknown")
                if name == "lobby_member":
                    lobby = disc.join(account, str(params.get("lobby_id", "")), payload)
                    return self._json(lobby or {}, source="discovery:lobby_member"
                                      + (":rejected" if lobby is None else ""))
                return self._json({}, source=src)

            if name == "lobby_put":
                lobby = st.lobby_put(payload if isinstance(payload, dict) else {})
                echo = bool(self.profile["lobby"].get("echo_put"))
                return self._json(lobby if echo else {}, source=src)
            if name == "lobby_accounts":
                mode = self.profile["lobby"].get("mode", "replay")
                if mode == "empty":
                    # 离线单机：不返回录制里的「别人的旧大厅」，否则客户端会尝试加入不存在的厅
                    return self._json({"accountLobbies": [], "lobbies": []},
                                      source=src + ":empty")
                if mode == "state" or st.lobbies:
                    return self._json(st.lobby_accounts(), source=src)
                match = self.index.lookup(method, raw_path)
                if match is not None:
                    return self._replay(match, raw_path)
                return self._json({"accountLobbies": [], "lobbies": []}, source=src)
            if name == "lobby_clear":
                st.lobby_clear()
                return self._json({}, source=src)
            if name == "lobby_get":
                lobby = st.lobby_get(str(params.get("lobby_id", "")))
                if lobby is not None:
                    return self._json(lobby, source=src)
                return self._json({}, source=src + ":unknown")
            if name == "lobby_member":
                lobby = st.lobby_member(str(params.get("lobby_id", "")), payload,
                                        default_account=st.account_id)
                return self._json(lobby or {}, source=src)
                return self._json(lobby or {}, source=src)
            if name == "progression_customize":
                return self._json(st.set_customization(payload, self.customize_template),
                                  source=src)
            if name == "progression_consume":
                # 任务结算前后的消耗（弹药/战备补充等）；客户端只关心 200
                return self._json({}, source=src)
            if name == "progression_achievements":
                # 结算后上报成就进度；离线不落账，回 200 空体（T3 实机验证客户端接受）
                return self._json({}, source=src)
            if name == "pes_post":
                match = self.index.lookup(method, raw_path)
                if match is not None:
                    return self._replay(match, raw_path)
                return self._json({}, source=src)
        except Exception as exc:  # noqa: BLE001 —— mock 不因为单个端点出错而挂掉
            return self._json({"error": "mock handler failed", "route": name,
                               "detail": repr(exc)}, status=500, source=src + ":error")
        return self._json({}, source=src + ":noop")

    # ---- 工具 -------------------------------------------------------------
    def _json(self, obj: Any, status: int = 200, source: str = "") -> Response:
        return Response(status, json_bytes(obj), "application/json", source=source)

    def _record(self, method: str, path: str, body: bytes, resp: Response) -> None:
        self.request_log.append({
            "t": time.time(), "method": method, "path": path,
            "in": len(body or b""), "status": resp.status,
            "out": len(resp.body or b""), "source": resp.source,
        })
        if len(self.request_log) > 5000:
            del self.request_log[:2500]

    # ---- 诊断 -------------------------------------------------------------
    @staticmethod
    def _anchor_json(anchor: dict | None) -> dict | None:
        if not anchor:
            return anchor
        return {"value": anchor.get("value"), "at": str(anchor.get("at")),
                "path": anchor.get("path")}

    def health(self) -> dict:
        return {
            "ok": True,
            "mock": "hd2-offline-mock",
            "stats": dict(self.stats),
            "index": self.index.describe(),
            "anchors": {
                "war_clock": self._anchor_json(self.anchors.get("war_clock")),
                "war_elapsed": self._anchor_json(self.anchors.get("war_elapsed")),
                "capture_first": str(self.anchors.get("capture_first")),
                "capture_last": str(self.anchors.get("capture_last")),
            },
            "war_clock_now": timeshift.war_clock_now(self.anchors, self.now_fn()),
            "state": self.state.diagnostics(),
            # 发现层诊断（计数 + 统计，不含任何账号/玩家标识）：下一轮复测直接读 keys_miss
            "discovery": self.discovery.diagnostics() if self.discovery is not None else None,
            # 按玩家覆盖层：只报数量/键名，不落任何玩家标识内容
            "players": self.players.describe() if self.players is not None else None,
            "unmatched": dict(sorted(self.unmatched.items())),
            "unknown_time_fields": sorted(f"{p}::{ptr}"
                                          for p, ptr in self.unknown_time_fields),
            "last_shift": self.last_shift.summary() if self.last_shift else None,
        }

    def entry_for(self, raw_path: str) -> CapturedEntry | None:
        match = self.index.lookup("GET", raw_path)
        return match.entry if match else None
