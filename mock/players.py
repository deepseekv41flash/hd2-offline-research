"""按玩家分档的响应覆盖层（自建服形态，2026-11-24 加）。

契约见 `docs/SELFHOST-SAVE-CONTRACT-2026-11-24.md`。目录布局（`save-export` 产物）：

```
<root>/index.json                     # {"schema":1,"players":[{"steamId64","hd2AccountId","saveId"}]}
<root>/players/<steamId64>/progression.json
<root>/players/<steamId64>/inventory.json
<root>/players/<steamId64>/stats_profile.json
<root>/players/<steamId64>/stats_profile_war.json    # 可选：/api/Stats/profile/<n>/summary
<root>/players/<steamId64>/customize_v2.json         # 可选：/api/Progression/Items/Customize/v2
<root>/players/<steamId64>/mission_end.json          # 可选：POST /api/Operation/Mission/end
```

身份解析顺序（2026-11-24 实测：游戏客户端**只在 lobby 端点上**带 `X-HD2-Account`，
登录体里也只有 `instanceId/token/publicKey`，没有账号 id；而 `/api/Progression` 在登录后
~1 s 就被读走——所以**多人在线时客户端若不加盖身份头，服务端在这第一读时无法分辨是谁**）：

1. 请求头 `X-HD2-Account`（值可以是 `steamId64` 或 HD2 20 位账号 id）——最可靠；
   产品引擎（>=0.7.0-alpha.1）还会给 `X-HD2-Account-Source`：`prebind`（仅预绑定、未校验）/
   `getmotd`/`getmessages`（本局已校验）。**`prebind` 视为"未证实断言"**：命中索引就照给，
   但**不学 IP 映射**；命中不到就回落公共数据，绝不用"唯一玩家"去猜。
2. 本进程学到的 `来源地址 → 玩家` 映射（任何带身份头的请求都会刷新；仅内存）；
3. 索引里**只有 1 个玩家**时直接用它（单人演示/单机自建服）。

> 此外约定：引擎若判定本局身份**不可信**（GetMotd 校验失败等），可以只发
> `X-HD2-Account-Source: uncertain`（不带 `X-HD2-Account`）；本层据此**跳过 IP/单玩家兜底**，
> 直接回落公共数据——避免把历史 IP 映射当成当前身份。
>
> 因此多人场景要正确分档，需要**客户端侧（MITM 引擎）在请求上加盖身份头**，
> 或产品化时改成 Steam 票据校验。见契约档 §9。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

STEAM_RE = re.compile(r"^\d{17}$")
HD2_RE = re.compile(r"^\d{19,20}$")

# 端点 → 玩家目录里的文件名（war 分档的 stats 用通配匹配）
ENDPOINT_FILES: dict[tuple[str, str], str] = {
    ("GET", "/api/Progression"): "progression.json",
    ("GET", "/api/Progression/inventory"): "inventory.json",
    ("GET", "/api/Stats/profile/summary"): "stats_profile.json",
    ("GET", "/api/Progression/Items/Customize/v2"): "customize_v2.json",
    ("POST", "/api/Operation/Mission/end"): "mission_end.json",
}
WAR_STATS_RE = re.compile(r"^/api/Stats/profile/\d+/summary$")
WAR_STATS_FILE = "stats_profile_war.json"


class Player:
    def __init__(self, steam_id: str, hd2_id: str, directory: Path):
        self.steam_id = steam_id
        self.hd2_id = hd2_id
        self.dir = directory

    def describe(self) -> dict:
        return {"steamId64": self.steam_id, "hd2AccountId": self.hd2_id}


class PlayerStore:
    """加载 `players/` 覆盖层，并按请求解析出"这是哪个玩家"。"""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.players: dict[str, Player] = {}
        self.by_hd2: dict[str, str] = {}
        self._ip_to_steam: dict[str, str] = {}
        self.last_source = ""
        self.last_header_source = ""
        self._load()

    # ---- 加载 -------------------------------------------------------------
    def _load(self) -> None:
        players_dir = self.root / "players" if (self.root / "players").is_dir() else self.root
        index_path = self.root / "index.json"
        index = {}
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8"))
        listed = {str(p.get("steamId64")): p for p in (index.get("players") or [])}
        for pdir in sorted(players_dir.iterdir()) if players_dir.is_dir() else []:
            if not pdir.is_dir():
                continue
            sid = pdir.name
            meta = listed.get(sid) or {}
            hd2 = str(meta.get("hd2AccountId") or "")
            if not hd2:
                # index 缺项时从 manifest 兜底
                man = pdir / "manifest.json"
                if man.exists():
                    try:
                        info = json.loads(man.read_text(encoding="utf-8"))
                        hd2 = str((info.get("identity") or {}).get("hd2AccountId") or "")
                    except Exception:  # noqa: BLE001
                        hd2 = ""
            player = Player(sid, hd2, pdir)
            self.players[sid] = player
            if hd2:
                self.by_hd2[hd2] = sid

    # ---- 解析 -------------------------------------------------------------
    @staticmethod
    def header_ids(headers: dict | None) -> list[str]:
        if not headers:
            return []
        raw = ""
        for key, value in headers.items():
            if str(key).lower().replace("_", "-") == "x-hd2-account":
                raw = str(value or "")
                break
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]

    @staticmethod
    def header_source(headers: dict | None) -> str:
        """`X-HD2-Account-Source` 的值（小写）；没有就返回空串。"""
        if not headers:
            return ""
        for key, value in headers.items():
            if str(key).lower().replace("_", "-") == "x-hd2-account-source":
                return str(value or "").strip().lower()
        return ""

    def _from_ids(self, ids: list[str]) -> Player | None:
        for ident in ids:
            if ident in self.players:
                return self.players[ident]
            sid = self.by_hd2.get(ident)
            if sid and sid in self.players:
                return self.players[sid]
        return None

    def resolve(self, headers: dict | None = None, client_addr: str = "") -> Player | None:
        """按「头 → 学到的 IP 映射 → 唯一玩家」解析；解析不到返回 None（回落公共数据）。

        关键规则（2026-11-24 与产品侧对齐）：
        * 只要**带了身份头**，就认为客户端已给出身份断言 —— 命中索引就回该玩家，
          **命中不到就回落公共数据，绝不用"唯一玩家"去猜**（否则会把别人的档给错人）；
        * `prebind` 是未校验断言：可以照给，但**不写 IP 映射**（避免污染后续无头请求）。

        `resolve_ex()` 的薄包装（老调用方/单测用）。
        """
        return self.resolve_ex(headers=headers, client_addr=client_addr)[0]

    def resolve_ex(self, headers: dict | None = None,
                   client_addr: str = "") -> tuple[Player | None, str]:
        """同 `resolve()`，但**原子地**返回本次解析依据，避免撞上 `last_source` 的 sticky 语义。

        返回 `(player, how)`，`how` ∈
          `header:<prebind|getmotd|getmessages|unknown>` / `header-unresolved:<tag>` /
          `uncertain:<src>` / `ip` / `single` / `""`（回落公共数据）。

        为什么要成对返回：`last_source` 存在实例上，多线程下"解析完再读 last_source"
        可能读到别的线程刚写的值；日志里"这个响应靠什么解析的"必须与响应一起产生。
        """
        player, how = self._resolve(headers=headers, client_addr=client_addr)
        self.last_source = how
        return player, how

    def _resolve(self, headers: dict | None = None,
                 client_addr: str = "") -> tuple[Player | None, str]:
        ids = self.header_ids(headers)
        src = self.header_source(headers)
        self.last_header_source = src
        if not ids and src:
            # 引擎显式声明"本局身份不可信"（如 GetMotd 校验失败）：只带 Source、不带 Account。
            # 此时必须跳过 IP 映射/唯一玩家兜底 —— 否则会把之前学到的映射当成当前身份用。
            return None, f"uncertain:{src}"
        if ids:
            player = self._from_ids(ids)
            tag = src or "unknown"
            if player is not None:
                if src != "prebind":
                    self._learn_ip(client_addr, player)
                return player, f"header:{tag}"
            return None, f"header-unresolved:{tag}"
        if client_addr:
            sid = self._ip_to_steam.get(client_addr)
            if sid in self.players:
                return self.players[sid], "ip"
        if len(self.players) == 1:
            return next(iter(self.players.values())), "single"
        return None, ""

    def _learn_ip(self, client_addr: str, player: Player) -> None:
        if client_addr:
            self._ip_to_steam[client_addr] = player.steam_id

    def remember(self, headers: dict | None = None, client_addr: str = "") -> None:
        """把「可信来源的身份头请求」学成 IP→玩家 映射（prebind 不学）。"""
        if self.header_source(headers) == "prebind":
            return
        player = self._from_ids(self.header_ids(headers))
        if player is not None:
            self._learn_ip(client_addr, player)

    # ---- 取响应体 ---------------------------------------------------------
    def body_for(self, player: Player, method: str, raw_path: str) -> bytes | None:
        """按 方法+路径 找玩家目录里的覆盖文件；没有就返回 None（调用方回落公共数据）。"""
        method = (method or "GET").upper()
        base = raw_path.split("?", 1)[0].rstrip("/") or "/"
        name = ENDPOINT_FILES.get((method, base))
        if name is None and method == "GET" and WAR_STATS_RE.match(base):
            name = WAR_STATS_FILE
        if name is None:
            return None
        path = player.dir / name
        if not path.exists():
            return None
        return path.read_bytes()

    # ---- 诊断 -------------------------------------------------------------
    def describe(self) -> dict:
        return {"count": len(self.players), "root": str(self.root),
                "players": [p.describe() for p in self.players.values()],
                "learned_ips": len(self._ip_to_steam), "last_source": self.last_source,
                "last_header_source": self.last_header_source}
