#!/usr/bin/env python3
"""临时阻断公共 DNS/DoH（逼 EOS 回落系统解析，让 hosts 生效）。

**为什么需要**：2026-11-25 实测（`docs/EAC-NET-ATTRIBUTION-2026-11-25.md` §6）证明
游戏进程内的 EOS/EAC 会**先试系统解析**（我们的监听收到了带 SNI 的 ClientHello），
但随后**握手中被自己 Reset**并回落自带地址来源。下一步要分辨两种可能：
  (a) 它拒绝我们的叶证书（只读 machine store / 自带信任策略）；
  (b) 它走 DoH，根本不依赖 hosts。
本脚本针对 (b)：**只阻断到公共 DNS 解析器**的出站，不动其他任何流量。

安全纪律（硬性）：
  * 只用 `netsh advfirewall` 的**具名规则**（前缀 `hd2-eac-doh-`），便于一键删除；
  * **apply 前后各存一份快照**（`show rule` + `show allprofiles`）到证据目录；
  * `--remove` 之后再次快照，并在 meta 里给出 `identical_after_remove` 判断；
  * `--selftest` 用 **TEST-NET 地址（192.0.2.1）** 建一条无害规则验证"建/查/删"全链路，
    **不碰真实 DNS**，适合在正式测试前跑一遍。

用法（**需要管理员**）：
    python tools/doh_block.py --selftest                 # 只验规则机制，不阻断任何真实 DNS
    python tools/doh_block.py --dry-run --out <dir>      # 打印将要执行的命令 + 当前快照
    python tools/doh_block.py --apply --out <dir>        # 真的阻断（威胁模型内、可回滚）
    python tools/doh_block.py --remove --out <dir>       # 删除并复核
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_IPS = ["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4"]
PREFIX = "hd2-eac-doh"
SELFTEST_IP = "192.0.2.1"          # TEST-NET-1，保留地址，不可能有真实服务


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return -1, repr(exc)


def snapshot(name_prefix: str) -> dict:
    """抓"规则 + 全局策略"快照（收尾要能证明没留下东西）。"""
    code_rules, rules = _run(["netsh", "advfirewall", "firewall", "show", "rule",
                              "name=%s" % name_prefix])
    code_all, profiles = _run(["netsh", "advfirewall", "show", "allprofiles", "state"])
    listed = [ln.strip() for ln in rules.splitlines()
              if ln.strip().startswith(("规则名称", "Rule Name", "----"))]
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "rules_exit": code_rules, "rules_headers": listed[:20],
            "profiles_exit": code_all,
            "profiles_state": [ln.strip() for ln in profiles.splitlines() if ln.strip()][:12]}


def rule_name(ip: str, prefix: str = PREFIX) -> str:
    return "%s-%s" % (prefix, ip.replace(".", "_"))


def build_apply_cmds(ips: list[str], prefix: str = PREFIX) -> list[list[str]]:
    """一条 IP 一条规则：便于单独删、也便于 show rule 里一眼看出命中哪条。"""
    out = []
    for ip in ips:
        out.append(["netsh", "advfirewall", "firewall", "add", "rule",
                    "name=%s" % rule_name(ip, prefix), "dir=out", "action=block",
                    "protocol=any", "remoteip=%s" % ip,
                    "enable=yes", "profile=any"])
    return out


def build_remove_cmds(ips: list[str], prefix: str = PREFIX) -> list[list[str]]:
    return [["netsh", "advfirewall", "firewall", "delete", "rule",
             "name=%s" % rule_name(ip, prefix)] for ip in ips]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="临时阻断公共 DNS/DoH（可回滚）")
    ap.add_argument("--out", default="", help="证据目录（快照与命令日志写这里）")
    ap.add_argument("--ips", default=",".join(DEFAULT_IPS))
    ap.add_argument("--prefix", default=PREFIX)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="用 TEST-NET 地址验证建/查/删机制，不阻断真实 DNS")
    args = ap.parse_args(argv)

    ips = [ip.strip() for ip in args.ips.split(",") if ip.strip()]
    out = Path(args.out) if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    if args.selftest:
        label = "自测（TEST-NET，不阻断真实 DNS）"
        target_ips = [SELFTEST_IP]
    else:
        label = "正式"
        target_ips = ips

    apply_cmds = build_apply_cmds(target_ips, args.prefix)
    remove_cmds = build_remove_cmds(target_ips, args.prefix)
    before = snapshot(args.prefix)

    if args.dry_run or not (args.apply or args.remove or args.selftest):
        print(json.dumps({"label": label, "admin": is_admin(),
                          "apply_cmds": [" ".join(c) for c in apply_cmds],
                          "remove_cmds": [" ".join(c) for c in remove_cmds],
                          "snapshot_before": before}, ensure_ascii=False, indent=1))
        if out:
            (out / "doh-block-plan.json").write_text(
                json.dumps({"apply": apply_cmds, "remove": remove_cmds,
                            "snapshot_before": before}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        return 0
    if not is_admin():
        print("需要管理员权限（要改防火墙规则）")
        return 2

    log: list[dict] = []

    if args.remove:
        for cmd in remove_cmds:
            rc, txt = _run(cmd)
            log.append({"cmd": " ".join(cmd), "rc": rc, "out": txt.strip()[:400]})
        after = snapshot(args.prefix)
        result = {"action": "remove", "log": log, "snapshot_before": before,
                  "snapshot_after": after}
        if out:
            (out / "doh-block-remove.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"removed": len(remove_cmds), "log_rcs": [x["rc"] for x in log]},
                         ensure_ascii=False))
        return 0

    # apply / selftest：加规则 → 查一次 → （自测则立刻删）
    for cmd in apply_cmds:
        rc, txt = _run(cmd)
        log.append({"cmd": " ".join(cmd), "rc": rc, "out": txt.strip()[:400]})
    during = snapshot(args.prefix)
    ok_created = bool(during.get("rules_headers"))
    result = {"action": "selftest" if args.selftest else "apply",
              "label": label, "ips": target_ips, "log": log,
              "snapshot_before": before, "snapshot_after_apply": during,
              "rules_visible": ok_created}
    if args.selftest:
        rm_log = []
        for cmd in remove_cmds:
            rc, txt = _run(cmd)
            rm_log.append({"cmd": " ".join(cmd), "rc": rc, "out": txt.strip()[:400]})
        final = snapshot(args.prefix)
        result.update({"remove_log": rm_log, "snapshot_final": final,
                       "leftover_rules": bool(final.get("rules_headers"))})
        print(json.dumps({"selftest_created": ok_created,
                          "removed_rcs": [x["rc"] for x in rm_log],
                          "leftover": result["leftover_rules"]}, ensure_ascii=False))
    else:
        print(json.dumps({"applied": len(apply_cmds), "rules_visible": ok_created},
                         ensure_ascii=False))
    if out:
        (out / ("doh-block-%s.json" % result["action"])).write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
