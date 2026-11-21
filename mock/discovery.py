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
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

DEFAULT_TTL_SECONDS = 600.0
DEFAULT_MAX_MEMBERS = 4
DEFAULT_TOMBSTONE_TTL_SECONDS = 1800.0   # 关闭标记保留 30 min（远大于客户端 90 s 重试窗）
STATE_VERSION = 1


class DiscoveryLobby:
    """多账号大厅发现服务（线程安全、内存态 + 可选落盘）。

    2026-11-21 E2 之后的两条设计约束（见 docs/STAGE2-DESIGN-STALE-LOBBY-2026-11-21.md）：
      * **未知大厅必须有终态**：tombstone 表记录 `left/expired/server_reset/conflict`，
        由 `resolve()` 给出 live/closed/unknown 三态，handler 据此返回关闭态而不是 `{}`；
      * **状态跨重启**：`persist_path` 开启 JSON 原子落盘；干净退出写 `.clean` 标记，
        非干净重启（标记缺失）会把上一代大厅全部转成 `server_reset` tombstone，
        让客户端拿到终态而不是空对象。
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_members: int = DEFAULT_MAX_MEMBERS,
        clock: Callable[[], float] = time.monotonic,
        account_prefix: str = "7656119",
        persist_path: "str | Path | None" = None,
        tombstone_ttl_seconds: float = DEFAULT_TOMBSTONE_TTL_SECONDS,
    ):
        self._lock = threading.Lock()
        self._ttl = float(ttl_seconds)
        self._max_members = int(max_members)
        self._clock = clock
        self._prefix = account_prefix
        self._seq = 0
        # account_id -> {"ip", "instance_id", "last_seen", "lobby_id", "real_id", "profile"}
        self._accounts: dict[str, dict] = {}
        # 来源 IP -> account_id（登录时建立；未登录的首次调用懒建）
        self._by_ip: dict[str, str] = {}
        # lobby_id -> {"body": dict, "owner": account_id, "updated": float}
        self._lobbies: dict[str, dict] = {}
        # lobby_id -> {"lobby_id", "closed_at", "reason"}
        self._tombstones: dict[str, dict] = {}
        self._tombstone_ttl = float(tombstone_ttl_seconds)
        self._generation = 0
        self._persist_path = Path(persist_path) if persist_path else None
        self._stats = {
            "login": 0, "put": 0, "get": 0, "get_hit": 0, "join": 0, "join_rejected": 0,
            "leave": 0, "conflict": 0, "expired": 0, "expired_accounts": 0,
            "closed": 0, "closed_hit": 0,
        }
        self._restore()

    # ------------------------------------------------------------------ 账号
    def _now(self) -> float:
        return float(self._clock())

    def _new_account_locked(self, ip: str, instance_id: str) -> str:
        self._seq += 1
        # 账号 ID 必须是 **20 位纯数字**（与真实 accountId 同形）：客户端会把大厅
        # players[].memberAccountId 按 64 位整数解析，早期用的 "acct-000001" 会在
        # game.dll 里触发 0xC0000005（2026-11-21 21:46 实测崩溃，dump 已取证）。
        account = f"{self._prefix}{self._seq:013d}"
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

    def _learn_identity_locked(self, account: str, lobby: dict) -> None:
        """从客户端自己 PUT 的大厅 body 里学会它的**真实 accountId** 与玩家资料。

        为什么必须学：`POST /api/lobby/<id>/member` 的请求体是 `null`（2026-11-21 E2 实测），
        服务端只能按来源 IP 归属。但**加入方自己的账号 ID 是客户端已知的**——如果我们把合成的
        `7656119…` 写进返回 body 的 players[]，客户端会看到「我进厅了，但里面不是我」，
        于是判定加入失败并每 ~90 s 重试一次 member（E2 现场现象）。
        大厅 PUT 的 `players[].isHost=true` 那一项就是客户端自己的真实 ID（20 位数字）与名字/等级。
        """
        info = self._accounts.get(account)
        if info is None:
            return
        players = lobby.get("players")
        if not isinstance(players, list):
            return
        for p in players:
            if not isinstance(p, dict) or not p.get("isHost"):
                continue
            mid = str(p.get("memberAccountId") or "")
            if not _looks_like_account(mid):
                continue
            info["real_id"] = mid
            profile = {k: p[k] for k in ("playerName", "playerRank", "experience",
                                         "commendRank", "sourcePlatform") if k in p}
            if profile:
                info["profile"] = profile
            return

    def _fill_member_locked(self, slot: dict, account: str, member_id: str) -> None:
        """把成员写进槽位：ID 用真实 ID，资料（名字/等级）尽量从它自己 PUT 过的缓存补。"""
        slot["memberAccountId"] = member_id
        slot["isHost"] = False
        if "hasInfo" in slot:
            slot["hasInfo"] = True
        info = self._accounts.get(account) or {}
        for k, v in (info.get("profile") or {}).items():
            if k != "isHost":
                slot[k] = v

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
            # 成员是**服务端权威**：owner 客户端周期重发它自己那份（可能还没看到新成员）的
            # lobby body 时，不能把已加入的成员抹掉（2026-11-21 E2 实测：PUT 抹掉 join 后，
            # 加入方永远看不到自己在厅里 → 卡加载 + 90s 重试）。
            members_before = _member_ids(existing["body"]) if existing is not None else []
            _normalize_members(lobby, account)
            _merge_member_ids(lobby, members_before, account)
            self._tombstones.pop(lobby_id, None)     # 重开同一厅号：清掉关闭标记
            self._lobbies[lobby_id] = {"body": lobby, "owner": account, "updated": now}
            self._stats["put"] += 1
            info = self._accounts.setdefault(
                account, {"ip": "", "instance_id": "", "last_seen": now, "lobby_id": None})
            info["lobby_id"] = lobby_id
            info["last_seen"] = now
            self._learn_identity_locked(account, lobby)
            _sync_member_count_locked(lobby)
            self._save_locked()
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
        explicit_id = _member_id_from(member)
        with self._lock:
            self._expire_locked()
            entry = self._find_locked((lobby_id or "").strip())
            if entry is None:
                return None
            lobby = entry["body"]
            info = self._accounts.get(account) or {}
            # 请求体通常是 null：优先用「它自己 PUT 大厅时暴露的真实 accountId」
            member_id = explicit_id or str(info.get("real_id") or "") or account
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
                _sync_member_count_locked(lobby)
                self._save_locked()
                return copy.deepcopy(lobby)
            if len(members) >= self._max_members:
                self._stats["join_rejected"] += 1
                return None
            slot = _empty_slot(players)
            if slot is not None:
                self._fill_member_locked(slot, account, member_id)
            else:
                new_slot: dict = {"memberAccountId": "", "isHost": False, "hasInfo": True}
                self._fill_member_locked(new_slot, account, member_id)
                players.append(new_slot)
            if member_id not in ids:
                ids.append(member_id)
            entry["updated"] = self._now()
            self._touch_locked(account)
            self._stats["join"] += 1
            _sync_member_count_locked(lobby)
            self._save_locked()
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
                lobby_id = info["lobby_id"]
                if self._lobbies.pop(lobby_id, None) is not None:
                    self._remember_closed_locked(lobby_id, "left")
                info["lobby_id"] = None
            # 成员可能以「真实 accountId」写入，两个身份都算自己
            identities = {account}
            if info and info.get("real_id"):
                identities.add(str(info["real_id"]))
            for entry in self._lobbies.values():
                lobby = entry["body"]
                players = lobby.get("players")
                if isinstance(players, list):
                    for p in players:
                        if isinstance(p, dict) and str(p.get("memberAccountId") or "") in identities:
                            p["memberAccountId"] = ""
                            if "hasInfo" in p:
                                p["hasInfo"] = False
                ids = lobby.get("memberAccountIds")
                if isinstance(ids, list):
                    for ident in identities:
                        if ident in ids:
                            ids.remove(ident)
                _sync_member_count_locked(lobby)
            self._stats["leave"] += 1
            self._save_locked()

    # ------------------------------------------------------------------ 过期
    def _expire_locked(self) -> None:
        now = self._now()
        stale = [lid for lid, e in self._lobbies.items() if now - e["updated"] > self._ttl]
        for lid in stale:
            self._lobbies.pop(lid, None)
            self._remember_closed_locked(lid, "expired")
            self._stats["expired"] += 1
        for lid, tb in list(self._tombstones.items()):
            if now - tb["closed_at"] > self._tombstone_ttl:
                self._tombstones.pop(lid, None)
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

    # ------------------------------------------------------------------ 终态 / 持久化
    def _remember_closed_locked(self, lobby_id: str, reason: str) -> None:
        """把一个已消失的大厅记成 tombstone（客户端据此拿到终态而不是空对象）。"""
        if not lobby_id:
            return
        self._tombstones[lobby_id] = {
            "lobby_id": lobby_id, "closed_at": self._now(), "reason": str(reason)}
        self._stats["closed"] += 1

    def _tombstone_for_locked(self, key: str) -> dict | None:
        if not key:
            return None
        hit = self._tombstones.get(key)
        if hit is not None:
            return hit
        for stored_id, entry in self._tombstones.items():
            short = stored_id.split(".")[0]
            if stored_id.startswith(key) or key.startswith(short) or short.startswith(key):
                return entry
        return None

    def resolve(self, lobby_id: str) -> dict:
        """三态解析：{"kind": "live"|"closed"|"unknown", ...}。

        handler 用它区分「厅还在」（返回 body）、「厅已关闭」（返回终态）与
        「从未见过这个 id」（同样按终态处理，不再回 `{}`）。
        """
        key = (lobby_id or "").strip()
        with self._lock:
            self._expire_locked()
            hit = self._find_locked(key) if key else None
            if hit is not None:
                self._stats["get"] += 1
                self._stats["get_hit"] += 1
                return {"kind": "live", "lobby": copy.deepcopy(hit["body"]),
                        "lobby_id": str(hit["body"].get("lobbyId") or key)}
            tb = self._tombstone_for_locked(key)
            if tb is not None:
                self._stats["closed_hit"] += 1
                return {"kind": "closed", "lobby_id": tb["lobby_id"],
                        "reason": tb["reason"], "closed_at": tb["closed_at"]}
            return {"kind": "unknown", "lobby_id": key}

    def _clean_marker(self) -> Path | None:
        if self._persist_path is None:
            return None
        return self._persist_path.with_name(self._persist_path.name + ".clean")

    def _snapshot_locked(self) -> dict:
        now = self._now()

        def age(ts: float) -> float:
            return max(0.0, now - float(ts))

        return {
            "version": STATE_VERSION,
            "generation": self._generation,
            "seq": self._seq,
            "accounts": {
                a: {"ip": i.get("ip", ""), "instance_id": i.get("instance_id", ""),
                    "real_id": i.get("real_id"), "profile": i.get("profile"),
                    "lobby_id": i.get("lobby_id"), "age": age(i.get("last_seen", now))}
                for a, i in self._accounts.items()
            },
            "lobbies": {
                lid: {"owner": e["owner"], "body": e["body"], "age": age(e["updated"])}
                for lid, e in self._lobbies.items()
            },
            "tombstones": {
                lid: {"reason": t["reason"], "age": age(t["closed_at"])}
                for lid, t in self._tombstones.items()
            },
        }

    def _save_locked(self) -> None:
        """原子落盘（变更即写）。落盘失败不打断服务，只记一次统计。"""
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._persist_path.with_name(self._persist_path.name + ".tmp")
            tmp.write_text(json.dumps(self._snapshot_locked(), ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self._persist_path)
            marker = self._clean_marker()
            if marker is not None and marker.exists():
                marker.unlink()          # 运行期没有"干净退出"标记
        except Exception:  # noqa: BLE001
            self._stats["persist_error"] = self._stats.get("persist_error", 0) + 1

    def _restore(self) -> None:
        """启动时恢复状态；上一代**非干净退出**则把所有厅转成 server_reset tombstone。"""
        if self._persist_path is None:
            return
        snap = None
        if self._persist_path.exists():
            try:
                snap = json.loads(self._persist_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                snap = None
        marker = self._clean_marker()
        clean = bool(marker is not None and marker.exists())
        now = self._now()
        if isinstance(snap, dict) and snap.get("version") == STATE_VERSION:
            self._seq = int(snap.get("seq") or 0)
            for acc, info in (snap.get("accounts") or {}).items():
                if not isinstance(info, dict):
                    continue
                self._accounts[acc] = {
                    "ip": info.get("ip", ""), "instance_id": info.get("instance_id", ""),
                    "real_id": info.get("real_id"), "profile": info.get("profile"),
                    "lobby_id": info.get("lobby_id"),
                    "last_seen": now - float(info.get("age") or 0.0)}
                if info.get("ip"):
                    self._by_ip[info["ip"]] = acc
            for lid, entry in (snap.get("lobbies") or {}).items():
                if not isinstance(entry, dict) or not isinstance(entry.get("body"), dict):
                    continue
                self._lobbies[lid] = {"body": entry["body"], "owner": entry.get("owner", ""),
                                      "updated": now - float(entry.get("age") or 0.0)}
            for lid, tb in (snap.get("tombstones") or {}).items():
                if isinstance(tb, dict):
                    self._tombstones[lid] = {
                        "lobby_id": lid, "reason": tb.get("reason", "expired"),
                        "closed_at": now - float(tb.get("age") or 0.0)}
            self._generation = int(snap.get("generation") or 0)
            if not clean:
                # 非干净退出：状态不可信，全部按 "服务端重置" 关闭，让客户端拿到终态
                self._generation += 1
                for lid in list(self._lobbies):
                    self._lobbies.pop(lid, None)
                    self._remember_closed_locked(lid, "server_reset")
                for info in self._accounts.values():
                    info["lobby_id"] = None
        if marker is not None and marker.exists():
            marker.unlink()
        self._save_locked()

    def close(self) -> None:
        """优雅退出：落盘并写 `.clean` 标记（下次启动据此沿用旧状态）。"""
        with self._lock:
            self._expire_locked()
            self._save_locked()
            marker = self._clean_marker()
            if marker is not None:
                try:
                    marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------ 诊断
    def stats(self) -> dict:
        with self._lock:
            out = dict(self._stats)
            out["accounts"] = len(self._accounts)
            out["lobbies"] = len(self._lobbies)
            out["tombstones"] = len(self._tombstones)
            out["generation"] = self._generation
            out["persist"] = bool(self._persist_path)
            return out

    def dump(self) -> dict:
        """调试/日志用快照（脱敏：不含 IP 之外的个人信息）。"""
        with self._lock:
            return {
                "accounts": {a: {"ip": i.get("ip", ""), "lobby_id": i.get("lobby_id")}
                             for a, i in self._accounts.items()},
                "lobbies": {lid: e["body"].get("lobbyId", lid) for lid, e in self._lobbies.items()},
                "tombstones": {lid: t["reason"] for lid, t in self._tombstones.items()},
                "generation": self._generation,
            }


# ---------------------------------------------------------------------- 工具
def _looks_like_account(mid: str) -> bool:
    """真实 accountId 形状：全数字且足够长（客户端按 64 位整数解析，见 0xC0000005 事故）。"""
    return bool(mid) and len(mid) >= 15 and mid.isdigit()


def _sync_member_count_locked(lobby: dict) -> None:
    """按 players[] 里真实成员数刷新 numLobbyMembers（客户端用它对账厅内人数）。"""
    players = lobby.get("players")
    if not isinstance(players, list):
        return
    count = 0
    for p in players:
        if isinstance(p, dict) and _looks_like_account(str(p.get("memberAccountId") or "")):
            count += 1
    lobby["numLobbyMembers"] = count


def _merge_member_ids(lobby: dict, member_ids: list[str], owner: str) -> None:
    """把「已在厅里、但这版 body 没提到」的成员补回空槽（owner 周期 PUT 不能抹掉已加入成员）。"""
    players = lobby.get("players")
    if not isinstance(players, list):
        return
    present = _member_ids(lobby)
    ids = lobby.get("memberAccountIds")
    if not isinstance(ids, list):
        ids = []
        lobby["memberAccountIds"] = ids
    for mid in member_ids:
        if not _looks_like_account(mid) or mid == owner or mid in present:
            continue
        slot = _empty_slot(players)
        if slot is None:
            slot = {"memberAccountId": "", "isHost": False, "hasInfo": True}
            players.append(slot)
        slot["memberAccountId"] = mid
        slot["isHost"] = False
        if "hasInfo" in slot:
            slot["hasInfo"] = True
        if mid not in ids:
            ids.append(mid)
        present.append(mid)


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
