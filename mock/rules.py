"""路径归一与回放匹配索引。

匹配优先级（高 → 低）:
  1. ``exact``    — 含 query 的原样路径（例如 ``/api/FriendsV2/Block?offset=0&limit=100``）
  2. ``base``     — 去掉 query 的路径（query 变化时命中，例如 ``NewsFeed/801?fromTimeStamp=…``）
  3. ``template`` — 把数字段/uuid 段抽象后的模板（例如 ``/api/lobby/{uuid}``）
  4. 交由 handler 的 fallback（未知端点）

同一路径被抓多次时，索引保留**最后一次**抓到的响应（最接近"当前状态"）；
时间平移以该响应自己的抓包时刻为基准，见 :mod:`mock.timeshift`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .capture import CaptureStore, CapturedEntry

_DIGITS = re.compile(r"^\d+$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# 大厅 id 常带 Steam 连接串后缀：`<uuid>.r-20260323`
_UUID_TAIL = re.compile(
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(\..*)?$"
)
_HEX = re.compile(r"^[0-9a-fA-F]{6,}$")


def split_path(raw: str) -> tuple[str, str]:
    base, _, query = raw.partition("?")
    return base, query


def template_path(base: str) -> str:
    """把路径里的动态段抽象成模板：数字 → {n}，uuid → {uuid}。"""
    out = []
    for seg in base.split("/"):
        if _DIGITS.match(seg):
            out.append("{n}")
        else:
            m = _UUID_TAIL.match(seg)
            if m:
                out.append("{uuid}" + (m.group(2) or ""))
            else:
                out.append(seg)
    return "/".join(out)


@dataclass(frozen=True)
class Match:
    entry: CapturedEntry
    kind: str  # exact | base | template


class ReplayIndex:
    """抓包 → 可查询索引。"""

    def __init__(self, store: CaptureStore):
        self.store = store
        self.exact: dict[str, CapturedEntry] = {}
        self.base: dict[str, CapturedEntry] = {}
        self.template: dict[str, CapturedEntry] = {}
        self._build(store)

    def _build(self, store: CaptureStore) -> None:
        for e in store.entries:
            if not e.file:
                continue
            # 后写覆盖先写 => 保留最后一次抓到的响应
            self.exact[e.raw_path] = e
            self.base[e.base_path] = e
            self.template[template_path(e.base_path)] = e

    # ---- 查询 -------------------------------------------------------------
    def lookup(self, method: str, raw_path: str) -> Match | None:
        if raw_path in self.exact:
            return Match(self.exact[raw_path], "exact")
        base, _q = split_path(raw_path)
        if base in self.base:
            return Match(self.base[base], "base")
        tpl = template_path(base)
        if tpl in self.template:
            return Match(self.template[tpl], "template")
        return None

    def known_bases(self) -> set[str]:
        return set(self.base.keys())

    def describe(self) -> dict:
        return {
            "entries": len(self.store.entries),
            "exact": len(self.exact),
            "base": len(self.base),
            "template": len(self.template),
            "templates": sorted(self.template.keys()),
        }


# ---- 有状态端点路由表（handler 用；与回放索引是两套） -------------------------
STATE_ROUTES: list[tuple[str, re.Pattern, str]] = [
    ("POST", re.compile(r"^/api/Account/Login$"), "account_login"),
    # 加入门槛：客户端加入前会取对端的公钥，拿不到就永远不接纳该玩家（2026-11-22 定论）。
    ("GET", re.compile(r"^/api/Account/Keys$"), "account_keys"),
    ("POST", re.compile(r"^/api/Account/ReportPosition$"), "report_position"),
    ("GET", re.compile(r"^/api/Operation$"), "operation_list"),
    ("POST", re.compile(r"^/api/Operation/Create$"), "operation_create"),
    ("POST", re.compile(r"^/api/Operation/Abandon$"), "operation_abandon"),
    ("POST", re.compile(r"^/api/Operation/Mission/Start$"), "mission_start"),
    ("POST", re.compile(r"^/api/Operation/Mission/end$"), "mission_end"),
    ("POST", re.compile(r"^/api/Operation/Mission/SetMaxRewards$"), "mission_max_rewards"),
    ("POST", re.compile(r"^/api/Operation/Mission/ActivityV2$"), "mission_activity"),
    ("POST", re.compile(r"^/api/Mission/Session$"), "mission_session"),
    ("PUT", re.compile(r"^/api/lobby$"), "lobby_put"),
    ("POST", re.compile(r"^/api/lobby/accounts$"), "lobby_accounts"),
    ("GET", re.compile(r"^/api/lobby/accounts$"), "lobby_accounts"),
    ("DELETE", re.compile(r"^/api/lobby/clear$"), "lobby_clear"),
    ("GET", re.compile(r"^/api/lobby/(?P<lobby_id>[^/]+)$"), "lobby_get"),
    ("POST", re.compile(r"^/api/lobby/(?P<lobby_id>[^/]+)/member$"), "lobby_member"),
    ("PUT", re.compile(r"^/api/Progression/Items/Customize/v2$"), "progression_customize"),
    # T3 实机新发现（2026-11-21 任务结算前后出现，启动抓包里没有录制）：
    # 两者客户端都接受 200 {}，登记为「已知空响应」而不是笼统 fallback。
    ("POST", re.compile(r"^/api/Progression/inventory/consume$"), "progression_consume"),
    ("PUT", re.compile(r"^/api/Progression/Achievements$"), "progression_achievements"),
    ("POST", re.compile(r"^/api/Pes/(?P<pes>[A-Za-z0-9]+)$"), "pes_post"),
]


def match_state_route(method: str, raw_path: str) -> tuple[str, dict] | None:
    """命中返回 (route_name, path_params)；否则 None。"""
    base, _q = split_path(raw_path)
    for m, pat, name in STATE_ROUTES:
        if m != method.upper():
            continue
        hit = pat.match(base)
        if hit:
            params = {k: v for k, v in hit.groupdict().items() if v is not None}
            return name, params
    return None
