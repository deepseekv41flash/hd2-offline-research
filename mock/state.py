"""有状态端点：登录 / 大厅 / 作战 / 任务链。

全部是**内存态**（进程退出即清空），会话级语义：
  * 登录发一个新 sessionId（抓包证据：响应只有 `{"sessionId": uuid}`）；
  * `PUT /api/lobby` 的 body **原样存表**、`GET /api/lobby/{id}` 原样返回
    （自建联机的核心：`connectionString` 不需要理解，转发即可）；
  * `Operation/Create` **回显客户端提出的 id**（18 组抓包实测：req id == resp id）；
  * `Mission/Start` 分配 missionId；`Mission/end` 用抓包模板回填 operationId/missionId。

未验证：`PUT /api/lobby` 的响应体形状、`POST /api/lobby/{id}/member` 的响应形状、
`Mission/end` 里 reward 是否被客户端本地采用。这些都靠实机跑一轮才知道。
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _parse_body(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


@dataclass
class MockState:
    """自建信令的最小状态表。"""

    # 合成默认值。注意：账号 ID 是 HD2 自有 64 位 ID，**不是** SteamID64
    # （445/445 反证见 docs/B2B3-LOBBY-LOGIN-RECON-2026-11-21.md §4/§5）。
    account_id: str = "76561190000000000"
    player_name: str = "Helldiver"
    operation_seed: int = 1283                 # 与抓包 nextOperationId 对齐
    keep_template_outcome: bool = False        # True = 保留抓包模板里的 campaignWon/othersImpact
    session_ids: list[str] = field(default_factory=list)
    lobbies: dict[str, dict] = field(default_factory=dict)
    account_lobbies: dict[str, str] = field(default_factory=dict)
    operations: dict[int, dict] = field(default_factory=dict)
    next_operation_id: int = 1283
    missions: dict[str, dict] = field(default_factory=dict)
    customization: dict | None = None
    max_rewards: dict[int, int] = field(default_factory=dict)
    last_activity: dict | None = None

    # ---- 账号 -------------------------------------------------------------
    def login(self) -> dict:
        sid = str(uuid.uuid4())
        self.session_ids.append(sid)
        return {"sessionId": sid}

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    # ---- 大厅 -------------------------------------------------------------
    def lobby_put(self, body: dict) -> dict:
        """存下客户端提交的大厅；返回规范化后的 lobby。"""
        lobby = copy.deepcopy(body) if isinstance(body, dict) else {}
        lobby_id = str(lobby.get("lobbyId") or "")
        if not lobby_id:
            lobby_id = f"{uuid.uuid4()}.r-unknown"
            lobby["lobbyId"] = lobby_id
        players = lobby.get("players") or []
        host_id = None
        for p in players:
            if isinstance(p, dict) and p.get("isHost"):
                host_id = str(p.get("memberAccountId") or "")
                break
        self.lobbies[lobby_id] = lobby
        if host_id:
            self.account_lobbies[host_id] = lobby_id
        return lobby

    def lobby_get(self, lobby_id: str) -> dict | None:
        if lobby_id in self.lobbies:
            return self.lobbies[lobby_id]
        # 客户端有时只带 uuid（不带 `.r-…` 后缀）
        for key, lobby in self.lobbies.items():
            if key.startswith(lobby_id) or lobby_id.startswith(key.split(".")[0]):
                return lobby
        return None

    def lobby_member(self, lobby_id: str, body: Any, *, default_account: str = "") -> dict | None:
        """加入大厅：body 可能是 `"<accountId>"` 字符串或对象。"""
        lobby = self.lobby_get(lobby_id)
        if lobby is None:
            return None
        member = ""
        if isinstance(body, str):
            member = body.strip().strip('"')
        elif isinstance(body, dict):
            for k in ("memberAccountId", "accountId", "account", "id"):
                if body.get(k):
                    member = str(body[k])
                    break
        elif isinstance(body, (int, float)):
            member = str(int(body))
        if not member:
            member = default_account or self.account_id
        players = lobby.setdefault("players", [])
        ids = lobby.setdefault("memberAccountIds", [])
        if not any(isinstance(p, dict) and p.get("memberAccountId") == member for p in players):
            slot = next((p for p in players
                         if isinstance(p, dict) and not p.get("hasInfo")
                         and str(p.get("memberAccountId")) in ("", "0")), None)
            if slot is None:
                slot = {"sourcePlatform": 1, "memberAccountId": member, "isHost": False,
                        "playerName": "", "playerRank": 0, "experience": 0,
                        "hasInfo": False, "commendRank": 0}
                players.append(slot)
            slot.update({"memberAccountId": member, "hasInfo": True})
            if member not in ids:
                ids.append(member)
            lobby["numLobbyMembers"] = sum(
                1 for p in players if isinstance(p, dict) and p.get("hasInfo"))
        self.account_lobbies[member] = lobby_id
        return lobby

    def lobby_clear(self, *, account_id: str = "") -> None:
        lobby_id = self.account_lobbies.pop(account_id or self.account_id, None)
        if lobby_id:
            self.lobbies.pop(lobby_id, None)

    def lobby_accounts(self) -> dict:
        acct = self.account_id
        out = {"accountLobbies": [], "lobbies": []}
        for aid, lid in self.account_lobbies.items():
            out["accountLobbies"].append({"account": aid, "lobbyId": lid})
        for lobby in self.lobbies.values():
            out["lobbies"].append(lobby)
        if not out["accountLobbies"] and acct:
            out["accountLobbies"].append({"account": acct, "lobbyId": ""})
        return out

    # ---- 作战 / 任务链 -----------------------------------------------------
    def operation_create(self, body: Any) -> dict:
        """回显客户端提出的 operation id（抓包实测：resp id == req id）。"""
        ops = []
        if isinstance(body, dict):
            ops = body.get("operations") or []
        created = []
        for raw in ops if isinstance(ops, list) else []:
            if not isinstance(raw, dict):
                continue
            op_id = raw.get("id")
            try:
                op_id = int(op_id)
            except Exception:  # noqa: BLE001
                op_id = self.next_operation_id
            missions = []
            for m in raw.get("missions") or []:
                if isinstance(m, dict):
                    missions.append({
                        "index": int(m.get("index", len(missions))),
                        "type": int(m.get("type", 1)),
                        "state": int(m.get("state", 1)),
                        "missionId": str(m.get("missionId") or ZERO_UUID),
                        "participants": list(m.get("participants") or []),
                    })
            op = {
                "id": op_id,
                "seed": raw.get("seed"),
                "warId": raw.get("warId"),
                "planetIndex": raw.get("planetIndex"),
                "placementNodeIndex": raw.get("placementNodeIndex"),
                "campaignId": raw.get("campaignId"),
                "jointOperationId": raw.get("jointOperationId", -1),
                "isActive": True,
                "type": raw.get("type"),
                "difficulty": raw.get("difficulty"),
                "race": raw.get("race"),
                "missions": missions,
                "rewards": list(raw.get("rewards") or []),
                "operationSettingsId": raw.get("operationSettingsId"),
            }
            self.operations[op_id] = op
            self.next_operation_id = max(self.next_operation_id, op_id + 1)
            created.append({"id": op_id, "rewards": [], "backupRewards": []})
        return {"operations": created}

    def operation_list(self) -> dict:
        return {"nextOperationId": self.next_operation_id,
                "operations": [copy.deepcopy(op) for op in self.operations.values()]}

    def operation_abandon(self, body: Any) -> None:
        if isinstance(body, dict):
            for key in ("operationId", "id"):
                if body.get(key) is not None:
                    self.operations.pop(_as_int(body.get(key)), None)
                    return
        # 客户端也可能直接给一个数字
        self.operations.pop(_as_int(body), None)

    def mission_start(self, body: Any) -> dict:
        b = body if isinstance(body, dict) else {}
        op_id = _as_int(b.get("operationId"))
        idx = _as_int(b.get("missionIndex"), 0)
        mission_id = str(uuid.uuid4())
        self.missions[mission_id] = {"operationId": op_id, "missionIndex": idx,
                                     "planetIndex": b.get("planetIndex"),
                                     "regionIndex": b.get("regionIndex")}
        op = self.operations.get(op_id)
        if op:
            for m in op.get("missions") or []:
                if int(m.get("index", -1)) == idx:
                    m["missionId"] = mission_id
                    m["state"] = 2
                    if b.get("participants"):
                        m["participants"] = list(b["participants"])
        return {"missionId": mission_id}

    def mission_end(self, body: Any, template: dict | None) -> dict:
        """用模板回填结算；返回给客户端的 reward 包。"""
        b = body if isinstance(body, dict) else {}
        op_id = _as_int(b.get("operationId"))
        idx = _as_int(b.get("missionIndex"), 0)
        mission_id = str(b.get("missionId") or "")
        if not mission_id:
            op = self.operations.get(op_id) or {}
            for m in op.get("missions") or []:
                if int(m.get("index", -1)) == idx and m.get("missionId"):
                    mission_id = m["missionId"]
        if not mission_id:
            mission_id = str(uuid.uuid4())

        if template:
            out = copy.deepcopy(template)
        else:
            out = {
                "operationId": op_id, "missionIndex": idx, "missionId": mission_id,
                "experience": 100, "multipliers": [], "badges": [], "campaignWon": False,
                "impact": 0, "othersImpactPlanet": 0, "othersImpactCommunityTarget": 0,
                "othersImpactPlanetEvent": 0, "progressionPackageUpdates": [],
                "lootedItemRewards": [], "lootedHeavyItemRewards": [],
                "rewards": [], "levelUpRewards": [], "repeatingPlayer": [],
            }
        out["operationId"] = op_id
        out["missionIndex"] = idx
        out["missionId"] = mission_id
        for upd in out.get("progressionPackageUpdates") or []:
            if isinstance(upd, dict):
                upd["platformAccountId"] = self.account_id
        if not self.keep_template_outcome:
            # 离线：不要假装社区战果（模板里的 othersImpact 是抓包当时别人的贡献）
            for k in ("othersImpactPlanet", "othersImpactCommunityTarget",
                      "othersImpactPlanetEvent"):
                if k in out:
                    out[k] = 0

        op = self.operations.get(op_id)
        if op:
            for m in op.get("missions") or []:
                if int(m.get("index", -1)) == idx:
                    m["missionId"] = mission_id
                    m["state"] = 3
            if all(int(m.get("state", 0)) >= 3 for m in op.get("missions") or []):
                op["isActive"] = False
        self.missions.setdefault(mission_id, {"operationId": op_id, "missionIndex": idx})
        return out

    def set_max_rewards(self, body: Any) -> None:
        b = body if isinstance(body, dict) else {}
        op_id = _as_int(b.get("operationId"))
        if op_id:
            self.max_rewards[op_id] = _as_int(b.get("maxRewards") or b.get("rewardCount"), 0)

    def set_customization(self, body: Any, template: dict | None) -> dict:
        self.customization = copy.deepcopy(body) if isinstance(body, (dict, list)) else None
        if isinstance(template, dict):
            out = copy.deepcopy(template)
            items = out.get("items")
            if isinstance(items, list) and isinstance(body, dict):
                for item in items:
                    if isinstance(item, dict) and item.get("mixId") == body.get("mixId"):
                        for k in ("modes", "activeIndex", "customizations"):
                            if k in body:
                                item[k] = copy.deepcopy(body[k])
            return out
        return {"items": []}

    def diagnostics(self) -> dict:
        return {
            "sessions": len(self.session_ids),
            "lobbies": sorted(self.lobbies.keys()),
            "operations": sorted(self.operations.keys()),
            "nextOperationId": self.next_operation_id,
            "missions": len(self.missions),
            "accountId": self.account_id,
        }


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:  # noqa: BLE001
        return default
