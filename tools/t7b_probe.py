"""T7b：PlayFab 不可达时，能否完整打一局（阶段1 的接管面验证）。

T7 已证：PlayFab 断开不阻断"启动到主界面"。但阶段1 真正要证明的是**整局单人也行**。
本探针把全流程包起来（跑完必复原本机代理）：

1. 确保 mock 在跑（官方 API → mock）、MITM 代理起来并把 PlayFab 设为 blackhole；
2. 备份并临时把 IE 代理指到 MITM（客户端旁路请求会失败，等价于"PlayFab 不可达"）；
3. 启动游戏 → 等主界面；然后**等用户操作打一局**（监控 mock 日志的任务链端点）；
4. 判定：出现 `Mission/Start`（进图）且出现 `Mission/end` 或 `Operation/Abandon`（结算/回船）；
5. 关闭游戏、恢复 IE 代理、停 MITM，写 JSON 结论。

用法（在后台跑，用户在游戏里操作）：
    python tools\\t7b_probe.py --timeout-mission 900
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from t7_probe import (  # noqa: E402
    MAIN_MENU_MARKERS, _ps, game_running, get_ie_proxy, kill_game,
    restore_ie_proxy, set_ie_proxy,
)

MISSION_START = "/api/Operation/Mission/Start"
MISSION_END = "/api/Operation/Mission/end"
MISSION_ABANDON = "/api/Operation/Abandon"


def start_mock(capture_bodies: bool = False) -> subprocess.Popen | None:
    """443 没监听就起 mock；返回 Popen 或 None（已在跑）。"""
    listeners = _ps("(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
                    "Where-Object { $_.LocalPort -eq 443 } | Measure-Object).Count")
    if (getattr(listeners, "stdout", "") or "").strip() not in ("", "0"):
        print("[t7b] mock 已在监听 443")
        return None
    mock_cmd = [sys.executable, str(REPO / "tools/mockctl.py"), "serve"]
    if capture_bodies:
        mock_cmd.append("--capture-bodies")
    proc = subprocess.Popen(mock_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=str(REPO))
    for _ in range(20):
        time.sleep(1)
        listeners = _ps("(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
                        "Where-Object { $_.LocalPort -eq 443 } | Measure-Object).Count")
        if (getattr(listeners, "stdout", "") or "").strip() not in ("", "0"):
            print("[t7b] mock 已启动")
            return proc
    raise SystemExit("mock 起不来（443 未监听）")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="T7b：PlayFab 不可达下的整局单人验证")
    p.add_argument("--name", default="playfab-blackhole-mission")
    p.add_argument("--proxy-port", type=int, default=18092)
    p.add_argument("--timeout-menu", type=int, default=180)
    p.add_argument("--timeout-mission", type=int, default=900)
    p.add_argument("--launch", choices=["steam", "exe"], default="steam",
                   help="steam=正常 EAC-on 启动；exe=直启 bin/helldivers2.exe（EAC-off）")
    p.add_argument("--exe", default=r"D:\SteamLibrary\steamapps\common\Helldivers 2\bin\helldivers2.exe")
    p.add_argument("--menu-only", action="store_true", help="只验证到主界面（不等待任务链）")
    p.add_argument("--capture-bodies", action="store_true", help="mock 以 --capture-bodies 启动（记录登录体 schema）")
    p.add_argument("--sslkeylog", type=Path, default=None, help="P3：设置 SSLKEYLOGFILE（探测 TLS/DTLS 能否解）")
    p.add_argument("--capture-pcap", type=Path, default=None, help="P3：会话期间用 dumpcap 抓包")
    p.add_argument("--blackhole", default="974a9.playfabapi.com",
                   help="逗号分隔：本轮要黑洞的域名（默认只 PlayFab；阶段1 验收可加 Sony 四条域）")
    p.add_argument("--cert-dir", type=Path, default=REPO / "state/certs")
    p.add_argument("--mock-log", type=Path, default=REPO / "state/logs/mock.log")
    p.add_argument("--out-dir", type=Path, default=REPO / "state/evidence/t7b")
    args = p.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    trust = _ps(f"certutil -user -verifystore Root "
                f"{(json.loads((args.cert_dir / 'session.json').read_text(encoding='utf-8'))['trust_thumbprint_sha1'])}")
    if (getattr(trust, "returncode", 1)) != 0:
        raise SystemExit("研究 CA 未受信：先跑 python tools/mockctl.py cert-install")

    if game_running():
        print("[t7b] 先关闭已在运行的游戏…")
        kill_game()
    mock_proc = start_mock(capture_bodies=args.capture_bodies)
    baseline = args.mock_log.stat().st_size if args.mock_log.exists() else 0

    log_path = args.out_dir / "mitm.jsonl"
    if log_path.exists():
        log_path.unlink()
    intercept = ("974a9.playfabapi.com,api.epicgames.dev,crs.api.wwsga.me,"
                 "testament.api.wwsga.me,clientconfig.api.wwsga.me,pss-cloud.net,"
                 "api.live.prod.thehelldiversgame.com")
    proxy = subprocess.Popen(
        [sys.executable, str(REPO / "tools/mitm_proxy.py"),
         "--port", str(args.proxy_port), "--intercept", intercept,
         "--mock-hosts", "api.live.prod.thehelldiversgame.com",
         "--mock-ca", str(args.cert_dir / "ca.pem"),
         "--cert-dir", str(args.cert_dir), "--log", str(log_path),
         "--blackhole-hosts", args.blackhole],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(REPO))
    time.sleep(2.5)
    old = get_ie_proxy()
    result = {"name": args.name, "launch": args.launch, "blackhole": args.blackhole, "reached_main_menu": False,
              "mission_started": False, "mission_ended": False, "abandoned": False,
              "elapsed_menu_s": None, "elapsed_mission_s": None, "new_lines": 0,
              "non_200": 0, "fallback": 0, "error": None, "chain": []}
    try:
        set_ie_proxy(f"127.0.0.1:{args.proxy_port}")
        print(f"[t7b] IE 代理 → 127.0.0.1:{args.proxy_port}（黑洞={args.blackhole or '无'}），启动游戏（{args.launch}）…", flush=True)
        env = dict(os.environ)
        if args.sslkeylog:
            args.sslkeylog.parent.mkdir(parents=True, exist_ok=True)
            env["SSLKEYLOGFILE"] = str(args.sslkeylog)
        cap = None
        if args.capture_pcap:
            args.capture_pcap.parent.mkdir(parents=True, exist_ok=True)
            if args.capture_pcap.exists():
                args.capture_pcap.unlink()
            dur = args.timeout_menu + args.timeout_mission + 60
            cap = subprocess.Popen(
                [r"C:\Program Files\Wireshark\dumpcap.exe", "-i", "9", "-i", "11",
                 "-f", "udp or tcp port 443 or tcp port 7897",
                 "-a", f"duration:{dur}", "-w", str(args.capture_pcap)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)
        if args.launch == "exe":
            exe = Path(args.exe)
            if not exe.exists():
                raise SystemExit(f"找不到 exe: {exe}")
            subprocess.Popen([str(exe)], cwd=str(exe.parent.parent), env=env)
        else:
            subprocess.Popen(["cmd", "/c", "start", "steam://rungameid/553850"], shell=False)

        # ---- 阶段1：等主界面 ----
        t0 = time.time()
        deadline = t0 + args.timeout_menu
        while time.time() < deadline:
            time.sleep(3)
            with open(args.mock_log, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(baseline)
                chunk = fh.read()
            if any(m in chunk for m in MAIN_MENU_MARKERS):
                result["reached_main_menu"] = True
                result["elapsed_menu_s"] = round(time.time() - t0, 1)
                break
        if not result["reached_main_menu"]:
            result["error"] = "timeout: 未到主界面"
            return 1
        print(f"[t7b] 已到主界面（{result['elapsed_menu_s']}s）", flush=True)
        if args.menu_only:
            time.sleep(8)
            result["error"] = None
            raise SystemExit(0)
        print(f"[t7b] **请在游戏里打完整一局**（进图→局内→撤离结算→回船），"
              f"最长等待 {args.timeout_mission}s…", flush=True)

        # ---- 阶段2：等任务链（用户操作） ----
        t1 = time.time()
        seen: set[str] = set()
        deadline = t1 + args.timeout_mission
        while time.time() < deadline:
            time.sleep(4)
            with open(args.mock_log, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(baseline)
                chunk = fh.read()
            for marker, key in ((MISSION_START, "mission_started"),
                                (MISSION_END, "mission_ended"),
                                (MISSION_ABANDON, "abandoned")):
                if marker in chunk and not result[key]:
                    result[key] = True
                    seen.add(marker)
                    result["chain"].append({"endpoint": marker,
                                            "after_s": round(time.time() - t1, 1)})
                    if key == "mission_started":
                        result["elapsed_mission_s"] = round(time.time() - t1, 1)
                    print(f"[t7b] 命中 {marker} @ +{round(time.time() - t1, 1)}s", flush=True)
            if result["mission_ended"] or result["abandoned"]:
                # 结算/回船后多等 20 s，收集回船轮询
                time.sleep(20)
                break
        if not (result["mission_ended"] or result["abandoned"]):
            result["error"] = "timeout: 未出现结算/回船端点"
    finally:
        with open(args.mock_log, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(baseline)
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        result["new_lines"] = len(lines)
        result["non_200"] = sum(1 for ln in lines if "-> " in ln and "-> 200" not in ln
                                and "-> 202" not in ln)
        result["fallback"] = sum(1 for ln in lines if "[fallback]" in ln)
        kill_game()
        restore_ie_proxy(old)
        proxy.terminate()
        if cap is not None:
            cap.terminate()
            try:
                cap.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cap.kill()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()
        if mock_proc is not None:
            mock_proc.terminate()
        (args.out_dir / f"{args.name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[t7b] 结论: {json.dumps(result, ensure_ascii=False)}", flush=True)
    ok = result["mission_started"] and (result["mission_ended"] or result["abandoned"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
