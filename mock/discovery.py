"""阶段2 发现层（co-op discovery）：多客户端大厅服务。

与阶段1 的 `MockState`（单账号、回放+最小状态机）不同，这里服务**两个（最多 4 个）真实客户端**：

  * **账号归属**：登录体带 `instanceId`（B9 已证字段），但后续 `/api/lobby*` 请求不一定带身份。
    因此按「来源 IP」归属账号（两台机器/两个网络天然不同；同机多实例由 instanceId 区分），
    并接受显式 `X-HD2-Account` 头覆盖（供我方探针与联调工具使用）。
  * **服务端权威**：大厅归属、成员列表、`accountLobbies` 索引都以本服务为准；
    客户端提交的 body 原样保存（`connectionString` 不解析，保持阶段1 的「原样转发」原则）。
  * **多客户端安全**：全部状态在 `threading.Lock` 内读写；对外返回深拷贝，调用方改不到内部状态。
  * **幂等**：重复 `PUT` 同一大厅、重复加入同一成员、重复清理都不产生重复条目。
  * **过期**：大厅/账号在 TTL（默认 600 s）内无活动即回收，防止离线残留把后来者带进不存在的大厅。

诚实边界：
  * 真实客户端在 `/api/lobby*` 上的**请求/响应形状尚未全部实机校准**（见 docs/STAGE2-DISCOVERY-API-CONTRACT），
    本模块对未知字段一律原样保留，不猜测、不生成；
  * 账号归属策略（IP / instanceId / 显式头）是当前最稳的工程选择，E2 实测时若发现客户端自带身份字段，再切换。
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from typing import Any, Callable

DEFAULT_TTL_SECONDS = 600.0
DEFAULT_MAX_MEMBERS = 4


class DiscoveryLobby:
    """多账号大厅发现服务（线程安全、内存态）。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_members: int = DEFAULT_MAX_MEMBERS,
        clock: Callable[[], float] = time.monotonic,
        account_prefix: str = "acct",
    ):
        self._lock = threading.Lock()
        self._ttl = float(ttl_seconds)
        self._max_members = int(max_members)
        self._clock = clock
        self._prefix = account_prefix
        self._seq = 0
        # account_id -> {"ip", "instance_id", "last_seen", "lobby_id"}
        self._accounts: dict[str, dict] = {}
        # 来源 IP -> account_id（登录时建立；未登录的首次调用懒建）
        self._by_ip: dict[str, str] = {}
        # lobby_id -> {"body": dict, "owner": account_id, "updated": float}
        self._lobbies: dict[str, dict] = {}
        self._stats = {
            "login": 0, "put": 0, "get": 0, "get_hit": 0, "join": 0, "join_rejected": 0,
            "leave": 0, "conflict": 0, "expired": 0, "expired_accounts": 0,
        }

    # ------------------------------------------------------------------ 账号
    def _now(self) -> float:
        return float(self._clock())

    def _new_account_locked(self, ip: str, instance_id: str) -> str:
        self._seq += 1
        account = f"{self._prefix}-{self._seq:06x}"
        self._accounts[account] = {
            "ip": ip, "instance_id": instance_id,
            "last_seen": self._now(), "lobby_id": None,
        }
        if ip:
            self._by_ip[ip] = account
        return account

    def login(self, *, client_addr: str = "", body: Any = None,
              account_hint: str = "") -> dict:
        """登录：建立/复用账号，返回与阶段1 同形状的 `{"sessionId": uuid}`。"""
        instance_id = ""
        if isinstance(body, dict):
            instance_id = str(body.get("instanceId") or "")
        with self._lock:
            self._expire_locked()
            self._stats["login"] += 1
            account = account_hint or self._by_ip.get(client_addr)
            if not account:
                account = self._new_account_locked(client_addr, instance_id)
            info = self._accounts.setdefault(
                account, {"ip": client_addr, "instance_id": instance_id,
                          "last_seen": self._now(), "lobby_id": None})
            info["last_seen"] = self._now()
            if instance_id:
                info["instance_id"] = instance_id
            if client_addr:
                self._by_ip[client_addr] = account
        return {"sessionId": str(uuid.uuid4())}

    def account_for(self, *, client_addr: str = "", account_hint: str = "",
                    create: bool = True) -> str | None:
        """把一次请求归属到账号：显式头 > 来源 IP > 懒建。"""
        with self._lock:
            self._expire_locked()
            account = account_hint or self._by_ip.get(client_addr)
            if account:
                info = self._accounts.get(account)
                if info is None:
                    # 显式账号头（探针/联调工具）可以直建账号，但仍以本服务为权威
                    info = self._accounts.setdefault(
                        account, {"ip": client_addr, "instance_id": "",
                                  "last_seen": self._now(), "lobby_id": None})
                info["last_seen"] = self._now()
                if client_addr:
                    self._by_ip.setdefault(client_addr, account)
                return account
            if not create:
                return None
            return self._new_account_locked(client_addr, "")

    def _touch_locked(self, account: str) -> None:
        info = self._accounts.get(account)
        if info is not None:
            info["last_seen"] = self._now()

    # ------------------------------------------------------------------ 大厅
    def put(self, account: str, body: Any) -> dict | None:
        """创建/更新大厅。返回保存后的 body；同一 lobbyId 被另一账号占用且未过期时返回 None。"""
        lobby = copy.deepcopy(body) if isinstance(body, dict) else {}
        lobby_id = str(lobby.get("lobbyId") or "").strip()
        if not lobby_id:
            lobby_id = f"{uuid.uuid4()}.r-{uuid.uuid4().hex[:8]}"
            lobby["lobbyId"] = lobby_id
        with self._lock:
            self._expire_locked()
            existing = self._lobbies.get(lobby_id)
            if existing is not None and existing["owner"] != account:
                # 同一大厅号被另一账号写入：更可能是客户端的旧缓存或串号，拒绝而不覆盖
                self._stats["conflict"] += 1
                return None
            now = self._now()
            _normalize_members(lobby, account)
            self._lobbies[lobby_id] = {"body": lobby, "owner": account, "updated": now}
            self._stats["put"] += 1
            info = self._accounts.setdefault(
                account, {"ip": "", "instance_id": "", "last_seen": now, "lobby_id": None})
            info["lobby_id"] = lobby_id
            info["last_seen"] = now
            return copy.deepcopy(lobby)

    def get(self, lobby_id: str) -> dict | None:
        """按 lobbyId 取大厅；支持客户端只带 uuid（不带 `.r-…` 后缀）的查法。"""
        key = (lobby_id or "").strip()
        if not key:
            return None
        with self._lock:
            self._expire_locked()
            self._stats["get"] += 1
            hit = self._find_locked(key)
            if hit is None:
                return None
            self._stats["get_hit"] += 1
            hit["updated"] = self._now()          # GET 视为活跃：避免客户端轮询时被 TTL 回收
            self._touch_locked(hit["owner"])
            return copy.deepcopy(hit["body"])

    def _find_locked(self, key: str) -> dict | None:
        if key in self._lobbies:
            return self._lobbies[key]
        for stored_id, entry in self._lobbies.items():
            short = stored_id.split(".")[0]
            if stored_id.startswith(key) or key.startswith(short) or short.startswith(key):
                return entry
        return None

    def join(self, account: str, lobby_id: str, member: Any) -> dict | None:
        """加入大厅：把成员填进空槽（或追加），幂等。返回更新后的大厅；失败返回 None。"""
        member_id = _member_id_from(member) or account
        with self._lock:
            self._expire_locked()
            entry = self._find_locked((lobby_id or "").strip())
            if entry is None:
                return None
            lobby = entry["body"]
            players = lobby.get("players")
            if not isinstance(players, list):
                players = []
                lobby["players"] = players
            ids = lobby.get("memberAccountIds")
            if not isinstance(ids, list):
                ids = []
                lobby["memberAccountIds"] = ids
            members = _member_ids(lobby)
            if member_id in members:                     # 幂等：已在厅里
                entry["updated"] = self._now()
                self._touch_locked(account)
                return copy.deepcopy(lobby)
            if len(members) >= self._max_members:
                self._stats["join_rejected"] += 1
                return None
            slot = _empty_slot(players)
            if slot is not None:
                slot["memberAccountId"] = member_id
                if "hasInfo" in slot:
                    slot["hasInfo"] = True
            else:
                players.append({"memberAccountId": member_id, "isHost": False, "hasInfo": True})
            if member_id not in ids:
                ids.append(member_id)
            entry["updated"] = self._now()
            self._touch_locked(account)
            self._stats["join"] += 1
            return copy.deepcopy(lobby)

    def accounts(self, account: str = "") -> dict:
        """`GET /api/lobby/accounts` 的响应：活跃大厅 + 账号→大厅索引。"""
        with self._lock:
            self._expire_locked()
            account_lobbies: list[dict] = []
            lobbies: list[dict] = []
            for entry in self._lobbies.values():
                lobbies.append(copy.deepcopy(entry["body"]))
                account_lobbies.append({"account": entry["owner"],
                                        "lobbyId": entry["body"].get("lobbyId", "")})
            if account and not any(a["account"] == account for a in account_lobbies):
                account_lobbies.append({"account": account, "lobbyId": ""})
            return {"accountLobbies": account_lobbies, "lobbies": lobbies}

    def clear(self, account: str) -> None:
        """离开/清理：删掉本账号建的大厅，并从别的大厅成员里移除本账号（幂等）。"""
        with self._lock:
            self._expire_locked()
            info = self._accounts.get(account)
            if info and info.get("lobby_id"):
                self._lobbies.pop(info["lobby_id"], None)
                info["lobby_id"] = None
            for entry in self._lobbies.values():
                lobby = entry["body"]
                players = lobby.get("players")
                if isinstance(players, list):
                    for p in players:
                        if isinstance(p, dict) and str(p.get("memberAccountId") or "") == account:
                            p["memberAccountId"] = ""
                            if "hasInfo" in p:
                                p["hasInfo"] = False
                ids = lobby.get("memberAccountIds")
                if isinstance(ids, list) and account in ids:
                    ids.remove(account)
            self._stats["leave"] += 1

    # ------------------------------------------------------------------ 过期
    def _expire_locked(self) -> None:
        now = self._now()
        stale = [lid for lid, e in self._lobbies.items() if now - e["updated"] > self._ttl]
        for lid in stale:
            self._lobbies.pop(lid, None)
            self._stats["expired"] += 1
        stale_accounts = [a for a, i in self._accounts.items()
                          if now - i["last_seen"] > self._ttl and i.get("lobby_id") is None]
        for a in stale_accounts:
            self._accounts.pop(a, None)
            for ip, mapped in list(self._by_ip.items()):
                if mapped == a:
                    self._by_ip.pop(ip, None)
            self._stats["expired_accounts"] += 1
        if stale:
            # 大厅被回收时同步清掉账号上的 lobby_id，避免索引指向不存在的大厅
            for info in self._accounts.values():
                if info.get("lobby_id") and info["lobby_id"] not in self._lobbies:
                    info["lobby_id"] = None

    # ------------------------------------------------------------------ 诊断
    def stats(self) -> dict:
        with self._lock:
            out = dict(self._stats)
            out["accounts"] = len(self._accounts)
            out["lobbies"] = len(self._lobbies)
            return out

    def dump(self) -> dict:
        """调试/日志用快照（脱敏：不含 IP 之外的个人信息）。"""
        with self._lock:
            return {
                "accounts": {a: {"ip": i.get("ip", ""), "lobby_id": i.get("lobby_id")}
                             for a, i in self._accounts.items()},
                "lobbies": {lid: e["body"].get("lobbyId", lid) for lid, e in self._lobbies.items()},
            }


# ---------------------------------------------------------------------- 工具
def _member_ids(lobby: dict) -> list[str]:
    """大厅里的成员账号（以 players[].memberAccountId 为准，memberAccountIds 兜底）。"""
    out: list[str] = []
    players = lobby.get("players")
    if isinstance(players, list):
        for p in players:
            if isinstance(p, dict):
                mid = str(p.get("memberAccountId") or "")
                if mid and mid not in ("0", "00000000000000000000") and mid not in out:
                    out.append(mid)
    ids = lobby.get("memberAccountIds")
    if isinstance(ids, list):
        for mid in ids:
            mid = str(mid or "")
            if mid and mid not in ("0",) and mid not in out:
                out.append(mid)
    return out


def _empty_slot(players: list) -> dict | None:
    for p in players:
        if isinstance(p, dict) and str(p.get("memberAccountId") or "") in ("", "0"):
            return p
    return None


def _member_id_from(member: Any) -> str:
    if isinstance(member, str):
        return member.strip().strip('"')
    if isinstance(member, dict):
        for key in ("memberAccountId", "accountId", "account", "id"):
            if member.get(key):
                return str(member[key])
    return ""


def _normalize_members(lobby: dict, owner: str) -> None:
    """把 body 里的成员信息规范化（不新增未知字段）：补 memberAccountIds、识别 host。"""
    players = lobby.get("players")
    ids = lobby.get("memberAccountIds")
    if not isinstance(players, list) and not isinstance(ids, list):
        return
    players = players if isinstance(players, list) else []
    ids = ids if isinstance(ids, list) else []
    host_id = ""
    for p in players:
        if isinstance(p, dict) and p.get("isHost"):
            host_id = str(p.get("memberAccountId") or "")
            break
    if host_id and host_id not in ids:
        ids.append(host_id)
    for p in players:
        if not isinstance(p, dict):
            continue
        mid = str(p.get("memberAccountId") or "")
        if not mid:
            continue
        if mid == owner and "isHost" not in p:
            p["isHost"] = True
        if mid not in ids:
            ids.append(mid)
    lobby["memberAccountIds"] = ids
