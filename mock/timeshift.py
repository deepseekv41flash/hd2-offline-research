"""抓包 → 当前时间的字段平移。

为什么必须平移：抓包里的时间字段是「那一刻」的战争时钟/Unix 时间。回放时如果原样返回，
客户端可能判定数据过期（任务/事件倒计时已结束、商店轮换过期）。平移策略是**按每条响应
自己的抓包时刻**计算 ``delta = now - captured_at``，这样同一条响应内部的相对时间关系不变，
而整体跟着真实时间一起走。

三个时间基准（2026-11-18 抓包实测）:

  war      战争时钟（Status.time 基准；Status 内事件 expireTime / assignment startTime 同基准）
  elapsed  「战争已进行秒数」独立计数器（timeSinceStart.secondsSinceStart，与 war 差约 1.9M 秒）
  unix     Unix 秒（warinfo.startDate/endDate、FriendsV2.blockedTime、StoreFront.rotation.expireTime）
  keep     不倒计时/绝对历史值，原样保留（expiresIn 倒计时、历史 blockedTime、war 起止日期）

未识别的「看起来像时间」的键不会被动，只记录一次（``ShiftStats.unknown``），方便下次抓包扩展规则。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

WAR = "war"
ELAPSED = "elapsed"
UNIX = "unix"
UNIX_FRESH = "unix_fresh"
KEEP = "keep"


@dataclass(frozen=True)
class Rule:
    """一条平移规则：路径 pattern + 键名 pattern → 基准。"""

    path_re: str
    key_re: str
    base: str
    note: str = ""


DEFAULT_RULES: tuple[Rule, ...] = (
    # 战况板：整份文档都基于战争时钟
    Rule(r"^/api/WarSeason/\d+/Status$",
         r"^(time|expireTime|startTime|endTime|endWarTime|currentElectionEndWarTime)$",
         WAR, "Status 事件/空间站倒计时都用 Status.time 基准"),
    Rule(r"^/api/WarSeason/\d+/timeSinceStart$", r"^secondsSinceStart$", ELAPSED,
         "独立计数器，不能用 Status.time 替代"),
    Rule(r"^/api/WarSeason/\d+/warinfo$", r"^(startDate|endDate)$", KEEP,
         "战争起止是 Unix 绝对时间，平移会改语义"),
    Rule(r"^/api/v2/Assignment/War/\d+$", r"^startTime$", WAR,
         "Major Order 起始时刻（战争时钟）"),
    Rule(r"^/api/v2/Assignment/War/\d+$", r"^expiresIn$", KEEP,
         "剩余秒数：startTime 平移后保持原剩余量 ⇒ 订单始终「还有这么久」"),
    Rule(r"^/api/StoreFront/rotation$", r"^expireTime$", UNIX_FRESH,
         "商店轮换过期时刻；若已过期则重置到 now+refresh_span"),
    # ---- 以下为「确认过、保持原值」的类别（显式登记，避免落入 unknown）----
    Rule(r"^/api/FriendsV2/", r"^blockedTime$", KEEP,
         "拉黑时刻是历史 Unix 时间，平移会改语义"),
    Rule(r"^/api/lobby/", r"^startTime$", KEEP,
         "大厅开局时刻；未开局时为 0"),
    Rule(r"^/api/Progression$", r"^expiresIn$", KEEP,
         "养成界面里的倒计时（抓包时多为 0）"),
    Rule(r"^/api/v2/Assignment/Player$", r"^expiresIn$", KEEP,
         "每日命令剩余秒数（不做窗口平移）"),
    Rule(r"^/api/Stats/", r"^.*[Tt]ime$", KEEP,
         "战绩时长/累计值，是统计数据不是时钟"),
)

# 只用于「报告未识别字段」，不参与平移
TIME_LIKE = re.compile(
    r"^(time|date|startTime|endTime|expireTime|expiresIn|expiresAt|deadline|"
    r"currentElectionEndWarTime|endWarTime|timestamp|ts|.*Date|.*Time)$"
)


@dataclass
class ShiftStats:
    shifted: list[tuple[str, str, int | float, int | float]] = field(default_factory=list)
    kept: list[tuple[str, str]] = field(default_factory=list)
    unknown: list[tuple[str, str, object]] = field(default_factory=list)
    refreshed: list[tuple[str, int, int]] = field(default_factory=list)
    delta_seconds: int = 0

    def summary(self) -> dict:
        return {
            "delta_seconds": self.delta_seconds,
            "shifted": len(self.shifted),
            "kept": len(self.kept),
            "refreshed": len(self.refreshed),
            "unknown": len(self.unknown),
        }


def _rules_for(path: str) -> list[tuple[re.Pattern, Rule]]:
    out = []
    for r in DEFAULT_RULES:
        pat = re.compile(r.path_re)
        if pat.match(path):
            out.append((re.compile(r.key_re), r))
    return out


def shift_document(
    doc,
    *,
    path: str,
    captured_at: datetime,
    now: datetime | None = None,
    refresh_span_seconds: int = 7 * 86400,
    stats: ShiftStats | None = None,
) -> tuple[object, ShiftStats]:
    """返回 (平移后的副本, 统计)。不修改入参。"""
    now = now or datetime.now(timezone.utc)
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    delta = int((now - captured_at).total_seconds())
    st = stats or ShiftStats()
    st.delta_seconds = delta
    rules = _rules_for(path)
    out = copy.deepcopy(doc)
    now_unix = int(now.timestamp())
    _walk(out, "", path, delta, rules, st, now_unix, refresh_span_seconds)
    return out, st


def _apply(value, base: str, delta: int, now_unix: int, refresh_span: int,
           pointer: str, st: ShiftStats):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    if base == WAR:
        new = value + delta
        st.shifted.append((pointer, WAR, value, new))
        return new
    if base == ELAPSED:
        new = value + delta
        st.shifted.append((pointer, ELAPSED, value, new))
        return new
    if base == UNIX:
        st.shifted.append((pointer, UNIX, value, value))
        return value + delta
    if base == UNIX_FRESH:
        if value <= now_unix:
            new = now_unix + refresh_span
            st.refreshed.append((pointer, int(value), int(new)))
            return new
        st.kept.append((pointer, UNIX_FRESH))
        return value
    if base == KEEP:
        st.kept.append((pointer, KEEP))
        return value
    return value


def _walk(node, prefix: str, path: str, delta: int, rules, st: ShiftStats,
          now_unix: int, refresh_span: int) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                _walk(v, p, path, delta, rules, st, now_unix, refresh_span)
                continue
            hit = None
            for key_pat, rule in rules:
                if key_pat.match(k):
                    hit = rule
                    break
            if hit is not None:
                node[k] = _apply(v, hit.base, delta, now_unix, refresh_span, p, st)
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and TIME_LIKE.match(k):
                st.unknown.append((p, k, v))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            p = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)):
                _walk(v, p, path, delta, rules, st, now_unix, refresh_span)


def war_clock_now(anchors: dict, now: datetime | None = None) -> int | None:
    """诊断用：由抓包锚点推算「现在」的战争时钟（Status.time 基准）。"""
    now = now or datetime.now(timezone.utc)
    a = anchors.get("war_clock")
    if not a:
        return None
    return int(a["value"] + (now - a["at"]).total_seconds())


def elapsed_now(anchors: dict, now: datetime | None = None) -> int | None:
    now = now or datetime.now(timezone.utc)
    a = anchors.get("war_elapsed")
    if not a:
        return None
    return int(a["value"] + (now - a["at"]).total_seconds())
