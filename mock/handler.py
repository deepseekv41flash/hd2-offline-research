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
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from . import timeshift
from .capture import CaptureStore, CapturedEntry
from .rules import Match, ReplayIndex, match_state_route, split_path
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
    "lobby": {"mode": "replay", "echo_put": False},
    "mission": {"keep_template_outcome": False},
    "identity": {"account_id": "76561190000000000", "player_name": "Helldiver"},
}


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
    ):
        self.store = store
        self.index = ReplayIndex(store)
        self.profile = _deep_merge(DEFAULT_PROFILE, profile or {})
        ident = self.profile.get("identity") or {}
        self.state = state or MockState(
            account_id=str(ident.get("account_id", "76561190000000000")),
            player_name=str(ident.get("player_name", "Helldiver")),
            keep_template_outcome=bool(self.profile["mission"].get("keep_template_outcome")),
        )
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.anchors = store.war_anchors()
        self.mission_end_template = mission_end_template
        self.customize_template = customize_template

        # 统计/诊断
        self.stats = {"requests": 0, "state": 0, "exact": 0, "base": 0,
                      "template": 0, "fallback": 0, "health": 0}
        self.unmatched: dict[str, int] = {}
        self.unknown_time_fields: set[tuple[str, str]] = set()
        self.last_shift: timeshift.ShiftStats | None = None
        self.request_log: list[dict] = []

    # ---- 入口 -------------------------------------------------------------
    def handle(self, method: str, raw_path: str, body: bytes = b"",
               headers: dict | None = None) -> Response:
        method = (method or "GET").upper()
        self.stats["requests"] += 1
        base, query = split_path(raw_path)

        if base == "/health":
            self.stats["health"] += 1
            return self._json(self.health(), source="health")

        route = match_state_route(method, raw_path)
        if route is not None:
            name, params = route
            resp = self._dispatch_state(name, params, method, raw_path, query, body)
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
                        query: str, body: bytes) -> Response:
        src = f"state:{name}"
        payload = parse_json_body(body)
        st = self.state
        try:
            if name == "account_login":
                return self._json(st.login(), source=src)
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

            # ---- 大厅 ----
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
            if name == "progression_customize":
                return self._json(st.set_customization(payload, self.customize_template),
                                  source=src)
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
            "unmatched": dict(sorted(self.unmatched.items())),
            "unknown_time_fields": sorted(f"{p}::{ptr}"
                                          for p, ptr in self.unknown_time_fields),
            "last_shift": self.last_shift.summary() if self.last_shift else None,
        }

    def entry_for(self, raw_path: str) -> CapturedEntry | None:
        match = self.index.lookup("GET", raw_path)
        return match.entry if match else None
